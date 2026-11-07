"""Boot acceptance behavior."""

import json
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from . import dispatch
from .guest import run
from .vm import installed_login, graphical_login


def configured_cmdline(root):
    # Evaluate the same Bash array and append operations as limine-entry-tool.
    return run(
        'bash',
        '-c',
        'source "$1"; printf "%s\\n" "${KERNEL_CMDLINE[default]}"',
        'bash',
        str(Path(root) / 'etc/default/limine'),
        stdout=subprocess.PIPE,
    ).stdout.split()


def prepare(root):
    # Enable post-unlock test control without changing the production boot UI.
    plymouth_config(root)
    assert not (root / 'etc/kernel/cmdline').exists(), 'Machine cmdline must have one owner.'
    expected = set(configured_cmdline(root))
    entries = []
    for line in (root / 'boot/limine.conf').read_text().splitlines():
        if not re.match(r'\s*(?:kernel_cmdline|cmdline):', line):
            continue
        tokens = line.split(':', 1)[1].split()
        assert expected - {token for token in expected if token.startswith('rootflags=')} <= set(tokens), tokens
        snapshot = any('/@snapshots/' in token for token in tokens)
        assert ('noresume' in tokens) == snapshot, tokens
        entries.append(tokens)
    assert entries and any('noresume' in entry for entry in entries), entries
    Path('/run/verify/limine-cmdlines.json').write_text(json.dumps(entries, indent=2) + '\n')
    run('arch-chroot', str(root), 'systemctl', 'enable', 'serial-getty@ttyS0.service')
    configuration = json.loads(Path('/run/verify/install.json').read_text())
    if configuration.get('swap_gib') == 0:
        assert not (root / 'swap/swapfile').exists()
        assert not any(token.startswith(('resume=', 'resume_offset=')) for token in configured_cmdline(root))
        assert 'AllowHibernation=no' in (root / 'etc/systemd/sleep.conf.d/60-hibernation.conf').read_text()


def serial_console(enabled, root='/'):
    # Serial kernel consoles force Plymouth's details mode. Keep this test
    # instrument off while qualifying the actual graphical and fallback UI.
    root = Path(root)
    arguments = ['console=tty0', 'console=ttyS0,115200n8']
    defaults = root / 'etc/default/limine'
    serial_line = 'KERNEL_CMDLINE[default]+=" ' + ' '.join(arguments) + '"'
    lines = [line for line in defaults.read_text().splitlines() if line != serial_line]
    if enabled:
        lines.append(serial_line)
    defaults.write_text('\n'.join(lines) + '\n')
    if root == Path('/'):
        run('limine-mkinitcpio')


def storage():
    configuration = json.loads(Path('/run/verify/install.json').read_text())
    cmdline = Path('/proc/cmdline').read_text().split()
    assert 'noresume' not in cmdline, 'Normal boot retained the recovery noresume flag.'
    status = run('cryptsetup', 'status', 'dux-root', stdout=subprocess.PIPE).stdout
    assert re.search(r'type:\s+LUKS2', status), status
    swaps = [line.split() for line in Path('/proc/swaps').read_text().splitlines()[1:]]
    if configuration.get('swap_gib') == 0:
        assert not swaps, swaps
        assert not any(token.startswith(('resume=', 'resume_offset=')) for token in cmdline)
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
        memory_kib = int(
            next(
                line.split()[1]
                for line in Path('/proc/meminfo').read_text().splitlines()
                if line.startswith('MemTotal:')
            )
        )
        expected_gib = (memory_kib * 1024 + 1024**3 - 1) // 1024**3 + 2
    assert Path('/swap/swapfile').stat().st_size == expected_gib * 1024**3
    offset = run(
        'btrfs', 'inspect-internal', 'map-swapfile', '-r', '/swap/swapfile', stdout=subprocess.PIPE
    ).stdout.strip()
    assert offset.isdecimal(), offset
    assert 'resume=/dev/mapper/dux-root' in cmdline
    assert f'resume_offset={offset}' in cmdline, (offset, cmdline)
    assert stat.S_IMODE(Path('/swap/swapfile').stat().st_mode) == 0o600
    Path('/run/verify/swap-evidence.json').write_text(
        json.dumps(
            {
                'subvolume': mounted_subvolume,
                'resume_offset': int(offset),
                'active_swap': swaps,
            },
            indent=2,
        )
        + '\n'
    )


