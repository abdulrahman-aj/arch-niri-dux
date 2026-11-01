import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'installer/install.py'
spec = importlib.util.spec_from_file_location('installer', MODULE_PATH)
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


def settings(**changes):
    values = dict(disk='/dev/vda', username='tester', hostname='dux',
                  timezone='UTC', keymap='us', passphrase='test-passphrase',
                  password='test-password')
    values.update(changes)
    return installer.Settings(**values)


def disk(**changes):
    values = dict(name='/dev/vda', type='disk', size=64 * 1024**3,
                  ro=False, mountpoints=[None], children=[])
    values.update(changes)
    return values


class InstallerSafetyTests(unittest.TestCase):
    def test_malformed_automation_config_stops_cleanly_without_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'invalid.json'
            config.write_text(json.dumps({'unexpected': 'field'}))
            config.chmod(0o600)
            with patch.object(sys, 'argv', ['install', '--config', str(config), '--confirm-disk', '/dev/vda']), \
                    patch.object(installer.os, 'geteuid', return_value=0), \
                    patch.object(Path, 'exists', return_value=True), \
                    patch.object(Path, 'glob', return_value=[]), \
                    patch.object(installer, 'install') as install:
                self.assertEqual(installer.main(), 1)
                install.assert_not_called()

    def test_accepts_unused_disk(self):
        self.assertIsNone(installer.disk_rejection(disk(), set()))

    def test_refuses_live_usb_with_mounted_descendant(self):
        device = disk(children=[dict(name='/dev/vda1', type='part', mountpoints=['/run/archiso/bootmnt'])])
        self.assertIn('mounted', installer.disk_rejection(device, set()))

    def test_refuses_active_swap(self):
        device = disk(children=[dict(name='/dev/vda2', type='part', mountpoints=[None])])
        self.assertIn('swap', installer.disk_rejection(device, {'/dev/vda2'}))

    def test_refuses_readonly_small_and_partition_targets(self):
        for device in (disk(ro=True), disk(size=1024**3), disk(type='part')):
            with self.subTest(device=device):
                self.assertIsNotNone(installer.disk_rejection(device, set()))

    def test_input_validation_blocks_unsafe_identity_values(self):
        for change in ({'username': 'root'}, {'username': '../root'},
                       {'hostname': 'name\ncommand'}, {'timezone': '../../etc/passwd'},
                       {'keymap': 'us\nHOOKS=()'}, {'disk': '/dev/vda1'},
                       {'disk': '/dev/loop0'}, {'password': 'short'},
                       {'passphrase': 'linebreak\nsecret'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                settings(**change).validate()

    def test_valid_inputs_and_passwords_are_not_in_repr(self):
        value = settings()
        value.validate()
        self.assertNotIn(value.password, repr(value))
        self.assertNotIn(value.passphrase, repr(value))

    def test_partition_names_cover_nvme_sata_and_vm(self):
        for drive, expected in (('/dev/nvme0n1', '/dev/nvme0n1p2'),
                                ('/dev/mmcblk0', '/dev/mmcblk0p2'),
                                ('/dev/sda', '/dev/sda2'), ('/dev/vda', '/dev/vda2')):
            self.assertEqual(installer.partition_name(drive, 2), expected)

    def test_fstab_uses_subvolume_paths_so_root_can_be_replaced(self):
        original = '# root\nUUID=abc / btrfs rw,subvolid=256,subvol=/@ 0 0\nUUID=abc /home btrfs rw,subvolid=257,subvol=/@home 0 0\n'
        result = installer.recovery_fstab(original)
        self.assertNotIn('subvolid=', result)
        self.assertIn('subvol=/@\t0\t0', result)
        self.assertIn('subvol=/@home', result)
        self.assertTrue(result.startswith('# root\n'))

    def test_disk_swap_between_confirmation_and_install_prevents_all_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\nlinux\n')
            (root / 'offline.db').touch()
            with patch.object(installer, 'SHARE', root), patch.object(installer, 'REPO', root), \
                    patch.object(installer, 'TARGET', root / 'target'), \
                    patch.object(installer, 'require_disk', side_effect=[1, 2]), \
                    patch.object(installer, 'verify_repository'), \
                    patch.object(installer.shutil, 'which', return_value='/usr/bin/tool'), \
                    patch.object(installer, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    installer.install(settings())
                run.assert_not_called()

    def test_bad_payload_package_name_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n--root=/\n')
            with patch.object(installer, 'SHARE', root), \
                    patch.object(installer, 'require_disk', return_value=1), \
                    patch.object(installer, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'package list'):
                    installer.install(settings())
                run.assert_not_called()

    def test_disk_replacement_after_wizard_confirmation_is_rejected(self):
        value = settings()
        value.confirmed_device = (1, 2, 64 * 1024**3)
        with patch.object(installer, 'require_disk', return_value=(1, 3, 64 * 1024**3)), \
                patch.object(installer, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'confirmed disk was replaced'):
                installer.install(value)
            run.assert_not_called()

    def test_typing_only_yes_does_not_confirm_erase(self):
        answers = ['/dev/vda', 'tester', 'dux', 'UTC', 'us', 'yes']
        with patch.object(installer, 'eligible_disks', return_value=[disk()]), \
                patch.object(installer, 'require_disk', return_value=1), \
                patch.object(installer, 'secret', return_value='test-password'), \
                patch('builtins.input', side_effect=answers), \
                patch.object(installer, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Cancelled'):
                installer.wizard()
            run.assert_called_once_with('loadkeys', 'us')

    def test_corrupted_offline_package_is_rejected_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'base-1.pkg.tar.zst'
            package.write_bytes(b'original package')
            (root / 'offline.db').write_bytes(b'repository database')
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            database_hash = hashlib.sha256((root / 'offline.db').read_bytes()).hexdigest()
            (root / 'SHA256SUMS').write_text(f'{checksum}  {package.name}\n{database_hash}  offline.db\n')
            installer.verify_repository(root)
            package.write_bytes(b'corrupted package')
            with self.assertRaisesRegex(ValueError, 'checksum failed'):
                installer.verify_repository(root)

    def test_offline_manifest_must_cover_every_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'base.pkg.tar.zst').touch()
            (root / 'offline.db').touch()
            (root / 'SHA256SUMS').write_text('')
            with self.assertRaisesRegex(ValueError, 'every package'):
                installer.verify_repository(root)


if __name__ == '__main__':
    unittest.main()
