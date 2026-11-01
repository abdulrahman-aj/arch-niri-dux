import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
check = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/verify-personal.py'))['CHECK_PORTABLE']


class PortableVerificationTests(unittest.TestCase):
    def run_check(self, target=None):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            source = home / 'dotfiles/home/.config/alacritty/alacritty.toml'
            source.parent.mkdir(parents=True)
            source.touch()
            config = home / '.config/alacritty'
            config.mkdir(parents=True)
            if target == 'relative':
                (config / source.name).symlink_to('../../dotfiles/home/.config/alacritty/alacritty.toml')
            elif target == 'absolute':
                (config / source.name).symlink_to(source)
            else:
                (config / source.name).write_text('base defaults')
            return subprocess.run(['bash', '-e', '-c', check], env={**os.environ, 'HOME': str(home)},
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode

    def test_relative_stow_link_is_rejected(self):
        self.assertNotEqual(self.run_check('relative'), 0)

    def test_absolute_dotfiles_link_is_rejected(self):
        self.assertNotEqual(self.run_check('absolute'), 0)

    def test_base_configuration_is_preserved(self):
        self.assertEqual(self.run_check(), 0)
