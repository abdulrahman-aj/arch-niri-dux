import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('build_lock', Path(__file__).with_name('build-lock.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

aur_spec = importlib.util.spec_from_file_location('build_dux_apps', Path(__file__).with_name('build-dux-apps.py'))
aur = importlib.util.module_from_spec(aur_spec)
aur_spec.loader.exec_module(aur)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.entry = dict(
            filename='base-1-any.pkg.tar.zst',
            sha256='a' * 64,
            url='https://example.org/base-1-any.pkg.tar.zst',
            signature='abc',
        )

    def test_invalid_fields_rejected(self):
        invalid = {
            'signature': '',
            'filename': '../base.pkg.tar.zst',
            'url': 'http://example.org/base',
            'sha256': 'invalid',
        }
        for field, value in invalid.items():
            # Each case rebuilds the valid entry so the mutations stay independent.
            with self.subTest(field=field):
                entry = self.entry.copy()
                entry[field] = value
                with self.assertRaises(ValueError):
                    module.validate_entry(entry)

    def test_payload_hash_changes_on_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'payload'
            path.write_bytes(b'original')
            before = module.digest(path)
            path.write_bytes(b'corrupted')
            self.assertNotEqual(before, module.digest(path))

    def test_unreadable_cache_falls_back_to_download(self):
        with patch.object(module, 'digest', side_effect=PermissionError):
            self.assertFalse(module.cache_matches(Path('/cache/package'), 'a' * 64))

    def test_lock_refresh_removes_old_package_and_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            old = dest / 'base-0-any.pkg.tar.zst'
            signature = old.with_name(old.name + '.sig')
            current = dest / self.entry['filename']
            for path in (old, signature, current):
                path.touch()
            module.prune_payload(dest, [self.entry])
            self.assertTrue(current.exists())
            self.assertFalse(old.exists())
            self.assertFalse(signature.exists())


class ReviewedRecipeTests(unittest.TestCase):
    def test_modified_recipe_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'packages/aur/example-bin'
            source.mkdir(parents=True)
            for name in ('PKGBUILD', '.SRCINFO'):
                (source / name).write_text('reviewed contents')
            recipe = dict(
                name='example-bin', files={name: aur.digest(source / name) for name in ('PKGBUILD', '.SRCINFO')}
            )
            self.assertEqual(aur.validate_recipe(root, recipe), source)
            (source / 'PKGBUILD').write_text('changed contents')
            with self.assertRaisesRegex(ValueError, 'recipe changed'):
                aur.validate_recipe(root, recipe)

    def test_recipe_cannot_read_outside_reviewed_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'packages/aur/example-bin'
            source.mkdir(parents=True)
            for name in ('PKGBUILD', '.SRCINFO'):
                (source / name).touch()
            outside = root / 'packages/aur/outside'
            outside.write_text('outside')
            recipe = dict(
                name='example-bin', files={name: aur.digest(source / name) for name in ('PKGBUILD', '.SRCINFO')}
            )
            recipe['files']['../outside'] = aur.digest(outside)
            with self.assertRaisesRegex(ValueError, 'recipe changed'):
                aur.validate_recipe(root, recipe)


class LiveKeyringTests(unittest.TestCase):
    def test_key_operations_wait_for_live_initializer_and_stop_if_it_fails(self):
        import os
        import subprocess

        source = Path(__file__).with_name('build-guest').read_text()
        preparation = source.split('# Serialize keyring preparation', 1)[1].split('# Import the existing', 1)[0]
        preparation = '# Serialize keyring preparation' + preparation
        for status in (0, 9):
            with self.subTest(initializer_status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                systemctl = root / 'systemctl'
                systemctl.write_text(
                    '#!/bin/bash\nsleep 0.02\n'
                    f'if [ {status} != 0 ]; then exit {status}; fi\n'
                    'touch "$KEYRING_TEST_STATE/ready"\n'
                )
                pacman_key = root / 'pacman-key'
                pacman_key.write_text(
                    '#!/bin/bash\n'
                    'test -f "$KEYRING_TEST_STATE/ready" || exit 29\n'
                    'printf "%s\\n" "$*" >> "$KEYRING_TEST_STATE/operations"\n'
                )
                systemctl.chmod(0o755)
                pacman_key.chmod(0o755)
                environment = os.environ | {
                    'PATH': str(root) + ':' + os.environ['PATH'],
                    'KEYRING_TEST_STATE': str(root),
                }
                result = subprocess.run(['bash', '-e', '-c', preparation], env=environment)
                self.assertEqual(result.returncode, status)
                operations = root / 'operations'
                if status == 0:
                    self.assertEqual(operations.read_text().splitlines(), ['--init', '--populate archlinux'])
                else:
                    self.assertFalse(operations.exists())
