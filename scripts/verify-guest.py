#!/usr/bin/env python3
"""Guest-only acceptance probes, executed exclusively on the disposable VM disk."""

import json
import html
import os
from pathlib import Path
import pwd
import re
import selectors
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request


BROWSER_PAGE = '''<!doctype html>
<meta charset="utf-8"><meta name="color-scheme" content="light dark">
<title>Dux verification loading</title>
<style>body { font: 24px sans-serif; color-scheme: light dark; padding: 40px; }
button { font: inherit; padding: 16px; } video, canvas { width: 500px; display: block; }
[hidden] { display: none !important; }</style>
<h1>Offline Dux verification</h1><p id="status"></p>
<button id="share" autofocus>Share a screen or window (C)</button>
<input id="picker" type="file" hidden><video muted autoplay></video><canvas hidden></canvas>
<script>
const media = matchMedia('(prefers-color-scheme: dark)');
const state = {scheme: '', changes: 0, capture: 'idle', frames: 0, width: 0, height: 0, file: 'idle'};
function publish() {
  state.scheme = media.matches ? 'dark' : 'light';
  document.title = 'DUX-VERIFY ' + JSON.stringify(state);
  document.getElementById('status').textContent = JSON.stringify(state);
}
media.addEventListener('change', () => { state.changes++; publish(); });
const picker = document.getElementById('picker');
picker.addEventListener('cancel', () => { state.file = 'cancelled'; publish(); });
picker.addEventListener('change', () => { state.file = 'selected:' + picker.files[0].name; publish(); });
document.addEventListener('keydown', event => {
  if (event.key === 'c') document.getElementById('share').click();
  if (event.key === 'o') { state.file = 'pending'; publish(); picker.click(); }
});
document.getElementById('share').onclick = async () => {
  state.capture = 'pending'; publish();
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
    const video = document.querySelector('video');
    video.srcObject = stream;
    const canvas = document.querySelector('canvas');
    canvas.width = 320; canvas.height = 200;
    const context = canvas.getContext('2d', {willReadFrequently: true});
    function sampleFrame() {
      try {
        state.frames++;
        state.width = video.videoWidth; state.height = video.videoHeight;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let nonBlack = 0, minimum = 765, maximum = 0;
        const colors = new Set();
        for (let y = 8; y < canvas.height; y += 16) {
          for (let x = 8; x < canvas.width; x += 16) {
            const i = (y * canvas.width + x) * 4;
            const sum = pixels[i] + pixels[i+1] + pixels[i+2];
            if (sum > 60) nonBlack++;
            minimum = Math.min(minimum, sum); maximum = Math.max(maximum, sum);
            colors.add([pixels[i] >> 3, pixels[i+1] >> 3, pixels[i+2] >> 3].join(','));
          }
        }
        state.pixels = {nonBlack, minimum, maximum, colors: colors.size};
        if (state.frames >= 3 && nonBlack >= 10 && maximum - minimum > 60 && colors.size >= 8) {
          state.capture = 'passed'; canvas.hidden = false; video.hidden = true;
          stream.getTracks().forEach(track => track.stop());
        } else {
          video.requestVideoFrameCallback(sampleFrame);
        }
        publish();
      } catch (error) {
        state.capture = 'error: ' + error.name + ': ' + error.message; publish();
        stream.getTracks().forEach(track => track.stop());
      }
    }
    video.requestVideoFrameCallback(sampleFrame);
    await video.play();
  } catch (error) { state.capture = 'error: ' + error.name + ': ' + error.message; publish(); }
};
publish();
</script>
'''


def wait_until(description, check, timeout=60):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, subprocess.CalledProcessError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.5)
    raise TimeoutError(f'{description} did not become ready. Last error: {last_error}')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def run_interactive(command, replies, timeout=180):
    pending = list(replies)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, bufsize=0, start_new_session=True)
    deadline = time.monotonic() + timeout
    buffered = b''
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'Interactive command stalled: {command}')
                if not selector.select(min(remaining, 0.25)):
                    continue
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                print(chunk.decode(errors='replace'), end='', flush=True)
                buffered += chunk
                if pending and pending[0][0] in buffered:
                    prompt, answer = pending.pop(0)
                    buffered = buffered.split(prompt, 1)[1]
                    # A fresh upstream BufferedReader per prompt can discard
                    # prefetched answers. Send only the current answer.
                    process.stdin.write(answer)
                    process.stdin.flush()
            code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if code or pending:
                raise RuntimeError(f'Interactive command failed ({code}); unanswered prompts: {pending}')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        process.stdin.close()
        process.stdout.close()


