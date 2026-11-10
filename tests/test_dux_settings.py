"""Seeded Dux defaults preserve saved choices and stay editable across starts."""

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


def effective(config):
    """Dux's merged view: settings.json, then config.d files in name order."""
    merged = {}
    directory = config / 'dms/config.d'
    for path in [config / 'dms/settings.json', *sorted(directory.glob('*.json'))]:
        if path.exists():
            merged.update(json.loads(path.read_text()))
    return merged


class DuxSettingsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name)
        self.directory = self.config / 'dms/config.d'

    def test_fresh_account_receives_every_default(self):
        module.initialize(self.config, {'a.json': {'x': 1}, 'b.json': {'y': 2}})
        self.assertEqual(effective(self.config), {'x': 1, 'y': 2})

    def test_explicit_choices_win_over_defaults(self):
        self.directory.mkdir(parents=True)
        (self.config / 'dms/settings.json').write_text('{"x": false}')
        (self.directory / 'personal.json').write_text('{"y": 120}')
        module.initialize(self.config, {'00-seed.json': {'x': True, 'y': 600, 'z': 600}})
        self.assertEqual(effective(self.config), {'x': False, 'y': 120, 'z': 600})

    def test_later_opt_out_survives_restarts(self):
        seeds = {'00-seed.json': {'x': True}}
        module.initialize(self.config, seeds)
        # Dux drops a key from the seed file once the user resets it to default.
        (self.directory / '00-seed.json').write_text('{}')
        module.initialize(self.config, seeds)
        self.assertNotIn('x', effective(self.config))

    def test_new_feature_reaches_already_seeded_account(self):
        module.initialize(self.config, {'00-old.json': {'x': True}})
        module.initialize(self.config, {'00-old.json': {'x': True}, '00-new.json': {'y': True}})
        self.assertEqual(effective(self.config), {'x': True, 'y': True})

    def test_invalid_user_settings_are_not_overwritten(self):
        (self.config / 'dms').mkdir()
        settings = self.config / 'dms/settings.json'
        settings.write_text('{invalid')
        module.initialize(self.config, {'00-seed.json': {'x': True}})
        self.assertEqual(settings.read_text(), '{invalid')
        self.assertFalse((self.directory / '00-seed.json').exists())


class DuxPluginSeedTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name)

    def plugin_file(self):
        return self.config / 'dms/plugin_settings.json'

    def test_fresh_account_enables_plugin(self):
        module.initialize(self.config, {})
        self.assertEqual(json.loads(self.plugin_file().read_text()), {module.PLUGIN_ID: {'enabled': True}})

    def test_explicit_opt_out_survives(self):
        self.plugin_file().parent.mkdir(parents=True)
        self.plugin_file().write_text(json.dumps({module.PLUGIN_ID: {'enabled': False}}))
        module.initialize(self.config, {})
        self.assertEqual(json.loads(self.plugin_file().read_text()), {module.PLUGIN_ID: {'enabled': False}})

    def test_other_plugins_preserved(self):
        self.plugin_file().parent.mkdir(parents=True)
        self.plugin_file().write_text(json.dumps({'otherPlugin': {'enabled': True, 'variant': 'wide'}}))
        module.initialize(self.config, {})
        self.assertEqual(
            json.loads(self.plugin_file().read_text()),
            {'otherPlugin': {'enabled': True, 'variant': 'wide'}, module.PLUGIN_ID: {'enabled': True}},
        )

    def test_partial_entry_gains_enabled_without_losing_keys(self):
        self.plugin_file().parent.mkdir(parents=True)
        self.plugin_file().write_text(json.dumps({module.PLUGIN_ID: {'variant': 'wide'}}))
        module.initialize(self.config, {})
        self.assertEqual(
            json.loads(self.plugin_file().read_text()),
            {module.PLUGIN_ID: {'variant': 'wide', 'enabled': True}},
        )

    def test_symlink_untouched(self):
        self.plugin_file().parent.mkdir(parents=True)
        target = self.config / 'elsewhere.json'
        target.write_text(json.dumps({module.PLUGIN_ID: {'enabled': False}}))
        self.plugin_file().symlink_to(target)
        module.initialize(self.config, {})
        self.assertTrue(self.plugin_file().is_symlink())
        self.assertEqual(json.loads(target.read_text()), {module.PLUGIN_ID: {'enabled': False}})

    def test_malformed_file_does_not_crash_login(self):
        self.plugin_file().parent.mkdir(parents=True)
        self.plugin_file().write_text('{invalid')
        module.initialize(self.config)
        self.assertEqual(self.plugin_file().read_text(), '{invalid')

    def test_bar_seed_uses_release_widget(self):
        module.initialize(self.config)
        merged = {}
        for path in sorted((self.config / 'dms/config.d').glob('*.json')):
            merged.update(json.loads(path.read_text()))
        bars = merged['barConfigs']
        center = next(bar for bar in bars if bar['id'] == 'default')['centerWidgets']
        ids = [entry if isinstance(entry, str) else entry.get('id') for entry in center]
        self.assertIn(module.PLUGIN_ID, ids)
        self.assertNotIn('systemUpdate', ids)
        self.assertEqual(merged['updaterIntervalSeconds'], 6 * 60 * 60)


if __name__ == '__main__':
    unittest.main()
