"""Desktop acceptance behavior."""

import json
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import time
from . import dispatch
from .guest import wait_until, run, user_command, ipc, windows, screenshot, png_dimensions, png_ready
from .browser import BROWSER_PAGE, browser_state
from .vm import PASSWORD, click_screen_text


def greeter():
    account = pwd.getpwnam('greeter')
    runtime = Path(f'/run/user/{account.pw_uid}')
    socket = wait_until('Greeter compositor', lambda: next(runtime.glob('niri.*.sock'), None))
    path = Path('/tmp/dux-greeter-verification.png')
    path.unlink(missing_ok=True)
    run(
        'runuser',
        '-u',
        'greeter',
        '--',
        'env',
        f'XDG_RUNTIME_DIR={runtime}',
        f'NIRI_SOCKET={socket}',
        'niri',
        'msg',
        'action',
        'screenshot-screen',
        '--path',
        str(path),
    )
    wait_until('Rendered graphical greeter', lambda: png_ready(path), timeout=10)
    shutil.copyfile(path, '/run/verify/01-greeter.png')


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


def niri_overrides():
    bindings = Path('/home/tester/.config/niri/dms/binds.kdl')
    # Dux 2.1.2 retired the generated binds-next.kdl compatibility layer; the
    # keybinds sheet reads the native bindings directly.
    assert not bindings.with_name('binds-next.kdl').exists(), 'Retired compatibility bindings reappeared.'
    before = bindings.read_bytes()
    overrides = bindings.parent.parent / 'overrides.kdl'
    before_overrides = overrides.read_bytes() if overrides.exists() else None

    def current():
        sheet = json.loads(user_command('dms', 'keybinds', 'show', 'niri', capture=True).stdout)
        assert sheet['dmsStatus']['effective'], sheet['dmsStatus']
        return {bind['key']: bind for group in sheet['binds'].values() for bind in group}

    stock = current()
    assert stock['Alt+F4']['action'] == 'close-window', stock.get('Alt+F4')
    # Stow owns the per-user file, not the separately included system defaults.
    tracked = bindings.with_name('verification-tracked-binds.kdl')
    assert not bindings.is_symlink() and not tracked.exists()
    bindings.rename(tracked)
    bindings.symlink_to(tracked.name)
    try:
        fixture = 'recent-windows {\n    binds {\n        Alt+Tab { next-window scope="workspace"; }\n    }\n}\n'
        if before_overrides is None:
            overrides.write_text(fixture)
        else:
            text = before_overrides.decode()
            if text and not text.endswith('\n'):
                text += '\n'
            overrides.write_text(text + fixture)
        assert current()['Alt+Tab']['action'] == 'next-window scope="workspace"'
        result = json.loads(user_command('dms', 'keybinds', 'set', 'niri', 'Mod+Q', 'spawn true', capture=True).stdout)
        assert result['success'], result
        overridden = current()
        assert overridden['Mod+Q']['action'] == 'spawn true', overridden['Mod+Q']
        assert overridden['Mod+Return']['action'] == stock['Mod+Return']['action']
        assert overridden['Alt+F4']['action'] == 'close-window'
        assert bindings.is_symlink(), 'Dux replaced the tracked override link.'
        assert overridden['Alt+Tab']['action'] == 'next-window scope="workspace"'
        user_command('niri', 'validate')
        user_command('dms', 'keybinds', 'reset', 'niri', 'Mod+Q')
        assert current()['Mod+Q']['action'] == stock['Mod+Q']['action']
        assert current()['Alt+Tab']['action'] == 'next-window scope="workspace"'
    finally:
        tracked.write_bytes(before)
        bindings.unlink()
        tracked.rename(bindings)
        if before_overrides is None:
            overrides.unlink(missing_ok=True)
        else:
            overrides.write_bytes(before_overrides)
    Path('/run/verify/niri-overrides.json').write_text(
        json.dumps(
            {
                'stock_inherited': True,
                'override_effective': True,
                'reset_restores_stock': True,
                'overrides_mru_preserved': True,
                'compat_binds_next_retired': True,
                'alt_f4_inherited': True,
                'tracked_override_link_preserved': True,
            },
            indent=2,
        )
        + '\n'
    )


