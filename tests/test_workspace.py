import contextlib
import io
import json
import os
import subprocess
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

    def test_agent_detection(self):
        harnesses = {
            'T3 agent': {'T3CODE_THREAD_ID': 'thread-1', 'T3CODE_PROJECT_ROOT': '/w'},
            'Claude Code': {'CLAUDECODE': '1'},
            'Codex': {'CODEX_THREAD_ID': 'abc', 'CODEX_CI': '1'},
            'OpenCode': {'OPENCODE': '1', 'OPENCODE_SESSION_ID': 'ses', 'AGENT': '1', 'AI_AGENT': 'opencode'},
        }
        for harness, env in harnesses.items():
            with self.subTest(harness=harness):
                self.assertTrue(workspace.agent_markers(env))
        humans = {
            'plain shell': {'HOME': '/home/u', 'PATH': '/usr/bin'},
            # Regression: T3's integrated terminal is a human, not an agent.
            'T3 integrated terminal': {'T3CODE_PROJECT_ROOT': '/w', 'T3CODE_HOME': '/h', 'AGENT_HEAVY_JOBS': 'deny'},
            'harness config in a shell profile': {'CODEX_HOME': '/c', 'OPENCODE_CONFIG': '/o'},
            'empty markers': {'CLAUDECODE': '', 'T3CODE_THREAD_ID': ''},
        }
        for who, env in humans.items():
            with self.subTest(human=who):
                self.assertEqual(workspace.agent_markers(env), [])

    def test_heavy_guard_denies_only_agents(self):
        with self.assertRaises(SystemExit):
            workspace.heavy_guard({'AGENT_HEAVY_JOBS': 'deny', 'T3CODE_THREAD_ID': 'thread-1'})
        workspace.heavy_guard({'AGENT_HEAVY_JOBS': 'deny', 'T3CODE_PROJECT_ROOT': '/w'})
        workspace.heavy_guard({'T3CODE_THREAD_ID': 'thread-1'})

    def test_space_needs_add_up_per_filesystem(self):
        with tempfile.TemporaryDirectory() as temp:
            free = workspace.shutil.disk_usage(temp).free
            # Each need fits alone; together on one filesystem they do not.
            share = int(free * 0.6)
            self.assertEqual(workspace.space_shortfalls([(Path(temp) / 'a', share, 'a')]), [])
            shortfalls = workspace.space_shortfalls(
                [(Path(temp) / 'a', share, 'a'), (Path(temp) / 'b/not-created', share, 'b')]
            )
            self.assertEqual(len(shortfalls), 1)
            _, need, free, purposes = shortfalls[0]
            self.assertGreater(need, free)
            self.assertEqual(sorted(purposes), ['a', 'b'])

    def test_earlier_builds_reduce_space_needed(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            state = Path(temp) / 'state'
            fresh = workspace.build_space_needed(state)
            state.mkdir()
            (state / 'builder.qcow2').write_bytes(os.urandom(4 * 1024**2))
            downloads = workspace.downloads_root()
            downloads.mkdir()
            (downloads / 'pkg.tar.zst').write_bytes(os.urandom(4 * 1024**2))
            self.assertLessEqual(workspace.build_space_needed(state), fresh - 8 * 1024**2)
            with patch.object(workspace, 'BUILD_SPACE', 1024), patch.object(workspace, 'DOWNLOADS_SPACE', 1024):
                self.assertEqual(workspace.build_space_needed(state), 0)

    def test_builder_memory_fits_humans_and_waits_for_agents(self):
        gib = 1024**3
        with patch.object(workspace, 'available_memory', return_value=24 * gib):
            self.assertEqual(workspace.builder_memory({}), (workspace.BUILDER_MIB, None))
        with patch.object(workspace, 'available_memory', return_value=6 * gib):
            mib, warning = workspace.builder_memory({})
            self.assertLess(mib, workspace.BUILDER_MIB)
            self.assertLessEqual(mib + workspace.MEMORY_HEADROOM_MIB, 6 * 1024)
            self.assertTrue(warning)
        with patch.object(workspace, 'available_memory', return_value=3 * gib):
            mib, warning = workspace.builder_memory({})
            self.assertEqual(mib, workspace.BUILDER_MIN_MIB)
            self.assertTrue(warning)
        with (
            patch.object(workspace, 'available_memory', return_value=3 * gib),
            patch('verification.vm.wait_for_memory') as wait,
        ):
            self.assertEqual(workspace.builder_memory({'CLAUDECODE': '1'}), (workspace.BUILDER_MIB, None))
            wait.assert_called_once()

    def test_builder_memory_command_prints_only_the_size(self):
        # build-iso captures stdout as QEMU's -m value; warnings must go to stderr.
        env = {key: value for key, value in os.environ.items() if key not in workspace.AGENT_MARKERS}
        result = subprocess.run(
            [sys.executable, str(Path(workspace.__file__)), 'builder-memory'],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertGreaterEqual(int(result.stdout), workspace.BUILDER_MIN_MIB)

    def test_guards(self):
        # Exercise the real filesystem without filling it or mocking commands.
        workspace.disk_guard(Path.cwd(), minimum=0)
        with self.assertRaises(SystemExit):
            workspace.disk_guard(Path.cwd() / 'not-created', minimum=2**100)


def git_run(*args, cwd, **kwargs):
    return subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True, check=True, **kwargs)


def init_repo(root):
    git_run('init', '-b', 'main', cwd=root)
    git_run('config', 'user.email', 'test@example.invalid', cwd=root)
    git_run('config', 'user.name', 'Test', cwd=root)
    (root / 'tracked.txt').write_text('base\n')
    (root / '.gitignore').write_text('/out/\n')
    git_run('add', '.', cwd=root)
    git_run('commit', '-m', 'base', cwd=root)
    return root


class WorktreeGCTests(unittest.TestCase):
    def test_status_filter_ignores_out(self):
        self.assertFalse(workspace._status_entry_outside_out(' M out/report.txt'))
        self.assertFalse(workspace._status_entry_outside_out('?? out/new-dir/file'))
        self.assertFalse(workspace._status_entry_outside_out('R  out/a -> out/b'))
        self.assertTrue(workspace._status_entry_outside_out(' M src/foo.py'))
        self.assertTrue(workspace._status_entry_outside_out('?? notes.txt'))
        self.assertTrue(workspace._status_entry_outside_out('R  old.txt -> out/moved.txt'))
        self.assertTrue(workspace._status_entry_outside_out('R  out/a -> src/b'))

    def test_status_outside_out_with_real_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            init_repo(root)
            (root / 'out').mkdir()
            (root / 'out' / 'ignored.txt').write_text('x')
            self.assertEqual(workspace.worktree_status_outside_out(root), [])
            (root / 'tracked.txt').write_text('dirty\n')
            self.assertEqual(workspace.worktree_status_outside_out(root), [' M tracked.txt'])
            git_run('checkout', '--', 'tracked.txt', cwd=root)
            (root / 'new.txt').write_text('untracked\n')
            self.assertEqual(workspace.worktree_status_outside_out(root), ['?? new.txt'])

    def test_merge_info_prefers_gh_then_ancestry(self):
        with (
            patch.object(workspace, 'merged_prs', return_value=[{'number': 7, 'title': 'Fix'}]),
            patch.object(workspace, 'is_ancestor', return_value=False),
        ):
            self.assertEqual(workspace.merge_info('branch'), (True, 7, 'Fix', 'gh'))
        with (
            patch.object(workspace, 'merged_prs', return_value=[]),
            patch.object(workspace, 'is_ancestor', return_value=True),
        ):
            self.assertEqual(workspace.merge_info('branch'), (True, None, '', 'ancestry'))
        with (
            patch.object(workspace, 'merged_prs', return_value=None),
            patch.object(workspace, 'is_ancestor', return_value=False),
        ):
            self.assertEqual(workspace.merge_info('branch'), (False, None, '', 'none'))

    def test_proven_old_fixture_needs_a_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertFalse(workspace.is_proven_old_fixture(root))
            (root / 'release-manifest.json').write_text(json.dumps({'version': '0.2.9-1'}))
            self.assertTrue(workspace.is_proven_old_fixture(root))
            (root / 'release-manifest.json').write_text(json.dumps({'version': '0.3.2-1'}))
            self.assertFalse(workspace.is_proven_old_fixture(root))
            (root / 'release-manifest.json').write_text('not json')
            self.assertFalse(workspace.is_proven_old_fixture(root))

    def test_plan_splits_removals_and_skips(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            root = Path(temp) / 'repo'
            root.mkdir()
            init_repo(root)
            for branch in ('merged-clean', 'merged-dirty', 'unmerged'):
                git_run('branch', branch, cwd=root)
            clean_wt = Path(temp) / 'wt-clean'
            dirty_wt = Path(temp) / 'wt-dirty'
            unmerged_wt = Path(temp) / 'wt-unmerged'
            git_run('worktree', 'add', str(clean_wt), 'merged-clean', cwd=root)
            git_run('worktree', 'add', str(dirty_wt), 'merged-dirty', cwd=root)
            git_run('worktree', 'add', str(unmerged_wt), 'unmerged', cwd=root)
            detached_wt = Path(temp) / 'wt-detached'
            git_run('worktree', 'add', '--detach', str(detached_wt), cwd=root)
            (dirty_wt / 'tracked.txt').write_text('dirty\n')

            def fake_merge(branch, _checkout=workspace.ROOT):
                if branch in ('merged-clean', 'merged-dirty'):
                    return True, 11, 'Merged work', 'gh'
                return False, None, '', 'none'

            with (
                patch.object(workspace, 'merge_info', side_effect=fake_merge),
                patch.object(workspace, 'worktree_busy', return_value=(False, '')),
                patch.object(workspace, 'open_prs', return_value=[]),
            ):
                removals, skipped, move = workspace.plan_worktree_gc(root)
            self.assertEqual([item['branch'] for item in removals], ['merged-clean'])
            reasons = {item['branch'] or 'detached': item['reason'] for item in skipped}
            self.assertIn('current checkout', reasons['main'])
            self.assertIn('dirty', reasons['merged-dirty'])
            self.assertIn('unmerged', reasons['unmerged'])
            self.assertIn('detached', reasons['detached'])
            self.assertIsNone(move)

    def make_fixture(self, parent, name, payload, mtime, owner=None):
        directory = parent / name
        directory.mkdir()
        workspace.own(directory, owner or parent)
        (directory / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
        (directory / 'disk.qcow2').write_bytes(payload)
        (directory / 'install.json').write_text('{}\n')
        (directory / 'inputs.json').write_text('{}\n')
        (directory / 'OVMF_VARS.fd').write_bytes(b'vars')
        os.utime(directory, (mtime, mtime))
        return directory

    def test_fixture_move_keeps_newest_readable(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            cache = Path(temp)
            doomed_owner = cache / 'doomed-checkout'
            doomed_owner.mkdir()
            kept_owner = cache / 'kept-checkout'
            kept_owner.mkdir()
            doomed_home = cache / 'wt-doomed'
            doomed_home.mkdir()
            old = self.make_fixture(cache, 'old-fixture', b'old', 1000, kept_owner)
            new = self.make_fixture(doomed_home, 'dux-verify-new', b'new-bytes', 2000, doomed_home)
            self.assertTrue(old.exists())
            self.assertEqual(workspace.newest_accepted_fixture(), new / 'disk.qcow2')
            target = kept_owner / 'out' / 'verify' / new.name
            moved = workspace.move_fixture(new, target, kept_owner)
            self.assertFalse(new.exists())
            self.assertEqual((moved / 'disk.qcow2').read_bytes(), b'new-bytes')
            self.assertTrue((moved / 'ACCEPTANCE-PASSED').is_file())
            picked = workspace.newest_accepted_fixture()
            self.assertEqual(picked, moved / 'disk.qcow2')
            self.assertEqual(picked.read_bytes(), b'new-bytes')

    def test_dry_run_changes_nothing_then_real_run_preserves_fixture(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            cache = Path(temp)
            root = cache / 'repo'
            root.mkdir()
            init_repo(root)
            git_run('branch', 'merged-clean', cwd=root)
            doomed = cache / 'wt-doomed'
            git_run('worktree', 'add', str(doomed), 'merged-clean', cwd=root)
            fixture = doomed / 'out' / 'verify' / 'dux-verify-new'
            fixture.mkdir(parents=True)
            workspace.own(fixture, doomed)
            (fixture / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
            (fixture / 'disk.qcow2').write_bytes(b'fixture-bytes')
            (fixture / 'install.json').write_text('{}\n')
            (fixture / 'inputs.json').write_text('{}\n')
            (fixture / 'OVMF_VARS.fd').write_bytes(b'vars')
            before = workspace.newest_accepted_fixture()
            self.assertEqual(before, fixture / 'disk.qcow2')

            def fake_merge(branch, _checkout=workspace.ROOT):
                return (True, 11, 'Merged work', 'gh') if branch == 'merged-clean' else (False, None, '', 'none')

            with (
                patch.object(workspace, 'merge_info', side_effect=fake_merge),
                patch.object(workspace, 'worktree_busy', return_value=(False, '')),
                patch.object(workspace, 'open_prs', return_value=[]),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    _removals, _skipped, move = workspace.gc_worktrees(root, True)
                self.assertTrue(doomed.exists())
                self.assertTrue(fixture.exists())
                self.assertEqual(workspace.newest_accepted_fixture(), before)
                self.assertIsNotNone(move)
                with contextlib.redirect_stdout(io.StringIO()):
                    workspace.gc_worktrees(root, False)
            self.assertFalse(doomed.exists())
            picked = workspace.newest_accepted_fixture()
            self.assertIsNotNone(picked)
            self.assertTrue(picked.is_file())
            self.assertEqual(picked.read_bytes(), b'fixture-bytes')
            self.assertTrue((picked.parent / 'ACCEPTANCE-PASSED').is_file())


if __name__ == '__main__':
    unittest.main()
