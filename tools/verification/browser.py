"""Browser acceptance behavior."""

import time
from tools.verification.login import click_screen_text
from tools.verification.payload import probe


def verify(vm, passed):
    probe(vm, 'browser', 'focus-browser')
    vm.key('o')
    probe(vm, 'browser', 'file-chooser', timeout=90)
    vm.key('esc')
    probe(vm, 'browser', 'file-chooser-cancelled', timeout=90)
    passed('Chromium open-file request uses Dux Files and responds to cancellation')
    probe(vm, 'browser', 'focus-browser')
    vm.key('c')
    time.sleep(1)
    # Chromium first asks for the source type, then GNOME selects the
    # actual screen; Chromium finally confirms that selected source.
    source_deadline = time.monotonic() + 60
    while time.monotonic() < source_deadline:
        probe(vm, 'browser', 'capture-source')
        if click_screen_text(vm, vm.directory / '02-chromium-share-source.png', 'Entire screen'):
            break
        time.sleep(1)
    else:
        raise TimeoutError('Chromium did not display its Entire screen source choice.')
    probe(vm, 'browser', 'capture-picker', timeout=90)
    vm.click(0.725, 0.245)
    time.sleep(1)
    vm.click(0.935, 0.8)
    probe(vm, 'browser', 'capture', timeout=90)
    passed('Chromium screen sharing produced nonblank captured desktop content')