def desktop():
    versions = run('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline', stdout=subprocess.PIPE).stdout
    owned = [
        '/usr/share/arch-niri-dux/niri/defaults.kdl',
        '/etc/skel/.config/niri/config.kdl',
        '/usr/lib/environment.d/90-dux.conf',
        '/usr/lib/systemd/user/dux-application-themes.service',
        '/etc/xdg/mimeapps.list',
        '/etc/greetd/dux.toml',
        '/usr/share/pacman/keyrings/arch-niri-dux.gpg',
        '/etc/pacman.d/arch-niri-dux.conf',
        '/usr/lib/systemd/system/greetd.service.d/50-arch-niri-dux.conf',
    ]
    for path in owned:
        assert run('pacman', '-Qqo', path, stdout=subprocess.PIPE).stdout.strip() == 'arch-niri-dux', path
    assert run('pacman', '-Qqo', '/etc/greetd/config.toml', stdout=subprocess.PIPE).stdout.strip() == 'greetd'
    files = run('pacman', '-Qlq', 'arch-niri-dux', stdout=subprocess.PIPE).stdout.splitlines()
    assert not any(path.startswith('/home/') for path in files), 'Package owns personal settings.'
    public = run(
        'gpg',
        '--batch',
        '--with-colons',
        '--show-keys',
        '/usr/share/pacman/keyrings/arch-niri-dux.gpg',
        stdout=subprocess.PIPE,
    ).stdout
    fingerprint = next(line.split(':')[9] for line in public.splitlines() if line.startswith('fpr:'))
    trusted = run(
        'gpg',
        '--batch',
        '--homedir',
        '/etc/pacman.d/gnupg',
        '--with-colons',
        '--list-keys',
        fingerprint,
        stdout=subprocess.PIPE,
    ).stdout
    assert next(line.split(':')[1] for line in trusted.splitlines() if line.startswith('pub:')) in ('u', 'f')
    assert 'Include = /etc/pacman.d/arch-niri-dux.conf' in Path('/etc/pacman.conf').read_text()
    assert 'SigLevel = Required TrustedOnly' in Path('/etc/pacman.d/arch-niri-dux.conf').read_text()
    Path('/run/verify/integration-packages.json').write_text(
        json.dumps(
            {
                'versions': versions.splitlines(),
                'owned_paths': owned,
                'no_packaged_home_files': True,
                'release_key_fingerprint': fingerprint,
                'release_key_trusted': True,
                'signed_repository_enabled': True,
            },
            indent=2,
        )
        + '\n'
    )
    fonts()
    niri_overrides()
    user_command('niri', 'validate')
    user_command('gh', '--version')
    user_command('git', '--version')
    user_command('fastfetch', '--logo', 'none', '--structure', 'OS:Kernel')
    user_command('test', '-w', '/home/tester/.config')
    assert user_command('xdg-terminal-exec', '--print-id', capture=True).stdout.strip() == 'Alacritty.desktop'
    assert Path('/home/tester/.config/xdg-terminals.list').read_text().strip() == 'Alacritty.desktop'
    outputs = json.loads(user_command('niri', 'msg', '--json', 'outputs', capture=True).stdout)
    assert any(output.get('current_mode') is not None for output in outputs.values())
    user_command('dms', 'ipc', 'call', 'lock', 'isLocked')
    user_command(
        'nvim',
        '--headless',
        '-u',
        'NONE',
        '-c',
        'call writefile(["editor works"], "/tmp/nvim-verified")',
        '-c',
        'qall!',
    )
    assert Path('/tmp/nvim-verified').read_text().strip() == 'editor works'
    marker = Path('/tmp/default-terminal-verified')
    marker.unlink(missing_ok=True)
    user_command(
        'niri',
        'msg',
        'action',
        'spawn',
        '--',
        'sh',
        '-c',
        'test "$TERMINAL" = xdg-terminal-exec && exec xdg-terminal-exec sh -c '
        '"echo terminal-works > /tmp/default-terminal-verified; sleep 15" '
        '>/tmp/default-terminal-verify.log 2>&1',
    )
    wait_until(
        'Default terminal launched by Niri',
        lambda: marker.is_file() and marker.read_text().strip() == 'terminal-works',
        timeout=30,
    )
    html = Path('/tmp/chromium-smoke.html')
    html.write_text(BROWSER_PAGE)
    user_command(
        'sh',
        '-c',
        'chromium --no-first-run --no-default-browser-check '
        '--user-data-dir=/tmp/chromium-verification --remote-debugging-port=9222 '
        'file:///tmp/chromium-smoke.html >/run/verify/chromium.log 2>&1 &',
    )
    # Read a DOM result from the real Wayland browser, not just a running PID.
    wait_until('Graphical Chromium verification page', browser_state)
    user_command('wpctl', 'status')
    screenshot('02-desktop-apps.png')


