import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import workspace


class WorkspaceTests(unittest.TestCase):
    def test_states_follow_checkout_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            with patch.dict(os.environ, {'BUILD_STATE': ''}):
                first = workspace.build_state(Path(temp) / 'one/source')
                self.assertEqual(first, workspace.build_state(Path(temp) / 'one/source'))
                self.assertNotEqual(first, workspace.build_state(Path(temp) / 'two/source'))
            with patch.dict(os.environ, {'BUILD_STATE': temp}):
                self.assertEqual(workspace.build_state(), Path(temp))

    def test_explicit_state_is_user_managed(self):
        for populated in (False, True):
            with self.subTest(populated=populated), tempfile.TemporaryDirectory() as temp:
                cache = Path(temp)
                state = cache / 'arch-niri-dux-build-private'
                if populated:
                    state.mkdir()
                    (state / 'payload').write_bytes(b'preserve')
                with patch.dict(os.environ, {'XDG_CACHE_HOME': temp, 'BUILD_STATE': str(state)}):
                    with patch.object(sys, 'argv', ['workspace.py', 'state']):
                        with contextlib.redirect_stdout(io.StringIO()):
                            workspace.main()
                    self.assertTrue(state.is_dir())
                    self.assertFalse((state / workspace.MARKER).exists())
                    self.assertFalse((cache / 'arch-niri-dux-states').exists())
                    # GC must also keep it when BUILD_STATE is no longer set.
                    with patch.dict(os.environ, {'BUILD_STATE': ''}):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(workspace.collect(cache, cache / 'gone'), [])
                    self.assertTrue(state.is_dir())
                    if populated:
                        self.assertEqual((state / 'payload').read_bytes(), b'preserve')

    def test_explicit_state_with_an_existing_marker_is_protected(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            cache = Path(temp)
            state = cache / 'arch-niri-dux-build-private'
            state.mkdir()
            workspace.own(state, cache / 'gone')
            with patch.dict(os.environ, {'BUILD_STATE': str(state)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(workspace.collect(cache, cache / 'checkout'), [])
            self.assertTrue(state.is_dir())

    def test_gc_only_deletes_owned_orphans_and_dry_run_preserves_them(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            cache = Path(temp)
            checkout = cache / 'checkout'
            checkout.mkdir()
            live = cache / 'arch-niri-dux-build-live'
            orphan = cache / 'custom-evidence'
            unowned = cache / 'arch-niri-dux-build-old'
            fixture = cache / 'arch-niri-dux-verification/audit4'
            downloads = cache / 'arch-niri-dux-downloads'
            linked = cache / 'arch-niri-dux-build-link'
            for path in (live, orphan, unowned, fixture, downloads):
                path.mkdir(parents=True)
            workspace.own(live, checkout)
            workspace.own(orphan, cache / 'gone')
            # Even a mistakenly registered downloads cache must survive.
            workspace.own(downloads, cache / 'gone')
            for path in (live, orphan, unowned, fixture, downloads):
                (path / 'payload').write_bytes(b'keep me')
            linked.symlink_to(orphan, target_is_directory=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(workspace.collect(cache, checkout, True), [orphan])
                self.assertTrue(orphan.exists())
                self.assertEqual(workspace.collect(cache, checkout), [orphan])
            self.assertFalse(orphan.exists())
            for path in (live, unowned, fixture, downloads):
                self.assertEqual((path / 'payload').read_bytes(), b'keep me')
            self.assertTrue(linked.is_symlink())

    def test_cannot_transfer_state_ownership(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            directory = Path(temp)
            workspace.own(directory, directory / 'one')
            with self.assertRaises(SystemExit):
                workspace.own(directory, directory / 'two')

    def test_nonempty_unowned_directory_cannot_be_claimed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            payload = directory / 'valuable-data'
            payload.write_bytes(b'preserve')
            with self.assertRaises(SystemExit):
                workspace.own(directory)
            self.assertFalse((directory / workspace.MARKER).exists())
            self.assertEqual(payload.read_bytes(), b'preserve')

    def test_guards(self):
        with patch.dict(os.environ, {'AGENT_HEAVY_JOBS': 'deny'}):
            with self.assertRaises(SystemExit):
                workspace.heavy_guard()
        with patch.dict(os.environ, {'AGENT_HEAVY_JOBS': ''}):
            workspace.heavy_guard()
        # Exercise the real filesystem without filling it or mocking commands.
        workspace.disk_guard(Path.cwd(), minimum=0)
        with self.assertRaises(SystemExit):
            workspace.disk_guard(Path.cwd() / 'not-created', minimum=2**100)


if __name__ == '__main__':
    unittest.main()
