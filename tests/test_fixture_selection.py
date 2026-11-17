"""Fixture discovery uses clean main commits; explicit paths bypass discovery."""

import argparse
import contextlib
import io
import json
import os
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_workspace import git_run, init_repo
from tools.verification.scenarios.fixture import run_fixture
from tools.workspace import fixtures, gc, resources, state


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

    def fixture(self, name, commit, mtime, dirty=False, nvidia=None):
        directory = self.root / name
        directory.mkdir()
        state.own(directory, self.repo)
        (directory / 'ACCEPTANCE-PASSED').touch()
        (directory / 'disk.qcow2').touch()
        inputs = {} if commit is None else {'checkout': {'commit': commit, 'dirty': dirty}, 'nvidia': nvidia}
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

    def test_main_run_with_a_forced_nvidia_driver_is_skipped(self):
        default = self.fixture('default', self.ancestor, 100)
        forced = self.fixture('forced', self.ancestor, 200, nvidia='open')
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(fixtures.newest_accepted_fixture(checkout=self.repo), default)
        self.assertIn(str(forced.parent), output.getvalue())

    def test_no_main_fixture_stops_before_overlay(self):
        self.fixture('legacy', None, 100)
        self.fixture('other', self.other, 200)
        self.fixture('dirty', self.ancestor, 300, dirty=True)
        destination = self.root / 'overlay'
        args = argparse.Namespace(fixture='auto', directory=destination)
        # The temporary commits are unknown to the real checkout, so discovery
        # finds no main fixture without replacing its behavior.
        with contextlib.redirect_stderr(io.StringIO()) as output:
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
        # Fake free space (stdlib boundary) so the real guard passes on small /tmp.
        roomy = type('Usage', (), {'free': 100 * 1024**3})()
        with patch.object(resources.shutil, 'disk_usage', return_value=roomy):
            with self.assertRaises(FileNotFoundError):
                run_fixture(args, argparse.ArgumentParser())
        self.assertEqual((destination / 'OVMF_VARS.fd').read_bytes(), b'explicit firmware')


class FixtureGcTests(FixtureSelectionTests):
    def test_superseded_disk_freed_selected_and_evidence_stay(self):
        older = self.fixture('older', self.ancestor, 100)
        selected = self.fixture('selected', self.ancestor, 200)
        other = self.fixture('unmerged', self.other, 300)
        dirty = self.fixture('dirty-main', self.ancestor, 400, dirty=True)
        # Reproduce qualify output inside the checkout that runs GC.
        destination = self.repo / 'out/qualify/old/verify'
        fixtures.move_fixture(older.parent, destination, self.repo)
        older = destination / 'disk.qcow2'
        os.utime(destination, (100, 100))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gc.collect(self.root, self.repo, True), [older])
            self.assertTrue(older.exists())
            self.assertEqual(gc.collect(self.root, self.repo), [older])
        self.assertFalse(older.exists())
        self.assertTrue((destination / 'inputs.json').exists())
        for disk in (selected, other, dirty):
            self.assertTrue(disk.exists())
        self.assertEqual(fixtures.newest_accepted_fixture(checkout=self.repo), selected)

    def test_referenced_fixture_and_running_fixture_stay(self):
        referenced = self.fixture('referenced', self.ancestor, 100)
        running = self.fixture('running', self.ancestor, 200)
        selected = self.fixture('selected', self.ancestor, 300)
        alongside = self.fixture('alongside-backing', self.ancestor, 150)
        overlay = self.root / 'overlay'
        overlay.mkdir()
        state.own(overlay, self.repo)
        (overlay / 'disk.qcow2').touch()
        (overlay / 'inputs.json').write_text(json.dumps({'fixture': str(referenced)}))
        preparing = self.root / 'preparing-alongside'
        preparing.mkdir()
        state.own(preparing, self.repo)
        (preparing / 'inputs.json').write_text(json.dumps({'fixture': {'path': str(alongside)}}))
        (running.parent / '.sockets').mkdir()
        os.utime(running.parent, (200, 200))
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(running.parent / '.sockets/qmp.sock'))
            server.listen()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gc.collect(self.root, self.repo), [])
        for disk in (referenced, alongside, running, selected):
            self.assertTrue(disk.exists())

    def test_orphaned_build_state_freed_live_owner_stays(self):
        orphan = self.root / 'arch-niri-dux-build-orphan'
        live = self.root / 'arch-niri-dux-build-live'
        for directory, owner in ((orphan, self.root / 'removed-checkout'), (live, self.repo)):
            directory.mkdir()
            state.own(directory, owner)
            (directory / 'builder.qcow2').write_bytes(b'build state')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gc.collect(self.root, self.repo, True), [orphan])
            self.assertTrue(orphan.exists())
            self.assertEqual(gc.collect(self.root, self.repo), [orphan])
        self.assertFalse(orphan.exists())
        self.assertTrue((live / 'builder.qcow2').exists())
