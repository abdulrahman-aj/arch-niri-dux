"""Qualification tiers, dirty-tree refusal, fail-fast runs and evidence records."""

import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.dev import qualify


class TierSelectionTests(unittest.TestCase):
    def test_representative_files_pick_their_tier(self):
        cases = (
            (['README.md'], 'docs', ['lint']),
            (['.github/workflows/dux-apps.yml'], 'docs', ['lint']),
            (['tools/dev/try_vm.py'], 'scripts', ['lint', 'test']),
            (['tests/test_try_vm.py'], 'scripts', ['lint', 'test']),
            (['Makefile'], 'scripts', ['lint', 'test']),
            (['desktop/usr/share/x'], 'desktop', ['lint', 'test', 'verify-fast']),
            (['packages/archive-date'], 'desktop', ['lint', 'test', 'verify-fast']),
            (['tools/verification/stages.py'], 'probe', ['lint', 'test', 'verify']),
            (['tools/vm/control.py'], 'probe', ['lint', 'test', 'verify']),
            (['archiso/profiledef'], 'installer', ['lint', 'test', 'iso-development', 'verify']),
            (['installer/install.py'], 'installer', ['lint', 'test', 'iso-development', 'verify']),
            (['tools/build/host.sh'], 'installer', ['lint', 'test', 'iso-development', 'verify']),
            (['packages/RELEASING.md'], 'installer', ['lint', 'test', 'iso-development', 'verify']),
        )
        for files, tier, steps in cases:
            with self.subTest(files=files):
                picked, _, planned = qualify.decide(files)
                self.assertEqual(picked, tier)
                self.assertEqual(planned, steps)

    def test_highest_tier_wins_and_empty_defaults_to_scripts(self):
        picked, _, _ = qualify.decide(['README.md', 'tests/test_x.py'])
        self.assertEqual(picked, 'scripts')
        picked, _, _ = qualify.decide(['tools/dev/x.py', 'tools/verification/y.py', 'installer/z.py'])
        self.assertEqual(picked, 'installer')
        picked, _, planned = qualify.decide([])
        self.assertEqual((picked, planned), ('scripts', ['lint', 'test']))

    def test_tier_override_picks_the_base_tier(self):
        picked, _, planned = qualify.decide(['installer/install.py'], tier_override='docs')
        self.assertEqual((picked, planned), ('docs', ['lint']))

    def test_override_keeps_the_alongside_rule(self):
        picked, reason, planned = qualify.decide(['installer/storage.py'], tier_override='docs')
        self.assertEqual(picked, 'installer')
        self.assertEqual(planned[-2:], ['alongside-linux', 'alongside-windows'])
        self.assertIn('alongside', reason)

    def test_alongside_trigger_adds_debian_then_windows_runs(self):
        for files in (
            ['installer/storage.py'],
            ['tools/verification/scenarios/alongside.py'],
            ['tests/test_alongside.py'],
            ['tools/fixtures/provision.py'],
        ):
            with self.subTest(files=files):
                picked, reason, planned = qualify.decide(files)
                self.assertEqual(picked, 'installer')
                self.assertEqual(planned[-2:], ['alongside-linux', 'alongside-windows'])
                self.assertIn('alongside', reason)
        picked, _, planned = qualify.decide(['installer/install.py'])
        self.assertNotIn('alongside-linux', planned)
        self.assertFalse(qualify.needs_alongside(['README.md', 'tools/dev/try_vm.py']))


