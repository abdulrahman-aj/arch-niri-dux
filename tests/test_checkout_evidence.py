"""Verification evidence retains checkout identity in every scenario."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.verification.evidence import Evidence
from tools.verification.scenarios import full, fixture, release, alongside
from tools.workspace.source import checkout_state
from tools.workspace.state import ROOT


class CheckoutEvidenceTests(unittest.TestCase):
    def test_clean_dirty_and_ignored_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args])

            git('init', '-q')
            (root / '.gitignore').write_text('out/\n')
            (root / 'tracked').write_bytes(b'original\x00bytes')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Initial')
            clean = {'commit': git('rev-parse', 'HEAD').decode().strip(), 'dirty': False}
            self.assertEqual(checkout_state(root), clean)
            (root / 'out').mkdir()
            (root / 'out/log').touch()
            self.assertEqual(checkout_state(root), clean)
            (root / 'tracked').write_bytes(b'changed\x00bytes')
            expected = hashlib.sha256(git('diff', 'HEAD', '--binary')).hexdigest()
            self.assertEqual(checkout_state(root), clean | {'dirty': True, 'diff_sha256': expected})
            git('add', 'tracked')
            self.assertEqual(checkout_state(root)['diff_sha256'], expected)
            git('reset', '--hard', 'HEAD')
            (root / 'untracked').touch()
            self.assertEqual(
                checkout_state(root), clean | {'dirty': True, 'diff_sha256': hashlib.sha256(b'').hexdigest()}
            )

    def test_every_scenario_writes_checkout_record(self):
        class Recorded(Exception):
            pass

        write_inputs = Evidence.inputs
        real_run = subprocess.run

        def record(evidence, values):
            write_inputs(evidence, values)
            raise Recorded

        def run(command, **kwargs):
            if command[0] == 'qemu-img':
                return subprocess.CompletedProcess(command, 0)
            if command[0] == 'bsdtar':
                return subprocess.CompletedProcess(command, 0, stdout=manifest)
            return real_run(command, **kwargs)

        def prepare_source(root, source, epoch):
            (source / 'integration.tar').write_bytes(b'fixture source')

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(
                [
                    'git',
                    '-C',
                    str(root),
                    '-c',
                    'user.name=Test',
                    '-c',
                    'user.email=test@example.com',
                    'commit',
                    '-qm',
                    'Initial',
                    '--allow-empty',
                ],
                check=True,
            )
            (root / 'tools').symlink_to(ROOT / 'tools', target_is_directory=True)
            (root / 'out').mkdir()
            iso = root / 'test.iso'
            iso.touch()
            manifest = b'{"integration":{"version":"0.0.1-1"}}'
            (root / 'out/packages.manifest.json').write_bytes(manifest)
            accepted = root / 'accepted'
            accepted.mkdir()
            for name in ('ACCEPTANCE-PASSED', 'disk.qcow2', 'OVMF_VARS.fd', 'install.json'):
                (accepted / name).touch()
            (accepted / 'inputs.json').write_text('{}')
            for scenario in (full, fixture, release, alongside):
                with self.subTest(scenario=scenario.__name__):
                    directory = root / scenario.__name__.rsplit('.', 1)[-1]
                    args = argparse.Namespace(
                        directory=directory,
                        iso=iso,
                        fixture=accepted / 'disk.qcow2',
                        firmware=accepted / 'OVMF_VARS.fd',
                        kernel_package=None,
                        swap_gib=None,
                        keymap='us',
                        keep=False,
                        disk=None,
                        install_only=False,
                        slow=False,
                        repository_url=None,
                        tag='v0.0.1-1',
                        os='linux',
                    )
                    if scenario is fixture:
                        args.iso = None
                    with (
                        patch.object(scenario, 'ROOT', root, create=True),
                        patch.object(scenario, 'disk_guard'),
                        patch.object(scenario, 'own'),
                        patch('os.access', return_value=True),
                        patch('subprocess.run', side_effect=run),
                        patch('tools.packages.integration.prepare_source', side_effect=prepare_source),
                        patch.object(Evidence, 'inputs', autospec=True, side_effect=record),
                    ):
                        with self.assertRaises(Recorded):
                            getattr(scenario, 'run_' + scenario.__name__.rsplit('.', 1)[-1])(args, None)
                    data = json.loads((directory / 'inputs.json').read_text())
                    self.assertEqual(len(data['checkout']['commit']), 40)
                    self.assertIsInstance(data['checkout']['dirty'], bool)
                    if data['checkout']['dirty']:
                        self.assertEqual(len(data['checkout']['diff_sha256']), 64)