def plymouth_config(root='/'):
    # Graphical boot qualification: stock theme, hook order, quiet splash and
    # text fallback availability. Runs on the live system (root='/') or on a
    # mounted target during prepare().
    root = Path(root)
    # Read the initramfs that will actually boot, rather than a single config
    # file: mkinitcpio combines the main file with later drop-ins.
    images = list((root / 'boot').glob('*/*/initramfs'))
    assert images, 'No built initramfs found.'
    for image in images:
        contents = run('lsinitcpio', str(image), stdout=subprocess.PIPE).stdout.splitlines()
        for required in ('usr/lib/systemd/systemd', 'usr/bin/plymouth', 'usr/lib/systemd/systemd-cryptsetup'):
            assert required in contents, (image, required)
    cmdline = configured_cmdline(root)
    assert 'quiet' in cmdline and 'splash' in cmdline, cmdline
    assert any(token.startswith('rd.luks.name=') for token in cmdline), cmdline
    assert 'plymouth.enable=0' not in cmdline, cmdline
    # Stock tasteful theme; Arch's default bgrt keeps firmware logos and falls
    # back to spinner, then to text/details without KMS or a framebuffer.
    theme_conf = root / 'etc/plymouth/plymouthd.conf'
    assert theme_conf.is_file(), 'Plymouth theme configuration is missing.'
    assert 'Theme=bgrt' in theme_conf.read_text(), theme_conf.read_text()
    for fallback in ('bgrt', 'spinner', 'details', 'text'):
        assert (root / f'usr/share/plymouth/themes/{fallback}').is_dir(), fallback
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
    journal = run('journalctl', '-b', '--no-pager', '-o', 'short-monotonic', stdout=subprocess.PIPE).stdout
    assert 'plymouth' in journal.lower(), 'No Plymouth activity in this boot journal.'
    run('systemctl', 'is-active', 'greetd')
    evidence = {'theme': theme, 'cmdline': cmdline}
    Path('/run/verify/plymouth-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')


def prepare_hibernate():
    storage()
    if json.loads(Path('/run/verify/install.json').read_text()).get('swap_gib') == 0:
        raise RuntimeError('This installation explicitly disabled hibernation.')
    if 'disk' not in Path('/sys/power/state').read_text().split():
        raise RuntimeError('This VM kernel does not advertise hibernation; no resume test was performed.')
    token = secrets.token_hex(16)
    Path('/run/dux-hibernate-memory-marker').write_text(token)
    Path('/run/dux-hibernate-resumed').unlink(missing_ok=True)
    Path('/run/verify/hibernate-expected.json').write_text(
        json.dumps(
            {
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'memory_token': token,
            },
            indent=2,
        )
        + '\n'
    )
    dropin = Path('/run/systemd/system/systemd-hibernate.service.d/90-verification.conf')
    dropin.parent.mkdir(parents=True, exist_ok=True)
    # ExecStartPost runs only after systemd-sleep's hibernation call returns.
    # /run is intentionally RAM-backed: a cold boot cannot satisfy this proof.
    dropin.write_text(
        '[Service]\nExecStartPost=/usr/bin/touch /run/dux-hibernate-resumed\n'
        "ExecStartPost=/usr/bin/sh -c 'echo DUX_HIBERNATION_RESUMED >/dev/ttyS0'\n"
    )
    run('systemctl', 'daemon-reload')
    run('sync')


