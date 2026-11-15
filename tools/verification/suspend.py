"""Qualify logind suspend through compositor and logind; capture optional evidence."""

import shlex
import time
from tools.verification.login import graphical_login, installed_login
from tools.verification.constants import LOCAL_PROBES
from tools.verification.payload import probe, mount_probes


RESUMED = 'DUX_SUSPEND_RESUMED'


def capture(vm, stage):
    name = f'03-suspend-{stage}'
    try:
        vm.screenshot(name)
    except Exception as error:
        (vm.directory / (name + '.unavailable.txt')).write_text(f'{type(error).__name__}: {error}\n')


def verify(vm, passed):
    # Earlier smoke clients retain cwd/file references to the host share. A
    # fresh boot isolates the sleep test and lets us detach 9p normally.
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    mount_probes(vm)
    graphical_login(vm)
    probe(vm, 'suspend', 'before', timeout=45)
    probe(vm, 'suspend', 'prepare')
    # A completed PNG can briefly retain an open writer on the share.
    vm.command(
        f'for attempt in $(seq 1 10); do '
        f'if umount /run/verify; then mount --bind {shlex.quote(str(LOCAL_PROBES))} /run/verify; exit $?; fi; '
        'sleep 1; done; exit 1'
    )
    vm.send('systemctl suspend\n')
    vm.expect(r'\r?\n' + RESUMED + r'\r?\n', timeout=90)
    # Let the emulated console settle after systemd-sleep thaws the user slice.
    time.sleep(3)
    probe(vm, 'suspend', 'wake', timeout=30)
    probe(vm, 'suspend', 'locked', timeout=45)
    capture(vm, 'locked')
    # QEMU's post-resume GL/input and orderly shutdown can stall. Desktop
    # already qualifies password unlock; sync evidence before restarting this
    # disposable VM through the host to restore the share.
    vm.command('sync')
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    mount_probes(vm)
    vm.command(f'cp {shlex.quote(str(LOCAL_PROBES))}/03-suspend-* /run/verify/; rm -r {shlex.quote(str(LOCAL_PROBES))}')
    graphical_login(vm)
    passed('Real S3 suspend resumes with logind and Niri reporting the session locked')
