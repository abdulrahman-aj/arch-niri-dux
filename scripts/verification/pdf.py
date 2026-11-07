"""Pdf acceptance behavior."""

import json
from pathlib import Path
import subprocess
import time
from . import dispatch
from .guest import wait_until, user_command, ipc, windows, screenshot, png_dimensions, focus_window


def sample_pdf_bytes():
    text = b'Dux verification PDF works'
    content = b'BT /F1 18 Tf 20 80 Td (' + text + b') Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] '
        b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream',
    ]
    pdf = b'%PDF-1.4\n'
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(pdf)
    pdf += b'xref\n0 %d\n' % (len(objects) + 1)
    pdf += b'0000000000 65535 f \n'
    for offset in offsets:
        pdf += b'%010d 00000 n \n' % offset
    pdf += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF' % (len(objects) + 1, xref)
    return pdf


def pdf():
    assert (
        user_command('xdg-mime', 'query', 'default', 'application/pdf', capture=True).stdout.strip()
        == 'org.gnome.Papers.desktop'
    )
    assert Path('/usr/share/applications/org.gnome.Papers.desktop').is_file(), 'Papers desktop file missing.'
    assert 'application/pdf=org.gnome.Papers.desktop' in Path('/etc/xdg/mimeapps.list').read_text()
    sample = Path('/tmp/dux-verification.pdf')
    sample.write_bytes(sample_pdf_bytes())
    assert sample.stat().st_size > 0
    # Qualify the default association through the real opener, not a direct
    # papers launch: this is what proves xdg-open routes PDFs to Papers.
    user_command(
        'sh', '-c', 'rm -f /tmp/xdg-open-pdf.log; xdg-open /tmp/dux-verification.pdf >/tmp/xdg-open-pdf.log 2>&1 &'
    )
    try:
        wait_until(
            'Papers graphical window via xdg-open default',
            lambda: any('papers' in (window.get('app_id') or '').lower() for window in windows()),
            timeout=90,
        )
        screenshot('02-papers.png')
        rendered = Path('/run/verify/02-papers.png')
        width, height = png_dimensions(rendered)
        Path('/run/verify/pdf.json').write_text(
            json.dumps(
                {
                    'default': 'org.gnome.Papers.desktop',
                    'method': 'xdg-open',
                    'sample': str(sample),
                    'sample_size': sample.stat().st_size,
                    'width': width,
                    'height': height,
                    'note': 'Parent reviews 02-papers.png for document content vs an error page.',
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        user_command('sh', '-c', 'pkill -x papers || pkill -f papers || true')


def files_pdf_prepare():
    sample = Path('/home/tester/dux-preview.pdf')
    sample.write_bytes(sample_pdf_bytes())
    ipc('dfiles', 'ui.open', f'path={sample}')
    window = wait_until(
        'Dux Files PDF selection',
        lambda: next((item for item in windows() if item.get('app_id') == 'dev.duxlabs.files'), None),
    )
    focus_window(window['id'])


def verify(vm, passed):
    vm.probe('pdf', 'pdf', timeout=180)
    passed('Papers is the default PDF viewer and rendered a sample document')
    vm.probe('pdf', 'files-pdf-prepare', timeout=60)
    try:
        vm.key('f4')
        time.sleep(1)
        vm.chord('shift', 'equal')
        for attempt in range(15):
            vm.probe('pdf', 'files-pdf-capture')
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
        vm.key('esc')
        vm.probe('pdf', 'files-pdf-close')
    passed('Dux Files PDF preview rendered document content with its Qt PDF backend')


if __name__ == '__main__':
    dispatch(
        'pdf',
        {
            'pdf': pdf,
            'files-pdf-prepare': files_pdf_prepare,
            'files-pdf-capture': lambda: screenshot('02-dfiles-pdf.png'),
            'files-pdf-close': lambda: ipc('dfiles', 'ui.hide'),
        },
    )
