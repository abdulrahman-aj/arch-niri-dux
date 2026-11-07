"""Qualify logind suspend using compositor and logind evidence, without Dux IPC."""

import json
from pathlib import Path
import subprocess
import time

from . import dispatch
from .guest import png_dimensions, run, screenshot, wait_until
from .vm import PASSWORD


def session_state():
    sessions = json.loads(run('loginctl', 'list-sessions', '--json=short', stdout=subprocess.PIPE).stdout)
    for session in sessions:
        properties = run(
            'loginctl',
            'show-session',
            session['session'],
            '-p',
            'Id',
            '-p',
            'Name',
            '-p',
            'Type',
            '-p',
            'LockedHint',
            stdout=subprocess.PIPE,
        ).stdout
        state = dict(line.split('=', 1) for line in properties.splitlines())
        if state.get('Name') == 'tester' and state.get('Type') == 'wayland':
            return state
    raise RuntimeError('No graphical tester session found')


def evidence(stage, locked):
    def expected_state():
        state = session_state()
        return state if state['LockedHint'] == ('yes' if locked else 'no') else None

    state = wait_until(f'Logind locked hint {locked}', expected_state, timeout=20)
    journal = run('journalctl', '-b', '_COMM=niri', '--no-pager', '-o', 'cat', stdout=subprocess.PIPE).stdout
    # This substring includes both locking and unlocking transitions.
    events = [line for line in journal.splitlines() if 'locking session' in line]
    if locked:
        assert events and 'unlocking session' not in events[-1], events
    else:
        assert not events or 'unlocking session' in events[-1], events
    name = f'03-suspend-{stage}'
    screenshot(name + '.png')
    dimensions = png_dimensions(Path('/run/verify') / (name + '.png'))
    Path('/run/verify', name + '.json').write_text(
        json.dumps({'session': state, 'niri_lock_events': events, 'screenshot_dimensions': dimensions}, indent=2) + '\n'
    )


def verify(vm, passed):
    vm.probe('suspend', 'before', timeout=45)
    # This share is test instrumentation. Reconnect it after sleep, as in the
    # hibernation harness, rather than carrying 9p client state across sleep.
    vm.command('umount /run/verify')
    vm.send("systemctl suspend; printf '\\nSUSPEND-RETURNED\\n'\n")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = vm.qmp_command('query-status')
        if state['status'] == 'suspended':
            break
        time.sleep(0.5)
    else:
        raise TimeoutError(f'Guest did not enter S3 suspend: {state}')
    vm.qmp_command('system_wakeup')
    vm.expect(r'\r?\nSUSPEND-RETURNED\r?\n')
    vm.expect(r'\x1b\[\?2004h')
    # The emulated console can discard input immediately after its first prompt.
    time.sleep(3)
    vm.mount_probes()
    vm.command('journalctl -b -u systemd-suspend.service --no-pager')
    vm.probe('suspend', 'locked', timeout=45)
    (vm.directory / 'suspend.json').write_text(
        json.dumps({'qemu_sleep_state': state, 'resume_locked': True}, indent=2) + '\n'
    )
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.probe('suspend', 'unlocked', timeout=45)
    passed('Logind S3 suspend resumes with a locked compositor and accepts the password')


if __name__ == '__main__':
    dispatch(
        'suspend',
        {
            'before': lambda: evidence('before', False),
            'locked': lambda: evidence('locked', True),
            'unlocked': lambda: evidence('unlocked', False),
        },
    )
