"""Evidence identifies host edits independently of unrelated checkout changes."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.verification.evidence import Evidence
from tools.verification.provenance import host_sources, checkout_state
from tools.verification.scenarios import alongside
from tools.workspace.state import ROOT


class ProvenanceTests(unittest.TestCase):
    def test_sources_track_host_and_lazy_dependencies_but_not_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / 'tools', root / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
            before = host_sources(root)
            self.assertEqual(list(before), sorted(before))
            for name in (
                'tools/verification/stages.py',
                'tools/verification/scenarios/alongside.py',
                'tools/vm/control.py',
                'tools/release/iso_checksums.py',
                'tools/fixtures/windows.py',
                'tools/verification/gate-allowlist.txt',
            ):
                with self.subTest(name=name):
                    path = root / name
                    original = path.read_bytes()
                    path.write_bytes(original + b'\n# provenance test\n')
                    after = host_sources(root)
                    self.assertNotEqual(after[name], before[name])
                    self.assertEqual(set(after), set(before))
                    path.write_bytes(original)
            (root / 'unrelated.py').write_text('value = 1\n')
            self.assertEqual(host_sources(root), before)
            # Follow relative and function-local imports without executing them.
            (root / 'tools/verification/extra.py').write_text('def later():\n    from ..extra import dependency\n')
            (root / 'tools/extra').mkdir()
            (root / 'tools/extra/__init__.py').write_text('')
            (root / 'tools/extra/dependency.py').write_text('raise RuntimeError()\n')
            self.assertIn('tools/extra/dependency.py', host_sources(root))

    def test_checkout_commit_and_dirty_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

            git('init', '-q')
            (root / '.gitignore').write_text('out/\n')
            git('add', '.gitignore')
            git(
                '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Initial', '--allow-empty'
            )
            self.assertEqual(checkout_state(root), {'commit': git('rev-parse', 'HEAD'), 'dirty': False})
            (root / 'out').mkdir()
            (root / 'out/build.log').write_text('ignored output')
            self.assertFalse(checkout_state(root)['dirty'])
            (root / 'untracked').touch()
            self.assertTrue(checkout_state(root)['dirty'])
            git('add', 'untracked')
            self.assertTrue(checkout_state(root)['dirty'])

    def test_alongside_records_staged_hashes_before_boot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / 'fixture'
            fixture.write_text('disposable')
            args = argparse.Namespace(
                directory=root / 'evidence', fixture=fixture, firmware=fixture, iso=fixture, os='linux'
            )
            real_run = subprocess.run

            def run(command, **kwargs):
                if command[0] == 'qemu-img':
                    return subprocess.CompletedProcess(command, 0)
                return real_run(command, **kwargs)

            with (
                patch.object(alongside, 'disk_guard'),
                patch.object(alongside, 'own'),
                patch.object(alongside.subprocess, 'run', side_effect=run),
                patch.object(alongside, 'VM') as vm,
            ):
                vm.return_value.start.side_effect = RuntimeError('stop before boot')
                with self.assertRaisesRegex(RuntimeError, 'stop before boot'):
                    alongside.run_alongside(args, None)
            evidence = Evidence(args.directory, exist_ok=True).inputs()
            self.assertIn('tools/verification/probes/alongside.py', evidence['verification_scripts'])
            self.assertIn('tools/fixtures/windows.py', evidence['host_verifier']['sources'])
            self.assertIn('commit', evidence['host_verifier']['checkout'])
