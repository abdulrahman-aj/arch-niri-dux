"""Exercise the session terminal command against real XDG preference resolution."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DefaultTerminalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / 'config'
        self.system = self.root / 'system'
        self.apps = self.root / 'data/applications'
        self.bin = self.root / 'bin'
        for directory in (self.config, self.system, self.apps, self.bin):
            directory.mkdir(parents=True)
        shutil.copyfile(ROOT / 'desktop/etc/xdg/xdg-terminals.list', self.system / 'xdg-terminals.list')
        for name, binary, flag in [('Alacritty', 'alacritty', '-e'), ('Other', 'other-terminal', '--')]:
            executable = self.bin / binary
            executable.write_text(
                f'#!{sys.executable}\nimport json, sys\nprint(json.dumps([{name!r}] + sys.argv[1:]))\n'
            )
            executable.chmod(0o755)
            (self.apps / f'{name}.desktop').write_text(
                f'[Desktop Entry]\nType=Application\nName={name}\nExec={executable}\n'
                f'Categories=TerminalEmulator;\nX-TerminalArgExec={flag}\n'
            )
        self.environment = os.environ | {
            'HOME': str(self.root),
            'PATH': str(self.bin) + ':' + os.environ['PATH'],
            'XDG_CONFIG_HOME': str(self.config),
            'XDG_CONFIG_DIRS': str(self.system),
            'XDG_DATA_HOME': str(self.apps.parent),
            'XDG_DATA_DIRS': str(self.root / 'empty'),
            'XDG_CACHE_HOME': str(self.root / 'cache'),
            'XDG_CURRENT_DESKTOP': 'niri',
        }
        settings = (ROOT / 'desktop/usr/lib/environment.d/90-dux.conf').read_text()
        self.terminal = next(line.split('=', 1)[1] for line in settings.splitlines() if line.startswith('TERMINAL='))

    def launch(self, *arguments):
        output = subprocess.check_output([self.terminal, *arguments], env=self.environment, text=True)
        return json.loads(output)

    def test_factory_preference_yields_to_the_user_choice(self):
        self.assertEqual(self.launch(), ['Alacritty'])
        (self.config / 'xdg-terminals.list').write_text('Other.desktop\n')
        self.assertEqual(self.launch(), ['Other'])

    def test_recovery_style_exec_uses_each_terminals_flags_and_preserves_arguments(self):
        self.assertEqual(
            self.launch('-e', 'printf', 'argument with spaces'), ['Alacritty', '-e', 'printf', 'argument with spaces']
        )
        (self.config / 'xdg-terminals.list').write_text('Other.desktop\n')
        self.assertEqual(
            self.launch('-e', 'printf', 'argument with spaces'), ['Other', '--', 'printf', 'argument with spaces']
        )


if __name__ == '__main__':
    unittest.main()
