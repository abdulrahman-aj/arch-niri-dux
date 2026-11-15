import base64
import socket
import subprocess
import tempfile
import types
from pathlib import Path
import io
import html
import json
import unittest
from unittest.mock import patch


from tools.verification.probes import browser as guest
from tools.verification.probes import guest as shared


class DesktopProbeTests(unittest.TestCase):
    def captured_frame(self, state):
        with patch.object(guest.urllib.request, 'urlopen', return_value=self.page(state)):
            return guest.captured_frame()

    def page(self, state):
        return io.StringIO(
            json.dumps(
                [
                    {
                        'url': 'file:///tmp/chromium-smoke.html',
                        'title': 'DUX-VERIFY ' + json.dumps(state),
                    }
                ]
            )
        )

    def test_video_dimensions_and_a_decoded_black_frame_cannot_pass_screen_sharing(self):
        state = {
            'capture': 'passed',
            'frames': 3,
            'width': 1280,
            'height': 800,
            'pixels': {'nonBlack': 0, 'minimum': 0, 'maximum': 0, 'colors': 1},
        }
        self.assertFalse(self.captured_frame(state))
        state['pixels'] = {'nonBlack': 240, 'minimum': 765, 'maximum': 765, 'colors': 1}
        self.assertFalse(self.captured_frame(state))
        state['pixels'] = {'nonBlack': 40, 'minimum': 6, 'maximum': 600, 'colors': 24}
        self.assertEqual(self.captured_frame(state), state)

    def test_chromium_html_escaped_title_is_decoded_before_reading_dom_state(self):
        state = {'scheme': 'dark', 'changes': 2, 'capture': 'idle'}
        response = io.StringIO(
            json.dumps(
                [
                    {
                        'url': 'file:///tmp/chromium-smoke.html',
                        'title': html.escape('DUX-VERIFY ' + json.dumps(state)),
                    }
                ]
            )
        )
        with patch.object(guest.urllib.request, 'urlopen', return_value=response):
            self.assertEqual(guest.browser_state(), state)

    def test_file_dialog_waits_for_the_page_and_open_dialog_before_screenshot(self):
        pages = iter([io.StringIO('[]'), self.page({'file': 'pending'})])
        listings = iter(
            [
                [],
                [{'app_id': 'dev.duxlabs.files', 'title': 'Save'}],
                [{'app_id': 'dev.duxlabs.files', 'title': 'Open file'}],
            ]
        )
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII='
        )

        def run(argv, **_kwargs):
            if argv[-1] == 'windows':
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(next(listings)))
            Path(argv[-1]).write_bytes(image)
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX) as wayland:
            runtime = Path(directory)
            wayland.bind(str(runtime / 'wayland-0'))
            (runtime / 'niri.test.sock').touch()
            user = types.SimpleNamespace(pw_uid=1000, pw_dir=directory)
            with (
                patch.object(guest.urllib.request, 'urlopen', side_effect=lambda *a, **kw: next(pages)),
                patch.object(shared.time, 'sleep'),
                patch.object(shared.subprocess, 'run', side_effect=run),
                patch.object(shared.pwd, 'getpwnam', return_value=user),
                patch.object(shared, 'Path', return_value=runtime),
                patch.object(shared, 'EVIDENCE', runtime),
            ):
                guest.file_chooser()
            self.assertTrue(shared.png_ready(runtime / '02-file-chooser.png'))
        self.assertEqual(list(pages), [])
        self.assertEqual(list(listings), [])

    def test_file_dialog_cancellation_retries_a_transient_missing_page(self):
        responses = iter([io.StringIO('[]'), self.page({'file': 'pending'}), self.page({'file': 'cancelled'})])
        with (
            patch.object(guest.urllib.request, 'urlopen', side_effect=lambda *a, **kw: next(responses)),
            patch.object(shared.time, 'sleep'),
        ):
            guest.file_chooser_cancelled()
        self.assertEqual(list(responses), [])


if __name__ == '__main__':
    unittest.main()