def prepare(root):
    # Test instrumentation adds a serial console; production ISO remains unchanged.
    config = (root / 'etc/mkinitcpio.conf').read_text()
    match = re.search(r'^HOOKS=\((.*)\)', config, re.M)
    assert match, 'mkinitcpio HOOKS are missing.'
    hooks = match.group(1).split()
    for hook in ('systemd', 'kms', 'plymouth', 'sd-encrypt'):
        assert hook in hooks, f'mkinitcpio hook {hook} is missing: {hooks}'
    assert hooks.index('systemd') < hooks.index('plymouth') < hooks.index('sd-encrypt'), hooks
    cmdline = root / 'etc/kernel/cmdline'
    tokens = cmdline.read_text().split()
    assert 'quiet' in tokens and 'splash' in tokens, tokens
    assert any(token.startswith('rd.luks.name=') for token in tokens), tokens
    theme = (root / 'etc/plymouth/plymouthd.conf').read_text() if (root / 'etc/plymouth/plymouthd.conf').is_file() else ''
    assert 'Theme=bgrt' in theme, theme
    cmdline.write_text(cmdline.read_text().strip() + ' console=tty0 console=ttyS0,115200n8\n')
    defaults = root / 'etc/default/limine'
    defaults.write_text(defaults.read_text() + '\nKERNEL_CMDLINE[default]+=" console=tty0 console=ttyS0,115200n8"\n')
    run('arch-chroot', str(root), 'systemctl', 'enable', 'serial-getty@ttyS0.service')
    run('arch-chroot', str(root), 'limine-mkinitcpio')
    configuration = json.loads(Path('/run/verify/install.json').read_text())
    if configuration.get('swap_gib') == 0:
        assert not (root / 'swap/swapfile').exists()
        assert not any(token.startswith('resume=') or token.startswith('resume_offset=')
                       for token in cmdline.read_text().split())
        assert 'AllowHibernation=no' in (root / 'etc/systemd/sleep.conf.d/60-hibernation.conf').read_text()


def user_command(*args, capture=False):
    user = pwd.getpwnam('tester')
    runtime = Path(f'/run/user/{user.pw_uid}')
    wayland = next((path for path in runtime.glob('wayland-*') if stat.S_ISSOCK(path.stat().st_mode)), None)
    if wayland is None:
        raise RuntimeError('No Wayland socket found for the graphical test user.')
    niri = next(runtime.glob('niri.*.sock'))
    return run('runuser', '-u', 'tester', '--', 'env', f'HOME={user.pw_dir}',
               f'XDG_CONFIG_HOME={user.pw_dir}/.config', f'XDG_CACHE_HOME={user.pw_dir}/.cache',
               f'XDG_DATA_HOME={user.pw_dir}/.local/share', f'XDG_STATE_HOME={user.pw_dir}/.local/state',
               'XDG_SESSION_TYPE=wayland',
               'XDG_CURRENT_DESKTOP=niri', f'NIRI_SOCKET={niri}', f'XDG_RUNTIME_DIR={runtime}',
               f'DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus', f'WAYLAND_DISPLAY={wayland.name}',
               *args, stdout=subprocess.PIPE if capture else None)


def ipc(application, method, *parameters):
    return json.loads(user_command(application, 'ipc', method, *parameters, capture=True).stdout)


def windows():
    return json.loads(user_command('niri', 'msg', '--json', 'windows', capture=True).stdout)


