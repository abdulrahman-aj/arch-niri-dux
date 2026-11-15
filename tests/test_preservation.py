"""Negative control for the upgrade preservation check."""

from pathlib import Path
import tempfile
import unittest

from tools.digest import digest
from tools.verification.probes.upgrade import assert_personal_preserved


class PreservationTests(unittest.TestCase):
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
