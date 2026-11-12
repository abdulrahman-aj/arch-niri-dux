"""Unit tests for verify.py continue-past-failures and the fixture gate."""

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from verify import guest_alive, run_stages


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

        ran = []
        stages = [('boot', fail), ('desktop', lambda _vm, _p: ran.append('desktop'))]
        failures, skipped = run_stages(StubVM(), lambda m: None, stages)
        self.assertEqual(list(failures), ['boot'])
        self.assertEqual(skipped, ['desktop'])
        self.assertEqual(ran, [])

    def test_desktop_failure_stops_run(self):
        def fail(_vm, _passed):
            raise RuntimeError('boom')

        ran = []
        stages = [
            ('boot', lambda _vm, _p: ran.append('boot')),
            ('desktop', fail),
            ('apps', lambda _vm, _p: ran.append('apps')),
        ]
        failures, skipped = run_stages(StubVM(), lambda m: None, stages)
        self.assertEqual(list(failures), ['desktop'])
        self.assertEqual(skipped, ['apps'])
        self.assertEqual(ran, ['boot'])

    def test_guest_death_stops_run(self):
        def fail(vm, _passed):
            vm.alive = False
            raise RuntimeError('boom')

        vm = StubVM()
        vm.process = FakeProcess()
        failures, skipped = run_stages(vm, lambda m: None, [('a', fail), ('b', lambda _v, _p: None)])
        self.assertEqual(list(failures), ['a'])
        self.assertEqual(skipped, ['b'])

    def test_guest_alive_needs_running_process(self):
        self.assertFalse(guest_alive(StubVM(alive=False)))
        vm = StubVM(alive=True)
        vm.process = DeadProcess()
        self.assertFalse(guest_alive(vm))
        self.assertTrue(guest_alive(StubVM(alive=True)))


class FixtureGateTests(unittest.TestCase):
    def test_fixture_without_acceptance_is_refused(self):
        import verify as verify_mod

        with tempfile.TemporaryDirectory() as tmp:
            accepted = Path(tmp)
            disk = accepted / 'disk.qcow2'
            disk.write_bytes(b'fake')
            parser = argparse.ArgumentParser()
            args = argparse.Namespace(
                fixture=str(disk),
                directory=None,
                kernel_package=None,
                keep=False,
                iso=None,
                disk=None,
                install_only=False,
                online_repository=False,
                swap_gib=None,
                slow=False,
            )
            with self.assertRaises(SystemExit):
                verify_mod.run_fixture(args, parser)


if __name__ == '__main__':
    unittest.main()