def dux_components():
    for service in ('dms', 'dsearch', 'dcal', 'dfiles'):
        wait_until(
            f'{service} user service',
            lambda: user_command('systemctl', '--user', 'is-active', service, capture=True).stdout.strip() == 'active',
        )
    wait_until(
        'Upstream GTK theme initialization',
        lambda: Path('/home/tester/.local/state/arch-niri-dux/application-themes').is_file(),
        timeout=120,
    )
    mode = user_command('dms', 'ipc', 'call', 'theme', 'getMode', capture=True).stdout.strip()
    gtk_theme = user_command(
        'gsettings', 'get', 'org.gnome.desktop.interface', 'gtk-theme', capture=True
    ).stdout.strip()
    assert gtk_theme == ("'adw-gtk3-dark'" if mode == 'dark' else "'adw-gtk3'"), (mode, gtk_theme)
    listeners = user_command('ss', '-H', '-ltn', capture=True).stdout
    assert not re.search(r':43654\b', listeners), 'Fresh Search must use its socket without an HTTP listener.'
    versions = user_command(
        'pacman',
        '-Q',
        'dux-shell',
        'dux-gop',
        'dux-greeter-bin',
        'dux-search-bin',
        'dux-calendar-bin',
        'dux-files-bin',
        capture=True,
    ).stdout
    metrics = json.loads(user_command('dgop', 'memory', '--json', capture=True).stdout)
    assert metrics, 'GOP did not return memory metrics.'
    fixture = Path('/home/tester/dux-verification-document.txt')
    user_command('sh', '-c', f'printf "offline file preview works\\n" > {fixture}')
    wait_until(
        'Dux Search indexing',
        lambda: any(
            hit.get('id') == str(fixture)
            for hit in json.loads(
                user_command(
                    'dsearch', 'search', 'dux-verification-document', '--field', 'filename', '--json', capture=True
                ).stdout
            ).get('hits', [])
        ),
    )
    preview = ipc('dfiles', 'files.preview', f'path={fixture}')
    assert preview['text'] == 'offline file preview works\n', preview
    assert (
        user_command('xdg-mime', 'query', 'default', 'inode/directory', capture=True).stdout.strip()
        == 'dev.duxlabs.files.desktop'
    )
    file_manager = ipc('dfiles', 'system.fileManager.get')
    assert file_manager['owned'], file_manager
    portal = ipc('dfiles', 'system.portal.get')
    assert portal['active'] and portal['backend']['owned'], portal
    assert portal['config']['effective'] == 'dfiles', portal

    account = ipc('dcal', 'accounts.local.add', 'root=/home/tester/vm-calendar', 'displayName=VM verification')
    ipc('dcal', 'calendars.create', f'accountId={account["accountId"]}', 'name=Offline verification')
    calendars = ipc('dcal', 'calendars.list')
    calendar = next(
        item
        for item in calendars
        if item.get('accountId') == account['accountId'] and item['name'] == 'Offline verification'
    )
    event = ipc(
        'dcal',
        'events.create',
        f'calendarId={calendar["id"]}',
        'summary=VM offline event',
        'start=2030-01-01T10:00:00Z',
        'end=2030-01-01T11:00:00Z',
    )
    listed = ipc(
        'dcal', 'events.list', 'query=VM offline event', 'from=2030-01-01T00:00:00Z', 'to=2030-01-02T00:00:00Z'
    )
    assert any(item['id'] == event['id'] for item in listed['events']), listed
    for application, app_id in (('dcal', 'dev.duxlabs.calendar'), ('dfiles', 'dev.duxlabs.files')):
        ipc(application, 'ui.show')
        wait_until(
            f'{application} graphical window', lambda: any(window.get('app_id') == app_id for window in windows())
        )
        screenshot(f'02-{application}.png')
        ipc(application, 'ui.hide')
    ipc('dcal', 'accounts.delete', f'accountId={account["accountId"]}')
    Path('/run/verify/dux-components.json').write_text(
        json.dumps(
            {
                'gop_metrics': metrics,
                'file_manager': file_manager,
                'portal': portal,
                'calendar_event_created_and_read': event['id'],
                'indexed_file': str(fixture),
                'versions': versions.splitlines(),
                'gtk_theme': gtk_theme,
                'mode': mode,
                'search_http_listener_absent': True,
            },
            indent=2,
        )
        + '\n'
    )


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
            setting = user_command(
                'gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme', capture=True
            ).stdout.strip()
            allowed = {"'prefer-dark'"} if mode == 'dark' else {"'prefer-light'", "'default'"}
            if setting not in allowed:
                return False
            gtk_theme = user_command(
                'gsettings', 'get', 'org.gnome.desktop.interface', 'gtk-theme', capture=True
            ).stdout.strip()
            if gtk_theme != ("'adw-gtk3-dark'" if mode == 'dark' else "'adw-gtk3'"):
                return False
            portal_value = {"'default'": 0, "'prefer-dark'": 1, "'prefer-light'": 2}[setting]
            portal = user_command(
                'gdbus',
                'call',
                '--session',
                '--dest',
                'org.freedesktop.portal.Desktop',
                '--object-path',
                '/org/freedesktop/portal/desktop',
                '--method',
                'org.freedesktop.portal.Settings.Read',
                'org.freedesktop.appearance',
                'color-scheme',
                capture=True,
            ).stdout
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