def screenshot(name):
    path = Path('/run/verify') / name
    path.unlink(missing_ok=True)
    user_command('niri', 'msg', 'action', 'screenshot-screen', '--path', str(path))
    wait_until('Desktop screenshot', lambda: path.is_file() and
               path.read_bytes().endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82'), timeout=10)


def greeter():
    account = pwd.getpwnam('greeter')
    runtime = Path(f'/run/user/{account.pw_uid}')
    socket = wait_until('Greeter compositor', lambda: next(runtime.glob('niri.*.sock'), None))
    path = Path('/tmp/dux-greeter-verification.png')
    path.unlink(missing_ok=True)
    run('runuser', '-u', 'greeter', '--', 'env', f'XDG_RUNTIME_DIR={runtime}',
        f'NIRI_SOCKET={socket}', 'niri', 'msg', 'action', 'screenshot-screen', '--path', str(path))
    wait_until('Rendered graphical greeter', lambda: path.is_file() and
               path.read_bytes().endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82'), timeout=10)
    shutil.copyfile(path, '/run/verify/01-greeter.png')


def browser_state():
    with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=2) as response:
        pages = json.load(response)
    for page in pages:
        if page.get('url') == 'file:///tmp/chromium-smoke.html' and page.get('title', '').startswith('DUX-VERIFY '):
            # Chromium's HTTP discovery endpoint HTML-escapes target titles.
            return json.loads(html.unescape(page['title'].removeprefix('DUX-VERIFY ')))
    return None


def storage():
    configuration = json.loads(Path('/run/verify/install.json').read_text())
    cmdline = Path('/proc/cmdline').read_text().split()
    assert 'noresume' not in cmdline, 'Normal boot retained the recovery noresume flag.'
    status = run('cryptsetup', 'status', 'dux-root', stdout=subprocess.PIPE).stdout
    assert re.search(r'type:\s+LUKS2', status), status
    swaps = [line.split() for line in Path('/proc/swaps').read_text().splitlines()[1:]]
    if configuration.get('swap_gib') == 0:
        assert not swaps, swaps
        assert not any(token.startswith('resume=') or token.startswith('resume_offset=') for token in cmdline)
        disabled = run('systemd-analyze', 'cat-config', 'systemd/sleep.conf', stdout=subprocess.PIPE).stdout
        assert 'AllowHibernation=no' in disabled
        return
    mounted_subvolume = run('findmnt', '-n', '-o', 'FSROOT', '/swap', stdout=subprocess.PIPE).stdout.strip()
    assert mounted_subvolume == '/@swap', mounted_subvolume
    assert run('findmnt', '-n', '-o', 'FSROOT', '/', stdout=subprocess.PIPE).stdout.strip() == '/@'
    assert run('findmnt', '-n', '-o', 'FSTYPE', '/swap', stdout=subprocess.PIPE).stdout.strip() == 'btrfs'
    assert any(row[0] == '/swap/swapfile' and int(row[2]) > 0 for row in swaps), swaps
    expected_gib = configuration.get('swap_gib')
    if expected_gib is None:
        memory_kib = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                              if line.startswith('MemTotal:')))
        expected_gib = (memory_kib * 1024 + 1024**3 - 1) // 1024**3 + 2
    assert Path('/swap/swapfile').stat().st_size == expected_gib * 1024**3
    offset = run('btrfs', 'inspect-internal', 'map-swapfile', '-r', '/swap/swapfile', stdout=subprocess.PIPE).stdout.strip()
    assert offset.isdecimal(), offset
    assert 'resume=/dev/mapper/dux-root' in cmdline
    assert f'resume_offset={offset}' in cmdline, (offset, cmdline)
    assert stat.S_IMODE(Path('/swap/swapfile').stat().st_mode) == 0o600
    Path('/run/verify/swap-evidence.json').write_text(json.dumps({
        'subvolume': mounted_subvolume, 'resume_offset': int(offset), 'active_swap': swaps,
    }, indent=2) + '\n')


