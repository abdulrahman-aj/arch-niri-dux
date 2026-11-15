"""Negative controls for the alongside and upgrade preservation checks."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.digest import digest
from tools.verification.probes.upgrade import assert_personal_preserved


class PreservationTests(unittest.TestCase):
    def test_original_partition_one_byte_change_is_rejected(self):
        # run_alongside_install uses these same coreutils operations in the
        # offline guest. Regular files exercise the full-byte check without a VM.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partitions = [root / 'esp', root / 'linux', root / 'windows']
            for path in partitions:
                path.write_bytes(bytes(range(256)) * 32)
            baseline = subprocess.check_output(['sha256sum', *map(str, partitions)])
            manifest = root / 'old-partitions.sha256'
            manifest.write_bytes(baseline)

            def check():
                return subprocess.run(['sha256sum', '-c', str(manifest)], capture_output=True, check=False)

            self.assertEqual(check().returncode, 0)
            for path in partitions:
                for offset in (0, path.stat().st_size // 2, path.stat().st_size - 1):
                    with self.subTest(partition=path.name, offset=offset):
                        original = path.read_bytes()
                        with path.open('r+b') as stream:
                            stream.seek(offset)
                            stream.write(bytes([original[offset] ^ 1]))
                        self.assertNotEqual(check().returncode, 0)
                        path.write_bytes(original)
                        self.assertEqual(check().returncode, 0)

    def test_upgrade_overwriting_any_personal_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            personal = [
                Path(directory) / name for name in ('niri/config.kdl', 'niri/dms/binds.kdl', 'xdg-terminals.list')
            ]
            for path in personal:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'personal settings\n')
            before = {str(path): digest(path) for path in personal}
            assert_personal_preserved(personal, before)
            for path in personal:
                with self.subTest(config=path.name):
                    original = path.read_bytes()
                    path.write_bytes(b'package defaults\n')
                    with self.assertRaises(AssertionError):
                        assert_personal_preserved(personal, before)
                    path.write_bytes(original)
                    assert_personal_preserved(personal, before)
