import importlib.util
import hashlib
import json
import io
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

    def test_cancel_at_final_summary_never_starts_installation(self):
        answers = ['tester', 'dux']
        with patch.object(installer, 'eligible_disks', return_value=[disk()]), \
                patch.object(installer, 'require_disk', return_value=1), \
                patch.object(installer, 'secret', return_value='test-password'), \
                patch.object(installer.shutil, 'which', return_value='/usr/bin/fzf'), \
                patch.object(installer, 'memory_gib', return_value=16), \
                patch.object(installer, 'choose', side_effect=[
                    '/dev/vda  64.0 GiB', 'Erase disk', 'us', 'UTC', 'No swap / no hibernation', 'Cancel']), \
                patch('builtins.input', side_effect=answers), \
                patch.object(installer, 'run', side_effect=['us\nuk', None, 'UTC\nAsia/Amman']) as run:
            with self.assertRaises(installer.Cancelled):
                installer.wizard()
            self.assertEqual(run.call_args_list[1].args, ('loadkeys', 'us'))
            self.assertFalse(any(call.args[0] in ('wipefs', 'sgdisk', 'cryptsetup') for call in run.call_args_list))

    def test_picker_starts_with_cancel_and_escape_cancels(self):
        result = installer.subprocess.CompletedProcess([], 130, stdout='')
        with patch.object(installer.subprocess, 'run', return_value=result) as run:
            with self.assertRaises(installer.Cancelled):
                installer.choose('Confirm installation', ['Erase and install', 'Cancel'], default='Cancel')
            self.assertEqual(run.call_args.kwargs['input'], 'Cancel\nErase and install\n')

    def test_errors_distinguish_preflight_from_a_started_installation(self):
        for started in (False, True):
            value = settings(swap_gib=0)

            def fail_install(_settings):
                _settings.installation_started = started
                raise ValueError('fixture failure')

            output = io.StringIO()
            with self.subTest(started=started), \
                    patch.object(sys, 'argv', ['install']), \
                    patch.object(installer.os, 'geteuid', return_value=0), \
                    patch.object(Path, 'exists', return_value=True), \
                    patch.object(Path, 'glob', return_value=[]), \
                    patch.object(installer, 'wizard', return_value=value), \
                    patch.object(installer, 'install', side_effect=fail_install), \
                    patch('sys.stderr', output):
                self.assertEqual(installer.main(), 1)
            self.assertEqual('No disk was changed.' in output.getvalue(), not started)
            self.assertEqual('may be incomplete' in output.getvalue(), started)

    def test_invalid_username_and_passwords_retry_inline(self):
        with patch('builtins.input', side_effect=['root', 'tester']):
            self.assertEqual(installer.prompt('Username', validate=installer.validate_username), 'tester')
        with patch.object(installer.getpass, 'getpass', side_effect=[
                'short', 'long-password', 'different-password', 'long-password', 'long-password']):
            self.assertEqual(installer.secret('Login password'), 'long-password')

    def test_swap_auto_custom_disabled_and_capacity(self):
        with patch.object(installer, 'memory_gib', return_value=16):
            self.assertEqual(installer.swap_size(settings(), 64 * installer.GIB), 18)
            self.assertEqual(installer.swap_size(settings(swap_gib=8), 64 * installer.GIB), 8)
            self.assertEqual(installer.swap_size(settings(swap_gib=0), 32 * installer.GIB), 0)
            with self.assertRaisesRegex(ValueError, 'will not fit'):
                installer.swap_size(settings(), 32 * installer.GIB)
        for value in (-1, 1.5, True, '18'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings(swap_gib=value).validate()

    def test_install_creates_isolated_swap_and_passes_physical_resume_offset(self):
        for size in (0, 8):
            with self.subTest(swap_gib=size), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / 'target'
                (target / 'root').mkdir(parents=True)
                (root / 'desktop').mkdir()
                (root / 'packages.target').write_text('base\n')
                (root / 'offline.db').touch()

                def command(*args, **kwargs):
                    if args[:3] == ('btrfs', 'inspect-internal', 'map-swapfile'):
                        return '12345'
                    if args[0] == 'genfstab':
                        return 'UUID=abc / btrfs rw,subvolid=256,subvol=/@ 0 0\n'
                    if args[0] == 'blkid':
                        return 'test-uuid'
                    return '' if kwargs.get('capture') else None

                value = settings(swap_gib=size)
                value.confirmed_at = 10
                output = io.StringIO()
                temporary_directory = tempfile.TemporaryDirectory
                with patch.object(installer, 'SHARE', root), patch.object(installer, 'REPO', root), \
                        patch.object(installer, 'TARGET', target), \
                        patch.object(installer, 'require_disk', return_value=(1, 2, 64 * installer.GIB)), \
                        patch.object(installer, 'verify_repository'), \
                        patch.object(installer.shutil, 'which', return_value='/usr/bin/tool'), \
                        patch.object(installer.os.path, 'ismount', return_value=False), \
                        patch.object(installer, 'run', side_effect=command) as run, \
                        patch.object(installer.tempfile, 'TemporaryDirectory',
                                     side_effect=lambda **kwargs: temporary_directory(dir=root)), \
                        patch.object(installer.subprocess, 'run'), \
                        patch.object(installer.time, 'monotonic', return_value=69), \
                        patch('sys.stdout', output):
                    installer.install(value)
                commands = [call.args for call in run.call_args_list]
                swap_created = ('btrfs', 'subvolume', 'create', str(target / '@swap'))
                swap_setup = [call for call in commands if call[:3] == ('btrfs', 'filesystem', 'mkswapfile')]
                configure = next(call for call in commands if '/root/configure-target.sh' in call)
                self.assertEqual(configure[-1], '12345' if size else '')
                self.assertEqual(swap_created in commands, bool(size))
                self.assertEqual(bool(swap_setup), bool(size))
                self.assertEqual('/swap/swapfile none swap defaults 0 0' in (target / 'etc/fstab').read_text(), bool(size))
                if size:
                    self.assertEqual(swap_setup[0][-2:], ('8G', str(target / 'swap/swapfile')))
                self.assertIn('took 59 seconds', output.getvalue())
                self.assertTrue(value.installation_started)

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
