"""Timeout evidence observes a live process and preserves the command failure."""

import os
import subprocess
import sys
import unittest

from tools.verification.probes.guest import _run_with_timeout_evidence


class TimeoutEvidenceTests(unittest.TestCase):
    def test_evidence_runs_before_timeout_kills_process(self):
        observed = []

        def collect(pid):
            os.kill(pid, 0)
            observed.append(pid)

        with self.assertRaises(subprocess.TimeoutExpired):
            _run_with_timeout_evidence([sys.executable, '-c', 'import time; time.sleep(60)'], False, 0.05, collect)
        self.assertEqual(len(observed), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(observed[0], 0)

    def test_failed_evidence_still_raises_timeout_and_reaps_process(self):
        observed = []

        def collect(pid):
            observed.append(pid)
            raise OSError('diagnostic collection failed')

        with self.assertRaises(subprocess.TimeoutExpired):
            _run_with_timeout_evidence([sys.executable, '-c', 'import time; time.sleep(60)'], False, 0.05, collect)
        with self.assertRaises(ProcessLookupError):
            os.kill(observed[0], 0)

    def test_success_does_not_collect_evidence(self):
        result = _run_with_timeout_evidence(
            [sys.executable, '-c', 'pass'], False, 5, lambda pid: self.fail('unexpected timeout evidence')
        )
        self.assertEqual(result.returncode, 0)

    def test_command_failure_keeps_its_exit_status(self):
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            _run_with_timeout_evidence(
                [sys.executable, '-c', 'raise SystemExit(3)'],
                False,
                5,
                lambda pid: self.fail('unexpected timeout evidence'),
            )
        self.assertEqual(failure.exception.returncode, 3)
