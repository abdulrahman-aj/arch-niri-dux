import hashlib
import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from installer import install as installer
from installer import storage


def settings(**changes):
    values = dict(
        disk='/dev/vda',
        username='tester',
        hostname='dux',
        timezone='UTC',
        keymap='us',
        passphrase='test-passphrase',
        password='test-password',
    )
    values.update(changes)
    return installer.Settings(**values)


def disk(**changes):
    values = dict(name='/dev/vda', type='disk', size=64 * 1024**3, ro=False, mountpoints=[None], children=[])
    values.update(changes)
    return values


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
                output = io.StringIO()
                with (
                    self.subTest(uefi=uefi, secure_boot=secure_boot),
                    patch.object(sys, 'argv', ['arch-niri-install']),
                    patch.object(installer.os, 'geteuid', return_value=0),
                    patch.object(Path, 'exists', return_value=uefi),
                    patch.object(Path, 'glob', return_value=[variable]),
                    patch.object(installer, 'wizard', return_value=settings()) as wizard,
                    patch.object(installer, 'install') as installation,
                    patch.object(installer, 'run') as commands,
                    patch('sys.stderr', output),
                ):
                    if message is None:
                        self.assertEqual(installer.main(), 0)
                        wizard.assert_called_once()
                        installation.assert_called_once_with(wizard.return_value, wizard.call_args.args[0])
                    else:
                        with self.assertRaises(SystemExit) as stopped:
                            installer.main()
                        self.assertEqual(stopped.exception.code, 2)
                        self.assertIn(message, output.getvalue())
                        wizard.assert_not_called()
                        installation.assert_not_called()
                    commands.assert_not_called()

    def test_malformed_automation_config_stops_cleanly_without_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'invalid.json'
            config.write_text(json.dumps({'unexpected': 'field'}))
            config.chmod(0o600)
            with (
                patch.object(sys, 'argv', ['install', '--config', str(config), '--confirm-disk', '/dev/vda']),
                patch.object(installer.os, 'geteuid', return_value=0),
                patch.object(Path, 'exists', return_value=True),
                patch.object(Path, 'glob', return_value=[]),
                patch.object(installer, 'install') as install,
            ):
                self.assertEqual(installer.main(), 1)
                install.assert_not_called()

    def test_disk_labels_distinguish_identical_models(self):
        first = disk(model='Same model', serial='SERIAL-A')
        second = disk(model='Same model', serial='SERIAL-B')
        self.assertNotEqual(installer.disk_label(first), installer.disk_label(second))
        self.assertIn('SERIAL-A', installer.disk_label(first))
        self.assertIn('WWN-A', installer.disk_label(disk(wwn='WWN-A')))

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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'iso-build-date').write_text('9999999999\n')
            with (
                patch.object(installer, 'SHARE', root),
                patch.object(installer, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.object(storage, 'require_disk', return_value=1),
                patch.object(installer, 'run') as run,
            ):
                with self.assertRaisesRegex(ValueError, 'System clock'):
                    installer.install(settings())
                run.assert_not_called()

    def test_missing_iso_build_date_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            with (
                patch.object(installer, 'SHARE', root),
                patch.object(installer, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.object(storage, 'require_disk', return_value=1),
                patch.object(installer, 'run') as run,
            ):
                with self.assertRaisesRegex(ValueError, 'build date'):
                    installer.install(settings())
                run.assert_not_called()

    def test_signature_preflight_failure_prevents_partitioning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'offline.db').touch()
            (root / 'base-1.pkg.tar.zst').touch()
            (root / 'iso-build-date').write_text('1\n')
            calls = []

            def fake_run(*args, **_kwargs):
                calls.append(args)
                if '--downloadonly' in args:
                    raise installer.subprocess.CalledProcessError(1, args)
                if '--print' in args:
                    return 'base-1.pkg.tar.zst\n'
                raise AssertionError(f'preflight must stop before partitioning: {args}')

            state = installer.InstallationState()
            real_staging = tempfile.TemporaryDirectory
            with (
                patch.object(installer, 'SHARE', root),
                patch.object(installer, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.object(storage, 'require_disk', return_value=(1, 2, 64 * 1024**3, None, None, 'vda')),
                patch.object(installer, 'verify_repository'),
                patch.object(installer.shutil, 'which', return_value='/usr/bin/tool'),
                patch.object(installer, 'run', side_effect=fake_run),
                patch.object(installer.tempfile, 'TemporaryDirectory', lambda *a, **k: real_staging(dir=root)),
                patch.object(storage, 'create_partitions') as partitions,
            ):
                with self.assertRaisesRegex(ValueError, 'signature preflight'):
                    installer.install(settings(), state)
                partitions.assert_not_called()
                self.assertFalse(state.installation_started)
                self.assertTrue(any('--downloadonly' in args for args in calls))

    def test_signature_preflight_success_reaches_partitioning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            (root / 'offline.db').touch()
            (root / 'base-1.pkg.tar.zst').touch()
            (root / 'iso-build-date').write_text('1\n')
            calls = []

            def fake_run(*args, **_kwargs):
                calls.append(args)
                if '--print' in args:
                    return 'base-1.pkg.tar.zst\n'
                return None

            state = installer.InstallationState()
            real_staging = tempfile.TemporaryDirectory
            with (
                patch.object(installer, 'SHARE', root),
                patch.object(installer, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.object(storage, 'require_disk', return_value=(1, 2, 64 * 1024**3, None, None, 'vda')),
                patch.object(installer, 'verify_repository'),
                patch.object(installer.shutil, 'which', return_value='/usr/bin/tool'),
                patch.object(installer, 'run', side_effect=fake_run),
                patch.object(installer.tempfile, 'TemporaryDirectory', lambda *a, **k: real_staging(dir=root)),
                patch.object(storage, 'create_partitions', side_effect=ValueError('sentinel partitioning reached')),
            ):
                with self.assertRaisesRegex(ValueError, 'sentinel partitioning'):
                    installer.install(settings(), state)
                self.assertFalse(state.installation_started)
            preflight = [args for args in calls if '--downloadonly' in args]
            self.assertEqual(len(preflight), 1)
            for name in ('base', 'arch-niri-dux', 'arch-niri-dux-baseline'):
                self.assertIn(name, preflight[0])

    def test_disk_swap_between_confirmation_and_install_prevents_all_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\nlinux\n')
            (root / 'offline.db').touch()
            (root / 'iso-build-date').write_text('1\n')
            with (
                patch.object(installer, 'SHARE', root),
                patch.object(installer, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.object(storage, 'require_disk', side_effect=[1, 2]),
                patch.object(installer, 'verify_repository'),
                patch.object(installer.shutil, 'which', return_value='/usr/bin/tool'),
                patch.object(installer, 'run') as run,
            ):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    installer.install(settings())
                run.assert_not_called()

    def test_bad_payload_package_name_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n--root=/\n')
            with (
                patch.object(installer, 'SHARE', root),
                patch.object(storage, 'require_disk', return_value=1),
                patch.object(installer, 'run') as run,
            ):
                with self.assertRaisesRegex(ValueError, 'package list'):
                    installer.install(settings())
                run.assert_not_called()

    def test_missing_repository_database_prevents_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages.target').write_text('base\n')
            package = root / 'base.pkg.tar.zst'
            package.write_bytes(b'package')
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            (root / 'SHA256SUMS').write_text(f'{checksum}  {package.name}\n')
            (root / 'iso-build-date').write_text('1\n')
            with (
                patch.object(installer, 'SHARE', root),
                patch.object(installer, 'REPO', root),
                patch.object(installer, 'TARGET', root / 'target'),
                patch.object(storage, 'require_disk', return_value=1),
                patch.object(installer, 'run') as run,
            ):
                value = settings()
                state = installer.InstallationState()
                with self.assertRaisesRegex(ValueError, 'repository database'):
                    installer.install(value, state)
                run.assert_not_called()
                self.assertFalse(state.installation_started)

    def test_disk_replacement_after_wizard_confirmation_is_rejected(self):
        value = settings()
        state = installer.InstallationState(confirmed_device=(1, 2, 64 * 1024**3))
        with (
            patch.object(storage, 'require_disk', return_value=(1, 3, 64 * 1024**3)),
            patch.object(installer, 'run') as run,
        ):
            with self.assertRaisesRegex(ValueError, 'confirmed disk was replaced'):
                installer.install(value, state)
            run.assert_not_called()

    def test_picker_starts_with_cancel_and_escape_cancels(self):
        result = installer.subprocess.CompletedProcess([], 130, stdout='')
        with patch.object(installer.subprocess, 'run', return_value=result) as run:
            with self.assertRaises(installer.Cancelled):
                installer.choose('Confirm installation', ['Erase and install', 'Cancel'], default='Cancel')
            self.assertEqual(run.call_args.kwargs['input'], 'Cancel\nErase and install\n')

    def test_errors_distinguish_preflight_from_a_started_installation(self):
        for started in (False, True):
            value = settings(swap_gib=0)

            def fail_install(_settings, state):
                state.installation_started = started
                raise ValueError('fixture failure')

            output = io.StringIO()
            with (
                self.subTest(started=started),
                patch.object(sys, 'argv', ['install']),
                patch.object(installer.os, 'geteuid', return_value=0),
                patch.object(Path, 'exists', return_value=True),
                patch.object(Path, 'glob', return_value=[]),
                patch.object(installer, 'wizard', return_value=value),
                patch.object(installer, 'install', side_effect=fail_install),
                patch('sys.stderr', output),
            ):
                self.assertEqual(installer.main(), 1)
            self.assertEqual('No disk was changed.' in output.getvalue(), not started)
            self.assertEqual('may be incomplete' in output.getvalue(), started)

    def test_invalid_username_and_passwords_retry_inline(self):
        with patch('builtins.input', side_effect=['root', 'tester']):
            self.assertEqual(installer.prompt('Username', validate=installer.validate_username), 'tester')
        with patch.object(
            installer.getpass,
            'getpass',
            side_effect=['short', 'long-password', 'different-password', 'long-password', 'long-password'],
        ):
            self.assertEqual(installer.secret('Login password'), 'long-password')

    def test_swap_auto_custom_disabled_and_capacity(self):
        with patch.object(installer, 'memory_gib', return_value=16):
            self.assertEqual(installer.swap_size(None, 64 * installer.GIB), 18)
            self.assertEqual(installer.swap_size(8, 64 * installer.GIB), 8)
            self.assertEqual(installer.swap_size(0, 32 * installer.GIB), 0)
            with self.assertRaisesRegex(ValueError, 'will not fit'):
                installer.swap_size(None, 32 * installer.GIB)
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
