"""Exercise the packaged Niri migration without touching the host configuration."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Exact seed first shipped at 64b5ce4, unchanged in integration-v0.2.0-{1,2,3,4}.
LEGACY = (ROOT / 'tests/fixtures/niri-stock-0.2.0.kdl').read_bytes()


@unittest.skipIf(os.geteuid() == 0, 'Niri migration deliberately rejects root')
class NiriMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.script = self.root / 'usr/lib/arch-niri-dux/migrate-niri-overrides'
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'desktop/usr/lib/arch-niri-dux/migrate-niri-overrides', self.script)
        self.defaults = self.root / 'usr/share/arch-niri-dux'
        self.defaults.mkdir(parents=True)
        for name in ('niri-user.kdl', 'niri-overrides.kdl'):
            shutil.copyfile(ROOT / 'desktop/usr/share/arch-niri-dux' / name, self.defaults / name)
        self.config = self.root / 'settings/niri/config.kdl'
        self.config.parent.mkdir(parents=True)
        self.config.write_bytes(LEGACY)
        self.config.chmod(0o600)
        self.overrides = self.config.with_name('overrides.kdl')
        self.backup = self.config.with_name('config.kdl.before-overrides')
        self.environment = os.environ | {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'settings')}

    def migrate(self):
        return subprocess.run(['bash', str(self.script)], env=self.environment, capture_output=True, text=True)

    def test_stock_entrypoint_migrates_once_and_can_repeat_after_snapshot_restore(self):
        result = self.migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_bytes(), (self.defaults / 'niri-user.kdl').read_bytes())
        self.assertEqual(self.backup.read_bytes(), LEGACY)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.overrides.read_bytes(), (self.defaults / 'niri-overrides.kdl').read_bytes())
        self.overrides.write_text('// Personal settings\n')
        before = self.config.stat().st_mtime_ns
        self.assertEqual(self.migrate().returncode, 0)
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.config.write_bytes(LEGACY)
        result = self.migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_bytes(), (self.defaults / 'niri-user.kdl').read_bytes())
        self.assertEqual(self.overrides.read_text(), '// Personal settings\n')

    def test_personal_entrypoints_and_symlinks_are_not_replaced(self):
        self.config.write_bytes(LEGACY + b'// Personal configuration\n')
        self.assertEqual(self.migrate().returncode, 0)
        self.assertEqual(self.config.read_bytes(), LEGACY + b'// Personal configuration\n')
        self.config.unlink()
        target = self.root / 'tracked.kdl'
        target.write_bytes(LEGACY)
        self.config.symlink_to(target)
        self.assertEqual(self.migrate().returncode, 0)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(target.read_bytes(), LEGACY)
        target.unlink()
        self.assertEqual(self.migrate().returncode, 0)
        self.assertTrue(self.config.is_symlink())
        self.assertFalse(self.backup.exists())

    def test_existing_overrides_symlink_survives_migration_even_if_dangling(self):
        self.overrides.symlink_to(self.root / 'not-yet-stowed.kdl')
        result = self.migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.overrides.is_symlink())
        self.assertEqual(self.overrides.readlink(), self.root / 'not-yet-stowed.kdl')

    def test_failed_preparation_preserves_entrypoint_and_retries(self):
        template = self.defaults / 'niri-user.kdl'
        content = template.read_bytes()
        template.unlink()
        self.assertNotEqual(self.migrate().returncode, 0)
        self.assertEqual(self.config.read_bytes(), LEGACY)
        self.assertEqual(self.backup.read_bytes(), LEGACY)
        self.assertEqual(list(self.config.parent.glob('.config.kdl.*')), [])
        template.write_bytes(content)
        result = self.migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_bytes(), content)

    def test_unrelated_backup_is_never_overwritten(self):
        self.backup.write_text('Keep this backup\n')
        self.assertNotEqual(self.migrate().returncode, 0)
        self.assertEqual(self.backup.read_text(), 'Keep this backup\n')
        self.assertEqual(self.config.read_bytes(), LEGACY)


if __name__ == '__main__':
    unittest.main()
