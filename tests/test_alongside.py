"""Exercise real GPT writes to sparse disposable files, without root or VM builds."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import uuid
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_installer import settings
from installer import storage as installer


def bin_path(bindir, image_tools=False):
    # Only these commands may run outside the fake bin; disk tools operate on sparse test images.
    tools = ('cat', 'cp', 'python3', 'sgdisk', 'sfdisk') if image_tools else ('cat',)
    for name in tools:
        target = bindir / name
        if not target.exists():
            target.symlink_to(shutil.which(name))
    return str(bindir)


def table(path):
    return json.loads(subprocess.check_output(['sfdisk', '--json', str(path)], text=True))['partitiontable']


def efi_option_bytes(partition, guid, loader, description):
    hd = struct.pack('<BBHIQQ', 4, 1, 42, partition, 2048, 1048576) + guid.bytes_le + b'\2\2'
    encoded = (loader + '\0').encode('utf-16-le')
    nodes = hd + struct.pack('<BBH', 4, 4, len(encoded) + 4) + encoded + b'\x7f\xff\4\0'
    return struct.pack('<IH', 1, len(nodes)) + (description + '\0').encode('utf-16-le') + nodes + b'optional'


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
    @classmethod
    def setUpClass(cls):
        if not shutil.which('sgdisk') or not shutil.which('sfdisk'):
            raise RuntimeError('Run through the project Nix shell; GPT tools are required.')
        cls.fixture_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture_directory.cleanup)
        cls.fixture = Path(cls.fixture_directory.name) / 'pristine.img'
        with cls.fixture.open('wb') as stream:
            stream.truncate(48 * installer.GIB)
        subprocess.run(
            [
                'sgdisk',
                '--clear',
                '--new=1:2048:+64M',
                '--typecode=1:ef00',
                '--new=4:0:+8M',
                '--typecode=4:0700',
                '--attributes=1:set:63',
                '--attributes=4:set:60',
                str(cls.fixture),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.disk = Path(self.directory.name) / 'disk.img'
        # Keep each real GPT mutation isolated without allocating a 48 GiB copy.
        subprocess.run(['cp', '--reflink=auto', '--sparse=always', str(self.fixture), str(self.disk)], check=True)
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
        self.firmware = (
            f'BootCurrent: 0000\nBootOrder: 0001,0000\n'
            f'Boot0000* USB\tPciRoot(0x0)\n'
            f'Boot0001* Existing OS\tHD(1,GPT,{guid},0x800,0x20000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)'
        )
        self.value = settings(disk=str(self.disk), mode='alongside', existing_boot='0001', swap_gib=0)
        # Fake firmware + disk inventory on PATH; real sgdisk/sfdisk run for real.
        self.fake_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.fake_dir.cleanup)
        fake_bin = Path(self.fake_dir.name) / 'bin'
        fake_bin.mkdir()
        firmware_file = Path(self.fake_dir.name) / 'firmware.txt'
        firmware_file.write_text(self.firmware)
        (fake_bin / 'efibootmgr').write_text(f'#!/bin/sh\ncat "{firmware_file}"\n')
        (fake_bin / 'efibootmgr').chmod(0o755)
        self.firmware_file = firmware_file
        lsblk_disk = {
            'name': str(self.disk),
            'type': 'disk',
            'size': 48 * installer.GIB,
            'ro': False,
            'mountpoints': [None],
            'children': [],
        }
        lsblk_file = Path(self.fake_dir.name) / 'lsblk.json'
        lsblk_file.write_text(json.dumps({'blockdevices': [lsblk_disk]}))
        (fake_bin / 'lsblk').write_text(f'#!/bin/sh\ncat "{lsblk_file}"\n')
        (fake_bin / 'lsblk').chmod(0o755)
        self.path_patch = patch.dict(os.environ, {'PATH': bin_path(fake_bin, image_tools=True)})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        # Fake efivars for the ESP's boot entry.
        self.efivars = tempfile.TemporaryDirectory()
        self.addCleanup(self.efivars.cleanup)
        entry_guid = uuid.UUID(guid)
        option = efi_option_bytes(1, entry_guid, r'\EFI\Microsoft\Boot\bootmgfw.efi', 'Existing OS')
        (Path(self.efivars.name) / 'Boot0001-8be4df61-93ca-11d2-aa0d-00e098032b8c').write_bytes(
            b'\x01\x00\x00\x00' + option
        )
        real_path = Path

        def fake_path(value, *args, **kwargs):
            if str(value) == '/sys/firmware/efi/efivars':
                return real_path(self.efivars.name)
            return real_path(value, *args, **kwargs)

        self.efivars_patch = patch.object(installer, 'Path', side_effect=fake_path)
        self.efivars_patch.start()
        self.addCleanup(self.efivars_patch.stop)
        self.identity = installer.require_disk(str(self.disk))
        self.snapshot = installer.alongside_snapshot(self.value.disk)
        self.value.free_region = self.snapshot['regions'][0]

    def assert_old_data(self):
        self.assertEqual(digest_range(self.disk, 0, 446), self.mbr_hash)
        current = table(self.disk)['partitions']
        for original, expected in zip(self.original, self.hashes, strict=False):
            self.assertIn(original, current)
            self.assertEqual(digest_range(self.disk, original['start'] * 512, original['size'] * 512), expected)

    def test_adds_only_two_partitions_and_preserves_all_old_bytes_and_metadata(self):
        result = installer.create_alongside_partitions(self.value, self.snapshot, self.identity, lambda: None)
        self.assertEqual(result, (str(self.disk) + '2', str(self.disk) + '3'))
        current = table(self.disk)['partitions']
        self.assertEqual(len(current), 4)
        esp = next(p for p in current if p['node'] == result[0])
        root = next(p for p in current if p['node'] == result[1])
        self.assertEqual(esp['size'] * 512, 4 * installer.GIB)
        self.assertEqual(root['start'], esp['start'] + esp['size'])
        self.assertEqual(root['start'] + root['size'] - 1, self.value.free_region[1])
        self.assert_old_data()

    def test_windows_disk_signature_with_zero_bootstrap_is_preserved(self):
        with self.disk.open('r+b') as stream:
            stream.write(bytes(440) + struct.pack('<I', 0x6086B967) + bytes(2))
        self.mbr_hash = digest_range(self.disk, 0, 446)
        snapshot = installer.alongside_snapshot(self.value.disk)
        installer.create_alongside_partitions(self.value, snapshot, self.identity, lambda: None)
        with self.disk.open('rb') as stream:
            stream.seek(440)
            self.assertEqual(stream.read(4), struct.pack('<I', 0x6086B967))
        self.assertTrue(all(part.get('attrs') for part in self.original))
        self.assert_old_data()

    def test_table_or_free_extent_change_aborts_without_any_further_disk_writes(self):
        for change in ('--change-name=4:changed', '--new=7:0:+1M'):
            with self.subTest(change=change):
                subprocess.run(['sgdisk', change, str(self.disk)], check=True, stdout=subprocess.DEVNULL)
                before = table(self.disk)
                with self.assertRaisesRegex(ValueError, 'changed'):
                    installer.create_alongside_partitions(self.value, self.snapshot, self.identity, lambda: None)
                self.assertEqual(table(self.disk), before)
                self.assert_old_data_except_name()

    def assert_old_data_except_name(self):
        for original, expected in zip(self.original, self.hashes, strict=False):
            self.assertEqual(digest_range(self.disk, original['start'] * 512, original['size'] * 512), expected)

    def test_identity_or_firmware_change_aborts(self):
        wrong = (99, 2, self.identity[2], None, None, 'vda')
        with self.assertRaisesRegex(ValueError, 'identity'):
            installer.create_alongside_partitions(self.value, self.snapshot, wrong, lambda: None)
        # Rewrite the firmware file so it no longer matches the snapshot.
        changed = self.firmware.replace('BootOrder: 0001,0000', 'BootOrder: 0000,0001')
        self.firmware_file.write_text(changed)
        with self.assertRaisesRegex(ValueError, 'changed'):
            installer.create_alongside_partitions(self.value, self.snapshot, self.identity, lambda: None)
        self.assert_old_data()

    def test_secondary_disk_booting_elsewhere_is_refused_for_its_missing_firmware_entry(self):
        # Issue #99's layout: free space and slots are fine, the kept OS boots from another disk.
        before = digest_range(self.disk, 0, 1024 * 1024)
        for child in Path(self.efivars.name).iterdir():
            child.unlink()
        with self.assertRaisesRegex(ValueError, 'firmware entry') as refused:
            installer.alongside_snapshot(self.value.disk)
        self.assertNotIn('32 GiB', str(refused.exception))
        self.assertEqual(digest_range(self.disk, 0, 1024 * 1024), before)
        self.assert_old_data()

    def test_blank_disk_is_refused_in_plain_words(self):
        subprocess.run(['sgdisk', '--zap-all', str(self.disk)], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, 'no partition table'):
            installer.alongside_snapshot(self.value.disk)

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
        import shutil as _shutil

        real_sgdisk = Path(_shutil.which('sgdisk')).resolve()
        fake_bin = Path(self.fake_dir.name) / 'bin'
        (fake_bin / 'sgdisk').unlink()
        (fake_bin / 'sgdisk').write_text(
            '#!/bin/sh\n'
            'for arg in "$@"; do if [ "$arg" = "--verify" ]; then printf "No problems found.\n"; exit 0; fi; done\n'
            f'exec {real_sgdisk} "$@"\n'
        )
        (fake_bin / 'sgdisk').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            installer.alongside_snapshot(self.value.disk)
        self.assertEqual(digest_range(self.disk, self.disk.stat().st_size - 512, 512), before)
        self.assert_old_data()

    def test_post_creation_gpt_corruption_is_rejected(self):
        import shutil as _shutil

        real_sfdisk = Path(_shutil.which('sfdisk')).resolve()
        fake_bin = Path(self.fake_dir.name) / 'bin'
        helper = Path(self.fake_dir.name) / 'corrupt_gpt.py'
        helper.write_text(
            'import pathlib\n'
            f"p = pathlib.Path(r'{self.disk}')\n"
            "with p.open('r+b') as f:\n"
            '    f.seek(-512 + 16, 2)\n'
            '    b = f.read(1)\n'
            '    f.seek(-1, 1)\n'
            '    f.write(bytes([b[0] ^ 1]))\n'
        )
        (fake_bin / 'sfdisk').unlink()
        (fake_bin / 'sfdisk').write_text(
            '#!/bin/sh\n'
            f'"{real_sfdisk}" "$@"\n'
            'status=$?\n'
            f'for arg in "$@"; do if [ "$arg" = "--append" ]; then python3 "{helper}"; fi; done\n'
            'exit $status\n'
        )
        (fake_bin / 'sfdisk').chmod(0o755)
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            installer.create_alongside_partitions(self.value, self.snapshot, self.identity, lambda: None)
        self.assert_old_data()

    def test_post_creation_bootstrap_change_is_rejected(self):
        import shutil as _shutil

        real_sfdisk = Path(_shutil.which('sfdisk')).resolve()
        fake_bin = Path(self.fake_dir.name) / 'bin'
        helper = Path(self.fake_dir.name) / 'corrupt_boot.py'
        helper.write_text(
            'import pathlib\n'
            f"p = pathlib.Path(r'{self.disk}')\n"
            "with p.open('r+b') as f:\n"
            '    f.seek(0)\n'
            "    f.write(b'X')\n"
        )
        (fake_bin / 'sfdisk').unlink()
        (fake_bin / 'sfdisk').write_text(
            '#!/bin/sh\n'
            f'"{real_sfdisk}" "$@"\n'
            'status=$?\n'
            f'for arg in "$@"; do if [ "$arg" = "--append" ]; then python3 "{helper}"; fi; done\n'
            'exit $status\n'
        )
        (fake_bin / 'sfdisk').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'MBR bootstrap changed'):
            installer.create_alongside_partitions(self.value, self.snapshot, self.identity, lambda: None)
        self.assert_old_data_except_name()

    def test_old_raw_gpt_entry_padding_is_preserved_even_when_json_would_hide_a_change(self):
        import shutil as _shutil

        real_sfdisk = Path(_shutil.which('sfdisk')).resolve()
        fake_bin = Path(self.fake_dir.name) / 'bin'
        helper = Path(self.fake_dir.name) / 'corrupt_entries.py'
        helper.write_text(
            'import pathlib, struct, zlib\n'
            f"p = pathlib.Path(r'{self.disk}')\n"
            "with p.open('r+b') as stream:\n"
            '    stream.seek(512)\n'
            '    primary_header = stream.read(512)\n'
            "    backup_lba = struct.unpack_from('<Q', primary_header, 32)[0]\n"
            '    for lba in (1, backup_lba):\n'
            '        stream.seek(lba * 512)\n'
            '        header = bytearray(stream.read(512))\n'
            "        array_lba = struct.unpack_from('<Q', header, 72)[0]\n"
            '        stream.seek(array_lba * 512)\n'
            '        entries = bytearray(stream.read(128 * 128))\n'
            '        entries[127] = 0x42\n'
            "        struct.pack_into('<I', header, 88, zlib.crc32(entries))\n"
            "        struct.pack_into('<I', header, 16, 0)\n"
            "        size = struct.unpack_from('<I', header, 12)[0]\n"
            "        struct.pack_into('<I', header, 16, zlib.crc32(header[:size]))\n"
            '        stream.seek(array_lba * 512)\n'
            '        stream.write(entries)\n'
            '        stream.seek(lba * 512)\n'
            '        stream.write(header)\n'
        )
        (fake_bin / 'sfdisk').unlink()
        (fake_bin / 'sfdisk').write_text(
            '#!/bin/sh\n'
            f'"{real_sfdisk}" "$@"\n'
            'status=$?\n'
            f'for arg in "$@"; do if [ "$arg" = "--append" ]; then python3 "{helper}"; fi; done\n'
            'exit $status\n'
        )
        (fake_bin / 'sfdisk').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'Original GPT entries'):
            installer.create_alongside_partitions(self.value, self.snapshot, self.identity, lambda: None)

    def test_selected_extent_must_match_exactly(self):
        self.value.free_region[0] += 2048
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            installer.alongside_plan(self.value, copy.deepcopy(self.snapshot) | {'regions': [[1, 2]]})
        self.assert_old_data()


class AlongsidePlanningTests(unittest.TestCase):
    def test_fragmented_space_does_not_get_combined(self):
        table_value = dict(
            label='gpt',
            unit='sectors',
            sectorsize=512,
            firstlba=34,
            lastlba=100 * installer.GIB // 512,
            partitions=[
                dict(start=20 * installer.GIB // 512, size=1),
                dict(start=40 * installer.GIB // 512, size=1),
                dict(start=60 * installer.GIB // 512, size=1),
                dict(start=80 * installer.GIB // 512, size=1),
            ],
        )
        self.assertEqual(installer.free_regions(table_value), [])

    def test_refusal_names_the_one_missing_requirement(self):
        gib = installer.GIB // 512
        fragmented = dict(
            label='gpt',
            unit='sectors',
            sectorsize=512,
            firstlba=34,
            lastlba=100 * gib,
            partitions=[dict(start=20 * gib, size=1), dict(start=40 * gib, size=60 * gib - 1)],
        )
        roomy = dict(fragmented, partitions=[dict(start=2048, size=10 * gib)])
        full = dict(roomy, partitions=[dict(start=2048 + n, size=1) for n in range(128)])
        boots = {'0001': {}}
        cases = {
            'space': (fragmented, boots),
            'table': (full, boots),
            'firmware': (roomy, {}),
        }
        refusals = {}
        for check, (value, value_boots) in cases.items():
            with self.subTest(check=check):
                refusal = installer.alongside_refusal(value, installer.free_regions(value), value_boots)
                self.assertIsNotNone(refusal)
                self.assertIn('Erase disk', refusal)
                refusals[check] = refusal
        # Each failing check gets its own explanation, and only that one.
        self.assertEqual(len(set(refusals.values())), len(cases))
        self.assertIn('32 GiB', refusals['space'])
        self.assertNotIn('firmware', refusals['space'])
        self.assertNotIn('32 GiB', refusals['firmware'])
        self.assertIn('firmware entry', refusals['firmware'])
        self.assertIsNone(installer.alongside_refusal(roomy, installer.free_regions(roomy), boots))

    def test_rejects_mbr_overlaps_and_unsupported_sector_sizes(self):
        for changes in (
            dict(label='dos'),
            dict(sectorsize=1024),
            dict(partitions=[dict(start=2048, size=5000), dict(start=3000, size=100)]),
        ):
            value = dict(label='gpt', unit='sectors', sectorsize=512, firstlba=34, lastlba=100000000, partitions=[])
            value.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                installer.free_regions(value)

    def test_settings_require_explicit_region_and_boot_selection(self):
        for changes in (
            {},
            {'free_region': [2048, 100000000]},
            {'free_region': [True, 100000000], 'existing_boot': '0001'},
            {'free_region': [2048, 100000000], 'existing_boot': '../1'},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                settings(mode='alongside', **changes).validate()
        settings(mode='alongside', free_region=[2048, 100000000], existing_boot='0001').validate()


class AlongsideBootTests(unittest.TestCase):
    def test_decodes_real_firmware_device_path_without_parsing_efibootmgr_display(self):
        guid = uuid.UUID('82e17562-9f24-4564-9735-bb223836c0b4')
        hd = struct.pack('<BBHIQQ', 4, 1, 42, 15, 8192, 253952) + guid.bytes_le + b'\2\2'
        path = r'\EFI\debian\shimx64.efi'
        encoded = (path + '\0').encode('utf-16-le')
        nodes = hd + struct.pack('<BBH', 4, 4, len(encoded) + 4) + encoded + b'\x7f\xff\4\0'
        option = struct.pack('<IH', 1, len(nodes)) + 'Debian\0'.encode('utf-16-le') + nodes + b'optional-data'
        self.assertEqual(installer.decode_efi_boot_path(option), (15, str(guid), path))
        with self.assertRaises(ValueError):
            installer.decode_efi_boot_path(option[:20])

    def test_detected_bitlocker_is_rejected_before_disk_mutation(self):
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            bitlocker = dict(
                name='/dev/vda',
                type='disk',
                size=64 * 1024**3,
                ro=False,
                mountpoints=[None],
                children=[dict(name='/dev/vda3', type='part', size=1024**3, fstype='BitLocker', mountpoints=[None])],
            )
            import json as _json

            (case_bin / 'lsblk').write_text('#!/bin/sh\ncat "' + str(Path(bindir) / 'lsblk.json') + '"\n')
            (case_bin / 'lsblk').chmod(0o755)
            Path(bindir, 'lsblk.json').write_text(_json.dumps({'blockdevices': [bitlocker]}))
            marker = Path(bindir) / 'touched'
            (case_bin / 'sgdisk').write_text(f'#!/bin/sh\n: > "{marker}"\nexit 0\n')
            (case_bin / 'sgdisk').chmod(0o755)
            with patch.dict(os.environ, {'PATH': bin_path(case_bin)}):
                with self.assertRaisesRegex(ValueError, 'BitLocker'):
                    installer.reject_unsupported_existing_storage(settings(), {'table': {'partitions': []}})
            self.assertFalse(os.path.exists(marker))

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

    def test_erase_replaced_identity_refuses_before_start_or_commands(self):
        started = []
        with tempfile.TemporaryDirectory() as bindir:
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            import json as _json

            target_disk = dict(
                name='/dev/vda', type='disk', size=64 * 1024**3, ro=False, mountpoints=[None], children=[]
            )
            (case_bin / 'lsblk').write_text('#!/bin/sh\ncat "' + str(Path(bindir) / 'lsblk.json') + '"\n')
            (case_bin / 'lsblk').chmod(0o755)
            Path(bindir, 'lsblk.json').write_text(_json.dumps({'blockdevices': [target_disk]}))
            marker = Path(bindir) / 'touched'
            (case_bin / 'sgdisk').write_text(f'#!/bin/sh\n: > "{marker}"\nexit 0\n')
            (case_bin / 'sgdisk').chmod(0o755)
            real_stat = __import__('os').stat

            def fake_stat(path, *args, **kwargs):
                if str(path) == '/dev/vda':
                    return type('Stat', (), {'st_rdev': 99, 'st_ino': 2})()
                return real_stat(path, *args, **kwargs)

            with (
                patch.dict(os.environ, {'PATH': bin_path(case_bin)}),
                patch('os.stat', side_effect=fake_stat),
            ):
                with self.assertRaisesRegex(ValueError, 'Disk identity changed'):
                    installer.create_partitions(settings(), None, (1, 2, 3), lambda: started.append(True))
            self.assertEqual(started, [])
            self.assertFalse(os.path.exists(marker))

    def test_erase_mode_verifies_kernel_geometry_against_created_table(self):
        table = {
            'sectorsize': 512,
            'partitions': [
                {'node': '/dev/vda1', 'start': 2048, 'size': 8388608},
                {'node': '/dev/vda2', 'start': 8390656, 'size': 85983232},
            ],
        }
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as bindir:
            sysfs = Path(directory)
            for part in table['partitions']:
                node = Path(part['node']).name
                (sysfs / node).mkdir()
                (sysfs / node / 'start').write_text(str(part['start']))
                (sysfs / node / 'size').write_text(str(part['size']))
            real_path = Path

            def path(value, *args):
                return sysfs if value == '/sys/class/block' else real_path(value, *args)

            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            import json as _json

            target_disk = dict(
                name='/dev/vda', type='disk', size=64 * 1024**3, ro=False, mountpoints=[None], children=[]
            )
            (case_bin / 'lsblk').write_text('#!/bin/sh\ncat "' + str(Path(bindir) / 'lsblk.json') + '"\n')
            (case_bin / 'lsblk').chmod(0o755)
            Path(bindir, 'lsblk.json').write_text(_json.dumps({'blockdevices': [target_disk]}))
            (case_bin / 'sfdisk').write_text(
                '#!/bin/sh\nif [ "$1" = "--json" ]; then cat "'
                + str(Path(bindir) / 'table.json')
                + '"; exit 0; fi\nexit 0\n'
            )
            (case_bin / 'sfdisk').chmod(0o755)
            Path(bindir, 'table.json').write_text(_json.dumps({'partitiontable': table}))
            for tool in ('wipefs', 'sgdisk', 'udevadm'):
                (case_bin / tool).write_text('#!/bin/sh\nexit 0\n')
                (case_bin / tool).chmod(0o755)
            real_stat = __import__('os').stat

            def fake_stat(path_arg, *args, **kwargs):
                if str(path_arg) == '/dev/vda':
                    return type('Stat', (), {'st_rdev': 1, 'st_ino': 2})()
                return real_stat(path_arg, *args, **kwargs)

            with (
                patch.dict(os.environ, {'PATH': bin_path(case_bin)}),
                patch('os.stat', side_effect=fake_stat),
                patch.object(installer, 'Path', side_effect=path),
            ):
                identity = installer.require_disk('/dev/vda')
                self.assertEqual(
                    installer.create_partitions(settings(), None, identity, lambda: None),
                    ('/dev/vda1', '/dev/vda2'),
                )
            (sysfs / 'vda2/size').write_text(str(table['partitions'][1]['size'] + 2))
            with (
                patch.dict(os.environ, {'PATH': bin_path(case_bin)}),
                patch('os.stat', side_effect=fake_stat),
                patch.object(installer, 'Path', side_effect=path),
                self.assertRaisesRegex(ValueError, 'Kernel partition geometry'),
            ):
                identity = installer.require_disk('/dev/vda')
                installer.create_partitions(settings(), None, identity, lambda: None)


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
        self.after = (
            self.before
            + '\nBoot0002* Arch Niri Dux\tHD(2,GPT,new,0x10000,0x800000)/File(\\EFI\\limine\\limine_x64.efi)'
        )
        self.final = self.after.replace('BootOrder: 0001,0000', 'BootOrder: 0002,0001,0000')
        self.snapshot = dict(
            entries=installer.firmware_entries(self.before),
            order='0001,0000',
            boots={'0001': dict(guid='original-esp-guid', path='/EFI/Microsoft/Boot/bootmgfw.efi')},
        )
        self.settings = settings(existing_boot='0001', mode='alongside')

    def finish(self, outputs, efi_path=None):
        # Fake blkid/efibootmgr on PATH; real firmware_boot_path reads temp efivars.
        # All efibootmgr invocations append to a log file for outcome (not argv) checks.
        bindir = tempfile.TemporaryDirectory()
        self.addCleanup(bindir.cleanup)
        case_bin = Path(bindir.name) / 'bin'
        case_bin.mkdir()
        log = Path(bindir.name) / 'efibootmgr.log'
        log.write_text('')
        self.efi_log = log
        for index, text in enumerate(outputs):
            (Path(bindir.name) / f'verbose-{index}').write_text(text)
        (Path(bindir.name) / 'count').write_text('0')
        (case_bin / 'blkid').write_text(f'#!/bin/sh\nprintf "{self.guid}"\n')
        (case_bin / 'blkid').chmod(0o755)
        (case_bin / 'efibootmgr').write_text(
            '#!/bin/sh\n'
            f'printf "%s\\n" "$*" >> "{log}"\n'
            'if [ "$1" = "--verbose" ]; then\n'
            f'  i=$(cat "{bindir.name}/count"); printf "%s" "$((i + 1))" > "{bindir.name}/count"; cat "{bindir.name}/verbose-$i"\n'
            'fi\n'
            'exit 0\n'
        )
        (case_bin / 'efibootmgr').chmod(0o755)
        # Temp efivars for the new entry.
        efivars = tempfile.TemporaryDirectory()
        self.addCleanup(efivars.cleanup)
        part, guid_str, loader = efi_path or (2, self.guid, r'\EFI\limine\limine_x64.efi')
        entry_guid = uuid.UUID(guid_str)
        option = efi_option_bytes(part, entry_guid, loader, 'Arch Niri Dux')
        (Path(efivars.name) / 'Boot0002-8be4df61-93ca-11d2-aa0d-00e098032b8c').write_bytes(b'\x01\x00\x00\x00' + option)
        real_path = Path

        def fake_path(value, *args, **kwargs):
            if str(value) == '/sys/firmware/efi/efivars':
                return real_path(efivars.name)
            return real_path(value, *args, **kwargs)

        with (
            patch.dict(os.environ, {'PATH': bin_path(case_bin)}),
            patch.object(installer, 'Path', side_effect=fake_path),
        ):
            installer.finish_alongside_boot(self.settings, self.snapshot, 2, self.target)

    def test_retains_original_entries_and_chainloads_the_original_esp(self):
        self.finish([self.before, self.after, self.final])
        self.assertIn('guid(original-esp-guid):/EFI/Microsoft/Boot/bootmgfw.efi', self.config.read_text())
        logged = self.efi_log.read_text()
        self.assertIn('--create-only', logged)
        self.assertIn(r'\EFI\limine\limine_x64.efi', logged)
        self.assertIn('--bootorder', logged)
        self.assertIn('0002,0001,0000', logged)
        self.assertNotIn('--delete-bootnum', logged)

    def test_missing_new_binary_never_changes_firmware_or_menu(self):
        self.binary.unlink()
        with self.assertRaisesRegex(ValueError, 'missing its Limine EFI binary'):
            self.finish([])
        self.assertEqual(self.efi_log.read_text(), '')
        self.assertEqual(self.config.read_text(), self.original_config)

    def test_inactive_new_entry_is_never_made_default(self):
        with self.assertRaisesRegex(ValueError, 'inactive'):
            self.finish([self.before, self.after.replace('Boot0002*', 'Boot0002 ')])
        self.assertNotIn('--bootorder', self.efi_log.read_text())

    def test_wrong_new_partition_guid_or_path_is_never_made_default(self):
        for efi_path in (
            (3, self.guid, r'\EFI\limine\limine_x64.efi'),
            (2, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', r'\EFI\limine\limine_x64.efi'),
            (2, self.guid, r'\EFI\other\wrong.efi'),
        ):
            with self.subTest(path=efi_path), self.assertRaisesRegex(ValueError, 'wrong ESP identity or loader path'):
                self.finish([self.before, self.after], efi_path=efi_path)
        self.assertNotIn('--bootorder', self.efi_log.read_text())

    def test_changed_firmware_stops_before_appending_chainload_config(self):
        for changed in (
            self.before.replace('Windows', 'Changed'),
            self.before.replace('BootOrder: 0001,0000', 'BootOrder: 0001,0000,0009'),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'Firmware entries changed'):
                self.finish([changed])
            self.assertEqual(self.config.read_text(), self.original_config)
        self.assertNotIn('--create-only', self.efi_log.read_text())


if __name__ == '__main__':
    unittest.main()
