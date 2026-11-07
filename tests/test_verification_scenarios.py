"""Exercise the boundaries most likely to break when moving acceptance code."""

import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.verification import dispatch, guest


class ProbeDispatchTests(unittest.TestCase):
    def test_only_the_selected_handler_runs_without_consuming_its_arguments(self):
        selected, other = Mock(), Mock()
        arguments = ['probe', 'selected', '/custom/root']
        with patch.object(sys, 'argv', arguments.copy()):
            dispatch('sample', {'selected': selected, 'other': other})
            self.assertEqual(sys.argv, arguments)
        selected.assert_called_once_with()
        other.assert_not_called()

    def test_unknown_action_reports_the_probe_name_without_running_a_handler(self):
        handler = Mock()
        with patch.object(sys, 'argv', ['probe', 'unknown']):
            with self.assertRaises(SystemExit) as error:
                dispatch('sample', {'known': handler})
        self.assertEqual(error.exception.code, 'Unknown sample probe')
        handler.assert_not_called()

    def test_handler_key_errors_are_not_misreported_as_unknown_actions(self):
        failure = KeyError('handler failure')
        with patch.object(sys, 'argv', ['probe', 'known']):
            with self.assertRaises(KeyError) as error:
                dispatch('sample', {'known': Mock(side_effect=failure)})
        self.assertIs(error.exception, failure)


class ScreenshotReadinessTests(unittest.TestCase):
    def test_screenshot_is_not_ready_until_the_png_write_is_complete(self):
        image = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII='
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'screenshot.png'
            self.assertFalse(guest.png_ready(path))
            for content, ready in ((image[:8], False), (image[:-1], False), (image, True)):
                with self.subTest(bytes_written=len(content)):
                    path.write_bytes(content)
                    self.assertEqual(guest.png_ready(path), ready)


class CameraFocusTests(unittest.TestCase):
    def test_focus_waits_for_a_snapshot_window_that_appears_late(self):
        from scripts.verification import camera

        unfocused = {'id': 7, 'app_id': 'org.gnome.Snapshot', 'is_focused': False}
        focused = dict(unfocused, is_focused=True)
        listings = iter([[], [{'id': 1, 'app_id': 'other'}], [unfocused], [unfocused], [focused]])
        focus = Mock()
        with (
            patch.object(camera, 'windows', side_effect=lambda: next(listings)),
            patch.object(camera, 'focus_window', focus),
            patch.object(guest.time, 'sleep'),
        ):
            camera.focus_camera()
        focus.assert_called_with(7)
