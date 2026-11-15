"""Qualify logind suspend through compositor and logind; capture optional evidence."""

import json
from pathlib import Path
import shutil
import subprocess
import time
from . import dispatch
from .guest import record, run, user_command, wait_until
from tools.verification.images import png_dimensions
from tools.verification.constants import LOCAL_PROBES


RESUMED = 'DUX_SUSPEND_RESUMED'


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


def kernel_events():
    journal = run('journalctl', '-b', '-k', '--no-pager', '-o', 'cat', stdout=subprocess.PIPE).stdout
    return [line for line in journal.splitlines() if 'PM: suspend entry' in line or 'PM: suspend exit' in line]


def prepare():
    # Check what Dux actually loaded, rather than inferring it from the seed.
    expected = {
        'lockBeforeSuspend': True,
        'loginctlLockIntegration': True,
        'acLockTimeout': 600,
        'batteryLockTimeout': 600,
    }

    def loaded_settings():
        try:
            return {
                key: json.loads(
                    user_command('dms', 'ipc', 'call', 'settings', 'get', key, capture=True, timeout=2).stdout
                )
                for key in expected
            }
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    settings = wait_until('Dux settings IPC ready', loaded_settings, timeout=60)
    record('suspend-settings', settings)
    assert settings == expected, settings
    # virtio-9p does not recover after S3 in this VM. Keep probes and evidence
    # on the guest disk until a reboot restores the host share.
    LOCAL_PROBES.mkdir(parents=True, exist_ok=True)
    shutil.chown(LOCAL_PROBES, user='tester', group='tester')
    shutil.copytree('/run/verify/tools', LOCAL_PROBES / 'tools', dirs_exist_ok=True)
    (LOCAL_PROBES / 'initial.json').write_text(
        json.dumps(
            {
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'kernel_events': kernel_events(),
            }
        )
    )
    dropin = Path('/run/systemd/system/systemd-suspend.service.d/90-verification.conf')
    dropin.parent.mkdir(parents=True, exist_ok=True)
    # systemctl suspend returns before sleep. Arm the alarm inside the sleep
    # service and signal the console only after systemd-sleep has returned.
    dropin.write_text(
        '[Service]\n'
        'ExecStartPre=/usr/bin/rtcwake -m no -s 20\n'
        "ExecStartPost=/usr/bin/sh -c 'echo > /dev/ttyS0; echo DUX_SUSPEND_RESUMED > /dev/ttyS0'\n"
    )
    Path('/sys/power/mem_sleep').write_text('deep')
    run('systemctl', 'daemon-reload')


def evidence(stage, locked):
    def expected_state():
        state = session_state()
        return state if state['LockedHint'] == ('yes' if locked else 'no') else None

    state = wait_until(f'Logind locked hint {locked}', expected_state, timeout=20)
    journal = run(
        'journalctl', '-b', '_COMM=niri', '_UID=1000', '--no-pager', '-o', 'cat', stdout=subprocess.PIPE
    ).stdout
    # This substring includes both locking and unlocking transitions.
    events = [line for line in journal.splitlines() if 'locking session' in line]
    if locked:
        assert events and 'unlocking session' not in events[-1], events
    else:
        assert not events or 'unlocking session' in events[-1], events
    report = {'session': state, 'niri_lock_events': events}
    if stage != 'before':
        initial = json.loads((LOCAL_PROBES / 'initial.json').read_text())
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        assert boot_id == initial['boot_id'], 'Resume changed the kernel boot ID'
        transitions = kernel_events()[len(initial['kernel_events']) :]
        assert len(transitions) == 2 and 'entry (deep)' in transitions[0] and 'exit' in transitions[1], transitions
        report.update(boot_id=boot_id, kernel_events=transitions)
    name = f'03-suspend-{stage}'
    record(name, report)
    print(json.dumps(report), flush=True)
    if stage != 'before':
        return
    # Capture cannot qualify the lock: the VM's post-resume GL path can stall.
    try:
        user_command(
            'dms',
            'screenshot',
            'full',
            '--no-clipboard',
            '--no-notify',
            '--dir',
            '/run/verify',
            '--filename',
            name + '.png',
            timeout=10,
        )
        report['screenshot_dimensions'] = png_dimensions(Path('/run/verify') / (name + '.png'))
    except Exception as error:
        report['screenshot_error'] = f'{type(error).__name__}: {error}'
        print(f'Optional screenshot unavailable: {error}', flush=True)
    record(name, report)


def wake_display():
    before = session_state()
    # An RTC wake supplies no physical lid/keyboard event to wake the monitor.
    user_command('niri', 'msg', 'action', 'power-on-monitors', timeout=10)
    time.sleep(2)
    assert session_state()['LockedHint'] == before['LockedHint'], 'Monitor wake changed the lock state'


if __name__ == '__main__':
    dispatch(
        'suspend',
        {
            'before': lambda: evidence('before', False),
            'prepare': prepare,
            'wake': wake_display,
            'locked': lambda: evidence('locked', True),
        },
    )
