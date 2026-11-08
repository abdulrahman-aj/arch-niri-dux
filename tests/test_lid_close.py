"""Exercise lid races and lock state through simulated desktop services."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVICE = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

path = Path(os.environ['LID_STATE'])
state = json.loads(path.read_text())
command = Path(sys.argv[0]).name
if command == 'busctl':
    prop = sys.argv[-1]
    if prop == 'LidClosed':
        state['lid_reads'] = state.get('lid_reads', 0) + 1
        value = state.get('closed', True) and state['lid_reads'] > state.get('lid_delay', 0)
        if state.get('reopen') and state.get('locking'):
            value = False
    else:
        value = state.get(prop, False)
    print('b ' + str(value).lower())
elif command == 'dms':
    if sys.argv[-1] == 'isLocked':
        if state.get('locking'):
            state['lock_reads'] = state.get('lock_reads', 0) + 1
            if state['lock_reads'] >= 3:
                state['locked'] = True
        print(str(state.get('locked', False)).lower())
    else:
        state['locking'] = True
        state['lock_requests'] = state.get('lock_requests', 0) + 1
elif command == 'niri':
    state['panel_off'] = True
    state['locked_at_power_off'] = state.get('locked', False)
path.write_text(json.dumps(state))
"""


class LidCloseTest(unittest.TestCase):
    def run_lid_close(self, **initial):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            state = directory / 'state.json'
            state.write_text(json.dumps(initial))
            service = directory / 'service'
            service.write_text(SERVICE)
            service.chmod(0o755)
            for name in ('busctl', 'dms', 'niri'):
                (directory / name).symlink_to(service)
            subprocess.run(
                ['bash', str(ROOT / 'desktop/usr/lib/arch-niri-dux/lid-close')],
                env={**os.environ, 'PATH': f'{directory}:{os.environ["PATH"]}', 'LID_STATE': str(state)},
                check=True,
                timeout=10,
            )
            return json.loads(state.read_text())

    def test_delayed_logind_state_locks_before_powering_off(self):
        state = self.run_lid_close(lid_delay=3)
        self.assertTrue(state['panel_off'])
        self.assertTrue(state['locked_at_power_off'])
        self.assertEqual(state['lock_requests'], 1)

    def test_already_locked_still_powers_off(self):
        state = self.run_lid_close(locked=True)
        self.assertTrue(state['panel_off'])
        self.assertNotIn('lock_requests', state)

    def test_open_docked_and_suspending_do_not_lock_or_power_off(self):
        for initial in ({'closed': False}, {'Docked': True}, {'PreparingForSleep': True}):
            with self.subTest(initial=initial):
                state = self.run_lid_close(**initial)
                self.assertNotIn('panel_off', state)
                self.assertNotIn('lock_requests', state)

    def test_reopening_during_lock_does_not_power_off_again(self):
        state = self.run_lid_close(reopen=True)
        self.assertTrue(state['locked'])
        self.assertNotIn('panel_off', state)
