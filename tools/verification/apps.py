"""Fresh-desktop acceptance; run only inside the disposable verification guest."""

import json
import time
from tools.verification.payload import probe


def verify(vm, passed):
    probe(vm, 'apps', 'cli', timeout=120)
    passed('Manuals, archive round-trip, rsync, JSON/search and diagnostic CLI tools work')
    probe(vm, 'apps', 'prepare', timeout=300)
    probe(vm, 'apps', 'image', timeout=120)
    probe(vm, 'apps', 'text', timeout=120)
    vm.click(0.2, 0.2)
    time.sleep(0.2)
    probe(vm, 'apps', 'text-focused')
    vm.chord('ctrl', 'a')
    time.sleep(0.1)
    vm.type_graphical('editedbyduxverification')
    vm.chord('ctrl', 's')
    probe(vm, 'apps', 'text-saved', timeout=60)
    probe(vm, 'apps', 'calculator', timeout=120)
    passed('Images and text open in the selected apps; text edits save; Calculator launches')
    # Reopen video four times: first-launch success missed the original hang.
    for kind in ('audio', 'video', 'video', 'video', 'video'):
        probe(vm, 'apps', 'open-' + kind, timeout=120)
        probe(vm, 'apps', 'capture-' + kind, timeout=30)
        capture_file = vm.directory / ('apps-' + kind + '.json')
        if not json.loads(capture_file.read_text())['tone_detected']:
            # Some players open paused; exercise their normal play shortcut.
            vm.key('spc')
            probe(vm, 'apps', 'capture-' + kind, timeout=30)
        probe(vm, 'apps', 'played-' + kind, timeout=60)
    passed('Decibels audio and four Celluloid open/play/close cycles output the expected fixture tones')
    probe(vm, 'apps', 'backends', timeout=90)
    passed('Dux discovers drive tools; Qt images, shared networking and desktop backends work')
    probe(vm, 'apps', 'printing', timeout=150)
    passed('Driverless CUPS queue delivered a PDF to a loopback IPP printer')
