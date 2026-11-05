import io
import html
import json
import unittest
from unittest.mock import patch


from scripts.verification import browser as guest


class DesktopProbeTests(unittest.TestCase):
    def test_video_dimensions_and_a_decoded_black_frame_cannot_pass_screen_sharing(self):
        state = {'capture': 'passed', 'frames': 3, 'width': 1280, 'height': 800,
                 'pixels': {'nonBlack': 0, 'minimum': 0, 'maximum': 0, 'colors': 1}}
        with patch.object(guest, 'browser_state', return_value=state):
            self.assertFalse(guest.captured_frame())
        state['pixels'] = {'nonBlack': 240, 'minimum': 765, 'maximum': 765, 'colors': 1}
        with patch.object(guest, 'browser_state', return_value=state):
            self.assertFalse(guest.captured_frame())
        state['pixels'] = {'nonBlack': 40, 'minimum': 6, 'maximum': 600, 'colors': 24}
        with patch.object(guest, 'browser_state', return_value=state):
            self.assertEqual(guest.captured_frame(), state)

    def test_chromium_html_escaped_title_is_decoded_before_reading_dom_state(self):
        state = {'scheme': 'dark', 'changes': 2, 'capture': 'idle'}
        response = io.StringIO(json.dumps([{
            'url': 'file:///tmp/chromium-smoke.html',
            'title': html.escape('DUX-VERIFY ' + json.dumps(state)),
        }]))
        with patch.object(guest.urllib.request, 'urlopen', return_value=response):
            self.assertEqual(guest.browser_state(), state)

    def test_file_dialog_waits_when_chromium_temporarily_has_no_page_state(self):
        with patch.object(guest, 'browser_state', side_effect=[None, {'file': 'pending'}]), \
                patch.object(guest, 'windows', return_value=[{'app_id': 'dev.duxlabs.files', 'title': 'Open file'}]), \
                patch.object(guest, 'screenshot'), patch.object(guest.time, 'sleep'):
            guest.file_chooser()

    def test_file_dialog_cancellation_retries_a_transient_missing_page(self):
        responses = [io.StringIO('[]'), io.StringIO(json.dumps([{
            'url': 'file:///tmp/chromium-smoke.html',
            'title': 'DUX-VERIFY ' + json.dumps({'file': 'cancelled'}),
        }]))]
        with patch.object(guest.urllib.request, 'urlopen', side_effect=responses), \
                patch.object(guest.time, 'sleep'):
            guest.file_chooser_cancelled()


if __name__ == '__main__':
    unittest.main()
