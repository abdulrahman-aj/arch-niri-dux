"""Exercise first-login opt-in without the host's desktop settings or D-Bus."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'desktop/usr/lib/arch-niri-dux/initialize-application-themes'


class ApplicationThemeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.config = self.home / 'config'
        self.state = self.home / 'state'
        self.log = self.home / 'calls'
        self.marker = self.state / 'arch-niri-dux/application-themes'
        binary = self.home / 'bin'
        binary.mkdir()
        for toolkit in ('gtk-3.0', 'gtk-4.0'):
            directory = self.config / toolkit
            directory.mkdir(parents=True)
            (directory / 'dank-colors.css').write_text('fixture colors\n')
        runtime = self.home / 'runtime'
        gtk = runtime / 'dms-shell/fixture/scripts/gtk.sh'
        gtk.parent.mkdir(parents=True)
        gtk.write_text(
            'printf "gtk %s %s\\n" "$2" "$3" >>"$CALL_LOG"\nif [[ $2 == patch ]]; then exit "$PATCH_STATUS"; fi\n'
        )
        (binary / 'dms').write_text('#!/bin/sh\nprintf "%s\\n" "$DMS_MODE"\n')
        (binary / 'gsettings').write_text(
            '#!/bin/sh\n'
            'if [ "$1" = get ]; then theme=$GTK_THEME; '
            '[ ! -e "$THEME_FILE" ] || theme=$(cat "$THEME_FILE"); '
            'printf "\\047%s\\047\\n" "$theme"; '
            'else printf "%s\\n" "$*" >>"$CALL_LOG"; fi\n'
        )
        for path in binary.iterdir():
            path.chmod(0o755)
        self.environment = os.environ | {
            'HOME': str(self.home),
            'XDG_CONFIG_HOME': str(self.config),
            'XDG_STATE_HOME': str(self.state),
            'XDG_RUNTIME_DIR': str(runtime),
            'PATH': str(binary) + ':' + os.environ['PATH'],
            'CALL_LOG': str(self.log),
            'DMS_MODE': 'dark',
            'GTK_THEME': 'Adwaita',
            'PATCH_STATUS': '0',
            'THEME_FILE': str(self.home / 'theme-choice'),
        }

    def run_helper(self, **changes):
        return subprocess.run(['bash', str(SCRIPT)], env=self.environment | changes, text=True, capture_output=True)

    def test_skipped_or_failed_patch_does_not_record_success(self):
        for status in ('1', '2'):
            with self.subTest(status=status):
                result = self.run_helper(PATCH_STATUS=status)
                self.assertEqual(result.returncode, int(status))
                self.assertFalse(self.marker.exists())
                self.assertNotIn('set ', self.log.read_text())

    def test_custom_theme_is_not_claimed(self):
        result = self.run_helper(GTK_THEME='Personal Theme')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.log.exists())

    def test_completed_initialization_never_replays(self):
        first = self.run_helper()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.log.read_bytes()
        result = self.run_helper(GTK_THEME='Personal Theme', DMS_MODE='light')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_bytes(), before)

    def test_theme_selected_while_waiting_is_preserved(self):
        binary = self.home / 'bin/dms'
        binary.write_text(
            '#!/bin/sh\n'
            'if [ ! -e "$THEME_FILE" ]; then '
            'printf "Personal Theme\\n" >"$THEME_FILE"; echo pending; '
            'else echo dark; fi\n'
        )
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.log.exists())
