"""Fixture discovery follows branch history; explicit paths bypass discovery."""

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
        git_run('checkout', 'main', cwd=self.repo)
        self.cache = patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.root)})
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def fixture(self, name, commit, mtime):
        directory = self.root / name
        directory.mkdir()
        state.own(directory, self.repo)
        (directory / 'ACCEPTANCE-PASSED').touch()
        (directory / 'disk.qcow2').touch()
        inputs = {} if commit is None else {'checkout': {'commit': commit}}
        (directory / 'inputs.json').write_text(json.dumps(inputs))
        os.utime(directory, (mtime, mtime))
        return directory / 'disk.qcow2'

    def test_ancestor_beats_newer_non_ancestor_and_unrecorded(self):
        ancestor = self.fixture('ancestor', self.ancestor, 100)
        other = self.fixture('other', self.other, 300)
        self.fixture('legacy', None, 200)
        with contextlib.redirect_stderr(io.StringIO()) as output:
            selected = fixtures.newest_accepted_fixture(checkout=self.repo)
        self.assertEqual(selected, ancestor)
        self.assertIn(str(other.parent), output.getvalue())
        self.assertIn('ancestor', output.getvalue().lower())

    def test_newest_unrecorded_is_last_resort_with_warning(self):
        self.fixture('old-legacy', None, 100)
        legacy = self.fixture('legacy', None, 200)
        self.fixture('other', self.other, 300)
        with contextlib.redirect_stderr(io.StringIO()) as output:
            selected = fixtures.newest_accepted_fixture(checkout=self.repo)
        self.assertEqual(selected, legacy)
        warnings = [line for line in output.getvalue().splitlines() if 'warning' in line.lower()]
        self.assertEqual(len(warnings), 1)
        self.assertIn(str(legacy.parent), warnings[0])

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
