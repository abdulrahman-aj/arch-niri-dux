"""Disposable VM fixtures for the migration execution contract."""

import json
from pathlib import Path
import pwd
import subprocess

from . import dispatch
from .guest import run, user_command, wait_until

CODE = Path('/usr/lib/arch-niri-dux/migrations')
STATE = Path('/var/lib/arch-niri-dux/migrations')
HOME = Path('/home/tester')
USER_STATE = HOME / '.local/state/arch-niri-dux/migrations'
COMMAND = 'arch-niri-dux-migrate'


def fixture(root, scope, number, body):
    directory = root / scope
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'99999999_{number:02}_verification.sh'
    path.write_text('set -eu\n' + body + '\n')
    path.chmod(0o644)
    return path


def effect(path, token):
    return f'grep -qx {token} {path} 2>/dev/null || echo {token} >> {path}'


def check():
    run('systemd-detect-virt', '--vm')
    assert Path('/run/verify/install.json').is_file()
    assert not any((CODE / 'system').iterdir())
    assert not any((CODE / 'user').iterdir())
    assert (STATE / 'generation').is_file()
    assert (USER_STATE / 'lock').is_file()
    run('useradd', '-m', 'migration-later')
    run('runuser', '-u', 'migration-later', '--', 'bash', '/usr/lib/arch-niri-dux/initialize-user')
    run('systemctl', 'stop', 'arch-niri-dux-migrate.service')
    system_effect = Path('/var/lib/arch-niri-dux/verification-effects')
    user_effect = HOME / '.local/state/verification-effects'
    # Queue in reverse creation order, representing a direct skipped release.
    fixture(CODE, 'system', 2, effect(system_effect, 'second'))
    fixture(CODE, 'system', 1, effect(system_effect, 'first'))
    fixture(CODE, 'user', 1, effect('"$HOME/.local/state/verification-effects"', 'first'))
    fixture(CODE, 'user', 2, effect('"$HOME/.local/state/verification-effects"', 'second'))
    user_command('bash', '/usr/lib/arch-niri-dux/initialize-user')
    assert not user_effect.exists(), 'Existing account was baselined or migrated before system success'
    # Exercise the queued service while ALPM's lock exists. The package upgrade
    # fixture separately invokes the real hook and checks the lock in scripts.
    lock = Path('/var/lib/pacman/db.lck')
    lock.touch(exist_ok=False)
    try:
        run('systemctl', '--no-block', 'start', 'arch-niri-dux-migrate.service')
        wait_until('System service waiting for transaction', lambda: subprocess.run(
            ['systemctl', 'is-active', '--quiet', 'arch-niri-dux-migrate.service']).returncode == 0
            or subprocess.check_output(['systemctl', 'show', '-p', 'ActiveState', '--value',
                                        'arch-niri-dux-migrate.service'], text=True).strip() == 'activating')
        assert not system_effect.exists()
    finally:
        lock.unlink()
    wait_until('Logged-in user path migration', lambda: user_effect.read_text().splitlines() == ['first', 'second'])
    assert system_effect.read_text().splitlines() == ['first', 'second']
    run(COMMAND, 'run')
    user_command(COMMAND, 'run')
    assert system_effect.read_text().splitlines() == ['first', 'second']
    assert user_effect.read_text().splitlines() == ['first', 'second']
    fixture(CODE, 'user', 3, effect('"$HOME/.local/state/verification-effects"', 'third'))
    run(COMMAND, 'run')
    wait_until('User-only generation', lambda: user_effect.read_text().splitlines() == ['first', 'second', 'third'])
    ready = Path('/var/lib/arch-niri-dux/verification-ready')
    failed = fixture(CODE, 'system', 3, f'test -e {ready}')
    last = fixture(CODE, 'system', 4, effect(system_effect, 'last'))
    result = subprocess.run(['systemctl', 'restart', 'arch-niri-dux-migrate.service'])
    assert result.returncode != 0
    assert not (STATE / (failed.name + '.json')).exists()
    assert not (STATE / (last.name + '.json')).exists()
    status = json.loads(subprocess.check_output([COMMAND, 'status'], text=True))
    assert status['failure'] and len(status['pending']) == 2
    wait_until('System failure reported in user session', lambda: (USER_STATE / 'failure').exists())
    journal = subprocess.check_output(['journalctl', '-u', 'arch-niri-dux-migrate.service',
                                       '--no-pager'], text=True)
    Path('/run/verify/migration-failure-journal.log').write_text(journal)
    notified = USER_STATE / 'notified'
    wait_until('Failure notification recorded', notified.exists)
    notification_time = notified.stat().st_mtime_ns
    subprocess.run(['runuser', '-u', 'tester', '--', COMMAND, 'run'], check=False)
    assert notified.stat().st_mtime_ns == notification_time
    ready.touch()
    run(COMMAND, 'run')
    assert system_effect.read_text().splitlines() == ['first', 'second', 'last']
    unsafe = fixture(CODE, 'system', 5, effect(system_effect, 'unsafe'))
    unsafe.chmod(0o666)
    assert subprocess.run([COMMAND, 'run']).returncode != 0
    assert system_effect.read_text().splitlines() == ['first', 'second', 'last']
    unsafe.unlink()
    unsafe.symlink_to('/home/tester/.profile')
    assert subprocess.run([COMMAND, 'run']).returncode != 0
    unsafe.unlink()
    run(COMMAND, 'run')
    # Skips preserve customized files and links, including their referents.
    custom = HOME / '.local/state/migration-custom'
    custom.write_text('personal settings\n')
    run('chown', 'tester', str(custom))
    link = HOME / '.local/state/migration-link'
    link.symlink_to(custom)
    before = custom.read_bytes()
    skipped = fixture(CODE, 'user', 4,
                      'if [[ -L "$HOME/.local/state/migration-link" || -e "$HOME/.local/state/migration-custom" ]]; then\n'
                      'echo "Customized files and symlinks preserved"; exit 77\nfi\ntrue')
    run(COMMAND, 'run')
    wait_until('Customized user migration skipped', lambda: (USER_STATE / (skipped.name + '.json')).exists())
    record = json.loads((USER_STATE / (skipped.name + '.json')).read_text())
    assert record['outcome'] == 'skip' and record['reason']
    assert link.is_symlink() and custom.read_bytes() == before
    custom.unlink()
    link.unlink()
    # A genuinely new entrypoint baselines without running the fixtures.
    run('useradd', '-m', 'migration-new')
    try:
        run('runuser', '-u', 'migration-new', '--', 'bash', '/usr/lib/arch-niri-dux/initialize-user')
        new = Path('/home/migration-new/.local/state')
        assert not (new / 'verification-effects').exists()
        assert len(list((new / 'arch-niri-dux/migrations').glob('*.json'))) == 4
    finally:
        run('userdel', '-r', 'migration-new')
    # A logged-out existing account catches up when its graphical target starts.
    account = pwd.getpwnam('migration-later')
    later = Path(account.pw_dir) / '.local/state/verification-effects'
    assert not later.exists()
    prefix = ['runuser', '-u', account.pw_name, '--', 'env',
              f'XDG_RUNTIME_DIR=/run/user/{account.pw_uid}']
    try:
        run('loginctl', 'enable-linger', account.pw_name)
        wait_until('Second user manager', lambda: Path(f'/run/user/{account.pw_uid}/bus').exists())
        assert not later.exists()
        target = Path('/etc/systemd/user/migration-verification.target')
        target.write_text('[Unit]\nRequires=graphical-session.target\nAfter=graphical-session.target\n')
        run(*prefix, 'systemctl', '--user', 'daemon-reload')
        run(*prefix, 'systemctl', '--user', 'start', 'migration-verification.target')
        wait_until('Next graphical login migration', lambda: later.read_text().splitlines() == ['first', 'second', 'third'])
    finally:
        run('loginctl', 'disable-linger', account.pw_name)
        run('systemctl', 'stop', f'user@{account.pw_uid}.service')
        wait_until('Second user processes stopped', lambda: subprocess.run(
            ['pgrep', '-u', str(account.pw_uid)], stdout=subprocess.DEVNULL).returncode == 1)
        Path('/etc/systemd/user/migration-verification.target').unlink(missing_ok=True)
        run('userdel', '-r', account.pw_name)
    for scope in ('system', 'user'):
        for path in (CODE / scope).glob('99999999_*'):
            path.unlink()
    for state in (STATE, USER_STATE):
        for path in state.glob('99999999_*'):
            path.unlink()
    system_effect.unlink()
    user_effect.unlink()
    ready.unlink()
    run(COMMAND, 'run')
    user_command('systemctl', '--user', 'is-active', 'arch-niri-dux-migrate.path')
    Path('/run/verify/migrations.json').write_text(json.dumps({
        'ordering_and_retry': True, 'failure_and_status': True,
        'path_in_active_session': True, 'user_only_release': True,
        'next_graphical_target': True, 'fresh_account_baseline': True,
        'pacman_lock_defers_execution': True, 'customizations_skipped': True,
        'failure_notification_deduplicated': True, 'installer_baselines': True,
    }, indent=2) + '\n')