def plymouth_config(root='/'):
    # Graphical boot qualification: stock theme, hook order, quiet splash and
    # text fallback availability. Runs on the live system (root='/') or on a
    # mounted target during prepare().
    root = Path(root)
    hooks_text = (root / 'etc/mkinitcpio.conf').read_text()
    match = re.search(r'^HOOKS=\((.*)\)', hooks_text, re.M)
    assert match, 'mkinitcpio HOOKS are missing.'
    hooks = match.group(1).split()
    for hook in ('systemd', 'kms', 'plymouth', 'sd-encrypt'):
        assert hook in hooks, f'mkinitcpio hook {hook} is missing: {hooks}'
    assert hooks.index('systemd') < hooks.index('plymouth') < hooks.index('sd-encrypt'), hooks
    cmdline = (root / 'etc/kernel/cmdline').read_text().split() if (root / 'etc/kernel/cmdline').is_file() \
        else Path('/proc/cmdline').read_text().split()
    assert 'quiet' in cmdline and 'splash' in cmdline, cmdline
    assert any(token.startswith('rd.luks.name=') for token in cmdline), cmdline
    assert 'plymouth.enable=0' not in cmdline, cmdline
    # Stock tasteful theme; Arch's default bgrt keeps firmware logos and falls
    # back to spinner, then to text/details without KMS or a framebuffer.
    theme_conf = root / 'etc/plymouth/plymouthd.conf'
    assert theme_conf.is_file(), 'Plymouth theme configuration is missing.'
    assert 'Theme=bgrt' in theme_conf.read_text(), theme_conf.read_text()
    for fallback in ('bgrt', 'spinner', 'details', 'text', 'tribar'):
        assert (root / f'usr/share/plymouth/themes/{fallback}').is_dir(), fallback
    dropin = root / 'etc/systemd/system/greetd.service.d/10-plymouth.conf'
    assert dropin.is_file(), 'Greeter Plymouth ordering is missing.'
    assert 'plymouth-quit.service' in dropin.read_text(), dropin.read_text()
    for unit in ('plymouth-start.service', 'plymouth-quit.service', 'plymouth-quit-wait.service'):
        assert (root / f'usr/lib/systemd/system/{unit}').is_file(), unit


def plymouth_status():
    # Running-system checks: splash was requested, failures stay visible and
    # the greeter started only after the splash quit.
    cmdline = Path('/proc/cmdline').read_text().split()
    assert 'quiet' in cmdline and 'splash' in cmdline, cmdline
    assert 'plymouth.enable=0' not in cmdline, cmdline
    theme = run('plymouth-set-default-theme', stdout=subprocess.PIPE).stdout.strip()
    assert theme == 'bgrt', theme
    assert Path('/usr/share/plymouth/themes/bgrt/bgrt.plymouth').is_file()
    for fallback in ('details', 'text'):
        assert Path(f'/usr/share/plymouth/themes/{fallback}').is_dir(), fallback
    # Boot failures must not be masked: only quiet+splash quiet the console.
    assert 'systemd.show_status=false' not in cmdline, cmdline
    boot_log = Path('/var/log/boot.log')
    assert boot_log.is_file(), 'Plymouth boot log is missing.'
    journal = run('journalctl', '-b', '--no-pager', '-o', 'short-monotonic',
                  stdout=subprocess.PIPE).stdout
    assert 'plymouth' in journal.lower(), 'No Plymouth activity in this boot journal.'
    run('systemctl', 'is-active', 'greetd')
    evidence = {'theme': theme, 'cmdline': cmdline}
    Path('/run/verify/plymouth-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')


def fonts():
    for pattern, expected in (
        ('sans-serif:lang=ar', 'Noto Naskh Arabic'),
        ('Arial:charset=0627', 'Noto Naskh Arabic'),
        ('sans-serif:lang=ur:charset=0679', 'Noto Nastaliq Urdu'),
        ('Arial:charset=0041', 'Liberation Sans'),
        ('sans-serif:charset=0041', 'Noto Sans'),
    ):
        actual = run('fc-match', '-f', '%{family}', pattern, stdout=subprocess.PIPE).stdout
        assert actual == expected, (pattern, actual, expected)


