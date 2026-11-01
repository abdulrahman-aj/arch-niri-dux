#!/usr/bin/env python3
"""Guest-only acceptance probes, executed exclusively on the disposable VM disk."""

import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request


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
    cmdline = root / 'etc/kernel/cmdline'
    cmdline.write_text(cmdline.read_text().strip() + ' console=tty0 console=ttyS0,115200n8\n')
    defaults = root / 'etc/default/limine'
    defaults.write_text(defaults.read_text() + '\nKERNEL_CMDLINE[default]+=" console=tty0 console=ttyS0,115200n8"\n')
    run('arch-chroot', str(root), 'systemctl', 'enable', 'serial-getty@ttyS0.service')
    run('arch-chroot', str(root), 'limine-mkinitcpio')


def user_command(*args, capture=False):
    user = pwd.getpwnam('tester')
    runtime = Path(f'/run/user/{user.pw_uid}')
    wayland = next((path for path in runtime.glob('wayland-*') if stat.S_ISSOCK(path.stat().st_mode)), None)
    if wayland is None:
        raise RuntimeError('No Wayland socket found for the graphical test user.')
    niri = next(runtime.glob('niri.*.sock'))
    return run('runuser', '-u', 'tester', '--', 'env', f'HOME={user.pw_dir}',
               f'XDG_CONFIG_HOME={user.pw_dir}/.config', f'XDG_CACHE_HOME={user.pw_dir}/.cache',
               f'XDG_DATA_HOME={user.pw_dir}/.local/share', 'XDG_SESSION_TYPE=wayland',
               'XDG_CURRENT_DESKTOP=niri', f'NIRI_SOCKET={niri}', f'XDG_RUNTIME_DIR={runtime}',
               f'DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus', f'WAYLAND_DISPLAY={wayland.name}',
               *args, stdout=subprocess.PIPE if capture else None)


def desktop():
    user_command('gh', '--version')
    user_command('git', '--version')
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
    html.write_text('<!doctype html><title>Offline Chromium verification</title><p>offline-browser-works</p>')
    user_command('sh', '-c',
                 'chromium --no-first-run --no-default-browser-check '
                 '--user-data-dir=/tmp/chromium-verification --remote-debugging-port=9222 '
                 'file:///tmp/chromium-smoke.html >/run/verify/chromium.log 2>&1 &')
    # The actual Wayland window must load our offline page, not just start a PID.
    for attempt in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=2) as response:
                pages = json.load(response)
            if any(page.get('title') == 'Offline Chromium verification' for page in pages):
                user_command('wpctl', 'status')
                screenshot = Path('/run/verify/02-desktop-apps.png')
                screenshot.unlink(missing_ok=True)
                user_command('niri', 'msg', 'action', 'screenshot-screen',
                             '--path', str(screenshot))
                # Niri acknowledges the action before its PNG writer finishes.
                for wait in range(100):
                    if screenshot.is_file() and screenshot.read_bytes().endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82'):
                        return
                    time.sleep(0.1)
                raise RuntimeError('Niri did not finish saving the desktop screenshot.')
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError('Graphical Chromium did not load the local verification page.')


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
    assert f'/@snapshots/{number}/snapshot' in Path('/proc/cmdline').read_text()
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


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'prepare':
        prepare(Path(sys.argv[2]))
    elif action == 'desktop':
        desktop()
    elif action == 'snapshots':
        snapshots()
    elif action == 'recovery':
        recovery()
    elif action == 'restored':
        restored()
    elif action == 'lock':
        user_command('dms', 'ipc', 'call', 'lock', 'lock')
    elif action == 'locked':
        assert 'true' in user_command('dms', 'ipc', 'call', 'lock', 'isLocked', capture=True).stdout.lower()
    elif action == 'unlocked':
        assert 'false' in user_command('dms', 'ipc', 'call', 'lock', 'isLocked', capture=True).stdout.lower()
    else:
        raise SystemExit('Unknown guest probe')
