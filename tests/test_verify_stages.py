"""Unit tests for verify.py continue-past-failures."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from verify import run_stages


class FakeProcess:
    def poll(self):
        return None


class DeadProcess:
    def poll(self):
        return 0


class StubVM:
    def __init__(self, alive=True):
        self.alive = alive
        self.process = FakeProcess() if alive else DeadProcess()

    def command(self, script, timeout=120):
        if not self.alive:
            raise RuntimeError('guest dead')
        return 'ok'

    def stop(self):
        pass


class RunStagesTests(unittest.TestCase):
    def test_continues_past_independent_failure(self):
        def fail(_vm, _passed):
            raise RuntimeError('boom')

        ran = []
        stages = [('a', lambda _vm, _p: ran.append('a')), ('b', fail), ('c', lambda _vm, _p: ran.append('c'))]
        failures, skipped = run_stages(StubVM(), lambda m: None, stages)
        self.assertEqual(list(failures), ['b'])
        self.assertEqual(skipped, [])
        self.assertEqual(ran, ['a', 'c'])

    def test_boot_failure_stops_run(self):
        def fail(_vm, _passed):
            raise RuntimeError('boom')

        stages = [('boot', fail), ('desktop', lambda _vm, _p: None)]
        failures, skipped = run_stages(StubVM(), lambda m: None, stages)
        self.assertEqual(list(failures), ['boot'])
        self.assertEqual(skipped, ['desktop'])

    def test_desktop_failure_stops_run(self):
        def fail(_vm, _passed):
            raise RuntimeError('boom')

        ran = []
        stages = [('boot', lambda _vm, _p: ran.append('boot')), ('desktop', fail), ('apps', lambda _vm, _p: None)]
        failures, skipped = run_stages(StubVM(), lambda m: None, stages)
        self.assertEqual(list(failures), ['desktop'])
        self.assertEqual(skipped, ['apps'])
        self.assertEqual(ran, ['boot'])


if __name__ == '__main__':
    unittest.main()