def splash_fallback(disable, root='/'):
    # Real text-fallback path: Plymouth stays installed, but the kernel
    # disables its splash so the plain console passphrase prompt is used.
    # Boot failures stay visible because only quiet+splash quiet the console.
    root = Path(root)
    defaults = root / 'etc/default/limine'
    fallback_line = 'KERNEL_CMDLINE[default]+=" plymouth.enable=0"'
    lines = [line for line in defaults.read_text().splitlines() if line != fallback_line]
    if disable:
        lines.append(fallback_line)
    defaults.write_text('\n'.join(lines) + '\n')
    if str(root) == '/':
        run('limine-mkinitcpio')
        run('sync')


def verify_boot(vm, passed, swap_gib):
    # A CPU-readable framebuffer qualifies the real pre-login UI. A serial
    # kernel console would force Plymouth into its details/text mode.
    vm.start(cdrom=False, software_display=True)
    installed_login(vm, keyboard_unlock=True, screenshot_tag='02-graphical-unlock')
    vm.mount_probes()
    vm.probe('boot', 'plymouth-status')
    passed('Graphical disk unlock accepts keyboard input and retries an incorrect password')
    vm.probe('boot', 'serial-console-enable', timeout=300)
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    passed('UEFI boot from encrypted disk without installation ISO')
    vm.command(
        'test -e /dev/mapper/dux-root; findmnt -n -o FSTYPE / | grep -x btrfs; test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "'
    )
    vm.command(
        'cat /proc/cmdline; ! grep -q /@snapshots/ /proc/cmdline; systemctl is-enabled greetd; systemctl is-active greetd; test -f /home/tester/.config/niri/config.kdl'
    )
    vm.command(
        'systemctl is-active NetworkManager; systemctl is-enabled bluetooth; command -v nvim chromium alacritty xdg-terminal-exec niri dms papers snapshot; ! command -v yay'
    )
    vm.command(
        'mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify || mountpoint -q /run/verify'
    )
    vm.probe('boot', 'plymouth-config')
    vm.probe('boot', 'plymouth-status')
    passed('Plymouth configuration and boot services are active')
    graphical_login(vm)
    vm.probe('boot', 'storage')
    passed(
        'No-swap installation disables hibernation'
        if swap_gib == 0
        else 'Encrypted swap outside root snapshots; physical resume offset matches the kernel'
    )


def verify_fallback(vm, passed):
    # Real text-fallback path: same serial automation unlocks the disk with
    # the splash disabled, then the graphical prompt is restored.
    vm.probe('boot', 'serial-console-disable', timeout=300)
    vm.probe('boot', 'splash-fallback-disable')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False, software_display=True)
    installed_login(vm, keyboard_unlock=True, screenshot_tag='06-text-fallback')
    vm.mount_probes()
    vm.command('grep -qw plymouth.enable=0 /proc/cmdline; cd /run/verify && python -m verification.boot storage')
    passed('Text fallback unlocks the disk with the splash disabled')
    vm.probe('boot', 'splash-fallback-enable')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False, software_display=True)
    installed_login(vm, keyboard_unlock=True, screenshot_tag='07-restored-splash')
    vm.mount_probes()
    vm.command(
        '! grep -qw plymouth.enable=0 /proc/cmdline; cd /run/verify && python -m verification.boot plymouth-status'
    )
    vm.probe('recovery', 'restored')
    passed('Graphical password screen works again after text fallback')
    vm.probe('boot', 'serial-console-enable', timeout=300)
    vm.poweroff()
    vm.stop()


if __name__ == '__main__':
    dispatch(
        'boot',
        {
            'prepare': lambda: prepare(Path(sys.argv[2])),
            'storage': storage,
            'plymouth-config': lambda: plymouth_config(sys.argv[2] if len(sys.argv) > 2 else '/'),
            'plymouth-status': plymouth_status,
            'prepare-hibernate': prepare_hibernate,
            'serial-console-enable': lambda: serial_console(True, sys.argv[2] if len(sys.argv) > 2 else '/'),
            'serial-console-disable': lambda: serial_console(False, sys.argv[2] if len(sys.argv) > 2 else '/'),
            'splash-fallback-disable': lambda: splash_fallback(True),
            'splash-fallback-enable': lambda: splash_fallback(False),
        },
    )
