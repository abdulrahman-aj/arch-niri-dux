"""Camera acceptance behavior."""

import time
from tools.verification.login import click_screen_text
from tools.verification.payload import probe


def verify(vm, passed):
    probe(vm, 'camera', 'camera', timeout=300)
    try:
        permission_deadline = time.monotonic() + 60
        while time.monotonic() < permission_deadline:
            probe(vm, 'camera', 'camera-screen')
            if click_screen_text(vm, vm.directory / '02-snapshot.png', 'Grant Access') or click_screen_text(
                vm, vm.directory / '02-snapshot.png', 'GrantAccess'
            ):
                break
            time.sleep(1)
        else:
            raise TimeoutError('Snapshot did not request camera permission on the fresh installation.')
        time.sleep(3)
        # The permission click leaves focus with the host pointer, so give it
        # back to Snapshot before the first shutter key.
        probe(vm, 'camera', 'camera-ready')
        # Use Snapshot 51's upstream shutter shortcut to exercise its own
        # capture path in both modes, rather than generating files separately.
        # https://github.com/GNOME/snapshot/blob/51.0/src/application.rs
        vm.chord('t')
        probe(vm, 'camera', 'camera-photo', timeout=90)
        vm.chord('t')
        time.sleep(3)
        vm.chord('t')
        probe(vm, 'camera', 'camera-video', timeout=90)
    finally:
        probe(vm, 'camera', 'camera-cleanup')
    passed('Snapshot preview, shutter photo and video recording work with a synthetic camera')
