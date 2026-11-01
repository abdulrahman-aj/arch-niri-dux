import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('build_lock', Path(__file__).with_name('build-lock.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.entry = dict(filename='base-1-any.pkg.tar.zst', sha256='a' * 64,
                          url='https://example.org/base-1-any.pkg.tar.zst', signature='abc')

    def test_unsigned_rejected(self):
        self.entry['signature'] = ''
        with self.assertRaises(ValueError):
            module.validate_entry(self.entry)

    def test_traversal_rejected(self):
        self.entry['filename'] = '../base.pkg.tar.zst'
        with self.assertRaises(ValueError):
            module.validate_entry(self.entry)

    def test_insecure_source_rejected(self):
        self.entry['url'] = 'http://example.org/base'
        with self.assertRaises(ValueError):
            module.validate_entry(self.entry)

    def test_invalid_hash_rejected(self):
        self.entry['sha256'] = 'invalid'
        with self.assertRaises(ValueError):
            module.validate_entry(self.entry)

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