def desktop():
    fonts()
    user_command('gh', '--version')
    user_command('git', '--version')
    user_command('fastfetch', '--logo', 'none', '--structure', 'OS:Kernel')
    user_command('test', '-w', '/home/tester/.config')
    user_command('yay', '--version')
    outputs = json.loads(user_command('niri', 'msg', '--json', 'outputs', capture=True).stdout)
    assert any(output.get('current_mode') is not None for output in outputs.values())
    user_command('dms', 'ipc', 'call', 'lock', 'isLocked')
    user_command('nvim', '--headless', '-u', 'NONE', '-c', 'call writefile(["editor works"], "/tmp/nvim-verified")', '-c', 'qall!')
    assert Path('/tmp/nvim-verified').read_text().strip() == 'editor works'
    user_command('sh', '-c', 'alacritty -e sh -c "echo terminal-works > /tmp/alacritty-verified; sleep 15" >/tmp/alacritty-verify.log 2>&1 &')
    run('bash', '-c', 'for attempt in $(seq 1 30); do test -s /tmp/alacritty-verified && exit 0; sleep 1; done; exit 1')
    html = Path('/tmp/chromium-smoke.html')
    html.write_text(BROWSER_PAGE)
    user_command('sh', '-c',
                 'chromium --no-first-run --no-default-browser-check '
                 '--user-data-dir=/tmp/chromium-verification --remote-debugging-port=9222 '
                 'file:///tmp/chromium-smoke.html >/run/verify/chromium.log 2>&1 &')
    # Read a DOM result from the real Wayland browser, not just a running PID.
    wait_until('Graphical Chromium verification page', browser_state)
    user_command('wpctl', 'status')
    screenshot('02-desktop-apps.png')


def dux_components():
    for service in ('dms', 'dsearch', 'dcal', 'dfiles'):
        wait_until(f'{service} user service', lambda: user_command(
            'systemctl', '--user', 'is-active', service, capture=True).stdout.strip() == 'active')
    wait_until('Upstream GTK theme initialization', lambda:
               Path('/home/tester/.local/state/arch-niri-dux/application-themes').is_file(), timeout=120)
    metrics = json.loads(user_command('dgop', 'memory', '--json', capture=True).stdout)
    assert metrics, 'GOP did not return memory metrics.'
    fixture = Path('/home/tester/dux-verification-document.txt')
    user_command('sh', '-c', f'printf "offline file preview works\\n" > {fixture}')
    wait_until('Dux Search indexing', lambda: any(
        hit.get('id') == str(fixture) for hit in json.loads(user_command(
            'dsearch', 'search', 'dux-verification-document', '--field', 'filename', '--json',
            capture=True).stdout).get('hits', [])))
    preview = ipc('dfiles', 'files.preview', f'path={fixture}')
    assert preview['text'] == 'offline file preview works\n', preview
    assert user_command('xdg-mime', 'query', 'default', 'inode/directory', capture=True).stdout.strip() == 'dev.duxlabs.files.desktop'
    file_manager = ipc('dfiles', 'system.fileManager.get')
    assert file_manager['owned'], file_manager
    portal = ipc('dfiles', 'system.portal.get')
    assert portal['active'] and portal['backend']['owned'], portal
    assert portal['config']['effective'] == 'dfiles', portal

    account = ipc('dcal', 'accounts.local.add', 'root=/home/tester/vm-calendar', 'displayName=VM verification')
    ipc('dcal', 'calendars.create', f'accountId={account["accountId"]}', 'name=Offline verification')
    calendars = ipc('dcal', 'calendars.list')
    calendar = next(item for item in calendars if item.get('accountId') == account['accountId']
                    and item['name'] == 'Offline verification')
    event = ipc('dcal', 'events.create', f'calendarId={calendar["id"]}', 'summary=VM offline event',
                'start=2030-01-01T10:00:00Z', 'end=2030-01-01T11:00:00Z')
    listed = ipc('dcal', 'events.list', 'query=VM offline event', 'from=2030-01-01T00:00:00Z',
                 'to=2030-01-02T00:00:00Z')
    assert any(item['id'] == event['id'] for item in listed['events']), listed
    for application, app_id in (('dcal', 'dev.duxlabs.calendar'), ('dfiles', 'dev.duxlabs.files')):
        ipc(application, 'ui.show')
        wait_until(f'{application} graphical window', lambda: any(window.get('app_id') == app_id for window in windows()))
        screenshot(f'02-{application}.png')
        ipc(application, 'ui.hide')
    ipc('dcal', 'accounts.delete', f'accountId={account["accountId"]}')
    Path('/run/verify/dux-components.json').write_text(json.dumps({
        'gop_metrics': metrics, 'file_manager': file_manager, 'portal': portal,
        'calendar_event_created_and_read': event['id'], 'indexed_file': str(fixture),
    }, indent=2) + '\n')


