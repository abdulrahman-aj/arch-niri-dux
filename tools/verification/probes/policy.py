"""Package policy qualification on disposable installed guests."""

import json
import os
from pathlib import Path
import pwd
import re
import subprocess

from tools.digest import digest

from . import dispatch
from .guest import EVIDENCE, output, record, run, user_command, wait_until


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
# The ISO's packages/packages.nvidia*, which the installer adds on --nvidia runs.
NVIDIA_PACKAGES = {
    'open': ('nvidia-open', 'nvidia-utils', 'lib32-nvidia-utils', 'libva-nvidia-driver'),
    '580xx': ('nvidia-580xx-dkms', 'nvidia-580xx-utils', 'lib32-nvidia-580xx-utils', 'linux-headers'),
}
NVIDIA_POLICY = '/usr/share/arch-niri-dux/mkinitcpio-nvidia.conf'


def nvidia_driver():
    """Check the forced NVIDIA driver of an --nvidia run, or its absence on a default install.

    VMs have no NVIDIA card, so this proves installation, the module build and
    the initramfs, not that the driver drives a display.
    """
    config = EVIDENCE / 'install.json'
    band = json.loads(config.read_text()).get('nvidia') if config.is_file() else None
    link = Path('/etc/mkinitcpio.conf.d/nvidia.conf')
    driver = subprocess.run(['pacman', '-T', 'NVIDIA-MODULE'], capture_output=True, check=False).returncode == 0
    if band not in NVIDIA_PACKAGES:
        assert not driver, 'A default install has an NVIDIA driver.'
        assert not link.exists() and not link.is_symlink(), 'A default install links the NVIDIA policy.'
        return {'band': None}
    kernel = os.uname().release
    initramfs = Path('/boot', Path('/etc/machine-id').read_text().strip(), 'linux/initramfs')
    contents = output('lsinitcpio', str(initramfs)).splitlines()
    evidence = {
        'band': band,
        'packages': output('pacman', '-Q', *NVIDIA_PACKAGES[band]).splitlines(),
        'link': os.readlink(link),
        'module': output('modinfo', '-k', kernel, '-F', 'filename', 'nvidia_drm'),
        'initramfs': [line for line in contents if re.search(r'(/nvidia[-\w]*|/gpu/drm/.+)\.ko', line)],
    }
    assert evidence['link'] == NVIDIA_POLICY, evidence
    names = {Path(line).name.split('.')[0] for line in evidence['initramfs']}
    assert {'nvidia', 'nvidia-drm', 'nvidia-modeset', 'nvidia-uvm'} <= names, evidence
    # The VM's display is not NVIDIA, so kms must stay. It adds the driver of the
    # display present at build time: bochs or virtio-gpu, depending on the stage.
    assert names & {'bochs', 'virtio-gpu'}, evidence
    if band == '580xx':
        evidence['dkms'] = output('dkms', 'status', '-k', kernel)
        assert 'installed' in evidence['dkms'], evidence
        # nvidia-580xx-utils enables these at install; video memory preservation needs them.
        for unit in ('nvidia-suspend.service', 'nvidia-resume.service', 'nvidia-hibernate.service'):
            assert output('systemctl', 'is-enabled', unit) == 'enabled', unit
    return evidence


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
    assert repositories == ['arch-niri-dux', 'core', 'extra', 'multilib', 'omarchy'], repositories

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
    nvidia = nvidia_driver()
    record(
        'package-policy',
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
            'nvidia': nvidia,
        },
    )


def boot_artifacts():
    # limine_history payloads are immutable recovery images, not current kernels.
    paths = [*sorted(Path('/boot').glob('*/*/initramfs')), Path('/boot/limine.conf')]
    assert len(paths) > 1, 'No installed initramfs found.'
    return {str(path): {'mtime_ns': path.stat().st_mtime_ns, 'sha256': digest(path)} for path in paths}


def record_boot():
    run('systemd-detect-virt', '--vm')
    record('boot-policy-before', boot_artifacts())


def upgraded_boot():
    transaction = Path('/run/verify/boot-policy-transaction.log').read_text()
    # A kernel co-transaction can rebuild artifacts even if our hook never ran.
    assert 'Rebuilding Arch Niri Dux boot policy' in transaction, 'Policy hook did not run.'
    before = json.loads(Path('/run/verify/boot-policy-before.json').read_text())
    after = boot_artifacts()
    for path, old in before.items():
        assert after[path]['mtime_ns'] > old['mtime_ns'], (path, old, after[path])
    record(
        'boot-policy-upgrade',
        {
            'before': before,
            'after': after,
            'installed_packages': output('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline', 'linux'),
            'all_boot_artifacts_regenerated': True,
        },
    )


if __name__ == '__main__':
    dispatch('policy', {'check': check, 'record-boot': record_boot, 'upgraded-boot': upgraded_boot})
