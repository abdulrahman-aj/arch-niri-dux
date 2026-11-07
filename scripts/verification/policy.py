"""Package policy qualification on disposable installed guests."""

import json
import hashlib
from pathlib import Path
import subprocess
import pwd

from . import dispatch
from .guest import run, user_command, wait_until


SYSTEM_UNITS = (
    'NetworkManager.service',
    'bluetooth.service',
    'snapper-cleanup.timer',
    'limine-snapper-sync.service',
    'systemd-timesyncd.service',
    'power-profiles-daemon.service',
    'getty@tty2.service',
    'cups.service',
    'avahi-daemon.service',
    'greetd.service',
)
USER_UNITS = ('dms.service', 'dsearch.service', 'dcal.service', 'dfiles.service', 'dux-application-themes.service')


def output(*args):
    return run(*args, stdout=subprocess.PIPE).stdout.strip()


def check():
    run('systemd-detect-virt', '--vm')
    owned = [
        '/etc/mkinitcpio.conf.d/arch-niri-dux.conf',
        '/etc/snapper/configs/root',
        '/usr/share/libalpm/hooks/95-arch-niri-dux-boot-policy.hook',
        '/usr/lib/pam.d/arch-niri-dux',
        '/usr/share/limine-entry-tool.d/arch-niri-dux.conf',
        '/usr/lib/systemd/system-preset/50-arch-niri-dux.preset',
        '/usr/lib/systemd/user-preset/50-arch-niri-dux.preset',
        '/usr/share/arch-niri-dux/niri/tail.kdl',
        '/usr/lib/systemd/system/greetd.service.d/10-plymouth.conf',
    ]
    owned += [
        f'/usr/lib/systemd/system/systemd-{name}.service.d/60-recovery.conf'
        for name in ('hibernate', 'hybrid-sleep', 'suspend-then-hibernate')
    ]
    for path in owned:
        assert output('pacman', '-Qqo', path) == 'arch-niri-dux', path
    for unit in SYSTEM_UNITS:
        assert output('systemctl', 'is-enabled', unit) == 'enabled', unit
    disabled = subprocess.run(
        ['systemctl', 'is-enabled', 'snapper-timeline.timer'], text=True, capture_output=True, check=False
    )
    assert disabled.returncode != 0 and disabled.stdout.strip() == 'disabled', disabled
    for unit in USER_UNITS:
        assert output('systemctl', '--global', 'is-enabled', unit) == 'enabled', unit
        assert user_command('systemctl', '--user', 'is-enabled', unit, capture=True).stdout.strip() == 'enabled', unit
    # Presets must work after account creation too, without a per-user enable.
    run('useradd', '-m', 'preset-check')
    uid = pwd.getpwnam('preset-check').pw_uid
    try:
        run('loginctl', 'enable-linger', 'preset-check')
        wait_until(
            'New user manager',
            lambda: (
                subprocess.run(['systemctl', 'is-active', '--quiet', f'user@{uid}.service'], check=False).returncode
                == 0
            ),
        )
        for unit in USER_UNITS:
            assert (
                output(
                    'runuser',
                    '-u',
                    'preset-check',
                    '--',
                    'env',
                    f'XDG_RUNTIME_DIR=/run/user/{uid}',
                    'systemctl',
                    '--user',
                    'is-enabled',
                    unit,
                )
                == 'enabled'
            ), unit
    finally:
        run('loginctl', 'disable-linger', 'preset-check')
        run('loginctl', 'terminate-user', 'preset-check')
        run('systemctl', 'stop', f'user@{uid}.service')
        wait_until(
            'Temporary user processes stopped',
            lambda: subprocess.run(['pgrep', '-u', str(uid)], stdout=subprocess.DEVNULL, check=False).returncode == 1,
        )
        run('userdel', '-r', 'preset-check')
    after = output('systemctl', 'show', 'greetd.service', '--property=After', '--value').split()
    assert {'plymouth-quit.service', 'plymouth-quit-wait.service'} <= set(after), after
    conditions = {}
    for name in ('hibernate', 'hybrid-sleep', 'suspend-then-hibernate'):
        unit = f'systemd-{name}.service'
        path = json.loads(
            output(
                'busctl',
                '--json=short',
                'call',
                'org.freedesktop.systemd1',
                '/org/freedesktop/systemd1',
                'org.freedesktop.systemd1.Manager',
                'LoadUnit',
                's',
                unit,
            )
        )['data'][0]
        conditions[unit] = json.loads(
            output(
                'busctl',
                '--json=short',
                'get-property',
                'org.freedesktop.systemd1',
                path,
                'org.freedesktop.systemd1.Unit',
                'Conditions',
            )
        )['data']
        assert any(row[:4] == ['ConditionKernelCommandLine', False, True, 'noresume'] for row in conditions[unit]), (
            conditions
        )
    repositories = output('pacman-conf', '--repo-list').splitlines()
    assert repositories == ['core', 'extra', 'omarchy', 'arch-niri-dux'], repositories

    # Get the real unlocked collection over the logged-in user's session bus.
    # Merely finding the daemon doesn't prove that PAM received the password.
    def unlocked():
        result = user_command(
            'busctl',
            '--user',
            'get-property',
            'org.freedesktop.secrets',
            '/org/freedesktop/secrets/collection/login',
            'org.freedesktop.Secret.Collection',
            'Locked',
            capture=True,
        )
        return result.stdout.strip() == 'b false'

    wait_until('PAM-unlocked login keyring', unlocked)
    user_command('niri', 'validate')
    Path('/run/verify/package-policy.json').write_text(
        json.dumps(
            {
                'package_owned_policy': owned,
                'system_units_enabled': SYSTEM_UNITS,
                'user_units_enabled': USER_UNITS,
                'new_and_existing_user_presets': True,
                'snapper_timeline_disabled': True,
                'greetd_after': after,
                'recovery_conditions': conditions,
                'repository_order': repositories,
                'login_keyring_unlocked': True,
            },
            indent=2,
        )
        + '\n'
    )


def boot_artifacts():
    # limine_history payloads are immutable recovery images, not current kernels.
    paths = [*sorted(Path('/boot').glob('*/*/initramfs')), Path('/boot/limine.conf')]
    assert len(paths) > 1, 'No installed initramfs found.'
    return {
        str(path): {'mtime_ns': path.stat().st_mtime_ns, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in paths
    }


def record_boot():
    run('systemd-detect-virt', '--vm')
    Path('/run/verify/boot-policy-before.json').write_text(json.dumps(boot_artifacts(), indent=2) + '\n')


def upgraded_boot():
    transaction = Path('/run/verify/boot-policy-transaction.log').read_text()
    # A kernel co-transaction can rebuild artifacts even if our hook never ran.
    assert 'Rebuilding Arch Niri Dux boot policy' in transaction, 'Policy hook did not run.'
    before = json.loads(Path('/run/verify/boot-policy-before.json').read_text())
    after = boot_artifacts()
    for path, old in before.items():
        assert after[path]['mtime_ns'] > old['mtime_ns'], (path, old, after[path])
    Path('/run/verify/boot-policy-upgrade.json').write_text(
        json.dumps(
            {
                'before': before,
                'after': after,
                'installed_packages': output('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline', 'linux'),
                'all_boot_artifacts_regenerated': True,
            },
            indent=2,
        )
        + '\n'
    )


if __name__ == '__main__':
    dispatch('policy', {'check': check, 'record-boot': record_boot, 'upgraded-boot': upgraded_boot})