class CommandTests(unittest.TestCase):
    def test_verify_fast_and_forwarded_args(self):
        evidence = Path('out/qualify/abc')
        argv = qualify.command_for('verify-fast', evidence, [])
        self.assertEqual(argv, ['make', 'verify-fast', f'ARGS=--directory {evidence}/verify-fast'])
        argv = qualify.command_for('verify', evidence, ['--slow'])
        self.assertEqual(argv, ['make', 'verify', f'ARGS=--slow --directory {evidence}/verify'])
        argv = qualify.command_for('verify', evidence, ['--directory', 'custom'])
        self.assertEqual(argv, ['make', 'verify', 'ARGS=--directory custom'])

    def test_alongside_argv_matches_the_documented_invocation(self):
        evidence = Path('out/qualify/abc')
        fixtures = {
            'linux fixture': Path('/fx/linux/disk.qcow2'),
            'linux firmware': Path('/fx/linux/OVMF_VARS.fd'),
            'windows fixture': Path('/fx/windows/disk.qcow2'),
            'windows firmware': Path('/fx/windows/OVMF_VARS.fd'),
            'windows report token': Path('/fx/windows/report-token'),
        }
        argv = qualify.command_for('alongside-linux', evidence, [], iso=Path('/out/x.iso'), fixtures=fixtures)
        self.assertEqual(argv[0:2], ['make', 'verify-alongside'])
        self.assertEqual(
            shlex.split(argv[2].removeprefix('ARGS=')),
            [
                '--os',
                'linux',
                '--iso',
                '/out/x.iso',
                '--fixture',
                '/fx/linux/disk.qcow2',
                '--firmware',
                '/fx/linux/OVMF_VARS.fd',
                '--directory',
                f'{evidence}/alongside-linux',
            ],
        )
        argv = qualify.command_for('alongside-windows', evidence, [], iso=Path('/out/x.iso'), fixtures=fixtures)
        args = shlex.split(argv[2].removeprefix('ARGS='))
        self.assertEqual(args[0:2], ['--os', 'windows'])
        self.assertIn('--windows-token', args)
        self.assertEqual(args[args.index('--windows-token') + 1], '/fx/windows/report-token')

    def test_dry_run_never_prints_a_stale_iso(self):
        evidence = Path('out/qualify/abc')
        fixtures = {
            'linux fixture': Path('/fx/linux/disk.qcow2'),
            'linux firmware': Path('/fx/linux/OVMF_VARS.fd'),
        }
        with tempfile.TemporaryDirectory() as root:
            stale = Path(root) / 'out'
            stale.mkdir(parents=True)
            (stale / 'old.iso').write_bytes(b'stale')
            with patch.object(qualify, 'ROOT', Path(root)):
                plan = qualify.format_plan(
                    'abc', 'installer', 'reason', ['iso-development', 'alongside-linux'], evidence, [], fixtures
                )
        self.assertIn('<ISO built by iso-development above>', plan)
        self.assertNotIn('old.iso', plan)

    def test_git_failures_name_the_fix(self):
        with tempfile.TemporaryDirectory() as root, patch.object(qualify, 'ROOT', Path(root)):
            with self.assertRaisesRegex(SystemExit, 'git fetch origin'):
                qualify.changed_files()

    def test_rerun_over_evidence_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            evidence = Path(root) / 'out'
            qualify.prepare_evidence_dir(evidence)
            self.assertTrue(evidence.is_dir())
            (evidence / 'lint.log').write_text('previous attempt\n')
            with self.assertRaisesRegex(SystemExit, 'remove it to requalify'):
                qualify.prepare_evidence_dir(evidence)


