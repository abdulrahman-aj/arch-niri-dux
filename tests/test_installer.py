import contextlib
import hashlib
import json
import io
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from installer import install as installer
from installer import nvidia
from installer import payload
from installer import storage
from installer import wizard


GUM_REPLIES_SCRIPT = """#!/bin/sh
if [ "$1" = "style" ]; then exit 0; fi
replies="$GUM_REPLIES"
line=$(head -n 1 "$replies")
tail -n +2 "$replies" > "$replies.tmp" && mv "$replies.tmp" "$replies"
code=${line%%:*}
text=${line#*:}
if [ "$code" = "$line" ]; then code="$line"; text=""; fi
if [ -n "$text" ]; then printf '%s' "$text"; fi
exit "$code"
"""


def make_bin(directory, files):
    bindir = Path(directory) / 'bin'
    bindir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    return bindir


def lsblk_script(disks):
    return "#!/bin/sh\nprintf '%s' '" + json.dumps({'blockdevices': disks}).replace("'", "'\"'\"'") + "'\n"


def marker_script(marker, code=0):
    # Destructive disk tools touch a marker so tests assert absence/presence
    # instead of pinning argv lists.
    return f'#!/bin/sh\n: > "{marker}"\nexit {code}\n'


def true_script():
    return '#!/bin/sh\nexit 0\n'


def fake_stat(real_stat, rdev=1, ino=2):
    def stat(path, *args, **kwargs):
        if str(path) == '/dev/vda':
            return type('Stat', (), {'st_rdev': rdev, 'st_ino': ino})()
        return real_stat(path, *args, **kwargs)

    return stat


def settings(**changes):
    values = dict(
        disk='/dev/vda',
        username='tester',
        hostname='dux',
        timezone='UTC',
        keymap='us',
        passphrase='test-passphrase',
        password='test-password',
        # Never detect from the test machine's own PCI tree.
        nvidia='none',
    )
    values.update(changes)
    return wizard.Settings(**values)


def disk(**changes):
    values = dict(name='/dev/vda', type='disk', size=64 * 1024**3, ro=False, mountpoints=[None], children=[])
    values.update(changes)
    return values


RECORDED_DISK = {
    'name': '/dev/sda',
    'type': 'disk',
    'size': 256 * 1024**3,
    'model': 'Samsung SSD 860',
    'serial': 'S1234',
    'tran': 'usb',
    'ro': False,
    'mountpoints': [None],
    'children': [
        {
            'name': '/dev/sda1',
            'type': 'part',
            'size': 100 * 1024**3,
            'fstype': 'vfat',
            'label': 'SYSTEM',
            'partuuid': '11111111-2222-3333-4444-555555555555',
            'mountpoints': [None],
        },
        {
            'name': '/dev/sda2',
            'type': 'part',
            'size': 150 * 1024**3,
            'fstype': 'ext4',
            'label': 'Data',
            'partuuid': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
            'mountpoints': [None],
        },
    ],
}

