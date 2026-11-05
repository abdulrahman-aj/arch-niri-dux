import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('integration_builder', ROOT / 'scripts/build-integration.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class IntegrationSourceTests(unittest.TestCase):
    def test_package_ships_runtime_defaults_and_one_license_without_build_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, payload = root / 'source', root / 'payload'
            shutil.copytree(ROOT / 'desktop', source / 'desktop')
            payload.mkdir()
            subprocess.run(['bash', '-eu', '-c',
                            'srcdir=$1; pkgdir=$2; source "$3"; pkgname=arch-niri-dux; '
                            'prepare; package_arch-niri-dux', 'package-test',
                            str(source), str(payload), str(ROOT / 'packages/integration/PKGBUILD')],
                           check=True)
            defaults = payload / 'usr/share/arch-niri-dux'
            self.assertTrue((defaults / 'niri/defaults.kdl').is_file())
            bindings = (defaults / 'niri/binds.kdl').read_text()
            self.assertIn('spawn "xdg-terminal-exec"', bindings)
            self.assertNotIn('{{TERMINAL_COMMAND}}', bindings)
            for name in ('colors', 'layout', 'alttab', 'input'):
                self.assertTrue((defaults / f'niri/dms/{name}.kdl').is_file())
            for name in ('niri/config.kdl', 'niri/dms/binds.kdl', 'DUX-LICENSE'):
                self.assertFalse((defaults / name).exists())
                original = ROOT / 'desktop/usr/share/arch-niri-dux' / name
                self.assertEqual((source / 'desktop/usr/share/arch-niri-dux' / name).read_bytes(),
                                 original.read_bytes())
            license_file = payload / 'usr/share/licenses/arch-niri-dux/DUX-LICENSE'
            self.assertEqual(license_file.read_bytes(),
                             (ROOT / 'desktop/usr/share/arch-niri-dux/DUX-LICENSE').read_bytes())

    def test_archive_is_reproducible_and_keeps_source_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages/integration').mkdir(parents=True)
            (root / 'desktop/usr/share').mkdir(parents=True)
            (root / 'packages/integration/PKGBUILD').write_text('pkgname=example\n')
            (root / 'packages/packages.target').write_text('base\nniri\n')
            asset = root / 'desktop/usr/share/default.kdl'
            asset.write_text('binds { Mod+Return { spawn "xdg-terminal-exec"; } }\n')
            one = root / 'one'
            two = root / 'two'
            one.mkdir()
            two.mkdir()
            first = builder.prepare_source(root, one, 1234)
            os.utime(asset, (9000, 9000))
            asset.chmod(0o755)
            second = builder.prepare_source(root, two, 1234)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                self.assertEqual(archive.extractfile('desktop/usr/share/default.kdl').read(), asset.read_bytes())
                self.assertEqual(archive.extractfile('packages.target').read(), b'base\nniri\n')
                self.assertTrue(all(entry.uid == entry.gid == 0 and entry.mtime == 1234 for entry in archive))

    def test_archive_refuses_linked_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages').mkdir()
            (root / 'packages/packages.target').write_text('base\n')
            (root / 'desktop').mkdir()
            (root / 'desktop/escape').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'source symlink'):
                builder.prepare_source(root, root, 1234)


@unittest.skipIf(os.geteuid() == 0, 'User initialization deliberately rejects root')
class ExistingUserTests(unittest.TestCase):
    def test_repeated_initialization_preserves_existing_files_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / '.config'
            state = home / '.local/state'
            data = home / '.local/share'
            files = [config / 'niri/dms' / f'{name}.kdl'
                     for name in ('colors', 'layout', 'alttab', 'input', 'binds')]
            files += [config / 'niri/config.kdl', config / 'xdg-terminals.list',
                      config / 'mimeapps.list', config / 'environment.d/90-dux.conf', state / 'dms/session.json']
            for index, file in enumerate(files):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(f'Personal file {index}\n')
                file.chmod(0o600)
            target = home / 'tracked-binds.kdl'
            target.write_text('Personal tracked shortcut\n')
            bindings = config / 'niri/dms/binds.kdl'
            bindings.unlink()
            bindings.symlink_to(target)
            manager = data / 'dbus-1/services/dev.duxlabs.files.FileManager1.service'
            manager.parent.mkdir(parents=True)
            manager.symlink_to('/missing/personal-manager.service')
            before = {file: (file.read_bytes(), file.stat().st_mode, file.stat().st_uid) for file in files}
            environment = os.environ | {'HOME': str(home), 'XDG_CONFIG_HOME': str(config),
                                        'XDG_STATE_HOME': str(state), 'XDG_DATA_HOME': str(data)}
            for _ in range(2):
                subprocess.run(['bash', str(ROOT / 'desktop/usr/lib/arch-niri-dux/initialize-user')],
                               env=environment, check=True)
            for file, value in before.items():
                self.assertEqual((file.read_bytes(), file.stat().st_mode, file.stat().st_uid), value)
            self.assertTrue(bindings.is_symlink())
            self.assertEqual(manager.readlink(), Path('/missing/personal-manager.service'))


if __name__ == '__main__':
    unittest.main()
