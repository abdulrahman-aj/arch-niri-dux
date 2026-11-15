import contextlib
import io
import json
import os
import socket
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.workspace import state as workspace
from tools.workspace import __main__, fixtures, gc, resources


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
                    with patch.object(sys, 'argv', ['tools.workspace.state.py', 'state']):
                        with contextlib.redirect_stdout(io.StringIO()):
                            __main__.main()
                    self.assertTrue(state.is_dir())
                    self.assertFalse((state / workspace.MARKER).exists())
                    self.assertFalse((cache / 'arch-niri-dux-states').exists())
                    # GC must also keep it when BUILD_STATE is no longer set.
                    with patch.dict(os.environ, {'BUILD_STATE': ''}):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(gc.collect(cache, cache / 'gone'), [])
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
                    self.assertEqual(gc.collect(cache, cache / 'checkout'), [])
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
                self.assertEqual(gc.collect(cache, checkout, True), [orphan])
                self.assertTrue(orphan.exists())
                self.assertEqual(gc.collect(cache, checkout), [orphan])
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
                self.assertTrue(resources.agent_markers(env))
        humans = {
            'plain shell': {'HOME': '/home/u', 'PATH': '/usr/bin'},
            # Regression: T3's integrated terminal is a human, not an agent.
            'T3 integrated terminal': {'T3CODE_PROJECT_ROOT': '/w', 'T3CODE_HOME': '/h', 'AGENT_HEAVY_JOBS': 'deny'},
            'harness config in a shell profile': {'CODEX_HOME': '/c', 'OPENCODE_CONFIG': '/o'},
            'empty markers': {'CLAUDECODE': '', 'T3CODE_THREAD_ID': ''},
        }
        for who, env in humans.items():
            with self.subTest(human=who):
                self.assertEqual(resources.agent_markers(env), [])

    def test_heavy_guard_denies_only_agents(self):
        with self.assertRaises(SystemExit):
            resources.heavy_guard({'AGENT_HEAVY_JOBS': 'deny', 'T3CODE_THREAD_ID': 'thread-1'})
        resources.heavy_guard({'AGENT_HEAVY_JOBS': 'deny', 'T3CODE_PROJECT_ROOT': '/w'})
        resources.heavy_guard({'T3CODE_THREAD_ID': 'thread-1'})

    def test_space_needs_add_up_per_filesystem(self):
        with tempfile.TemporaryDirectory() as temp:
            free = resources.shutil.disk_usage(temp).free
            # Each need fits alone; together on one filesystem they do not.
            share = int(free * 0.6)
            self.assertEqual(resources.space_shortfalls([(Path(temp) / 'a', share, 'a')]), [])
            shortfalls = resources.space_shortfalls(
                [(Path(temp) / 'a', share, 'a'), (Path(temp) / 'b/not-created', share, 'b')]
            )
            self.assertEqual(len(shortfalls), 1)
            _, need, free, purposes = shortfalls[0]
            self.assertGreater(need, free)
            self.assertEqual(sorted(purposes), ['a', 'b'])

    def test_earlier_builds_reduce_space_needed(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            state = Path(temp) / 'state'
            fresh = resources.build_space_needed(state)
            state.mkdir()
            (state / 'builder.qcow2').write_bytes(os.urandom(4 * 1024**2))
            downloads = workspace.downloads_root()
            downloads.mkdir()
            (downloads / 'pkg.tar.zst').write_bytes(os.urandom(4 * 1024**2))
            self.assertLessEqual(resources.build_space_needed(state), fresh - 8 * 1024**2)
            with patch.object(resources, 'BUILD_SPACE', 1024), patch.object(resources, 'DOWNLOADS_SPACE', 1024):
                self.assertEqual(resources.build_space_needed(state), 0)

    def meminfo(self, gib_available):
        kib = int(gib_available * 1024**2)
        return f'MemTotal:       16384000 kB\nMemAvailable:    {kib} kB\n'

    def test_builder_memory_fits_humans_and_waits_for_agents(self):
        real_read_text = Path.read_text

        def fake_read_text(path_self, *args, **kwargs):
            if str(path_self) == '/proc/meminfo':
                return self.current_meminfo
            return real_read_text(path_self, *args, **kwargs)

        with patch.object(Path, 'read_text', fake_read_text):
            self.current_meminfo = self.meminfo(24)
            self.assertEqual(resources.builder_memory({}), (resources.BUILDER_MIB, None))
            self.current_meminfo = self.meminfo(6)
            mib, warning = resources.builder_memory({})
            self.assertLess(mib, resources.BUILDER_MIB)
            self.assertLessEqual(mib + resources.MEMORY_HEADROOM_MIB, 6 * 1024)
            self.assertTrue(warning)
            self.current_meminfo = self.meminfo(3)
            mib, warning = resources.builder_memory({})
            self.assertEqual(mib, resources.BUILDER_MIN_MIB)
            self.assertTrue(warning)
        # Agents wait for memory and get the full builder without a warning.
        # Fake abundant memory at the stdlib boundary so the real wait returns at once.
        import tools.vm.control as control

        def fake_control_read_text(path_self, *args, **kwargs):
            if str(path_self) == '/proc/meminfo':
                return self.meminfo(24)
            return real_read_text(path_self, *args, **kwargs)

        with (
            patch.object(Path, 'read_text', fake_control_read_text),
            patch.object(control.subprocess, 'run', return_value=None),
            patch.object(control.time, 'sleep'),
        ):
            self.assertEqual(resources.builder_memory({'CLAUDECODE': '1'}), (resources.BUILDER_MIB, None))

    def test_builder_memory_command_prints_only_the_size(self):
        # build-iso captures stdout as QEMU's -m value; warnings must go to stderr.
        env = {key: value for key, value in os.environ.items() if key not in resources.AGENT_MARKERS}
        result = subprocess.run(
            [sys.executable, '-m', 'tools.workspace', 'builder-memory'],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertGreaterEqual(int(result.stdout), resources.BUILDER_MIN_MIB)

    def test_guards(self):
        # Exercise the real filesystem without filling it or mocking commands.
        resources.disk_guard(Path.cwd(), minimum=0)
        with self.assertRaises(SystemExit):
            resources.disk_guard(Path.cwd() / 'not-created', minimum=2**100)


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
        self.assertFalse(gc._status_entry_outside_out(' M out/report.txt'))
        self.assertFalse(gc._status_entry_outside_out('?? out/new-dir/file'))
        self.assertFalse(gc._status_entry_outside_out('R  out/a -> out/b'))
        self.assertTrue(gc._status_entry_outside_out(' M src/foo.py'))
        self.assertTrue(gc._status_entry_outside_out('?? notes.txt'))
        self.assertTrue(gc._status_entry_outside_out('R  old.txt -> out/moved.txt'))
        self.assertTrue(gc._status_entry_outside_out('R  out/a -> src/b'))

    def test_status_outside_out_with_real_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            init_repo(root)
            (root / 'out').mkdir()
            (root / 'out' / 'ignored.txt').write_text('x')
            self.assertEqual(gc.worktree_status_outside_out(root), [])
            (root / 'tracked.txt').write_text('dirty\n')
            self.assertEqual(gc.worktree_status_outside_out(root), [' M tracked.txt'])
            git_run('checkout', '--', 'tracked.txt', cwd=root)
            (root / 'new.txt').write_text('untracked\n')
            self.assertEqual(gc.worktree_status_outside_out(root), ['?? new.txt'])

    def test_registered_custom_evidence_protects_busy_checkout_and_orphan(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp, 'BUILD_STATE': ''}):
            cache = Path(temp)
            owner = cache / 'checkout'
            owner.mkdir()
            evidence = cache / 'custom'
            evidence.mkdir()
            workspace.own(evidence, owner)
            sockets = evidence / '.sockets'
            sockets.mkdir()
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(sockets / 'qmp.sock'))
                server.listen()
                with self.subTest(check='busy checkout'):
                    self.assertTrue(resources.worktree_busy(owner)[0])
                owner.rmdir()
                with self.subTest(check='orphan evidence'):
                    self.assertEqual(gc.collect(cache, cache / 'runner'), [])
                    self.assertTrue(evidence.exists())
            self.assertEqual(gc.collect(cache, cache / 'runner'), [evidence])

    def test_merge_proof_must_cover_current_tip(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as bindir:
            root = init_repo(Path(temp))
            git_run('branch', 'topic', cwd=root)
            old_head = git_run('rev-parse', 'HEAD', cwd=root).stdout.strip()
            git_run('update-ref', 'refs/remotes/origin/main', old_head, cwd=root)
            gh = Path(bindir) / 'gh'
            gh.write_text(
                f"#!/bin/sh\nprintf '%s' '{json.dumps([{'number': 7, 'title': 'Old PR', 'headRefOid': old_head}])}'\n"
            )
            gh.chmod(0o755)
            with patch.dict(os.environ, {'PATH': bindir + os.pathsep + os.environ['PATH']}):
                self.assertTrue(gc.merge_info('topic', root)[0])
                git_run('checkout', 'topic', cwd=root)
                (root / 'tracked.txt').write_text('new work')
                git_run('commit', '-am', 'unmerged work', cwd=root)
                self.assertFalse(gc.merge_info('topic', root)[0])
            failing = Path(bindir) / 'gh'
            failing.write_text('#!/bin/sh\nexit 1\n')
            failing.chmod(0o755)
            with patch.dict(os.environ, {'PATH': bindir + os.pathsep + os.environ['PATH']}):
                self.assertFalse(gc.merge_info('topic', root)[0])
                git_run('update-ref', 'refs/remotes/origin/main', 'HEAD', cwd=root)
                self.assertTrue(gc.merge_info('topic', root)[0])

    def test_merge_unknowns_never_authorize_removal(self):
        import shutil

        real_git = shutil.which('git')
        cases = [
            (0, [], 0, False),
            (0, [{'number': 7}], 0, None),
            (0, [{'headRefOid': 'unavailable'}], 128, None),
            (1, [], 128, None),
        ]
        for gh_code, records, git_code, expected in cases:
            with self.subTest(gh_code=gh_code, records=records, git_code=git_code):
                with tempfile.TemporaryDirectory() as bindir:
                    gh = Path(bindir) / 'gh'
                    if gh_code == 0:
                        gh.write_text("#!/bin/sh\nprintf '%s' '" + json.dumps(records).replace("'", "'\"'\"'") + "'\n")
                    else:
                        gh.write_text('#!/bin/sh\nexit 1\n')
                    gh.chmod(0o755)
                    git = Path(bindir) / 'git'
                    # Fake only merge-base; delegate everything else to the real git.
                    git.write_text(
                        '#!/bin/sh\n'
                        'if [ "$1" = "-C" ]; then shift 2; fi\n'
                        'if [ "$1" = "merge-base" ]; then exit ' + str(git_code) + '; fi\n'
                        f'exec {real_git} "$@"\n'
                    )
                    git.chmod(0o755)
                    with patch.dict(os.environ, {'PATH': bindir + os.pathsep + os.environ['PATH']}):
                        self.assertIs(gc.merge_info('topic')[0], expected)

    def test_gc_keeps_parent_of_running_registered_evidence(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}):
            cache = Path(temp)
            state = cache / 'arch-niri-dux-build-test'
            state.mkdir()
            workspace.own(state, cache / 'gone')
            evidence = state / 'custom'
            evidence.mkdir()
            workspace.own(evidence, cache / 'gone')
            (evidence / '.sockets').mkdir()
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(evidence / '.sockets/qmp.sock'))
                server.listen()
                self.assertEqual(gc.collect(cache, cache / 'runner'), [])
                self.assertTrue(evidence.exists())

    def test_plan_splits_removals_and_skips(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            tempfile.TemporaryDirectory() as bindir,
            patch.dict(os.environ, {'XDG_CACHE_HOME': temp}),
        ):
            root = Path(temp) / 'repo'
            root.mkdir()
            init_repo(root)
            main_sha = git_run('rev-parse', 'HEAD', cwd=root).stdout.strip()
            git_run('update-ref', 'refs/remotes/origin/main', main_sha, cwd=root)
            for branch in ('merged-clean', 'merged-dirty', 'mystery'):
                git_run('branch', branch, cwd=root)
            git_run('branch', 'unmerged', cwd=root)
            git_run('checkout', 'unmerged', cwd=root)
            (root / 'tracked.txt').write_text('unmerged work\n')
            git_run('commit', '-am', 'unmerged work', cwd=root)
            git_run('checkout', 'main', cwd=root)
            clean_wt = Path(temp) / 'wt-clean'
            dirty_wt = Path(temp) / 'wt-dirty'
            unmerged_wt = Path(temp) / 'wt-unmerged'
            mystery_wt = Path(temp) / 'wt-mystery'
            git_run('worktree', 'add', str(clean_wt), 'merged-clean', cwd=root)
            git_run('worktree', 'add', str(dirty_wt), 'merged-dirty', cwd=root)
            git_run('worktree', 'add', str(unmerged_wt), 'unmerged', cwd=root)
            git_run('worktree', 'add', str(mystery_wt), 'mystery', cwd=root)
            detached_wt = Path(temp) / 'wt-detached'
            git_run('worktree', 'add', '--detach', str(detached_wt), cwd=root)
            (dirty_wt / 'tracked.txt').write_text('dirty\n')
            gh = Path(bindir) / 'gh'
            gh.write_text(
                '#!/bin/sh\n'
                'branch=""; prev=""\n'
                'for arg in "$@"; do if [ "$prev" = "--head" ]; then branch="$arg"; fi; prev="$arg"; done\n'
                'case "$branch" in\n'
                f"  merged-clean|merged-dirty) printf '%s' '{json.dumps([{'number': 11, 'title': 'Merged work', 'headRefOid': main_sha}])}';;\n"
                "  mystery) printf '%s' '[{\"number\": 7}]';;\n"
                "  *) printf '%s' '[]';;\n"
                'esac\n'
            )
            gh.chmod(0o755)
            with patch.dict(os.environ, {'PATH': bindir + os.pathsep + os.environ['PATH']}):
                removals, skipped, move = gc.plan_worktree_gc(root)
            self.assertEqual([item['branch'] for item in removals], ['merged-clean'])
            reasons = {item['branch'] or 'detached': item['reason'] for item in skipped}
            self.assertIn('current checkout', reasons['main'])
            self.assertIn('dirty', reasons['merged-dirty'])
            self.assertIn('not merged', reasons['unmerged'])
            self.assertIn("can't tell", reasons['mystery'])
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
            self.assertEqual((fixtures.accepted_fixtures()[0] / 'disk.qcow2'), new / 'disk.qcow2')
            target = kept_owner / 'out' / 'verify' / new.name
            moved = fixtures.move_fixture(new, target, kept_owner)
            self.assertFalse(new.exists())
            self.assertEqual((moved / 'disk.qcow2').read_bytes(), b'new-bytes')
            self.assertTrue((moved / 'ACCEPTANCE-PASSED').is_file())
            picked = fixtures.accepted_fixtures()[0] / 'disk.qcow2'
            self.assertEqual(picked, moved / 'disk.qcow2')
            self.assertEqual(picked.read_bytes(), b'new-bytes')

    def test_dry_run_changes_nothing_then_real_run_preserves_fixture(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            tempfile.TemporaryDirectory() as bindir,
            patch.dict(os.environ, {'XDG_CACHE_HOME': temp}),
        ):
            cache = Path(temp)
            root = cache / 'repo'
            root.mkdir()
            init_repo(root)
            git_run('branch', 'merged-clean', cwd=root)
            main_sha = git_run('rev-parse', 'HEAD', cwd=root).stdout.strip()
            git_run('update-ref', 'refs/remotes/origin/main', main_sha, cwd=root)
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
            before = fixtures.accepted_fixtures()[0] / 'disk.qcow2'
            self.assertEqual(before, fixture / 'disk.qcow2')

            gh = Path(bindir) / 'gh'
            gh.write_text('#!/bin/sh\nexit 1\n')
            gh.chmod(0o755)
            with patch.dict(os.environ, {'PATH': bindir + os.pathsep + os.environ['PATH']}):
                with contextlib.redirect_stdout(io.StringIO()):
                    _removals, _skipped, move = gc.gc_worktrees(root, True)
                self.assertTrue(doomed.exists())
                self.assertTrue(fixture.exists())
                self.assertEqual((fixtures.accepted_fixtures()[0] / 'disk.qcow2'), before)
                self.assertIsNotNone(move)
                with contextlib.redirect_stdout(io.StringIO()):
                    gc.gc_worktrees(root, False)
            self.assertFalse(doomed.exists())
            picked = fixtures.accepted_fixtures()[0] / 'disk.qcow2'
            self.assertIsNotNone(picked)
            self.assertTrue(picked.is_file())
            self.assertEqual(picked.read_bytes(), b'fixture-bytes')
            self.assertTrue((picked.parent / 'ACCEPTANCE-PASSED').is_file())

    def test_shared_alongside_home_survives_merged_worktree_gc(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            tempfile.TemporaryDirectory() as bindir,
            patch.dict(os.environ, {'XDG_CACHE_HOME': temp}),
        ):
            cache = Path(temp)
            root = cache / 'repo'
            root.mkdir()
            init_repo(root)
            git_run('branch', 'merged-clean', cwd=root)
            main_sha = git_run('rev-parse', 'HEAD', cwd=root).stdout.strip()
            git_run('update-ref', 'refs/remotes/origin/main', main_sha, cwd=root)
            doomed = cache / 'wt-doomed'
            git_run('worktree', 'add', str(doomed), 'merged-clean', cwd=root)
            # Shared alongside home outside every worktree (the documented default).
            shared = Path(temp) / 'shared' / 'alongside' / 'linux-fixture'
            shared.mkdir(parents=True)
            (shared / 'disk.qcow2').write_bytes(b'shared-fixture-bytes')
            (shared / 'READY').write_text('Official Debian 13 GPT/UEFI fixture\n')
            # The merged worktree references it by symlink and by path in evidence.
            link_parent = doomed / 'out' / 'alongside'
            link_parent.mkdir(parents=True)
            (link_parent / 'linux-fixture').symlink_to(shared, target_is_directory=True)
            evidence = link_parent / 'linux-test'
            evidence.mkdir()
            (evidence / 'inputs.json').write_text(json.dumps({'fixture': str(shared / 'disk.qcow2')}))
            # A real alongside fixture dir inside the worktree has no verify-fixture protection.
            inner = link_parent / 'windows'
            inner.mkdir()
            (inner / 'disk.qcow2').write_bytes(b'inner-fixture-bytes')
            (inner / 'READY').write_text('Real Windows 11 Enterprise LTSC evaluation\n')

            gh = Path(bindir) / 'gh'
            gh.write_text('#!/bin/sh\nexit 1\n')
            gh.chmod(0o755)
            with patch.dict(os.environ, {'PATH': bindir + os.pathsep + os.environ['PATH']}):
                removals, _skipped, move = gc.plan_worktree_gc(root)
                # The worktree holding a real alongside dir is reported as a removal ...
                self.assertEqual([item['branch'] for item in removals], ['merged-clean'])
                # ... and gc offers it no verify-fixture move (alongside uses READY).
                self.assertIsNone(move)
                # The shared home outside every worktree is never in the plan.
                resolved_shared = shared.resolve()
                for item in removals:
                    self.assertNotEqual(item['resolved'], resolved_shared)
                    self.assertNotIn(item['resolved'], resolved_shared.parents)
                if move is not None:
                    self.assertNotEqual(move['src'], resolved_shared)
                    self.assertNotEqual(move['dst'], resolved_shared)
                with contextlib.redirect_stdout(io.StringIO()) as stream:
                    gc.gc_worktrees(root, True)
                    output = stream.getvalue()
                self.assertIn(str(doomed), output)
                self.assertNotIn(str(resolved_shared), output)
                self.assertTrue(doomed.exists())
                self.assertTrue(shared.exists())
                with contextlib.redirect_stdout(io.StringIO()):
                    gc.gc_worktrees(root, False)
            self.assertFalse(doomed.exists())
            self.assertTrue((shared / 'disk.qcow2').is_file())
            self.assertTrue((shared / 'READY').is_file())


if __name__ == '__main__':
    unittest.main()