def themes():
    evidence = []
    for mode in ('light', 'dark', 'light', 'dark'):
        before = browser_state()
        user_command('dms', 'ipc', 'call', 'theme', mode)

        def propagated():
            current = browser_state()
            if not current or current['scheme'] != mode:
                return False
            if before and before['scheme'] != mode and current['changes'] <= before['changes']:
                return False
            setting = user_command('gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme', capture=True).stdout.strip()
            allowed = {"'prefer-dark'"} if mode == 'dark' else {"'prefer-light'", "'default'"}
            if setting not in allowed:
                return False
            portal_value = {"'default'": 0, "'prefer-dark'": 1, "'prefer-light'": 2}[setting]
            portal = user_command('gdbus', 'call', '--session', '--dest', 'org.freedesktop.portal.Desktop',
                                  '--object-path', '/org/freedesktop/portal/desktop',
                                  '--method', 'org.freedesktop.portal.Settings.Read',
                                  'org.freedesktop.appearance', 'color-scheme', capture=True).stdout
            if not re.search(rf'\buint32 {portal_value}\b', portal):
                return False
            for application in ('dcal', 'dfiles'):
                state = ipc(application, 'system.colorScheme.get')
                if not state.get('available') or state.get('colorScheme') != portal_value:
                    return False
            return current

        evidence.append(wait_until(f'Dux {mode} mode reaching apps and portals', propagated))
        screenshot(f'02-theme-{len(evidence)}-{mode}.png')
    Path('/run/verify/theme-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')


def focus_browser():
    browser = next(window for window in windows() if window.get('app_id', '').lower().startswith('chromium'))
    user_command('niri', 'msg', 'action', 'focus-window', '--id', str(browser['id']))
    assert browser_state(), 'The graphical verification page is unavailable.'


def file_chooser():
    wait_until('Chromium file-picker request', lambda:
               (state := browser_state()) is not None and state['file'] == 'pending')
    wait_until('Dux Files handling Chromium open-file dialog', lambda: any(
        window.get('app_id') == 'dev.duxlabs.files' and re.match(r'(?i)(open|select)', window.get('title', ''))
        for window in windows()))
    screenshot('02-file-chooser.png')


def captured_frame():
    state = browser_state()
    if state and state['capture'].startswith('error:'):
        raise RuntimeError(state['capture'])
    if state and state['capture'] == 'passed' and state['frames'] >= 3 and state['width'] > 0 and state['height'] > 0:
        pixels = state.get('pixels', {})
        if pixels.get('nonBlack', 0) >= 10 and pixels.get('maximum', 0) - pixels.get('minimum', 0) > 60 and pixels.get('colors', 0) >= 8:
            return state
    return False


def capture():
    state = wait_until('Chromium screen sharing producing nonblank video content', captured_frame)
    Path('/run/verify/screen-sharing.json').write_text(json.dumps(state, indent=2) + '\n')
    screenshot('02-screen-sharing.png')


def prepare_hibernate():
    storage()
    if json.loads(Path('/run/verify/install.json').read_text()).get('swap_gib') == 0:
        raise RuntimeError('This installation explicitly disabled hibernation.')
    if 'disk' not in Path('/sys/power/state').read_text().split():
        raise RuntimeError('This VM kernel does not advertise hibernation; no resume test was performed.')
    token = secrets.token_hex(16)
    Path('/run/dux-hibernate-memory-marker').write_text(token)
    Path('/run/dux-hibernate-resumed').unlink(missing_ok=True)
    Path('/run/verify/hibernate-expected.json').write_text(json.dumps({
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'memory_token': token,
    }, indent=2) + '\n')
    dropin = Path('/run/systemd/system/systemd-hibernate.service.d/90-verification.conf')
    dropin.parent.mkdir(parents=True, exist_ok=True)
    # ExecStartPost runs only after systemd-sleep's hibernation call returns.
    # /run is intentionally RAM-backed: a cold boot cannot satisfy this proof.
    dropin.write_text('[Service]\nExecStartPost=/usr/bin/touch /run/dux-hibernate-resumed\n'
                      "ExecStartPost=/usr/bin/sh -c 'echo DUX_HIBERNATION_RESUMED >/dev/ttyS0'\n")
    run('systemctl', 'daemon-reload')
    run('sync')


