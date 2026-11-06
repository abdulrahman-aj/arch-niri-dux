"""Exercise real GPT writes to sparse disposable files, without root or VM builds."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import uuid
import zlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_installer import installer, settings


def table(path):
    return json.loads(subprocess.check_output(['sfdisk', '--json', str(path)], text=True))['partitiontable']


def digest_range(path, start, size):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        stream.seek(start)
        while size:
            chunk = stream.read(min(size, 1024 * 1024))
            if not chunk:
                raise AssertionError('short disk read')
            digest.update(chunk)
            size -= len(chunk)
    return digest.hexdigest()


class GPTMetadataTests(unittest.TestCase):
    def test_short_mbr_and_either_header_are_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            disk = Path(directory) / 'gpt.img'
            with disk.open('wb') as stream:
                stream.truncate(1024 * 1024)
            subprocess.run(['sgdisk', '--clear', str(disk)], check=True, stdout=subprocess.DEVNULL)
            original = disk.read_bytes()
            self.assertEqual(installer.read_gpt_metadata(disk, 512)[0], original[:512])
            for length in (0, 511, 512 + 88, 1023, len(original) - 512 + 79, len(original) - 1):
                with self.subTest(bytes_available=length):
                    disk.write_bytes(original[:length])
                    with self.assertRaisesRegex(ValueError, 'Truncated GPT metadata'):
                        installer.read_gpt_metadata(disk, 512)


class AlongsideDiskTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('sgdisk') or not shutil.which('sfdisk'):
            self.fail('Run through the project Nix shell; GPT tools are required.')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.disk = Path(self.directory.name) / 'disk.img'
        with self.disk.open('wb') as stream:
            stream.truncate(48 * installer.GIB)
        subprocess.run(['sgdisk', '--clear', '--new=1:2048:+64M', '--typecode=1:ef00',
                        '--new=4:0:+8M', '--typecode=4:0700', '--attributes=1:set:63',
                        '--attributes=4:set:60', str(self.disk)],
                       check=True, stdout=subprocess.DEVNULL)
        with self.disk.open('r+b') as stream:
            stream.write(b'preserve-protective-MBR-bootstrap-and-disk-signature'.ljust(446, b'!'))
        self.mbr_hash = digest_range(self.disk, 0, 446)
        self.original = copy.deepcopy(table(self.disk)['partitions'])
        # Arbitrary existing filesystem bytes, including beginning/end sentinels.
        with self.disk.open('r+b') as stream:
            for part in self.original:
                stream.seek(part['start'] * 512)
                stream.write(b'PRESERVE EFI/fallback/Windows/Linux\0' * 100)
                stream.seek((part['start'] + part['size']) * 512 - 4096)
                stream.write(b'last-sector-sentinel' * 100)
        self.hashes = [digest_range(self.disk, p['start'] * 512, p['size'] * 512) for p in self.original]
        guid = self.original[0]['uuid']
        self.firmware = (f'BootCurrent: 0000\nBootOrder: 0001,0000\n'
                         f'Boot0000* USB\tPciRoot(0x0)\n'
                         f'Boot0001* Existing OS\tHD(1,GPT,{guid},0x800,0x20000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)')
        self.value = settings(disk=str(self.disk), mode='alongside', existing_boot='0001', swap_gib=0)
        self.identity = (1, 2, 48 * installer.GIB)
        self.real_run = installer.run
        self.commands = []

        def command(*args, **kwargs):
            self.commands.append(args)
            if args[0] == 'efibootmgr':
                return self.firmware
            return self.real_run(*args, **kwargs)

        self.run_patch = patch.object(installer, 'run', side_effect=command)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.disk_patch = patch.object(installer, 'require_disk', return_value=self.identity)
        self.disk_patch.start()
        self.addCleanup(self.disk_patch.stop)
        self.path_patch = patch.object(installer, 'firmware_boot_path', side_effect=lambda number: (1, guid, r'\EFI\Microsoft\Boot\bootmgfw.efi') if number == '0001' else None)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.snapshot = installer.alongside_snapshot(self.value.disk)
        self.value.free_region = self.snapshot['regions'][0]
        self.commands.clear()

    def assert_old_data(self):
        self.assertEqual(digest_range(self.disk, 0, 446), self.mbr_hash)
        current = table(self.disk)['partitions']
        for original, expected in zip(self.original, self.hashes):
            self.assertIn(original, current)
            self.assertEqual(digest_range(self.disk, original['start'] * 512, original['size'] * 512), expected)

    def test_adds_only_two_partitions_and_preserves_all_old_bytes_and_metadata(self):
        result = installer.create_alongside_partitions(self.value, self.snapshot, self.identity)
        self.assertEqual(result, (str(self.disk) + '2', str(self.disk) + '3'))
        current = table(self.disk)['partitions']
        self.assertEqual(len(current), 4)
        esp = next(p for p in current if p['node'] == result[0])
        root = next(p for p in current if p['node'] == result[1])
        self.assertEqual(esp['size'] * 512, 4 * installer.GIB)
        self.assertEqual(root['start'], esp['start'] + esp['size'])
        self.assertEqual(root['start'] + root['size'] - 1, self.value.free_region[1])
        self.assert_old_data()
        self.assertFalse(any('--zap-all' in c or c[0] == 'wipefs' for c in self.commands))

    def test_windows_disk_signature_with_zero_bootstrap_is_preserved(self):
        with self.disk.open('r+b') as stream:
            stream.write(bytes(440) + struct.pack('<I', 0x6086b967) + bytes(2))
        self.mbr_hash = digest_range(self.disk, 0, 446)
        snapshot = installer.alongside_snapshot(self.value.disk)
        installer.create_alongside_partitions(self.value, snapshot, self.identity)
        with self.disk.open('rb') as stream:
            stream.seek(440)
            self.assertEqual(stream.read(4), struct.pack('<I', 0x6086b967))
        self.assertTrue(all(part.get('attrs') for part in self.original))
        self.assert_old_data()

    def test_table_or_free_extent_change_aborts_without_any_further_disk_writes(self):
        for change in ('--change-name=4:changed', '--new=7:0:+1M'):
            with self.subTest(change=change):
                subprocess.run(['sgdisk', change, str(self.disk)], check=True, stdout=subprocess.DEVNULL)
                before = table(self.disk)
                self.commands.clear()
                with self.assertRaisesRegex(ValueError, 'changed'):
                    installer.create_alongside_partitions(self.value, self.snapshot, self.identity)
                self.assertEqual(table(self.disk), before)
                self.assertFalse(any(c[0] == 'sgdisk' and '--verify' not in c for c in self.commands))
                self.assert_old_data_except_name()

    def assert_old_data_except_name(self):
        for original, expected in zip(self.original, self.hashes):
            self.assertEqual(digest_range(self.disk, original['start'] * 512, original['size'] * 512), expected)

    def test_identity_or_firmware_change_aborts(self):
        with patch.object(installer, 'require_disk', return_value=(99, 2, self.identity[2])):
            with self.assertRaisesRegex(ValueError, 'identity'):
                installer.create_alongside_partitions(self.value, self.snapshot, self.identity)
        self.firmware = self.firmware.replace('BootOrder: 0001,0000', 'BootOrder: 0000,0001')
        with self.assertRaisesRegex(ValueError, 'firmware'):
            installer.create_alongside_partitions(self.value, self.snapshot, self.identity)
        self.assert_old_data()

    def test_hybrid_mbr_is_rejected_without_repair(self):
        with self.disk.open('r+b') as stream:
            stream.seek(466)
            stream.write(b'\x07')
        before = digest_range(self.disk, 0, 1024 * 1024)
        with self.assertRaises(ValueError):
            installer.alongside_snapshot(self.value.disk)
        self.assertEqual(digest_range(self.disk, 0, 1024 * 1024), before)
        self.assert_old_data()

    def test_on_disk_crc_is_checked_even_when_the_tool_reports_an_in_memory_recovery(self):
        with self.disk.open('r+b') as stream:
            stream.seek(-512 + 16, 2)
            value = stream.read(1)
            stream.seek(-1, 1)
            stream.write(bytes([value[0] ^ 1]))
        before = digest_range(self.disk, self.disk.stat().st_size - 512, 512)
        with patch.object(installer, 'verify_gpt'):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                installer.alongside_snapshot(self.value.disk)
        self.assertEqual(digest_range(self.disk, self.disk.stat().st_size - 512, 512), before)
        self.assert_old_data()

    def test_post_creation_gpt_corruption_is_rejected(self):
        def command(*args, **kwargs):
            self.commands.append(args)
            if args[0] == 'efibootmgr':
                return self.firmware
            result = self.real_run(*args, **kwargs)
            if args[:2] == ('sfdisk', '--append'):
                with self.disk.open('r+b') as stream:
                    stream.seek(-512 + 16, 2)
                    value = stream.read(1)
                    stream.seek(-1, 1)
                    stream.write(bytes([value[0] ^ 1]))
            return result
        with patch.object(installer, 'run', side_effect=command):
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                installer.create_alongside_partitions(self.value, self.snapshot, self.identity)
        self.assert_old_data()

    def test_post_creation_bootstrap_change_is_rejected(self):
        def command(*args, **kwargs):
            if args[0] == 'efibootmgr':
                return self.firmware
            result = self.real_run(*args, **kwargs)
            if args[:2] == ('sfdisk', '--append'):
                with self.disk.open('r+b') as stream:
                    stream.write(b'X')
            return result
        with patch.object(installer, 'run', side_effect=command):
            with self.assertRaisesRegex(ValueError, 'MBR bootstrap changed'):
                installer.create_alongside_partitions(self.value, self.snapshot, self.identity)
        self.assert_old_data_except_name()

    def test_old_raw_gpt_entry_padding_is_preserved_even_when_json_would_hide_a_change(self):
        def command(*args, **kwargs):
            if args[0] == 'efibootmgr':
                return self.firmware
            result = self.real_run(*args, **kwargs)
            if args[:2] == ('sfdisk', '--append'):
                with self.disk.open('r+b') as stream:
                    stream.seek(512)
                    primary_header = stream.read(512)
                    backup_lba = struct.unpack_from('<Q', primary_header, 32)[0]
                    for lba in (1, backup_lba):
                        stream.seek(lba * 512)
                        header = bytearray(stream.read(512))
                        array_lba = struct.unpack_from('<Q', header, 72)[0]
                        stream.seek(array_lba * 512)
                        entries = bytearray(stream.read(128 * 128))
                        entries[127] = 0x42  # beyond the first NUL of an old partition name
                        struct.pack_into('<I', header, 88, zlib.crc32(entries))
                        struct.pack_into('<I', header, 16, 0)
                        size = struct.unpack_from('<I', header, 12)[0]
                        struct.pack_into('<I', header, 16, zlib.crc32(header[:size]))
                        stream.seek(array_lba * 512)
                        stream.write(entries)
                        stream.seek(lba * 512)
                        stream.write(header)
            return result
        with patch.object(installer, 'run', side_effect=command):
            with self.assertRaisesRegex(ValueError, 'Original GPT entries'):
                installer.create_alongside_partitions(self.value, self.snapshot, self.identity)
        for part in self.original:
            self.assertIn(part, table(self.disk)['partitions'])
        self.assert_old_data()

    def test_selected_extent_must_match_exactly(self):
        self.value.free_region[0] += 2048
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            installer.alongside_plan(self.value, copy.deepcopy(self.snapshot) | {'regions': [[1, 2]]})
        self.assert_old_data()


class AlongsidePlanningTests(unittest.TestCase):
    def test_fragmented_space_does_not_get_combined(self):
        table_value = dict(label='gpt', unit='sectors', sectorsize=512, firstlba=34,
                           lastlba=100 * installer.GIB // 512,
                           partitions=[dict(start=20 * installer.GIB // 512, size=1),
                                       dict(start=40 * installer.GIB // 512, size=1),
                                       dict(start=60 * installer.GIB // 512, size=1),
                                       dict(start=80 * installer.GIB // 512, size=1)])
        self.assertEqual(installer.free_regions(table_value), [])

    def test_rejects_mbr_overlaps_and_unsupported_sector_sizes(self):
        for changes in (dict(label='dos'), dict(sectorsize=1024),
                        dict(partitions=[dict(start=2048, size=5000), dict(start=3000, size=100)])):
            value = dict(label='gpt', unit='sectors', sectorsize=512, firstlba=34,
                         lastlba=100000000, partitions=[])
            value.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                installer.free_regions(value)

    def test_settings_require_explicit_region_and_boot_selection(self):
        for changes in ({}, {'free_region': [2048, 100000000]},
                        {'free_region': [True, 100000000], 'existing_boot': '0001'},
                        {'free_region': [2048, 100000000], 'existing_boot': '../1'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                settings(mode='alongside', **changes).validate()
        settings(mode='alongside', free_region=[2048, 100000000], existing_boot='0001').validate()

class AlongsideBootTests(unittest.TestCase):
    def test_decodes_real_firmware_device_path_without_parsing_efibootmgr_display(self):
        guid = uuid.UUID('82e17562-9f24-4564-9735-bb223836c0b4')
        hd = struct.pack('<BBHIQQ', 4, 1, 42, 15, 8192, 253952) + guid.bytes_le + b'\2\2'
        path = r'\EFI\debian\shimx64.efi'
        encoded = (path + '\0').encode('utf-16-le')
        nodes = hd + struct.pack('<BBH', 4, 4, len(encoded)+4) + encoded + b'\x7f\xff\4\0'
        option = struct.pack('<IH', 1, len(nodes)) + 'Debian\0'.encode('utf-16-le') + nodes + b'optional-data'
        self.assertEqual(installer.decode_efi_boot_path(option), (15, str(guid), path))
        with self.assertRaises(ValueError):
            installer.decode_efi_boot_path(option[:20])

    def test_detected_bitlocker_is_rejected_before_disk_mutation(self):
        with patch.object(installer, 'disk_inventory', return_value=[
                dict(name='/dev/vda', children=[dict(name='/dev/vda3', fstype='BitLocker')])]), \
                patch.object(installer, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'BitLocker'):
                installer.reject_unsupported_existing_storage(settings(), {'table': {'partitions': []}})
            run.assert_not_called()

    def test_kernel_must_accept_the_exact_planned_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            sysfs = Path(directory)
            (sysfs / 'vda2').mkdir()
            (sysfs / 'vda2/start').write_text('4096')
            (sysfs / 'vda2/size').write_text('8192')
            real_path = Path
            def path(value, *args):
                return sysfs if value == '/sys/class/block' else real_path(value, *args)
            with patch.object(installer, 'Path', side_effect=path):
                installer.verify_partition_nodes(settings(), [(2, 512, 1535, 'ef00', 'ESP')], 4096)
                with self.assertRaisesRegex(ValueError, 'Kernel partition geometry'):
                    installer.verify_partition_nodes(settings(), [(2, 513, 1536, 'ef00', 'ESP')], 4096)

    def test_loader_inspection_unmounts_and_never_requests_writable_mount(self):
        temporary_directory = tempfile.TemporaryDirectory
        with patch.object(installer.tempfile, 'TemporaryDirectory',
                          side_effect=lambda **kwargs: temporary_directory()), \
                patch.object(installer, 'run', side_effect=['vfat', None, None]) as run:
            with self.assertRaises(FileNotFoundError):
                installer.validate_existing_loader(dict(partition='/dev/vda1', path='/missing.efi'))
            commands = [call.args for call in run.call_args_list]
            self.assertEqual(commands[1][:5], ('mount', '-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec'))
            self.assertEqual(commands[-1][0], 'umount')

class AlongsideRegistrationTests(unittest.TestCase):
    def setUp(self):
        # The host may itself use Dux's mapper name. These disposable installer
        # fixtures must not inherit the host's occupied-device state.
        exists = Path.exists
        patcher = patch.object(Path, 'exists', lambda path:
                               path != Path('/dev/mapper/dux-root') and exists(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hooks_defer_registration_and_failure_does_not_enable_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            (target / 'root').mkdir(parents=True)
            (root / 'desktop').mkdir()
            (root / 'packages.target').write_text('base\nsnap-pac\n')
            (root / 'offline.db').touch()
            region = [2097152, 134215679]
            value = settings(mode='alongside', free_region=region, existing_boot='0001', swap_gib=0)
            snapshot = dict(regions=[region], boots={'0001': {}},
                            table=dict(sectorsize=512, partitions=[dict(node='/dev/vda1')]))
            transactions = []
            configured = []

            def command(*args, **kwargs):
                if args[0] == 'pacstrap':
                    self.assertIn('SKIP_UEFI=yes', (target / 'etc/default/limine').read_text())
                    transactions.append(args)
                if '/root/configure-target.sh' in args:
                    configured.append(args)
                if args[0] == 'genfstab':
                    return 'UUID=test / btrfs subvol=/@ 0 0\n'
                if args[0] == 'blkid':
                    return 'test-uuid'
                return '' if kwargs.get('capture') else None

            def final_registration(*_args):
                self.assertIn('SKIP_UEFI=yes', (target / 'etc/default/limine').read_text())

            temporary_directory = tempfile.TemporaryDirectory
            with patch.object(installer, 'SHARE', root), patch.object(installer, 'REPO', root), \
                    patch.object(installer, 'TARGET', target), \
                    patch.object(installer, 'require_disk', return_value=(1, 2, 64 * installer.GIB)), \
                    patch.object(installer, 'alongside_snapshot', return_value=snapshot), \
                    patch.object(installer, 'reject_unsupported_existing_storage'), \
                    patch.object(installer, 'validate_existing_loader'), \
                    patch.object(installer, 'verify_repository'), \
                    patch.object(installer, 'create_alongside_partitions', return_value=('/dev/vda2', '/dev/vda3')), \
                    patch.object(installer, 'verify_partition_nodes'), \
                    patch.object(installer, 'finish_alongside_boot', side_effect=final_registration) as finish, \
                    patch.object(installer.shutil, 'which', return_value='/usr/bin/tool'), \
                    patch.object(installer.os.path, 'ismount', return_value=False), \
                    patch.object(installer, 'run', side_effect=command), \
                    patch.object(installer.tempfile, 'TemporaryDirectory',
                                 side_effect=lambda **kwargs: temporary_directory(dir=root)), \
                    patch.object(installer.subprocess, 'run'):
                installer.install(value)
                self.assertEqual(len(transactions), 2)
                self.assertIn('arch-niri-dux', transactions[0])
                self.assertNotIn('arch-niri-dux-baseline', transactions[0])
                self.assertIn('arch-niri-dux-baseline', transactions[1])
                self.assertNotIn('SKIP_UEFI', (target / 'etc/default/limine').read_text())
                finish.side_effect = ValueError('unexpected firmware entries')
                with self.assertRaisesRegex(ValueError, 'unexpected firmware entries'):
                    installer.install(value)
            self.assertEqual(len(transactions), 4)
            self.assertTrue(all(call[-1] == 'defer-efi' for call in configured))
            self.assertIn('SKIP_UEFI=yes', (target / 'etc/default/limine').read_text())


class FirmwareRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = Path(self.directory.name)
        self.binary = self.target / 'boot/EFI/limine/limine_x64.efi'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'new EFI binary')
        self.config = self.target / 'boot/limine.conf'
        self.original_config = '/Arch Niri Dux\n protocol: linux\n'
        self.config.write_text(self.original_config)
        self.guid = '11111111-2222-3333-4444-555555555555'
        self.before = 'BootOrder: 0001,0000\nBoot0000* USB\tPciRoot(0x0)\nBoot0001* Windows\tHD(1,GPT,old,0x800,0x10000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)'
        self.after = self.before + '\nBoot0002* Arch Niri Dux\tHD(2,GPT,new,0x10000,0x800000)/File(\\EFI\\limine\\limine_x64.efi)'
        self.final = self.after.replace('BootOrder: 0001,0000', 'BootOrder: 0002,0001,0000')
        self.snapshot = dict(entries=installer.firmware_entries(self.before), order='0001,0000',
                             boots={'0001': dict(guid='original-esp-guid', path='/EFI/Microsoft/Boot/bootmgfw.efi')})
        self.settings = settings(existing_boot='0001', mode='alongside')
        self.commands = []

    def finish(self, outputs, path=None):
        outputs = iter(outputs)
        def command(*args, **_kwargs):
            self.commands.append(args)
            if args[0] == 'blkid':
                return self.guid
            if args == ('efibootmgr', '--verbose'):
                return next(outputs)
        if path is None:
            path = (2, self.guid, r'\EFI\limine\limine_x64.efi')
        with patch.object(installer, 'TARGET', self.target), \
                patch.object(installer, 'run', side_effect=command), \
                patch.object(installer, 'firmware_boot_path', return_value=path):
            installer.finish_alongside_boot(self.settings, self.snapshot, 2)

    def test_retains_original_entries_and_chainloads_the_original_esp(self):
        self.finish([self.before, self.after, self.final])
        self.assertIn('guid(original-esp-guid):/EFI/Microsoft/Boot/bootmgfw.efi', self.config.read_text())
        create = next(command for command in self.commands if '--create-only' in command)
        self.assertEqual(create[-1], r'\EFI\limine\limine_x64.efi')
        self.assertIn(('efibootmgr', '--bootorder', '0002,0001,0000'), self.commands)
        self.assertFalse(any('--delete-bootnum' in command for command in self.commands))

    def test_missing_new_binary_never_changes_firmware_or_menu(self):
        self.binary.unlink()
        with self.assertRaisesRegex(ValueError, 'missing its Limine EFI binary'):
            self.finish([])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.config.read_text(), self.original_config)

    def test_inactive_new_entry_is_never_made_default(self):
        with self.assertRaisesRegex(ValueError, 'inactive'):
            self.finish([self.before, self.after.replace('Boot0002*', 'Boot0002 ')])
        self.assertFalse(any('--bootorder' in command for command in self.commands))

    def test_wrong_new_partition_guid_or_path_is_never_made_default(self):
        for path in ((3, self.guid, r'\EFI\limine\limine_x64.efi'),
                     (2, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', r'\EFI\limine\limine_x64.efi'),
                     (2, self.guid, r'\EFI\other\wrong.efi')):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'wrong ESP identity or loader path'):
                self.finish([self.before, self.after], path=path)
        self.assertFalse(any('--bootorder' in command for command in self.commands))

    def test_changed_firmware_stops_before_appending_chainload_config(self):
        for changed in (self.before.replace('Windows', 'Changed'),
                        self.before.replace('BootOrder: 0001,0000', 'BootOrder: 0001,0000,0009')):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'Firmware entries changed'):
                self.finish([changed])
            self.assertEqual(self.config.read_text(), self.original_config)
        self.assertFalse(any('--create-only' in command for command in self.commands))


if __name__ == '__main__':
    unittest.main()
