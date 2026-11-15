"""Fixture discovery uses clean main commits; explicit paths bypass discovery."""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_workspace import git_run, init_repo
from tools.verification.scenarios.fixture import run_fixture
from tools.workspace import fixtures, state


class FixtureSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        init_repo(self.repo)
        self.ancestor = git_run('rev-parse', 'HEAD', cwd=self.repo).stdout.strip()
        git_run('checkout', '-b', 'other', cwd=self.repo)
        git_run('commit', '--allow-empty', '-m', 'other branch', cwd=self.repo)
        self.other = git_run('rev-parse', 'HEAD', cwd=self.repo).stdout.strip()
        git_run('update-ref', 'refs/remotes/origin/main', self.ancestor, cwd=self.repo)
        self.cache = patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.root)})
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def fixture(self, name, commit, mtime, dirty=False):
        directory = self.root / name
        directory.mkdir()
        state.own(directory, self.repo)
        (directory / 'ACCEPTANCE-PASSED').touch()
        (directory / 'disk.qcow2').touch()
        inputs = {} if commit is None else {'checkout': {'commit': commit, 'dirty': dirty}}
        (directory / 'inputs.json').write_text(json.dumps(inputs))
        os.utime(directory, (mtime, mtime))
        return directory / 'disk.qcow2'

    def test_main_beats_newer_pr_and_unrecorded(self):
        main = self.fixture('main', self.ancestor, 100)
        other = self.fixture('other', self.other, 300)
        legacy = self.fixture('legacy', None, 200)
        with contextlib.redirect_stderr(io.StringIO()) as output:
            selected = fixtures.newest_accepted_fixture(checkout=self.repo)
        self.assertEqual(selected, main)
        for path in (main, other, legacy):
            self.assertIn(str(path.parent), output.getvalue())
        self.assertIn(self.ancestor, output.getvalue())

    def test_dirty_main_is_skipped(self):
        clean = self.fixture('clean', self.ancestor, 100)
        dirty = self.fixture('dirty', self.ancestor, 200, dirty=True)
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(fixtures.newest_accepted_fixture(checkout=self.repo), clean)
        self.assertIn(str(dirty.parent), output.getvalue())

    def test_no_main_fixture_stops_before_overlay(self):
        self.fixture('legacy', None, 100)
        self.fixture('other', self.other, 200)
        self.fixture('dirty', self.ancestor, 300, dirty=True)
        destination = self.root / 'overlay'
        args = argparse.Namespace(fixture='auto', directory=destination)
        # Bind discovery to the temporary repo without replacing its behavior.
        with (
            patch(
                'tools.verification.scenarios.fixture.newest_accepted_fixture',
                lambda: fixtures.newest_accepted_fixture(checkout=self.repo),
            ),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            with self.assertRaises(SystemExit) as error:
                run_fixture(args, argparse.ArgumentParser())
        self.assertNotEqual(error.exception.code, 0)
        self.assertFalse(destination.exists())
        self.assertIn('--fixture', output.getvalue())

    def test_explicit_non_ancestor_bypasses_discovery(self):
        self.fixture('ancestor', self.ancestor, 300)
        explicit = self.fixture('explicit', self.other, 100)
        (explicit.parent / 'OVMF_VARS.fd').write_bytes(b'explicit firmware')
        destination = self.root / 'overlay'
        args = argparse.Namespace(fixture=explicit, directory=destination, keep=False, kernel_package=None)
        # Stop before staging packages or starting a VM. The copied firmware
        # proves the explicit fixture reached overlay preparation unchanged.
        with patch('tools.verification.scenarios.fixture.disk_guard'):
            with self.assertRaises(FileNotFoundError):
                run_fixture(args, argparse.ArgumentParser())
        self.assertEqual((destination / 'OVMF_VARS.fd').read_bytes(), b'explicit firmware')
