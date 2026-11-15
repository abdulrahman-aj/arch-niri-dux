"""Exercise the boundaries most likely to break when moving acceptance code."""

import base64
import json
import subprocess
import socket
import types
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.verification.probes import dispatch, guest


class ProbeDispatchTests(unittest.TestCase):
    def test_only_the_selected_handler_runs_without_consuming_its_arguments(self):
        calls = []
        arguments = ['probe', 'selected', '/custom/root']
        with patch.object(sys, 'argv', arguments.copy()):
            dispatch('sample', {'selected': lambda: calls.append('selected'), 'other': lambda: calls.append('other')})
            self.assertEqual(sys.argv, arguments)
        self.assertEqual(calls, ['selected'])

    def test_unknown_action_reports_the_probe_name_without_running_a_handler(self):
        calls = []
        with patch.object(sys, 'argv', ['probe', 'unknown']):
            with self.assertRaises(SystemExit) as error:
                dispatch('sample', {'known': lambda: calls.append('known')})
        self.assertEqual(error.exception.code, 'Unknown sample probe')
        self.assertEqual(calls, [])

    def test_handler_key_errors_are_not_misreported_as_unknown_actions(self):
        failure = KeyError('handler failure')
        with patch.object(sys, 'argv', ['probe', 'known']):
            with self.assertRaises(KeyError) as error:

                def broken():
                    raise failure

                dispatch('sample', {'known': broken})
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
        from tools.verification.probes import camera

        unfocused = {'id': 7, 'app_id': 'org.gnome.Snapshot', 'is_focused': False}
        focused = dict(unfocused, is_focused=True)
        listings = iter([[], [{'id': 1, 'app_id': 'other'}], [unfocused], [unfocused], [focused]])
        focus = None

        def run(argv, **_kwargs):
            nonlocal focus
            if argv[-1] == 'windows':
                listing = next(listings)
                if listing == [focused] and focus != 7:
                    raise AssertionError('Snapshot became ready before focus was requested')
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(listing))
            focus = int(argv[-1])
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX) as wayland:
            runtime = Path(directory)
            wayland.bind(str(runtime / 'wayland-0'))
            (runtime / 'niri.test.sock').touch()
            user = types.SimpleNamespace(pw_uid=1000, pw_dir=directory)
            with (
                patch.object(guest.time, 'sleep'),
                patch.object(guest.subprocess, 'run', side_effect=run),
                patch.object(guest.pwd, 'getpwnam', return_value=user),
                patch.object(guest, 'Path', return_value=runtime),
            ):
                camera.focus_camera()
        self.assertEqual(list(listings), [])


if __name__ == '__main__':
    unittest.main()