def prepare_snapshot():
    Path('/var/log/arch-niri-dux-migration-snapshot-effects').unlink(missing_ok=True)
    fixture(CODE, 'system', 10, 'echo applied >> /var/log/arch-niri-dux-migration-snapshot-effects')
    fixture(CODE, 'user', 10, 'echo applied >> "$HOME/.local/state/migration-user-snapshot-effects"')


def after_snapshot():
    run(COMMAND, 'run')
    wait_until('User snapshot fixture', lambda: (USER_STATE / '99999999_10_verification.sh.json').exists())


def restored():
    wait_until('System migration after root restore', lambda: Path(
        '/var/log/arch-niri-dux-migration-snapshot-effects').read_text().splitlines().count('applied') >= 2)
    Path('/run/verify/migration-system-snapshot-effects').write_text(Path('/var/log/arch-niri-dux-migration-snapshot-effects').read_text())
    assert (HOME / '.local/state/migration-user-snapshot-effects').read_text().splitlines() == ['applied']
    assert (USER_STATE / '99999999_10_verification.sh.json').is_file()
    for scope in ('system', 'user'):
        (CODE / scope / '99999999_10_verification.sh').unlink(missing_ok=True)
    run(COMMAND, 'run')


if __name__ == '__main__':
    dispatch('migrations', {'check': check})