def boot_entry_path(text, snapshot=None):
    names = []
    for line in text.splitlines():
        entry = re.match(r'^\s*(/+)(?:\+)?(.+)$', line)
        if entry:
            depth = len(entry.group(1))
            names = names[:depth - 1] + [entry.group(2).strip()]
        if re.match(r'\s*(?:kernel_cmdline|cmdline):', line):
            expected = f'/@snapshots/{snapshot}/snapshot' if snapshot else None
            matches = expected in line if snapshot else re.search(r'rootflags=subvol=/?@(?:\s|$)', line)
            if matches:
                return '/'.join(name.replace('\\', '\\\\').replace('/', '\\/').replace('#', '\\#') for name in names)
    raise RuntimeError(f'No matching Limine boot entry for snapshot {snapshot}')


def select_boot_entry(path):
    config = Path('/boot/limine.conf')
    text = config.read_text()
    # Only global options precede the first menu header.
    lines = text.splitlines()
    first_entry = next(index for index, line in enumerate(lines) if line.lstrip().startswith('/'))
    global_options = [line for line in lines[:first_entry]
                      if not re.match(r'\s*(default_entry|remember_last_entry|timeout|serial):', line)]
    global_options += [f'default_entry: {path}', 'remember_last_entry: no', 'timeout: 1', 'serial: yes']
    config.write_text('\n'.join(global_options + lines[first_entry:]) + '\n')


def snapshots():
    run('systemctl', 'stop', 'snapper-cleanup.timer')
    home = Path('/home/tester/keep-this-file')
    home.write_text('home-must-survive-rollback\n')
    before = run('snapper', '-c', 'root', '--csvout', 'list', stdout=subprocess.PIPE).stdout
    stage = Path('/tmp/verify-package')
    (stage / 'etc').mkdir(parents=True, exist_ok=True)
    (stage / 'etc/dux-verification-version').write_text('known-good\n')
    (stage / '.PKGINFO').write_text('pkgname = dux-verification\npkgver = 1-1\npkgdesc = Offline verification fixture\narch = any\nsize = 11\nlicense = MIT\n')
    package = '/tmp/dux-verification-1-1-any.pkg.tar.gz'
    run('tar', '-C', str(stage), '-czf', package, '.PKGINFO', 'etc')
    run('pacman', '-U', '--noconfirm', package)
    after = run('snapper', '-c', 'root', '--csvout', 'list', stdout=subprocess.PIPE).stdout
    if len(after.splitlines()) <= len(before.splitlines()):
        raise RuntimeError('The package transaction did not create automatic snapshots.')
    number = run('snapper', '-c', 'root', 'create', '--print-number', '--cleanup-algorithm', 'number',
                 '--description', 'VM known-good', stdout=subprocess.PIPE).stdout.strip()
    Path('/run/verify/recovery-snapshot').write_text(number + '\n')
    home.write_text('home-created-after-snapshot\n')
    Path('/etc/dux-verification-version').write_text('changed-after-snapshot\n')
    run('limine-snapper-sync')
    text = Path('/boot/limine.conf').read_text()
    Path('/run/verify/limine-before-recovery.conf').write_text(text)
    select_boot_entry(boot_entry_path(text, snapshot=number))
    # A controlled broken desktop on the normal root makes recovery observable.
    unit = Path('/etc/systemd/system/greetd.service')
    unit.unlink(missing_ok=True)
    unit.symlink_to('/dev/null')
    run('sync')


