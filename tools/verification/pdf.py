"""Pdf acceptance behavior."""

import json
import subprocess
import time
from tools.verification.payload import probe


def verify(vm, passed):
    probe(vm, 'pdf', 'pdf', timeout=180)
    passed('Papers is the default PDF viewer and rendered a sample document')
    probe(vm, 'pdf', 'files-pdf-prepare', timeout=60)
    try:
        vm.chord('f4')
        time.sleep(1)
        vm.chord('shift', 'equal')
        for _attempt in range(15):
            probe(vm, 'pdf', 'files-pdf-capture')
            text = subprocess.check_output(
                ['tesseract', str(vm.directory / '02-dfiles-pdf.png'), 'stdout'], text=True, stderr=subprocess.DEVNULL
            )
            if 'dux verification pdf works' in ' '.join(text.lower().split()):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Dux Files PDF preview did not render the sample document text.')
        (vm.directory / 'files-pdf.json').write_text(
            json.dumps(
                {
                    'rendered_text': text,
                    'method': 'Dux Files selection, F4 preview and zoom-in key',
                    'screenshot': '02-dfiles-pdf.png',
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        vm.chord('esc')
        probe(vm, 'pdf', 'files-pdf-close')
    passed('Dux Files PDF preview rendered document content with its Qt PDF backend')