RECOVERY_PLAN = [(5, 100, 200, 'ef00', 'DUX EFI'), (6, 201, 300, '8309', 'DUX encrypted')]
RECOVERY_SNAPSHOT = {'order': '0001,0000'}


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        # The host may itself use Dux's mapper name. These disposable installer
        # fixtures must not inherit the host's occupied-device state.
        exists = Path.exists
        patcher = patch.object(Path, 'exists', lambda path: path != Path('/dev/mapper/dux-root') and exists(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_firmware_guards_stop_unsupported_boots_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            variable = Path(directory) / 'SecureBoot-fixture'
            for uefi, secure_boot, message in (
                (False, False, 'Boot the ISO in UEFI mode'),
                (True, True, 'Disable Secure Boot'),
                (True, False, None),
            ):
                variable.write_bytes(b'\x06\x00\x00\x00' + bytes([secure_boot]))
                err = io.StringIO()
                out = io.StringIO()
                with tempfile.TemporaryDirectory() as bindir:
                    marker = Path(bindir) / 'disk-touched'
                    usable = disk()
                    case_bin = Path(bindir) / 'bin'
                    case_bin.mkdir()
                    files = {
                        'gum': '#!/bin/sh\nexit 1\n',
                        'lsblk': lsblk_script([usable]),
                        'sgdisk': marker_script(marker),
                        'wipefs': marker_script(marker),
                    }
                    for tool in (
                        'cryptsetup',
                        'mkfs.btrfs',
                        'mkfs.fat',
                        'pacstrap',
                        'arch-chroot',
                        'sfdisk',
                        'efibootmgr',
                    ):
                        files[tool] = true_script()
                    for name, body in files.items():
                        (case_bin / name).write_text(body)
                        (case_bin / name).chmod(0o755)
                    with (
                        self.subTest(uefi=uefi, secure_boot=secure_boot),
                        patch.object(sys, 'argv', ['arch-niri-install']),
                        patch.object(installer.os, 'geteuid', return_value=0),
                        patch.object(Path, 'exists', return_value=uefi),
                        patch.object(Path, 'glob', return_value=[variable]),
                        patch.dict(os.environ, {'PATH': str(case_bin)}),
                        patch.object(sys.stdin, 'isatty', return_value=True),
                        patch('sys.stderr', err),
                        patch('sys.stdout', out),
                    ):
                        if message is None:
                            # Guards passed; the wizard cancels immediately (fake gum
                            # exits nonzero) without touching the disk.
                            self.assertEqual(installer.main(), 130)
                            self.assertIn('No disk was changed.', out.getvalue())
                        else:
                            with self.assertRaises(SystemExit) as stopped:
                                installer.main()
                            self.assertEqual(stopped.exception.code, 2)
                            self.assertIn(message, err.getvalue())
                    # Path.exists is stubbed above; check the marker with os.path.
                    self.assertFalse(os.path.exists(marker))

    def test_malformed_automation_config_stops_cleanly_without_installing(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            config = Path(directory) / 'invalid.json'
            config.write_text(json.dumps({'unexpected': 'field'}))
            config.chmod(0o600)
            marker = Path(bindir) / 'disk-touched'
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            make_bin(case_bin.parent, {'sgdisk': marker_script(marker)})
            with (
                patch.object(sys, 'argv', ['install', '--config', str(config), '--confirm-disk', '/dev/vda']),
                patch.object(installer.os, 'geteuid', return_value=0),
                patch.object(Path, 'exists', return_value=True),
                patch.object(Path, 'glob', return_value=[]),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
            ):
                self.assertEqual(installer.main(), 1)
            self.assertFalse(os.path.exists(marker))

    def test_disk_labels_distinguish_identical_models(self):
        first = disk(model='Same model', serial='SERIAL-A')
        second = disk(model='Same model', serial='SERIAL-B')
        self.assertNotEqual(storage.disk_identity(first), storage.disk_identity(second))
        self.assertIn('SERIAL-A', storage.disk_identity(first))
        self.assertIn('WWN-A', storage.disk_identity(disk(wwn='WWN-A')))
        self.assertIn('usb', storage.disk_identity(disk(tran='usb')))

    def test_erase_description_lists_transport_partitions_and_boot_entries(self):
        boots = {'/dev/sda1': [('0001', 'Windows Boot Manager')]}
        text = storage.describe_disk(RECORDED_DISK, boots)
        for fact in ('usb', '100.0 GiB', 'vfat', 'SYSTEM', 'ext4', 'Data', 'Windows Boot Manager', 'Boot0001'):
            self.assertIn(fact, text)
        self.assertIn('boots from this disk', text)

    def test_erase_description_names_empty_disks(self):
        empty = dict(RECORDED_DISK, children=[])
        self.assertIn('no partitions', storage.describe_disk(empty, {}))

    def test_erase_description_handles_missing_filesystem_facts(self):
        raw = dict(RECORDED_DISK, children=[dict(name='/dev/sda9', type='part', size=1024**3)])
        text = storage.describe_disk(raw, {})
        self.assertIn('no filesystem detected', text)
        self.assertNotIn('"', text)

    def test_boot_entries_map_partitions_by_firmware_guid(self):
        import struct
        import uuid

        guid = uuid.UUID('11111111-2222-3333-4444-555555555555')
        hd = struct.pack('<BBHIQQ', 4, 1, 42, 1, 2048, 1048576) + guid.bytes_le + b'\2\2'
        loader = r'\EFI\BOOT\BOOTX64.EFI'
        encoded = (loader + '\0').encode('utf-16-le')
        nodes = hd + struct.pack('<BBH', 4, 4, len(encoded) + 4) + encoded + b'\x7f\xff\4\0'
        option = struct.pack('<IH', 1, len(nodes)) + 'Windows Boot Manager\0'.encode('utf-16-le') + nodes + b'optional'
        firmware = (
            'BootCurrent: 0000\nBootOrder: 0001,0000\n'
            'Boot0000* USB\tPciRoot(0x0)\n'
            'Boot0001* Windows Boot Manager\tHD(1,GPT,11111111-2222-3333-4444-555555555555,0x800,0x100000)/File(\\EFI\\x.efi)'
        )
        with tempfile.TemporaryDirectory() as efivars, tempfile.TemporaryDirectory() as bindir:
            (Path(efivars) / 'Boot0001-8be4df61-93ca-11d2-aa0d-00e098032b8c').write_bytes(b'\x01\x00\x00\x00' + option)
            real_path = Path

            def fake_path(value, *args, **kwargs):
                if str(value) == '/sys/firmware/efi/efivars':
                    return real_path(efivars)
                return real_path(value, *args, **kwargs)

            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            make_bin(
                case_bin.parent, {'efibootmgr': "#!/bin/sh\nprintf '%s' '" + firmware.replace("'", "'\"'\"'") + "'\n"}
            )
            with (
                patch.object(storage, 'Path', side_effect=fake_path),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
            ):
                self.assertEqual(
                    storage.boot_entry_nodes([RECORDED_DISK]), {'/dev/sda1': [('0001', 'Windows Boot Manager')]}
                )

    def test_boot_entry_lookup_never_breaks_the_picker(self):
        with tempfile.TemporaryDirectory() as bindir:
            # No efibootmgr on PATH: the picker sees no firmware entries, not a crash.
            with patch.dict(os.environ, {'PATH': str(Path(bindir))}):
                self.assertEqual(storage.boot_entry_nodes([RECORDED_DISK]), {})
        stale = dict(
            RECORDED_DISK,
            children=[
                dict(
                    name='/dev/sda9',
                    type='part',
                    size=1024**3,
                    partuuid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                    mountpoints=[None],
                )
            ],
        )
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            make_bin(case_bin.parent, {'efibootmgr': "#!/bin/sh\nprintf '%s' 'Boot0001* Other\\tHD(9)/File(x.efi)'\n"})
            with patch.dict(os.environ, {'PATH': str(case_bin)}):
                # No PARTUUID on this disk matches the firmware GUID (no efivars
                # file decodes), so the picker sees no entries.
                self.assertEqual(storage.boot_entry_nodes([stale]), {})

    def test_alongside_refusal_names_partitions_and_recovery_commands(self):
        text = storage.alongside_recovery_text(settings(), RECOVERY_SNAPSHOT, RECOVERY_PLAN)
        for fact in (
            '/dev/vda5',
            '/dev/vda6',
            'efibootmgr --create-only --disk /dev/vda --part 5',
            "--label 'Arch Niri Dux'",
            '\\EFI\\limine\\limine_x64.efi',
            'efibootmgr --bootorder <new-entry-number>,0001,0000',
            'sgdisk --delete=5 /dev/vda',
            'sgdisk --delete=6 /dev/vda',
        ):
            self.assertIn(fact, text)

    def test_alongside_registration_failure_keeps_recovery_commands(self):
        value = settings(mode='alongside', free_region=[2048, 2097152], existing_boot='0001')
        with tempfile.TemporaryDirectory() as target_dir, tempfile.TemporaryDirectory() as bindir:
            target = Path(target_dir)
            # Missing EFI binary: the real finish raises before touching firmware.
            with patch.object(installer, 'TARGET', target):
                with self.assertRaisesRegex(ValueError, 'sgdisk --delete=5 /dev/vda'):
                    try:
                        installer.register_alongside_boot(value, RECOVERY_SNAPSHOT, RECOVERY_PLAN)
                    except ValueError as error:
                        self.assertIn('efibootmgr --create-only', str(error))
                        raise
            # Present binary but failing firmware tool: the real efibootmgr
            # failure is wrapped with the same recovery commands.
            binary = target / 'boot/EFI/limine/limine_x64.efi'
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b'efi')
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            make_bin(case_bin.parent, {'efibootmgr': '#!/bin/sh\nexit 1\n'})
            make_bin(case_bin.parent, {'blkid': '#!/bin/sh\nprintf 11111111-2222-3333-4444-555555555555\n'})
            with (
                patch.object(installer, 'TARGET', target),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
            ):
                with self.assertRaisesRegex(ValueError, 'sgdisk --delete=5 /dev/vda'):
                    try:
                        installer.register_alongside_boot(value, RECOVERY_SNAPSHOT, RECOVERY_PLAN)
                    except ValueError as error:
                        self.assertIn('efibootmgr --create-only', str(error))
                        raise

    def test_console_keymap_maps_to_x11_through_systemd_table(self):
        with tempfile.TemporaryDirectory() as directory:
            mapping = Path(directory) / 'kbd-model-map'
            mapping.write_text(
                '# consolelayout xlayout xmodel xvariant xoptions\n'
                'us\tus\tpc105+inet\t-\tterminate:ctrl_alt_bksp\n'
                'de\tde\tpc105\t-\tterminate:ctrl_alt_bksp\n'
                'ru\tru,us\tpc105\t-\tgrp:shifts_toggle,grp_led:scroll\n'
            )
            self.assertEqual(
                installer.x11_keyboard_layout('de', mapping), ('de', 'pc105', None, 'terminate:ctrl_alt_bksp')
            )
            self.assertEqual(
                installer.x11_keyboard_layout('ru', mapping),
                ('ru,us', 'pc105', None, 'grp:shifts_toggle,grp_led:scroll'),
            )
            self.assertIsNone(installer.x11_keyboard_layout('xx', mapping))
            self.assertIsNone(installer.x11_keyboard_layout('de', Path(directory) / 'missing'))
        self.assertEqual(installer.x11_keyboard_layout('de')[0], 'de')
        self.assertEqual(installer.x11_keyboard_layout('us')[0], 'us')

    def test_x11_keyboard_file_matches_localectl_format(self):
        text = installer.xorg_keyboard_conf('de', 'de', 'pc105', None, 'terminate:ctrl_alt_bksp')
        for fact in ('Section "InputClass"', 'Identifier "system-keyboard"', 'MatchIsKeyboard "on"'):
            self.assertIn(fact, text)
        self.assertIn('Option "XkbLayout" "de"', text)
        self.assertIn('Option "XkbOptions" "terminate:ctrl_alt_bksp"', text)
        self.assertNotIn('XkbVariant', text)
        variant = installer.xorg_keyboard_conf('us-acentos', 'us', 'pc105', 'intl', 'terminate:ctrl_alt_bksp')
        self.assertIn('Option "XkbVariant" "intl"', variant)

    def test_install_writes_matching_x11_layout_for_non_us_keymap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(installer, 'TARGET', root):
                installer.write_keyboard_layout('de')
            conf = (root / 'etc/X11/xorg.conf.d/00-keyboard.conf').read_text()
            self.assertIn('Option "XkbLayout" "de"', conf)

    def test_unknown_keymap_falls_back_to_us_login_layout_with_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            with patch.object(installer, 'TARGET', root), patch('sys.stdout', output):
                installer.write_keyboard_layout('zz-unknown')
            self.assertIn('Warning', output.getvalue())
            conf = (root / 'etc/X11/xorg.conf.d/00-keyboard.conf').read_text()
            self.assertIn('Option "XkbLayout" "us"', conf)

    def test_accepts_unused_disk(self):
        self.assertIsNone(storage.disk_rejection(disk(), set()))

    def test_refuses_live_usb_with_mounted_descendant(self):
        device = disk(children=[dict(name='/dev/vda1', type='part', mountpoints=['/run/archiso/bootmnt'])])
        self.assertIn('mounted', storage.disk_rejection(device, set()))

    def test_refuses_active_swap(self):
        device = disk(children=[dict(name='/dev/vda2', type='part', mountpoints=[None])])
        self.assertIn('swap', storage.disk_rejection(device, {'/dev/vda2'}))

    def test_refuses_readonly_small_and_partition_targets(self):
        for device in (disk(ro=True), disk(size=1024**3), disk(type='part')):
            with self.subTest(device=device):
                self.assertIsNotNone(storage.disk_rejection(device, set()))

    def test_exactly_32gib_disk_passes_the_size_check(self):
        self.assertIsNone(storage.disk_rejection(disk(size=32 * 1024**3), set()))

    def test_min_disk_gib_covers_esp_reserve_and_swap(self):
        self.assertEqual(storage.min_disk_gib(0), 32)
        self.assertEqual(storage.min_disk_gib(6), 35)
        self.assertEqual(storage.min_disk_gib(10), 39)
        self.assertEqual(storage.max_swap_gib(32), 3)
        self.assertEqual(storage.max_swap_gib(48), 19)

    def test_rejection_report_names_each_disk_with_its_reason(self):
        small = disk(name='/dev/sda', type='disk', size=1024**3, model='USB')
        held = disk(
            name='/dev/vda',
            type='disk',
            size=32 * 1024**3,
            children=[dict(name='/dev/vda1', type='part', mountpoints=[None])],
        )
        report = storage.describe_rejections([(small, 'smaller than 32 GiB')])
        self.assertIn('/dev/sda', report)
        self.assertIn('smaller than 32 GiB', report)
        report = storage.describe_rejections([(held, 'has an active device mapping')])
        self.assertIn('/dev/vda  32.0 GiB', report)
        self.assertIn('has an active device mapping', report)

    def test_rejection_report_lists_only_whole_disks(self):
        rom = disk(name='/dev/sr0', type='rom', size=1024**3)
        small = disk(name='/dev/sda', type='disk', size=1024**3)
        self.assertEqual(storage.describe_rejections([(rom, 'not a whole disk')]), '')
        report = storage.describe_rejections([(rom, 'not a whole disk'), (small, 'smaller than 32 GiB')])
        self.assertNotIn('sr0', report)
        self.assertIn('/dev/sda', report)

    def test_require_disk_reports_the_rejection_reason(self):
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk(ro=True)])})
            with patch.dict(os.environ, {'PATH': str(case_bin)}):
                with self.assertRaisesRegex(ValueError, 'read-only'):
                    storage.require_disk('/dev/vda')
                try:
                    storage.require_disk('/dev/vda')
                except ValueError as error:
                    self.assertIn('Target disk is unavailable', str(error))
                    self.assertIn('/dev/vda', str(error))

    def test_require_disk_names_a_missing_disk(self):
        good = disk(name='/dev/vda', type='disk', size=64 * 1024**3)
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([good])})
            with patch.dict(os.environ, {'PATH': str(case_bin)}):
                with self.assertRaisesRegex(ValueError, 'was not found among the disks'):
                    storage.require_disk('/dev/vdb')

    def test_wizard_without_usable_disks_names_each_rejection(self):
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk(ro=True)])})
            make_bin(case_bin.parent, {'gum': true_script()})
            with (
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch.object(sys.stdin, 'isatty', return_value=True),
            ):
                with self.assertRaisesRegex(ValueError, 'No unused writable disk') as stopped:
                    wizard.ask(installer.InstallationState())
            self.assertIn(f'at least {storage.MIN_DISK_BYTES // storage.GIB} GiB', str(stopped.exception))
            self.assertIn('/dev/vda', str(stopped.exception))
            self.assertIn('read-only', str(stopped.exception))

    def test_swap_fit_is_the_inverse_of_the_disk_minimum(self):
        real_read_text = Path.read_text

        def fake_read_text(path_self, *args, **kwargs):
            if str(path_self) == '/proc/meminfo':
                return 'MemTotal:        4194304 kB\nMemAvailable:    4194304 kB\n'
            return real_read_text(path_self, *args, **kwargs)

        with patch.object(Path, 'read_text', fake_read_text):
            self.assertEqual(storage.swap_size(None, 35 * storage.GIB), 6)
            with self.assertRaisesRegex(ValueError, 'will not fit'):
                storage.swap_size(None, 34 * storage.GIB)
            self.assertEqual(storage.min_disk_gib(storage.swap_size(None, 35 * storage.GIB)), 35)

    def test_input_validation_blocks_unsafe_identity_values(self):
        for change in (
            {'username': 'root'},
            {'username': '../root'},
            {'hostname': 'name\ncommand'},
            {'timezone': '../../etc/passwd'},
            {'keymap': 'us\nHOOKS=()'},
            {'disk': '/dev/vda1'},
            {'disk': '/dev/loop0'},
            {'password': 'short'},
            {'passphrase': 'linebreak\nsecret'},
            {'nvidia': 'proprietary'},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                settings(**change).validate()

    def test_valid_inputs_and_passwords_are_not_in_repr(self):
        value = settings()
        value.validate()
        self.assertNotIn(value.password, repr(value))
        self.assertNotIn(value.passphrase, repr(value))

    def test_partition_names_cover_nvme_sata_and_vm(self):
        for drive, expected in (
            ('/dev/nvme0n1', '/dev/nvme0n1p2'),
            ('/dev/mmcblk0', '/dev/mmcblk0p2'),
            ('/dev/sda', '/dev/sda2'),
            ('/dev/vda', '/dev/vda2'),
        ):
            self.assertEqual(storage.partition_name(drive, 2), expected)

    def test_fstab_uses_subvolume_paths_so_root_can_be_replaced(self):
        original = '# root\nUUID=abc / btrfs rw,subvolid=256,subvol=/@ 0 0\nUUID=abc /home btrfs rw,subvolid=257,subvol=/@home 0 0\n'
        result = installer.recovery_fstab(original)
        self.assertNotIn('subvolid=', result)
        self.assertIn('subvol=/@\t0\t0', result)
        self.assertIn('subvol=/@home', result)
        self.assertTrue(result.startswith('# root\n'))

    def test_clock_before_iso_build_date_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'iso-build-date').write_text('9999999999\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            for tool in ('sgdisk', 'wipefs'):
                make_bin(case_bin.parent, {tool: marker_script(marker)})
            for tool in ('pacman', 'cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'pacstrap', 'arch-chroot'):
                make_bin(case_bin.parent, {tool: true_script()})
            real_stat = os.stat
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
            ):
                with self.assertRaisesRegex(ValueError, 'System clock'):
                    installer.install(settings())
            self.assertFalse(os.path.exists(marker))

    def test_missing_iso_build_date_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            for tool in ('sgdisk', 'wipefs'):
                make_bin(case_bin.parent, {tool: marker_script(marker)})
            for tool in ('pacman', 'cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'pacstrap', 'arch-chroot'):
                make_bin(case_bin.parent, {tool: true_script()})
            real_stat = os.stat
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
            ):
                with self.assertRaisesRegex(ValueError, 'build date'):
                    installer.install(settings())
            self.assertFalse(os.path.exists(marker))

    def test_signature_preflight_failure_prevents_partitioning(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'offline.db').write_bytes(b'db')
            (root / 'base-1.pkg.tar.zst').write_bytes(b'pkg')
            import hashlib as _hashlib

            sums = [
                f'{_hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n'
                for name in ('offline.db', 'base-1.pkg.tar.zst')
            ]
            (root / 'SHA256SUMS').write_text(''.join(sums))
            (root / 'iso-build-date').write_text('1\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            pacman_fail = (
                '#!/bin/sh\n'
                'for arg in "$@"; do if [ "$arg" = "--upgrade" ]; then exit 1; fi; done\n'
                'for arg in "$@"; do if [ "$arg" = "--print" ]; then printf "base-1.pkg.tar.zst\\n"; exit 0; fi; done\n'
                'exit 0\n'
            )
            make_bin(case_bin.parent, {'pacman': pacman_fail})
            for tool in ('sgdisk', 'wipefs'):
                make_bin(case_bin.parent, {tool: marker_script(marker)})
            for tool in ('cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'pacstrap', 'arch-chroot', 'sfdisk', 'udevadm'):
                make_bin(case_bin.parent, {tool: true_script()})
            state = installer.InstallationState()
            real_stat = os.stat
            real_staging = tempfile.TemporaryDirectory
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
                patch.object(installer.tempfile, 'TemporaryDirectory', lambda *a, **k: real_staging(dir=root)),
            ):
                with self.assertRaisesRegex(ValueError, 'signature preflight'):
                    installer.install(settings(), state)
                self.assertFalse(state.installation_started)
            self.assertFalse(os.path.exists(marker))

    def test_signature_preflight_success_reaches_partitioning(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'offline.db').write_bytes(b'db')
            (root / 'base-1.pkg.tar.zst').write_bytes(b'pkg')
            import hashlib as _hashlib

            sums = [
                f'{_hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n'
                for name in ('offline.db', 'base-1.pkg.tar.zst')
            ]
            (root / 'SHA256SUMS').write_text(''.join(sums))
            (root / 'iso-build-date').write_text('1\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            pacman_ok = (
                '#!/bin/sh\n'
                'for arg in "$@"; do if [ "$arg" = "--print" ]; then printf "base-1.pkg.tar.zst\\n"; exit 0; fi; done\n'
                'exit 0\n'
            )
            make_bin(case_bin.parent, {'pacman': pacman_ok})
            for tool in ('sgdisk', 'wipefs'):
                make_bin(case_bin.parent, {tool: marker_script(marker)})
            for tool in ('cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'pacstrap', 'arch-chroot', 'udevadm'):
                make_bin(case_bin.parent, {tool: true_script()})
            # sfdisk returns invalid JSON so partitioning stops after the marker.
            make_bin(case_bin.parent, {'sfdisk': '#!/bin/sh\nprintf "not json\\n"\n'})
            state = installer.InstallationState()
            real_stat = os.stat
            real_staging = tempfile.TemporaryDirectory
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
                patch.object(installer.tempfile, 'TemporaryDirectory', lambda *a, **k: real_staging(dir=root)),
            ):
                with self.assertRaises(ValueError):
                    installer.install(settings(), state)
                # Preflight passed: partitioning was attempted and the install started.
                self.assertTrue(os.path.exists(marker))
                self.assertTrue(state.installation_started)

    def test_disk_swap_between_confirmation_and_install_prevents_all_commands(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\nlinux\n')
            (root / 'offline.db').write_bytes(b'db')
            (root / 'base-1.pkg.tar.zst').write_bytes(b'pkg')
            import hashlib as _hashlib

            sums = [
                f'{_hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n'
                for name in ('offline.db', 'base-1.pkg.tar.zst')
            ]
            (root / 'SHA256SUMS').write_text(''.join(sums))
            (root / 'iso-build-date').write_text('1\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            # lsblk answers differently on the first and second inventory.
            first = json.dumps({'blockdevices': [disk()]})
            second_disk = disk(size=128 * 1024**3)
            second = json.dumps({'blockdevices': [second_disk]})
            (case_bin / 'lsblk').write_text(
                '#!/bin/sh\n'
                'count_file="' + str(Path(bindir) / 'count') + '"\n'
                "if [ -f \"$count_file\" ]; then printf '%s' '" + second.replace("'", "'\"'\"'") + "'; "
                "else : > \"$count_file\"; printf '%s' '" + first.replace("'", "'\"'\"'") + "'; fi\n"
            )
            (case_bin / 'lsblk').chmod(0o755)
            (case_bin / 'pacman').write_text(
                '#!/bin/sh\nfor arg in "$@"; do if [ "$arg" = "--print" ]; then printf "base-1.pkg.tar.zst\\n"; exit 0; fi; done\nexit 0\n'
            )
            (case_bin / 'pacman').chmod(0o755)
            for tool in ('sgdisk', 'wipefs'):
                make_bin(case_bin.parent, {tool: marker_script(marker)})
            for tool in ('cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'pacstrap', 'arch-chroot'):
                make_bin(case_bin.parent, {tool: true_script()})
            real_stat = os.stat
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
            ):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    installer.install(settings())
            self.assertFalse(os.path.exists(marker))

    def test_bad_payload_package_name_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n--root=/\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            make_bin(case_bin.parent, {'sgdisk': marker_script(marker)})
            real_stat = os.stat
            with (
                patch.object(payload, 'SHARE', root),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
            ):
                with self.assertRaisesRegex(ValueError, 'package list'):
                    installer.install(settings())
            self.assertFalse(os.path.exists(marker))

    def test_malformed_driver_list_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'packages.nvidia').write_text('nvidia-open\n--root=/\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            make_bin(case_bin.parent, {'sgdisk': marker_script(marker)})
            real_stat = os.stat
            with (
                patch.object(payload, 'SHARE', root),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
            ):
                with self.assertRaisesRegex(ValueError, 'packages.nvidia'):
                    installer.install(settings(nvidia='open'))
            self.assertFalse(os.path.exists(marker))

    def test_missing_repository_database_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            package = root / 'base.pkg.tar.zst'
            package.write_bytes(b'package')
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            (root / 'SHA256SUMS').write_text(f'{checksum}  {package.name}\n')
            (root / 'iso-build-date').write_text('1\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            make_bin(case_bin.parent, {'sgdisk': marker_script(marker)})
            real_stat = os.stat
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
            ):
                value = settings()
                state = installer.InstallationState()
                with self.assertRaisesRegex(ValueError, 'repository database'):
                    installer.install(value, state)
                self.assertFalse(state.installation_started)
            self.assertFalse(os.path.exists(marker))

    def test_disk_replacement_after_wizard_confirmation_is_rejected(self):
        with tempfile.TemporaryDirectory() as bindir:
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            make_bin(case_bin.parent, {'sgdisk': marker_script(marker)})
            value = settings()
            # Confirmed identity differs in the device number: the disk was replaced.
            state = installer.InstallationState(confirmed_device=(1, 2, 64 * 1024**3))
            real_stat = os.stat

            def swapped_stat(path, *args, **kwargs):
                if str(path) == '/dev/vda':
                    return type('Stat', (), {'st_rdev': 1, 'st_ino': 3})()
                return real_stat(path, *args, **kwargs)

            with (
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=swapped_stat),
            ):
                with self.assertRaisesRegex(ValueError, 'confirmed disk was replaced'):
                    installer.install(value, state)
            self.assertFalse(os.path.exists(marker))

    def test_picker_puts_the_default_first_and_escape_cancels(self):
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            # Echo the first stdin line: the default must sort first to be returned.
            make_bin(case_bin.parent, {'gum': '#!/bin/sh\nIFS= read -r first || exit 1\nprintf "%s" "$first"\n'})
            with patch.dict(os.environ, {'PATH': str(case_bin)}):
                self.assertEqual(wizard.choose('Keyboard layout', ['de', 'us'], default='us', search=True), 'us')
            make_bin(case_bin.parent, {'gum': '#!/bin/sh\nexit 1\n'})
            with patch.dict(os.environ, {'PATH': str(case_bin)}):
                with self.assertRaises(wizard.Cancelled):
                    wizard.choose('Keyboard layout', ['de', 'us'], default='us', search=True)

    def test_errors_distinguish_preflight_from_a_started_installation(self):
        # Preflight failure (bad package list) never starts the install: no partitioning.
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n--root=/\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            make_bin(case_bin.parent, {'sgdisk': marker_script(marker)})
            real_stat = os.stat
            with (
                patch.object(payload, 'SHARE', root),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
            ):
                with self.assertRaisesRegex(ValueError, 'package list'):
                    installer.install(settings(), installer.InstallationState())
            self.assertFalse(os.path.exists(marker))
        # Started failure (pacstrap fails after partitioning) leaves the install started.
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'offline.db').write_bytes(b'db')
            (root / 'base-1.pkg.tar.zst').write_bytes(b'pkg')
            import hashlib as _hashlib

            sums = [
                f'{_hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n'
                for name in ('offline.db', 'base-1.pkg.tar.zst')
            ]
            (root / 'SHA256SUMS').write_text(''.join(sums))
            (root / 'iso-build-date').write_text('1\n')
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            (case_bin / 'pacman').write_text(
                '#!/bin/sh\nfor arg in "$@"; do if [ "$arg" = "--print" ]; then printf "base-1.pkg.tar.zst\\n"; exit 0; fi; done\nexit 0\n'
            )
            (case_bin / 'pacman').chmod(0o755)
            for tool in ('sgdisk', 'wipefs'):
                make_bin(case_bin.parent, {tool: marker_script(Path(bindir) / 'touched')})
            for tool in ('cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'arch-chroot', 'udevadm', 'sfdisk'):
                make_bin(case_bin.parent, {tool: true_script()})
            make_bin(case_bin.parent, {'pacstrap': '#!/bin/sh\nexit 1\n'})
            real_stat = os.stat
            real_staging = tempfile.TemporaryDirectory
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.dict(os.environ, {'PATH': str(case_bin)}),
                patch('os.stat', side_effect=fake_stat(real_stat)),
                patch.object(installer.tempfile, 'TemporaryDirectory', lambda *a, **k: real_staging(dir=root)),
            ):
                state = installer.InstallationState()
                with self.assertRaises((ValueError, installer.subprocess.CalledProcessError, OSError)):
                    installer.install(settings(), state)
                self.assertTrue(state.installation_started)

    def test_main_reports_retry_cleanup_only_for_a_started_installation(self):
        # main() maps a started failure to exit 1 plus the retry/cleanup
        # guidance, while a preflight failure reports no disk was changed.
        host_exists = Path.exists
        real_glob = Path.glob

        def efi_exists(path):
            if str(path) == '/sys/firmware/efi':
                return True
            return host_exists(path)

        def efi_glob(path_self, pattern):
            # Only the Secure Boot probe is faked; payload globs stay real.
            if str(path_self) == '/sys/firmware/efi/efivars':
                return []
            return real_glob(path_self, pattern)

        def config_argv(root):
            config = root / 'settings.json'
            config.write_text(
                json.dumps(
                    {
                        'disk': '/dev/vda',
                        'username': 'tester',
                        'hostname': 'dux',
                        'timezone': 'UTC',
                        'keymap': 'us',
                        'passphrase': 'test-passphrase',
                        'password': 'test-password',
                    }
                )
            )
            config.chmod(0o600)
            return ['install', '--config', str(config), '--confirm-disk', '/dev/vda']

        def run_main(case_bin, *extra):
            # The failure handler needs root, UEFI and a tool PATH; the stack
            # keeps those patches scoped to the single main() call.
            real_stat = os.stat
            with contextlib.ExitStack() as stack:
                for pending in (
                    patch.object(installer.os, 'geteuid', return_value=0),
                    patch.object(Path, 'exists', efi_exists),
                    patch.object(Path, 'glob', efi_glob),
                    patch.dict(os.environ, {'PATH': str(case_bin)}),
                    patch('os.stat', side_effect=fake_stat(real_stat)),
                    *extra,
                ):
                    stack.enter_context(pending)
                return installer.main()

        # Preflight failure (bad package list) never starts the install.
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n--root=/\n')
            marker = Path(bindir) / 'disk-touched'
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            make_bin(case_bin.parent, {'sgdisk': marker_script(marker)})
            err, out = io.StringIO(), io.StringIO()
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(sys, 'argv', config_argv(root)),
                patch('sys.stderr', err),
                patch('sys.stdout', out),
            ):
                self.assertEqual(run_main(case_bin), 1)
            self.assertIn('No disk was changed.', err.getvalue())
            self.assertNotIn('umount -R', err.getvalue())
            self.assertFalse(os.path.exists(marker))
        # Started failure (pacstrap fails after partitioning) names the
        # retry/cleanup steps for this attempt's mounts and mappings.
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'offline.db').write_bytes(b'db')
            (root / 'base-1.pkg.tar.zst').write_bytes(b'pkg')
            import hashlib as _hashlib

            sums = [
                f'{_hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n'
                for name in ('offline.db', 'base-1.pkg.tar.zst')
            ]
            (root / 'SHA256SUMS').write_text(''.join(sums))
            (root / 'iso-build-date').write_text('1\n')
            case_bin = make_bin(bindir, {'lsblk': lsblk_script([disk()])})
            (case_bin / 'pacman').write_text(
                '#!/bin/sh\nfor arg in "$@"; do if [ "$arg" = "--print" ]; then printf "base-1.pkg.tar.zst\\n"; exit 0; fi; done\nexit 0\n'
            )
            (case_bin / 'pacman').chmod(0o755)
            for tool in ('sgdisk', 'wipefs'):
                make_bin(case_bin.parent, {tool: marker_script(Path(bindir) / 'touched')})
            for tool in ('cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'arch-chroot', 'udevadm', 'sfdisk', 'blkid'):
                make_bin(case_bin.parent, {tool: true_script()})
            make_bin(case_bin.parent, {'pacstrap': '#!/bin/sh\nexit 1\n'})
            real_staging = tempfile.TemporaryDirectory
            err, out = io.StringIO(), io.StringIO()
            with (
                patch.object(payload, 'SHARE', root),
                patch.object(payload, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.object(sys, 'argv', config_argv(root)),
                patch('sys.stderr', err),
                patch('sys.stdout', out),
            ):
                self.assertEqual(
                    run_main(
                        case_bin,
                        patch.object(installer.tempfile, 'TemporaryDirectory', lambda *a, **k: real_staging(dir=root)),
                    ),
                    1,
                )
            self.assertIn('may be incomplete', err.getvalue())
            self.assertIn('umount -R', err.getvalue())
            self.assertIn('cryptsetup close', err.getvalue())
            self.assertTrue(os.path.exists(Path(bindir) / 'touched'))

    def test_invalid_username_and_passwords_retry_inline(self):
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            replies = Path(bindir) / 'replies'
            replies.write_text('0:root\n0:tester\n')
            make_bin(case_bin.parent, {'gum': GUM_REPLIES_SCRIPT})
            for name in ('head', 'tail', 'mv'):
                (case_bin / name).symlink_to(shutil.which(name))
            with patch.dict(os.environ, {'PATH': str(case_bin), 'GUM_REPLIES': str(replies)}):
                self.assertEqual(wizard.prompt('Username', validate=wizard.validate_username), 'tester')
            replies.write_text('0:short\n0:long-password\n0:different-password\n0:long-password\n0:long-password\n')
            with patch.dict(os.environ, {'PATH': str(case_bin), 'GUM_REPLIES': str(replies)}):
                self.assertEqual(wizard.secret('Login password'), 'long-password')

    def test_pacman_progress_counts_packages_then_hooks(self):
        progress = installer.PacmanProgress()
        self.assertIsNone(progress.fraction())
        output = ['resolving dependencies...', 'Packages (4) a-1  b-1  c-1  d-1', '', 'Total Installed Size:  1 MiB']
        output += [':: Processing package changes...', 'installing a...', 'installing b...', 'warning: noise']
        for line in output:
            progress.feed(line)
        halfway = progress.fraction()
        self.assertGreater(halfway, 0)
        for line in ['installing c...', 'installing d...', ':: Running post-transaction hooks...']:
            progress.feed(line)
        packages_done = progress.fraction()
        self.assertGreater(packages_done, halfway)
        # Hooks continue the same bar, which never moves back.
        seen = [packages_done]
        for number in range(1, 11):
            progress.feed(f'({number:2d}/10) Hook number {number}...')
            seen.append(progress.fraction())
        self.assertEqual(seen, sorted(seen))
        self.assertLess(seen[-1], 1)
        self.assertIn('10 of 10', progress.counts())

    def test_swap_auto_custom_disabled_and_capacity(self):
        real_read_text = Path.read_text

        def fake_read_text(path_self, *args, **kwargs):
            if str(path_self) == '/proc/meminfo':
                return 'MemTotal:       16777216 kB\nMemAvailable:    16777216 kB\n'
            return real_read_text(path_self, *args, **kwargs)

        with patch.object(Path, 'read_text', fake_read_text):
            self.assertEqual(storage.swap_size(None, 64 * storage.GIB), 18)
            self.assertEqual(storage.swap_size(8, 64 * storage.GIB), 8)
            self.assertEqual(storage.swap_size(0, 32 * storage.GIB), 0)
            with self.assertRaisesRegex(ValueError, 'will not fit'):
                storage.swap_size(None, 32 * storage.GIB)
        for value in (-1, 1.5, True, '18'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings(swap_gib=value).validate()

    def test_corrupted_offline_package_is_rejected_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'base-1.pkg.tar.zst'
            package.write_bytes(b'original package')
            (root / 'offline.db').write_bytes(b'repository database')
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            database_hash = hashlib.sha256((root / 'offline.db').read_bytes()).hexdigest()
            (root / 'SHA256SUMS').write_text(f'{checksum}  {package.name}\n{database_hash}  offline.db\n')
            payload.verify_repository(root)
            package.write_bytes(b'corrupted package')
            with self.assertRaisesRegex(ValueError, 'checksum failed'):
                payload.verify_repository(root)

    def test_offline_manifest_must_cover_every_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'base.pkg.tar.zst').touch()
            (root / 'offline.db').touch()
            (root / 'SHA256SUMS').write_text('')
            with self.assertRaisesRegex(ValueError, 'every package'):
                payload.verify_repository(root)


if __name__ == '__main__':
    unittest.main()


def pci_tree(root, devices):
    """Write fake sysfs PCI devices: (vendor, class, device) per entry; None leaves the entry unreadable."""
    for index, entry in enumerate(devices):
        device = Path(root) / f'0000:{index:02x}:00.0'
        device.mkdir()
        if entry is not None:
            for name, value in zip(('vendor', 'class', 'device'), entry, strict=True):
                (device / name).write_text(value + '\n')
    return Path(root)


class NvidiaTests(unittest.TestCase):
    def detect(self, *devices):
        with tempfile.TemporaryDirectory() as directory:
            return nvidia.detect(pci_tree(directory, devices))

    def test_bands_follow_the_turing_and_maxwell_device_boundaries(self):
        for device, band in (
            ('0x1e00', 'open'),
            ('0x1dff', '580xx'),
            ('0x1340', '580xx'),
            ('0x133f', 'unsupported'),
        ):
            with self.subTest(device=device):
                self.assertEqual(self.detect(('0x10de', '0x030000', device)), (band, f'10de:{device[2:]}'))

    def test_only_display_controllers_count(self):
        audio = ('0x10de', '0x040300', '0x10f0')
        self.assertIsNone(self.detect(audio, ('0x8086', '0x030000', '0x46a6')))
        self.assertEqual(self.detect(audio, ('0x10de', '0x030000', '0x1b81')), ('580xx', '10de:1b81'))

    def test_hybrid_laptop_and_unreadable_entries_still_find_the_card(self):
        found = self.detect(('0x8086', '0x030000', '0x46a6'), None, ('0x10de', '0x030200', '0x2520'))
        self.assertEqual(found, ('open', '10de:2520'))

    def test_newest_card_wins_when_several_are_present(self):
        found = self.detect(('0x10de', '0x030000', '0x0fc6'), ('0x10de', '0x030000', '0x2684'))
        self.assertEqual(found, ('open', '10de:2684'))

    def test_choice_resolves_to_a_driver_list(self):
        with tempfile.TemporaryDirectory() as directory:
            kepler = pci_tree(directory, [('0x10de', '0x030000', '0x0fc6')])
            self.assertIsNone(nvidia.band('auto', kepler))
            self.assertIsNone(nvidia.band('none', kepler))
            # Forced bands install without a matching card, as VM verification does.
            self.assertEqual(nvidia.band('580xx', kepler), '580xx')
            self.assertEqual(nvidia.band('open', kepler), 'open')
        with tempfile.TemporaryDirectory() as directory:
            pascal = pci_tree(directory, [('0x10de', '0x030000', '0x1b81')])
            self.assertEqual(nvidia.band('auto', pascal), '580xx')
            self.assertIsNone(nvidia.band('none', pascal))

    def check_driver(self, modinfo_code, linked=True):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            target = Path(directory)
            (target / 'usr/lib/modules/7.3.4-arch1-2').mkdir(parents=True)
            (target / 'usr/lib/modules/7.3.4-arch1-2/vmlinuz').touch()
            (target / 'etc/mkinitcpio.conf.d').mkdir(parents=True)
            if linked:
                (target / 'etc/mkinitcpio.conf.d/nvidia.conf').symlink_to(
                    '/usr/share/arch-niri-dux/mkinitcpio-nvidia.conf'
                )
            ran = Path(bindir) / 'modinfo-ran'
            case_bin = make_bin(bindir, {'arch-chroot': marker_script(ran, modinfo_code)})
            with patch.object(installer, 'TARGET', target), patch.dict(os.environ, {'PATH': str(case_bin)}):
                installer.check_nvidia_driver()
            return ran.exists()

    def test_driver_check_passes_when_the_module_exists_for_every_kernel(self):
        self.assertTrue(self.check_driver(modinfo_code=0))

    def test_failed_module_build_stops_the_installation_with_advice(self):
        with self.assertRaisesRegex(ValueError, 'not built for kernel 7.3.4-arch1-2.*Nouveau'):
            self.check_driver(modinfo_code=1)

    def test_missing_policy_link_stops_the_installation(self):
        with self.assertRaisesRegex(ValueError, 'policy was not linked'):
            self.check_driver(modinfo_code=0, linked=False)