def recovery():
    number = Path('/run/verify/recovery-snapshot').read_text().strip()
    cmdline = Path('/proc/cmdline').read_text()
    assert f'/@snapshots/{number}/snapshot' in cmdline
    assert 'noresume' in cmdline.split(), 'Recovery could resume a stale normal-root memory image.'
    condition = subprocess.run(['systemd-analyze', 'condition', 'ConditionKernelCommandLine=!noresume'],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert condition.returncode == 1, condition.stdout
    assert Path('/etc/dux-verification-version').read_text().strip() == 'known-good'
    assert Path('/home/tester/keep-this-file').read_text().strip() == 'home-created-after-snapshot'
    assert not Path('/etc/systemd/system/greetd.service').is_symlink()
    # Restore through the same upstream terminal interface the laptop will use.
    run_interactive(['limine-snapper-restore'], [
        (b'Choice [r/l/c]: ', b'r\n'),
        (b'Description for backup subvolume @: ', b'VM pre-restore backup\n'),
        (b'Restore complete. Reboot now? [Y/n]:', b'n\n'),
    ])
    select_boot_entry(boot_entry_path(Path('/boot/limine.conf').read_text()))
    run('limine-snapper-info')
    run('sync')


def restored():
    assert '/@snapshots/' not in Path('/proc/cmdline').read_text()
    assert Path('/etc/dux-verification-version').read_text().strip() == 'known-good'
    assert Path('/home/tester/keep-this-file').read_text().strip() == 'home-created-after-snapshot'
    run('systemctl', 'is-active', 'greetd')
    storage()


def splash_fallback(disable, root='/'):
    # Real text-fallback path: Plymouth stays installed, but the kernel
    # disables its splash so the plain console passphrase prompt is used.
    # Boot failures stay visible because only quiet+splash quiet the console.
    root = Path(root)
    cmdline = root / 'etc/kernel/cmdline'
    tokens = cmdline.read_text().split()
    if disable:
        if 'plymouth.enable=0' not in tokens:
            tokens.append('plymouth.enable=0')
    else:
        tokens = [token for token in tokens if token != 'plymouth.enable=0']
    cmdline.write_text(' '.join(tokens) + '\n')
    defaults = root / 'etc/default/limine'
    lines = [line for line in defaults.read_text().splitlines()
             if 'plymouth.enable=0' not in line]
    if disable:
        lines.append('KERNEL_CMDLINE[default]+=" plymouth.enable=0"')
    defaults.write_text('\n'.join(lines) + '\n')
    if str(root) == '/':
        run('limine-snapper-sync')
        run('sync')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'prepare':
        prepare(Path(sys.argv[2]))
    elif action == 'desktop':
        desktop()
    elif action == 'storage':
        storage()
    elif action == 'dux-components':
        dux_components()
    elif action == 'themes':
        themes()
    elif action == 'focus-browser':
        focus_browser()
    elif action == 'file-chooser':
        file_chooser()
    elif action == 'file-chooser-cancelled':
        wait_until('Chromium file chooser cancellation', lambda:
                   (state := browser_state()) is not None and state['file'] == 'cancelled')
    elif action == 'capture':
        capture()
    elif action == 'capture-picker':
        wait_until('GNOME screen-sharing source chooser', lambda: any(
            'portal' in window.get('app_id', '').lower() and window.get('title') == 'Share Screen'
            for window in windows()))
        screenshot('02-screen-share-picker.png')
    elif action == 'prepare-hibernate':
        prepare_hibernate()
    elif action == 'fonts':
        fonts()
    elif action == 'greeter':
        greeter()
    elif action == 'snapshots':
        snapshots()
    elif action == 'recovery':
        recovery()
    elif action == 'restored':
        restored()
    elif action == 'plymouth-config':
        plymouth_config(sys.argv[2] if len(sys.argv) > 2 else '/')
    elif action == 'plymouth-status':
        plymouth_status()
    elif action == 'splash-fallback-disable':
        splash_fallback(True)
    elif action == 'splash-fallback-enable':
        splash_fallback(False)
    elif action == 'lock':
        user_command('dms', 'ipc', 'call', 'lock', 'lock')
    elif action == 'locked':
        assert 'true' in user_command('dms', 'ipc', 'call', 'lock', 'isLocked', capture=True).stdout.lower()
    elif action == 'unlocked':
        assert 'false' in user_command('dms', 'ipc', 'call', 'lock', 'isLocked', capture=True).stdout.lower()
    else:
        raise SystemExit('Unknown guest probe')
