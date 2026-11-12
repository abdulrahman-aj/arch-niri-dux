"""Unit tests for verify.py stage selection and the continue-past-failures runner."""

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from verify import run_stages, select_stages


def args(**overrides):
    base = dict(stages=None, include_tags=None, online_repository=False)
    base.update(overrides)
    return argparse.Namespace(**base)


class SelectStagesTests(unittest.TestCase):
    def test_default_skips_slow_and_online(self):
        names = select_stages(args())
        self.assertNotIn('dux_update', names)
        self.assertNotIn('online', names)
        self.assertEqual(names[0], 'boot')

    def test_release_includes_slow_and_online(self):
        names = select_stages(args(online_repository=True))
        self.assertIn('dux_update', names)
        self.assertEqual(names[-1], 'online')

    def test_explicit_slow_runs_despite_default(self):
        names = select_stages(args(stages='dux_update'))
        self.assertIn('dux_update', names)

    def test_explicit_subset_closes_over_dependencies(self):
        names = select_stages(args(stages='apps,pdf,camera,browser'))
        for dep in ('boot', 'desktop'):
            self.assertIn(dep, names)
        self.assertLess(names.index('desktop'), names.index('apps'))

    def test_all_includes_slow_but_not_online(self):
        names = select_stages(args(stages='all'))
        self.assertIn('dux_update', names)
        self.assertNotIn('online', names)

    def test_all_with_release_appends_online(self):
        names = select_stages(args(stages='all', online_repository=True))
        self.assertIn('dux_update', names)
        self.assertEqual(names[-1], 'online')

    def test_unknown_stage_is_an_error(self):
        with self.assertRaises(ValueError):
            select_stages(args(stages='nope'))

    def test_empty_selection_is_an_error(self):
        with self.assertRaises(ValueError):
            select_stages(args(stages=' , '))

    def test_unknown_tag_is_an_error(self):
        with self.assertRaises(ValueError):
            select_stages(args(include_tags='slo'))

    def test_online_needs_flag(self):
        with self.assertRaises(ValueError):
            select_stages(args(stages='online'))


class StubVM:
    def __init__(self, alive=True):
        self.alive = alive
        self.process = object()
        self.stopped = False

    def command(self, script, timeout=120):
        if not self.alive:
            raise RuntimeError('guest dead')
        return 'ok'

    def stop(self):
        self.stopped = True


class FakeProcess:
    def poll(self):
        return None


class LiveVM(StubVM):
    def __init__(self):
        super().__init__(alive=True)
        self.process = FakeProcess()


class RunStagesTests(unittest.TestCase):
    def setUp(self):
        import verify

        self._stages = verify.STAGE_MAP
        verify.STAGE_MAP = {
            'a': {
                'name': 'a',
                'func': lambda _vm, _passed, _args: None,
                'tags': frozenset(),
                'requires': [],
            },
            'b': {
                'name': 'b',
                'func': lambda _vm, _passed, _args: None,
                'tags': frozenset(),
                'requires': ['a'],
            },
            'c': {
                'name': 'c',
                'func': lambda _vm, _passed, _args: None,
                'tags': frozenset(),
                'requires': ['a'],
            },
        }
        self.addCleanup(setattr, verify, 'STAGE_MAP', self._stages)

    def test_continues_past_independent_failure(self):
        import verify

        def fail(_vm, _passed, _args):
            raise RuntimeError('boom')

        verify.STAGE_MAP['b']['func'] = fail
        failures, skipped = run_stages(LiveVM(), lambda m: None, args(), ['a', 'b', 'c'])
        self.assertEqual(list(failures), ['b'])
        self.assertEqual(skipped, [])

    def test_skips_dependents_of_failed_stage(self):
        import verify

        def fail(_vm, _passed, _args):
            raise RuntimeError('boom')

        verify.STAGE_MAP['a']['func'] = fail
        failures, skipped = run_stages(LiveVM(), lambda m: None, args(), ['a', 'b', 'c'])
        self.assertEqual(list(failures), ['a'])
        self.assertEqual(skipped, ['b', 'c'])

    def test_guest_death_accounts_for_remaining_stages(self):
        import verify

        order = []

        def fail(vm, _passed, _args):
            order.append('fail')
            vm.alive = False
            raise RuntimeError('boom')

        verify.STAGE_MAP['b']['func'] = fail
        failures, skipped = run_stages(StubVM(), lambda m: None, args(), ['a', 'b', 'c'])
        self.assertEqual(list(failures), ['b'])
        self.assertEqual(skipped, ['c'])


if __name__ == '__main__':
    unittest.main()
