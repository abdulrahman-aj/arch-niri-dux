"""Qualify the ordinary logind suspend path with the session unlocked."""

import json
import time

from .vm import PASSWORD


def verify(vm, passed):
    vm.probe('desktop', 'unlocked')
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
    vm.command('journalctl -b -u systemd-suspend.service --no-pager')
    vm.probe('desktop', 'locked')
    vm.screenshot('03-suspend-resume-locked')
    (vm.directory / 'suspend.json').write_text(
        json.dumps({'qemu_sleep_state': state, 'resume_locked': True}, indent=2) + '\n'
    )
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.command(
        'for attempt in $(seq 1 30); do cd /run/verify && python -m verification.desktop unlocked && exit 0; sleep 1; done; exit 1',
        timeout=60,
    )
    passed('Logind S3 suspend from an unlocked desktop resumes locked and accepts the password')
