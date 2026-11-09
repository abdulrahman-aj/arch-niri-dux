"""Release button checker shows only on newer arch-niri-dux releases."""

import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'desktop/usr/lib/arch-niri-dux/release-update-check'
import importlib.machinery

loader = importlib.machinery.SourceFileLoader('release_update_check', str(CHECKER))
spec = importlib.util.spec_from_loader('release_update_check', loader)
checker = importlib.util.module_from_spec(spec)
loader.exec_module(checker)

SETTINGS_SPEC = importlib.util.spec_from_file_location(
    'dux_settings', ROOT / 'desktop/usr/lib/arch-niri-dux/initialize-dux-settings.py'
)
settings = importlib.util.module_from_spec(SETTINGS_SPEC)
SETTINGS_SPEC.loader.exec_module(settings)


def make_db(path, entries):
    """Write a repository database tar with (name, version) desc entries."""
    with tarfile.open(path, 'w:gz') as archive:
        for name, version in entries:
            directory = f'{name}-{version}' if version else name
            desc = f'%NAME%\n{name}\n\n%VERSION%\n{version}\n' if version else f'%NAME%\n{name}\n'
            data = desc.encode()
            info = tarfile.TarInfo(f'{directory}/desc')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


class CheckerDecisionTest(unittest.TestCase):
    def test_newer_release_shows(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux', '0.3.2-1')])
            self.assertEqual(checker.candidate_version_from_db(db), '0.3.2-1')
            self.assertTrue(checker.is_newer('0.3.1-1', '0.3.2-1'))
            self.assertTrue(checker.check(db_urls=[f'file://{db}'], installed='0.3.1-1'))

    def test_same_version_hides(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux', '0.3.1-1')])
            self.assertFalse(checker.is_newer('0.3.1-1', '0.3.1-1'))
            self.assertFalse(checker.check(db_urls=[f'file://{db}'], installed='0.3.1-1'))

    def test_older_candidate_hides(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux', '0.3.1-1')])
            self.assertFalse(checker.is_newer('0.3.2-1', '0.3.1-1'))
            self.assertFalse(checker.check(db_urls=[f'file://{db}'], installed='0.3.2-1'))

    def test_offline_hides_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.db'
            result = subprocess.run(
                ['python3', str(CHECKER)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={
                    'PATH': '/usr/bin:/bin',
                    'ARCH_NIRI_DUX_DB_URL': f'file://{missing}',
                    'ARCH_NIRI_DUX_INSTALLED_VERSION': '0.3.1-1',
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '')

    def test_malformed_database_hides(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / 'broken.db'
            broken.write_bytes(b'not a database')
            self.assertIsNone(checker.candidate_version_from_db(broken))
            self.assertFalse(checker.check(db_urls=[f'file://{broken}'], installed='0.3.1-1'))
            empty = Path(directory) / 'empty.db'
            make_db(empty, [('arch-niri-dux-baseline', '0.3.2-1')])
            self.assertIsNone(checker.candidate_version_from_db(empty))
            self.assertFalse(checker.check(db_urls=[f'file://{empty}'], installed='0.3.1-1'))

    def test_ignores_baseline_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux-baseline', '9.9.9-1'), ('arch-niri-dux', '0.3.1-1')])
            self.assertEqual(checker.candidate_version_from_db(db), '0.3.1-1')


class PluginSeedTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name)

    def test_fresh_account_enables_plugin(self):
        settings.initialize(self.config, {})
        value = json.loads((self.config / 'dms/plugin_settings.json').read_text())
        self.assertEqual(value, {settings.PLUGIN_ID: {'enabled': True}})

    def test_explicit_opt_out_survives(self):
        target = self.config / 'dms/plugin_settings.json'
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({settings.PLUGIN_ID: {'enabled': False}}))
        settings.initialize(self.config, {})
        value = json.loads(target.read_text())
        self.assertEqual(value, {settings.PLUGIN_ID: {'enabled': False}})

    def test_other_plugins_preserved(self):
        target = self.config / 'dms/plugin_settings.json'
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'otherPlugin': {'enabled': True}}))
        settings.initialize(self.config, {})
        value = json.loads(target.read_text())
        self.assertEqual(value, {'otherPlugin': {'enabled': True}, settings.PLUGIN_ID: {'enabled': True}})

    def test_bar_seed_uses_plugin_widget(self):
        settings.initialize(self.config)
        merged = {}
        for path in sorted((self.config / 'dms/config.d').glob('*.json')):
            merged.update(json.loads(path.read_text()))
        bars = merged['barConfigs']
        center = next(bar for bar in bars if bar['id'] == 'default')['centerWidgets']
        ids = [entry if isinstance(entry, str) else entry.get('id') for entry in center]
        self.assertIn(settings.PLUGIN_ID, ids)
        self.assertNotIn('systemUpdate', ids)
        self.assertEqual(merged['updaterIntervalSeconds'], 7 * 24 * 60 * 60)


if __name__ == '__main__':
    unittest.main()
