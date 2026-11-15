"""Boot acceptance behavior."""

import json
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from . import dispatch
from .guest import record, run


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


OLD_PINNED_MEMORY_KIB = 262144


def parse_luks_memories(dump):
    assert re.search(r'PBKDF:\s+argon2id', dump), dump
    memories = [int(value) for value in re.findall(r'Memory:\s+(\d+)', dump)]
    assert memories, dump
    return memories


def assert_luks_pbkdf(dump):
    memories = parse_luks_memories(dump)
    assert min(memories) > OLD_PINNED_MEMORY_KIB, dump
    return memories


def luks_pbkdf():
    status = run('cryptsetup', 'status', 'dux-root', stdout=subprocess.PIPE).stdout
    device = re.search(r'device:\s+(\S+)', status)
    assert device, status
    dump = run('cryptsetup', 'luksDump', device.group(1), stdout=subprocess.PIPE).stdout
    memories = assert_luks_pbkdf(dump)
    record('luks-pbkdf', {'device': device.group(1), 'pbkdf': 'argon2id', 'memory_kib': memories})


def x11_keyboard(keymap, map_path=Path('/usr/share/systemd/kbd-model-map')):
    """Expected (layout, model, variant, options) for a console keymap.

    Mirrors the installer's conversion through systemd's kbd-model-map —
    including its US fallback for unmapped keymaps — so the probe asserts
    the login layout the installer actually designed. The probe keeps its own
    copy because it also runs on the installed system, where the installer
    package is not staged.
    """
    for line in map_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        fields = stripped.split()
        if len(fields) < 5 or fields[0] != keymap:
            continue
        layout, model, variant, options = fields[1], fields[2], fields[3], fields[4]
        return (layout, model, None if variant == '-' else variant, None if options == '-' else options)
    return ('us', 'pc105', None, None)


def keyboard_conf_text(root):
    return (root / 'etc/X11/xorg.conf.d/00-keyboard.conf').read_text()


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
    record('limine-cmdlines', entries)
    run('arch-chroot', str(root), 'systemctl', 'enable', 'serial-getty@ttyS0.service')
    configuration = json.loads(Path('/run/verify/install.json').read_text())
    keymap = configuration.get('keymap', 'us')
    layout, _model, variant, _options = x11_keyboard(keymap)
    assert (root / 'etc/vconsole.conf').read_text() == f'KEYMAP={keymap}\n'
    text = keyboard_conf_text(root)
    assert f'Option "XkbLayout" "{layout}"' in text, text
    if variant:
        assert f'Option "XkbVariant" "{variant}"' in text, text
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
    luks_pbkdf()
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
    record(
        'swap-evidence',
        {
            'subvolume': mounted_subvolume,
            'resume_offset': int(offset),
            'active_swap': swaps,
        },
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
    record('plymouth-evidence', evidence)


def prepare_hibernate():
    storage()
    if json.loads(Path('/run/verify/install.json').read_text()).get('swap_gib') == 0:
        raise RuntimeError('This installation explicitly disabled hibernation.')
    if 'disk' not in Path('/sys/power/state').read_text().split():
        raise RuntimeError('This VM kernel does not advertise hibernation; no resume test was performed.')
    token = secrets.token_hex(16)
    Path('/run/dux-hibernate-memory-marker').write_text(token)
    Path('/run/dux-hibernate-resumed').unlink(missing_ok=True)
    record(
        'hibernate-expected',
        {
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'memory_token': token,
        },
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


def keyboard():
    # Niri leaves its xkb section empty so it inherits systemd's X11 keyboard
    # setting, which is also what the greetd login screen uses. localectl
    # reads that setting from the file the installer wrote.
    configuration = json.loads(Path('/run/verify/install.json').read_text())
    keymap = configuration.get('keymap', 'us')
    layout, _model, variant, _options = x11_keyboard(keymap)
    status = run('localectl', 'status', stdout=subprocess.PIPE).stdout
    match = re.search(r'X11 Layout:\s*(\S+)', status)
    assert match and match.group(1) == layout, status
    if variant:
        found = re.search(r'X11 Variant:\s*(\S+)', status)
        assert found and found.group(1) == variant, status


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


if __name__ == '__main__':
    dispatch(
        'boot',
        {
            'prepare': lambda: prepare(Path(sys.argv[2])),
            'storage': storage,
            'keyboard': keyboard,
            'luks-pbkdf': luks_pbkdf,
            'plymouth-config': lambda: plymouth_config(sys.argv[2] if len(sys.argv) > 2 else '/'),
            'plymouth-status': plymouth_status,
            'prepare-hibernate': prepare_hibernate,
            'serial-console-enable': lambda: serial_console(True, sys.argv[2] if len(sys.argv) > 2 else '/'),
            'serial-console-disable': lambda: serial_console(False, sys.argv[2] if len(sys.argv) > 2 else '/'),
            'splash-fallback-disable': lambda: splash_fallback(True),
            'splash-fallback-enable': lambda: splash_fallback(False),
        },
    )
