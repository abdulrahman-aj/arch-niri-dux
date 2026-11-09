"""Focused release-button tier: guards, provenance inputs and cleanup."""

import importlib.machinery
import importlib.util
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_focus

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'desktop/usr/lib/arch-niri-dux/release-update-check'

loader = importlib.machinery.SourceFileLoader('release_update_check_focus', str(CHECKER))
spec = importlib.util.spec_from_loader('release_update_check_focus', loader)
checker = importlib.util.module_from_spec(spec)
loader.exec_module(checker)

SNAPSHOTS = """Snapshot list:
ID      TAG               VM_SIZE                DATE        VM_CLOCK     ICOUNT
1       pristine              0 B 2026-11-09 04:34:27  0000:00:00.000          0
2       recovery           5.0 GiB 2026-11-09 09:12:44  0000:00:00.000          0
"""


class PristineSnapshotTests(unittest.TestCase):
    def test_post_install_snapshot_is_detected(self):
        self.assertTrue(verify_focus.pristine_present(SNAPSHOTS))

    def test_snapshot_list_without_pristine_is_refused(self):
        listing = SNAPSHOTS.replace('pristine', 'post-install')
        self.assertFalse(verify_focus.pristine_present(listing))

    def test_empty_or_header_only_lists_have_no_snapshot(self):
        self.assertFalse(verify_focus.pristine_present('Snapshot list:\nID      TAG\n'))
        self.assertFalse(verify_focus.pristine_present(''))


class FixtureValidationTests(unittest.TestCase):
    def test_fixture_without_acceptance_marker_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / 'disk.qcow2'
            fixture.write_bytes(b'not a disk')
            with self.assertRaises(SystemExit):
                verify_focus.validate_fixture(fixture)

    def test_missing_fixture_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            accepted = Path(temp)
            (accepted / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
            with self.assertRaises(SystemExit):
                verify_focus.validate_fixture(accepted / 'disk.qcow2')


class PreparedStateTests(unittest.TestCase):
    def setUp(self):
        self.prepared = dict(source_sha256='source', recipe_sha256='recipe', epoch=1234)

    def test_missing_prepared_state_requires_preparation(self):
        self.assertTrue(verify_focus.prepare_needed(None, 'source', 'recipe', 1234))

    def test_changed_source_requires_preparation(self):
        self.assertTrue(verify_focus.prepare_needed(self.prepared, 'changed', 'recipe', 1234))

    def test_recipe_only_change_requires_preparation(self):
        self.assertTrue(verify_focus.prepare_needed(self.prepared, 'source', 'changed', 1234))

    def test_changed_build_epoch_requires_preparation(self):
        self.assertTrue(verify_focus.prepare_needed(self.prepared, 'source', 'recipe', 1235))

    def test_probe_changes_do_not_reinstall_unchanged_packages(self):
        self.prepared['verification_scripts'] = {'probe.py': 'old-probe'}
        self.assertFalse(verify_focus.prepare_needed(self.prepared, 'source', 'recipe', 1234))

    def test_old_cache_without_complete_identity_requires_preparation(self):
        self.assertTrue(verify_focus.prepare_needed({'source_sha256': 'source'}, 'source', 'recipe', 1234))


class BaseIntegrityTests(unittest.TestCase):
    def test_unchanged_base_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.qcow2'
            base.write_bytes(b'original disk bytes')
            verify_focus.validate_base(base, verify_focus.digest(base))

    def test_changed_base_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.qcow2'
            base.write_bytes(b'original disk bytes')
            expected = verify_focus.digest(base)
            base.write_bytes(b'modified disk bytes')
            with self.assertRaises(RuntimeError):
                verify_focus.validate_base(base, expected)


class FixtureDatabaseTests(unittest.TestCase):
    def test_database_offers_the_candidate_to_the_real_checker(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'release-focus.db'
            verify_focus.write_fixture_db(database, '99.0-1')
            self.assertEqual(checker.candidate_version_from_db(database), '99.0-1')
            self.assertTrue(checker.check(db_urls=[f'file://{database}'], installed='0.3.1-1'))
            self.assertFalse(checker.check(db_urls=[f'file://{database}'], installed='99.0-1'))

    def test_database_records_both_package_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'release-focus.db'
            verify_focus.write_fixture_db(database, '99.0-1')
            with tarfile.open(database) as archive:
                self.assertIn('arch-niri-dux-99.0-1/desc', archive.getnames())
                self.assertIn('arch-niri-dux-baseline-99.0-1/desc', archive.getnames())


class RunDirectoryTests(unittest.TestCase):
    def test_runs_are_numbered_after_existing_ones(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'run-01').mkdir()
            (directory / 'run-02').mkdir()
            self.assertEqual(verify_focus.next_run(directory), directory / 'run-03')
            self.assertTrue((directory / 'run-03').is_dir())

    def test_evidence_moves_to_the_run_and_leaves_the_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            run = verify_focus.next_run(directory)
            for name in ('release-hidden.png', 'release-panel.png', 'focus-results.json'):
                (directory / name).write_bytes(b'x')
            verify_focus.finish_run(directory, run)
            for name in ('release-hidden.png', 'release-panel.png', 'focus-results.json'):
                self.assertTrue((run / name).is_file())
                self.assertFalse((directory / name).exists())


class WorkspaceCleanupTests(unittest.TestCase):
    def test_owned_workspace_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'dux-focus'
            directory.mkdir()
            (directory / '.arch-niri-dux-owner').write_text(str(Path(temp).resolve()) + '\n')
            verify_focus.clean(directory, checkout=Path(temp))
            self.assertFalse(directory.exists())

    def test_unowned_workspace_is_left_alone(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'dux-focus'
            directory.mkdir()
            (directory / '.arch-niri-dux-owner').write_text('/another/checkout\n')
            with self.assertRaises(SystemExit):
                verify_focus.clean(directory, checkout=Path(temp))
            self.assertTrue(directory.is_dir())


if __name__ == '__main__':
    unittest.main()
