"""Exercise the boundaries most likely to break when moving acceptance code."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.verification import camera, integration, pdf, vm


class ProbeStagingTests(unittest.TestCase):
    def test_staged_probes_import_without_the_checkout_and_match_recorded_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hashes = vm.stage_probes(root)
            for name, expected in hashes.items():
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected)
            subprocess.run([sys.executable, '-c',
                            'from verification import apps, boot, browser, camera, desktop, '
                            'guest, integration, pdf, recovery, vm'],
                           cwd=root, env=os.environ | {'PYTHONPATH': ''}, check=True)


class ScenarioCleanupTests(unittest.TestCase):
    def test_pdf_render_failure_closes_its_preview_without_reporting_success(self):
        with tempfile.TemporaryDirectory() as directory:
            machine = SimpleNamespace(directory=Path(directory))
            actions = []
            machine.probe = lambda *args, **_kwargs: actions.append(args)
            machine.key = lambda *args: actions.append(args)
            machine.chord = lambda *args: actions.append(args)
            milestones = []
            with patch.object(pdf.subprocess, 'check_output', return_value='unrendered'), \
                    patch.object(pdf.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'did not render'):
                    pdf.verify(machine, milestones.append)
            self.assertEqual(actions[-2:], [('esc',), ('pdf', 'files-pdf-close')])
            self.assertEqual(len(milestones), 1)  # Papers passed; the preview did not.

    def test_camera_permission_timeout_stops_the_synthetic_source(self):
        actions = []
        machine = SimpleNamespace(probe=lambda *args, **_kwargs: actions.append(args))
        milestones = []
        with patch.object(camera.time, 'monotonic', side_effect=[0, 61]):
            with self.assertRaisesRegex(TimeoutError, 'camera permission'):
                camera.verify(machine, milestones.append)
        self.assertEqual(actions[-1], ('camera', 'camera-cleanup'))
        self.assertFalse(milestones)


class UpgradeRestorationTests(unittest.TestCase):
    def test_restoration_uses_fixture_paths_and_rejects_each_kind_of_incomplete_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {
                'before': ['arch-niri-dux 7-1', 'arch-niri-dux-baseline 7-1'],
                'restoration': {
                    'files_present': ['/different/retired-file'],
                    'files_absent': ['/different/added-file'],
                    'packages_absent': ['different-fixture-app'],
                },
            }
            (root / 'integration-upgrade.json').write_text(json.dumps(expected))
            restored, added = root / 'different/retired-file', root / 'different/added-file'
            restored.parent.mkdir()
            restored.write_text('restored')
            versions = '\n'.join(expected['before'])
            with patch.object(integration, 'output', return_value=versions), \
                    patch.object(integration.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as query:
                integration.assert_restored(root=root, evidence=root)
                self.assertEqual(query.call_args.args[0], ['pacman', '-Q', 'different-fixture-app'])
                restored.unlink()
                with self.assertRaises(AssertionError):
                    integration.assert_restored(root=root, evidence=root)
                restored.write_text('restored')
                added.touch()
                with self.assertRaises(AssertionError):
                    integration.assert_restored(root=root, evidence=root)
                added.unlink()
                query.return_value = SimpleNamespace(returncode=0)
                with self.assertRaises(AssertionError):
                    integration.assert_restored(root=root, evidence=root)
            with patch.object(integration, 'output', return_value='unexpected versions'):
                with self.assertRaises(AssertionError):
                    integration.assert_restored(root=root, evidence=root)
