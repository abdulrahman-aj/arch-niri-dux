"""Lock defaults preserve saved choices and remain editable across starts."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'lock_defaults', ROOT / 'desktop/usr/lib/arch-niri-dux/initialize-lock-settings.py'
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LockDefaultsTest(unittest.TestCase):
    def test_existing_choices_and_later_disable_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            directory = config / 'dms/config.d'
            directory.mkdir(parents=True)
            (config / 'dms/settings.json').write_text('{"lockBeforeSuspend": false}')
            (directory / 'personal.json').write_text('{"acLockTimeout": 120}')
            defaults = {'lockBeforeSuspend': True, 'acLockTimeout': 600, 'batteryLockTimeout': 600}
            module.initialize(config, defaults)
            seed = directory / '00-arch-niri-dux-lock.json'
            self.assertEqual(json.loads(seed.read_text()), {'batteryLockTimeout': 600})
            seed.write_text('{}')
            module.initialize(config, defaults)
            self.assertEqual(json.loads(seed.read_text()), {})

    def test_fresh_user_receives_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            defaults = {'lockBeforeSuspend': True}
            module.initialize(config, defaults)
            self.assertEqual(json.loads((config / 'dms/config.d/00-arch-niri-dux-lock.json').read_text()), defaults)

    def test_invalid_user_settings_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            (config / 'dms').mkdir()
            settings = config / 'dms/settings.json'
            settings.write_text('{invalid')
            module.initialize(config, {'lockBeforeSuspend': True})
            self.assertEqual(settings.read_text(), '{invalid')