def wallpaper():
    session = Path('/home/tester/.local/state/dms/session.json')
    assert session.is_file(), 'Dux first-session state is missing.'
    data = json.loads(session.read_text())
    preset = data.get('materialWallpapers', {}).get('', {}).get('shared', {}).get('preset')
    assert preset == 'bloom', f'First-session wallpaper must be bloom, found {preset!r}.'
    assert not data.get('wallpaperPath'), 'Material wallpaper must be active (empty wallpaperPath).'
    assert not data.get('monitorWallpapers'), 'Fresh user must not have per-monitor overrides.'
    screenshot('02-wallpaper-bloom.png')
    evidence = Path('/run/verify/02-wallpaper-bloom.png')
    width, height = png_dimensions(evidence)
    Path('/run/verify/wallpaper.json').write_text(
        json.dumps(
            {
                'preset': preset,
                'wallpaperPath': data.get('wallpaperPath', ''),
                'width': width,
                'height': height,
                'note': 'Parent visually confirms bloom vs dank in 02-wallpaper-bloom.png; '
                'settings plus a dimensioned render do not assert the visual match in code.',
            },
            indent=2,
        )
        + '\n'
    )


def wallpaper_preserved():
    # useradd -m seeds homes from /etc/skel; package upgrades never touch
    # home. A second account proves the seeded desktop stands alone.
    users = ('dux-preserve-check', 'dux-second-check')
    for user in users:
        run('sh', '-c', f'userdel -r {user} 2>/dev/null || true')
    for user in users:
        run('useradd', '-m', '-s', '/bin/bash', user)
    try:
        for user in users:
            home = Path(f'/home/{user}')
            session = home / '.local/state/dms/session.json'
            assert json.loads(session.read_text())['materialWallpapers']['']['shared']['preset'] == 'bloom', user
            run('runuser', '-u', user, '--', 'niri', 'validate', '--config', str(home / '.config/niri/config.kdl'))
            for name in ('00-arch-niri-dux-lock.json', '00-arch-niri-dux-updates.json'):
                assert (home / '.config/dms/config.d' / name).is_file(), (user, name)
            plugin = json.loads((home / '.config/dms/plugin_settings.json').read_text())
            assert plugin.get('archNiriDuxRelease', {}).get('enabled') is True, (user, plugin)
        user = users[0]
        home = Path(f'/home/{user}')
        session = home / '.local/state/dms/session.json'
        custom = {'configVersion': 7, 'materialWallpapers': {'': {'shared': {'preset': 'orbit', 'seed': '#ff0000'}}}}
        session.write_text(json.dumps(custom))
        before = session.read_text()
        terminal = home / '.config/xdg-terminals.list'
        terminal_before = '# Personal terminal preference\nOther.desktop\nAlacritty.desktop\n'
        terminal.write_text(terminal_before)
        mime = home / '.config/mimeapps.list'
        mime_before = '[Default Applications]\ntext/plain=org.example.Editor.desktop\n'
        mime.write_text(mime_before)
        applications = home / '.local/share/applications'
        applications.mkdir(parents=True)
        (applications / 'org.example.Editor.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Personal editor\nExec=/usr/bin/true\nMimeType=text/plain;\n'
        )
        niri = home / '.config/niri'
        (niri / 'dms').mkdir(parents=True, exist_ok=True)
        niri_files = {
            niri / 'config.kdl': 'include "/usr/share/arch-niri-dux/niri/defaults.kdl"\n'
            'include "dms/layout.kdl"\ninclude "dms/windowrules.kdl"\n'
            'include "dms/binds.kdl"\n',
            niri / 'dms/binds.kdl': 'binds {\n    Mod+Q repeat=false { spawn "true"; }\n}\n',
            niri / 'dms/layout.kdl': 'layout { gaps 19; }\n',
            niri / 'dms/windowrules.kdl': 'window-rule { match app-id="preserve-check"; open-floating true; }\n',
        }
        for path, content in niri_files.items():
            path.write_text(content)
        # The fixture represents an existing user's own files, not root-owned data.
        run('chown', '-R', f'{user}:{user}', str(home))
        # Nothing re-seeds an existing home: skel copies once at useradd -m.
        assert session.read_text() == before, 'Existing wallpaper choice was overwritten.'
        assert terminal.read_text() == terminal_before, 'Existing terminal choice was overwritten.'
        assert mime.read_text() == mime_before, 'Existing MIME choices were overwritten.'
        selected = run(
            'runuser',
            '-u',
            user,
            '--',
            'env',
            'XDG_CURRENT_DESKTOP=niri',
            'xdg-mime',
            'query',
            'default',
            'text/plain',
            stdout=subprocess.PIPE,
        ).stdout.strip()
        assert selected == 'org.example.Editor.desktop', selected
        for path, content in niri_files.items():
            assert path.read_text() == content, f'Existing Niri file overwritten: {path}.'
        run('runuser', '-u', user, '--', 'niri', 'validate', '--config', str(niri / 'config.kdl'))
        Path('/run/verify/wallpaper-preserved.json').write_text(
            json.dumps(
                {
                    'user': user,
                    'preset': 'orbit',
                    'preserved': True,
                    'terminal_preserved': True,
                    'niri_preserved': True,
                    'mime_preserved': True,
                    'fresh_account_initialized': True,
                    'second_account_seeded': True,
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        for user in users:
            run('sh', '-c', f'userdel -r {user} 2>/dev/null || true')


def session_path():
    # Exercise compositor command lookup with a disposable local executable.
    account = pwd.getpwnam('tester')
    home = Path(account.pw_dir)
    bindir = home / '.local/bin'
    bindir.mkdir(parents=True, exist_ok=True)
    probe = bindir / 'dux-path-probe'
    probe.write_text('#!/bin/sh\nprintf probe-ok > /tmp/dux-path-probe-marker\n')
    probe.chmod(0o755)
    run('chown', f'{account.pw_name}:{account.pw_gid}', str(bindir), str(probe))
    marker = Path('/tmp/dux-path-probe-marker')
    if marker.is_file():
        marker.unlink()
    try:
        environment = user_command('systemctl', '--user', 'show-environment', capture=True).stdout
        service_path = next(line.removeprefix('PATH=') for line in environment.splitlines() if line.startswith('PATH='))
        assert str(bindir) in service_path.split(':'), f'systemd user PATH lacks {bindir}: {service_path}'
        user_command('bash', '-lc', 'command -v dux-path-probe')
        user_command('niri', 'msg', 'action', 'spawn', '--', 'dux-path-probe')
        wait_until(
            'Niri-spawned ~/.local/bin wrapper',
            lambda: marker.is_file() and marker.read_text() == 'probe-ok',
            timeout=30,
        )
        Path('/run/verify/session-path.json').write_text(
            json.dumps(
                {
                    'wrapper': str(probe),
                    'systemd_user_path': service_path,
                    'niri_spawn_marker': marker.read_text(),
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        probe.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)


def verify(vm, passed):
    vm.probe('desktop', 'desktop', timeout=180)
    passed('Niri, Dux, terminal, Neovim and Chromium smoke checks')
    vm.probe('desktop', 'session-path', timeout=90)
    passed('Login shells, user services and Niri spawns resolve local user executables')
    vm.probe('desktop', 'dux-components', timeout=300)
    passed('GOP metrics, live file indexing, Calendar event create/read and Dux Files integration')
    vm.probe('desktop', 'themes', timeout=300)
    passed('Live Dux light/dark changes reach Chromium, GTK settings, portals and Dux apps')
    # The native first-login welcome must not cover the document/camera UI.
    vm.probe('desktop', 'onboarding-screen')
    if click_screen_text(vm, vm.directory / '02-onboarding.png', 'Skip'):
        time.sleep(0.5)
    vm.probe('desktop', 'wallpaper', timeout=90)
    passed('Bloom material wallpaper is the fresh-user default and rendered')
    vm.probe('desktop', 'wallpaper-preserved', timeout=180)
    passed('Skel-seeded homes preserve existing desktop choices; second account seeded')


def verify_lock(vm, passed):
    vm.probe('desktop', 'lock')
    vm.probe('desktop', 'locked')
    vm.screenshot('03-locked')
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.command(
        'for attempt in $(seq 1 30); do cd /run/verify && python -m verification.desktop unlocked && exit 0; sleep 1; done; exit 1',
        timeout=60,
    )
    passed('Screen lock and password unlock')


def assert_locked(locked):
    state = user_command('dms', 'ipc', 'call', 'lock', 'isLocked', capture=True).stdout.lower()
    assert str(locked).lower() in state


if __name__ == '__main__':
    dispatch(
        'desktop',
        {
            'desktop': desktop,
            'niri-overrides': niri_overrides,
            'greeter': greeter,
            'fonts': fonts,
            'dux-components': dux_components,
            'themes': themes,
            'wallpaper': wallpaper,
            'wallpaper-preserved': wallpaper_preserved,
            'session-path': session_path,
            'onboarding-screen': lambda: screenshot('02-onboarding.png'),
            'failure-screenshot': lambda: screenshot('failure.png'),
            'lock': lambda: user_command('dms', 'ipc', 'call', 'lock', 'lock'),
            'locked': lambda: assert_locked(True),
            'unlocked': lambda: assert_locked(False),
        },
    )
