"""Behavioral regression tests for the ~/.local/bin session PATH (issue #23).

Each context is executed in a subprocess with a controlled HOME/PATH, so the
tests prove the resulting environment instead of asserting on file contents:
POSIX login shells, fish shells, and the systemd user-environment generator
reading the shipped environment.conf. The VM probe verifies the real greeter
and Niri launch path.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SNIPPET = ROOT / 'desktop/etc/profile.d/dux-localbin.sh'
FISH_SNIPPET = ROOT / 'desktop/usr/share/fish/vendor_conf.d/dux-localbin.fish'
ENVIRONMENT_CONF = ROOT / 'desktop/usr/share/arch-niri-dux/environment.conf'
GENERATOR = Path('/usr/lib/systemd/user-environment-generators/30-systemd-environment-d-generator')
SH = shutil.which('sh')
FISH = shutil.which('fish')

# Realistic inherited PATH: system entries plus Nix profiles, no local bin.
START_PATH = '/usr/local/sbin:/usr/local/bin:/usr/bin:/nix/var/nix/profiles/default/bin:/home/tester/.nix-profile/bin'


def entries(path):
    return [entry for entry in path.split(':') if entry]


class SessionPathFixtures(unittest.TestCase):
    def make_home(self, directory):
        home = Path(directory) / 'home'
        home.mkdir()
        return home

    def run_sh(self, preamble, home, path=START_PATH, extra_env=None):
        if SH is None:
            self.skipTest('no POSIX sh available')
        env = {'HOME': str(home), 'PATH': path}
        if extra_env:
            env.update(extra_env)
        return subprocess.run([SH, '-c', preamble], capture_output=True, text=True,
                              env=env, check=True).stdout

    def test_login_shell_appends_local_bin_preserving_existing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            result = self.run_sh(f'. "{PROFILE_SNIPPET}"; printf "%s" "$PATH"', home)
            self.assertEqual(entries(result), entries(START_PATH) + [f'{home}/.local/bin'])

    def test_login_shell_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            result = self.run_sh(f'. "{PROFILE_SNIPPET}"; . "{PROFILE_SNIPPET}"; printf "%s" "$PATH"', home)
            self.assertEqual(entries(result).count(f'{home}/.local/bin'), 1)
            self.assertEqual(entries(result), entries(START_PATH) + [f'{home}/.local/bin'])

    def test_login_shell_with_empty_path_has_no_leading_colon(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            result = self.run_sh(f'. "{PROFILE_SNIPPET}"; printf "%s" "$PATH"', home, path='')
            self.assertEqual(result, f'{home}/.local/bin')

    def test_login_shell_without_home_leaves_path_unchanged(self):
        if SH is None:
            self.skipTest('no POSIX sh available')
        env = {'PATH': START_PATH}
        output = subprocess.run([SH, '-c', f'. "{PROFILE_SNIPPET}"; printf "%s" "$PATH"'],
                                capture_output=True, text=True, env=env, check=True).stdout
        self.assertEqual(output, START_PATH)


    @unittest.skipUnless(FISH, 'fish is not installed')
    def test_fish_login_shell_appends_local_bin(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            env = {'HOME': str(home), 'PATH': START_PATH}
            output = subprocess.run(
                [FISH, '--no-config', '-c',
                 f'source "{FISH_SNIPPET}"; string join : $PATH'],
                capture_output=True, text=True, env=env, check=True).stdout.strip()
            self.assertEqual(entries(output), entries(START_PATH) + [f'{home}/.local/bin'])

    @unittest.skipUnless(FISH, 'fish is not installed')
    def test_fish_login_shell_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            env = {'HOME': str(home), 'PATH': START_PATH}
            output = subprocess.run(
                [FISH, '--no-config', '-c',
                 f'source "{FISH_SNIPPET}"; source "{FISH_SNIPPET}"; string join : $PATH'],
                capture_output=True, text=True, env=env, check=True).stdout.strip()
            self.assertEqual(entries(output).count(f'{home}/.local/bin'), 1)

    @unittest.skipUnless(GENERATOR.is_file(), 'systemd environment generator is not installed')
    def test_environment_conf_expands_through_real_systemd_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            home = self.make_home(directory)
            xdg = scratch / 'xdg/environment.d'
            xdg.mkdir(parents=True)
            shutil.copy(ENVIRONMENT_CONF, xdg / '90-dux.conf')
            env = {'XDG_CONFIG_HOME': str(xdg.parent), 'HOME': str(home), 'PATH': START_PATH}
            output = subprocess.run([str(GENERATOR)], capture_output=True, text=True,
                                    env=env, check=True).stdout
            generated = next(line.removeprefix('PATH=') for line in output.splitlines()
                             if line.startswith('PATH='))
            self.assertEqual(entries(generated).count(f'{home}/.local/bin'), 1)
            start = entries(START_PATH)
            found = entries(generated)
            positions = [found.index(entry) for entry in start]
            self.assertEqual(positions, sorted(positions), 'existing PATH entries were reordered or dropped')
            for entry in start:
                self.assertIn(entry, found)


if __name__ == '__main__':
    unittest.main()
