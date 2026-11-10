"""User migration to the release button rewrites only our untouched seed."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'migrate_dux_release_button', ROOT / 'desktop/usr/lib/arch-niri-dux/migrate-dux-release-button.py'
)
migrate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate_module)


def old_seed(**overrides):
    value = {'updaterIntervalSeconds': 7 * 24 * 60 * 60, 'barConfigs': [migrate_module.OLD_MAIN_BAR]}
    value.update(overrides)
    return value


class ReleaseButtonMigrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.target = self.home / '.config/dms/config.d/00-arch-niri-dux-updates.json'
        patcher = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.home / '.config')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_seed(self, value):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(json.dumps(value, indent=2) + '\n')

    def read_seed(self):
        return json.loads(self.target.read_text())

    def run_main(self):
        return migrate_module.main()

    def test_untouched_seed_migrates_interval_and_bar(self):
        self.write_seed(old_seed())
        self.assertEqual(self.run_main(), 0)
        value = self.read_seed()
        self.assertEqual(value['updaterIntervalSeconds'], 6 * 60 * 60)
        center = next(bar for bar in value['barConfigs'] if bar['id'] == 'default')['centerWidgets']
        ids = [entry if isinstance(entry, str) else entry.get('id') for entry in center]
        self.assertIn('archNiriDuxRelease', ids)
        self.assertNotIn('systemUpdate', ids)

    def test_rerun_after_migration_skips(self):
        self.write_seed(old_seed())
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.run_main(), 77)

    def test_user_changed_interval_stays_but_bar_still_migrates(self):
        self.write_seed(old_seed(updaterIntervalSeconds=300))
        self.assertEqual(self.run_main(), 0)
        value = self.read_seed()
        self.assertEqual(value['updaterIntervalSeconds'], 300)
        center = next(bar for bar in value['barConfigs'] if bar['id'] == 'default')['centerWidgets']
        ids = [entry if isinstance(entry, str) else entry.get('id') for entry in center]
        self.assertIn('archNiriDuxRelease', ids)

    def test_user_changed_bar_stays_but_interval_still_migrates(self):
        custom = dict(migrate_module.OLD_MAIN_BAR)
        custom['centerWidgets'] = ['music', 'clock', 'weather']
        self.write_seed(old_seed(barConfigs=[custom, {'id': 'other'}]))
        self.assertEqual(self.run_main(), 0)
        value = self.read_seed()
        self.assertEqual(value['updaterIntervalSeconds'], 6 * 60 * 60)
        self.assertEqual(value['barConfigs'][0]['centerWidgets'], ['music', 'clock', 'weather'])
        self.assertEqual(value['barConfigs'][1], {'id': 'other'})

    def test_fully_customized_seed_skips(self):
        seed = {'updaterIntervalSeconds': 300, 'barConfigs': [{'id': 'default', 'centerWidgets': ['clock']}]}
        self.write_seed(seed)
        self.assertEqual(self.run_main(), 77)
        self.assertEqual(self.read_seed(), seed)

    def test_other_keys_preserved(self):
        self.write_seed(old_seed(customFlag=True))
        self.assertEqual(self.run_main(), 0)
        self.assertTrue(self.read_seed()['customFlag'])

    def test_missing_seed_skips(self):
        self.assertEqual(self.run_main(), 77)

    def test_symlink_untouched(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        real = self.home / 'real.json'
        real.write_text(json.dumps(old_seed()))
        self.target.symlink_to(real)
        self.assertEqual(self.run_main(), 77)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(json.loads(real.read_text())['updaterIntervalSeconds'], 7 * 24 * 60 * 60)

    def test_malformed_seed_skips_without_rewriting(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text('{invalid')
        self.assertEqual(self.run_main(), 77)
        self.assertEqual(self.target.read_text(), '{invalid')

    def test_non_object_seed_skips(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(json.dumps(['updaterIntervalSeconds']))
        self.assertEqual(self.run_main(), 77)

    def test_file_mode_preserved(self):
        self.write_seed(old_seed())
        os.chmod(self.target, 0o600)
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_unreadable_seed_skips(self):
        # A directory in the seed's place makes the read fail with OSError,
        # which must skip rather than stall the migration queue.
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.mkdir()
        self.assertEqual(self.run_main(), 77)
        self.assertTrue(self.target.is_dir())


if __name__ == '__main__':
    unittest.main()