class DirtyTreeTests(unittest.TestCase):
    def test_uncommitted_and_untracked_files_refuse(self):
        qualify.check_clean('')
        with self.assertRaises(SystemExit):
            qualify.check_clean(' M installer/storage.py\n')
        with self.assertRaises(SystemExit):
            qualify.check_clean('?? out/scratch\n')

    def test_changed_files_come_from_one_commit(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            subprocess.run(['git', 'init', '-b', 'main'], check=True, capture_output=True, cwd=directory)
            subprocess.run(['git', 'config', 'user.email', 't@t'], check=True, capture_output=True, cwd=directory)
            subprocess.run(['git', 'config', 'user.name', 't'], check=True, capture_output=True, cwd=directory)
            (directory / 'base.txt').write_text('base\n')
            subprocess.run(['git', 'add', '.'], check=True, capture_output=True, cwd=directory)
            subprocess.run(['git', 'commit', '-m', 'base'], check=True, capture_output=True, cwd=directory)
            subprocess.run(['git', 'checkout', '-b', 'feature'], check=True, capture_output=True, cwd=directory)
            (directory / 'tools').mkdir()
            (directory / 'tools' / 'new.py').write_text('new\n')
            subprocess.run(['git', 'add', '.'], check=True, capture_output=True, cwd=directory)
            subprocess.run(['git', 'commit', '-m', 'feature'], check=True, capture_output=True, cwd=directory)
            with patch.object(qualify, 'ROOT', directory):
                self.assertEqual(qualify.changed_files('main'), ['tools/new.py'])
                qualify.check_clean(qualify.git('status', '--porcelain'))
                (directory / 'tools' / 'new.py').write_text('dirty\n')
                with self.assertRaises(SystemExit):
                    qualify.check_clean(qualify.git('status', '--porcelain'))


class FailFastTests(unittest.TestCase):
    def test_first_failure_stops_later_steps(self):
        calls = []

        def runner(argv, log):
            calls.append(argv[1])
            Path(log).write_text('fake log\n')
            return {'lint': 0, 'test': 3}.get(argv[1], 0)

        with tempfile.TemporaryDirectory() as root:
            results = qualify.execute(
                ['lint', 'test', 'verify'], Path(root), [], None, runner=runner, resolve_iso=lambda: None
            )
        self.assertEqual(calls, ['lint', 'test'])
        self.assertEqual([(result.step, result.code) for result in results], [('lint', 0), ('test', 3)])
        self.assertTrue(all(result.seconds >= 0 for result in results))

    def test_missing_fixtures_refuse_with_their_paths(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root) / 'alongside'
            (home / 'linux-fixture').mkdir(parents=True)
            (home / 'linux-fixture' / 'disk.qcow2').write_bytes(b'x')
            with self.assertRaises(SystemExit) as failure:
                qualify.check_fixtures(home)
            message = str(failure.exception)
            self.assertIn(str(home), message)
            self.assertIn('windows fixture', message)
        if qualify.ALONGSIDE_HOME.is_dir():
            wanted = qualify.check_fixtures()
            self.assertTrue(all(Path(path).is_file() for path in wanted.values()))


class EvidenceTests(unittest.TestCase):
    def test_evidence_records_sha_and_exit_codes(self):
        sha = 'f' * 40
        with tempfile.TemporaryDirectory() as root:
            evidence = Path(root)
            results = [
                qualify.StepResult('lint', ['make', 'lint'], 0, 4.0),
                qualify.StepResult('test', ['make', 'test'], 2, 9.0),
            ]
            qualify.write_evidence(evidence, sha, 'scripts', 'tests/test_x.py needs the scripts tier', results)
            text = (evidence / 'EVIDENCE.md').read_text()
        self.assertIn(sha, re.search(r'Commit: ([0-9a-f]{40})', text).group(1))
        codes = dict(re.findall(r'`([^`]+)` → exit (\d+)', text))
        self.assertEqual(codes, {'make lint': '0', 'make test': '2'})
        self.assertIn('FAIL at test (exit 2)', text)
        self.assertIn(str(evidence), text)

    def test_evidence_marks_verify_and_alongside_markers(self):
        sha = 'a' * 40
        with tempfile.TemporaryDirectory() as root:
            evidence = Path(root)
            (evidence / 'verify').mkdir()
            (evidence / 'verify' / 'ACCEPTANCE-PASSED').write_text('ok\n')
            (evidence / 'alongside-linux').mkdir()
            results = [
                qualify.StepResult('verify', ['make', 'verify'], 0, 1.0),
                qualify.StepResult('alongside-linux', ['make', 'verify-alongside'], 1, 2.0),
            ]
            qualify.write_evidence(evidence, sha, 'installer', 'alongside reason', results)
            text = (evidence / 'EVIDENCE.md').read_text()
        self.assertIn('ACCEPTANCE-PASSED present', text)
        self.assertIn('SUCCESS missing', text)


if __name__ == '__main__':
    unittest.main()
