"""Qualify logind suspend through compositor and logind; capture optional evidence."""

import json
from pathlib import Path
import shutil
import subprocess
import time

from . import dispatch
from .guest import png_dimensions, run, user_command, wait_until
from .vm import graphical_login, installed_login

LOCAL_PROBES = Path('/var/tmp/arch-niri-dux-suspend')
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
    Path('/run/verify/suspend-settings.json').write_text(json.dumps(settings, indent=2) + '\n')
    assert settings == expected, settings
    # virtio-9p does not recover after S3 in this VM. Keep probes and evidence
    # on the guest disk until a reboot restores the host share.
    LOCAL_PROBES.mkdir(parents=True, exist_ok=True)
    shutil.chown(LOCAL_PROBES, user='tester', group='tester')
    shutil.copytree('/run/verify/verification', LOCAL_PROBES / 'verification', dirs_exist_ok=True)
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
    record = {'session': state, 'niri_lock_events': events}
    if stage != 'before':
        initial = json.loads((LOCAL_PROBES / 'initial.json').read_text())
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        assert boot_id == initial['boot_id'], 'Resume changed the kernel boot ID'
        transitions = kernel_events()[len(initial['kernel_events']) :]
        assert len(transitions) == 2 and 'entry (deep)' in transitions[0] and 'exit' in transitions[1], transitions
        record.update(boot_id=boot_id, kernel_events=transitions)
    name = f'03-suspend-{stage}'
    destination = Path('/run/verify', name + '.json')
    destination.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record), flush=True)
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
        record['screenshot_dimensions'] = png_dimensions(Path('/run/verify') / (name + '.png'))
    except Exception as error:
        record['screenshot_error'] = f'{type(error).__name__}: {error}'
        print(f'Optional screenshot unavailable: {error}', flush=True)
    destination.write_text(json.dumps(record, indent=2) + '\n')


def capture(vm, stage):
    name = f'03-suspend-{stage}'
    try:
        vm.screenshot(name)
    except Exception as error:
        (vm.directory / (name + '.unavailable.txt')).write_text(f'{type(error).__name__}: {error}\n')


def wake_display():
    before = session_state()
    # An RTC wake supplies no physical lid/keyboard event to wake the monitor.
    user_command('niri', 'msg', 'action', 'power-on-monitors', timeout=10)
    time.sleep(2)
    assert session_state()['LockedHint'] == before['LockedHint'], 'Monitor wake changed the lock state'


def verify(vm, passed):
    # Earlier smoke clients retain cwd/file references to the host share. A
    # fresh boot isolates the sleep test and lets us detach 9p normally.
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    vm.mount_probes()
    graphical_login(vm)
    vm.probe('suspend', 'before', timeout=45)
    vm.probe('suspend', 'prepare')
    # A completed PNG can briefly retain an open writer on the share.
    vm.command(
        f'for attempt in $(seq 1 10); do '
        f'if umount /run/verify; then mount --bind {LOCAL_PROBES} /run/verify; exit $?; fi; '
        'sleep 1; done; exit 1'
    )
    vm.send('systemctl suspend\n')
    vm.expect(r'\r?\n' + RESUMED + r'\r?\n', timeout=90)
    # Let the emulated console settle after systemd-sleep thaws the user slice.
    time.sleep(3)
    vm.probe('suspend', 'wake', timeout=30)
    vm.probe('suspend', 'locked', timeout=45)
    capture(vm, 'locked')
    # QEMU's post-resume GL/input path can stall. The desktop probe already
    # qualifies password unlock before sleep; native signals qualify this lock.
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    vm.mount_probes()
    vm.command(f'cp {LOCAL_PROBES}/03-suspend-* /run/verify/; rm -r {LOCAL_PROBES}')
    graphical_login(vm)
    passed('Real S3 suspend resumes with logind and Niri reporting the session locked')


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
