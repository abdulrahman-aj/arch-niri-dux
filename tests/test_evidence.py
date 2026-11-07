"""Evidence files retain scenario data and protect installation secrets."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verification.evidence import Evidence


class EvidenceTests(unittest.TestCase):
    def test_records_inputs_and_ordered_milestones_with_private_key(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'scenario'
            evidence = Evidence(directory)
            inputs = {'iso': '/tmp/disposable.iso', 'scripts': {'probe': 'abc'}}
            evidence.inputs(inputs)
            self.assertEqual(evidence.inputs(), inputs)
            evidence.key('secret')
            self.assertEqual((directory / 'key').read_text(), 'secret')
            self.assertEqual((directory / 'key').stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            evidence.passed('Installed')
            evidence.passed('Booted')
            self.assertEqual(json.loads((directory / 'milestones.json').read_text()), ['Installed', 'Booted'])

    def test_fresh_workspace_refuses_reuse_without_changing_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'scenario'
            evidence = Evidence(directory)
            evidence.inputs({'fixture': 'accepted'})
            with self.assertRaises(FileExistsError):
                Evidence(directory)
            self.assertEqual(evidence.inputs(), {'fixture': 'accepted'})
