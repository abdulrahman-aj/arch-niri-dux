"""Shipped lock defaults reach accounts without overriding their choices."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'dux_settings', ROOT / 'desktop/usr/lib/arch-niri-dux/initialize-dux-settings.py'
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

LOCK_KEYS = ('lockBeforeSuspend', 'acLockTimeout', 'batteryLockTimeout')


def effective(config):
    """Dux's merged view: settings.json, then config.d files in name order."""
    merged = {}
    for path in [config / 'dms/settings.json', *sorted((config / 'dms/config.d').glob('*.json'))]:
        if path.exists():
            merged.update(json.loads(path.read_text()))
    return {key: merged[key] for key in LOCK_KEYS if key in merged}


class LockDefaultsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name)

    def test_fresh_account_locks_before_suspend_and_on_idle(self):
        module.initialize(self.config)
        self.assertEqual(
            effective(self.config), {'lockBeforeSuspend': True, 'acLockTimeout': 600, 'batteryLockTimeout': 600}
        )

    def test_existing_choices_and_later_disable_survive(self):
        directory = self.config / 'dms/config.d'
        directory.mkdir(parents=True)
        (self.config / 'dms/settings.json').write_text('{"lockBeforeSuspend": false}')
        (directory / 'personal.json').write_text('{"acLockTimeout": 120}')
        module.initialize(self.config)
        self.assertEqual(
            effective(self.config), {'lockBeforeSuspend': False, 'acLockTimeout': 120, 'batteryLockTimeout': 600}
        )
        # Dux drops the key from the seed file once the user turns it off.
        (directory / '00-arch-niri-dux-lock.json').write_text('{}')
        module.initialize(self.config)
        self.assertNotIn('batteryLockTimeout', effective(self.config))


if __name__ == '__main__':
    unittest.main()
