"""Behavioral regression tests for the ~/.local/bin session PATH (issue #23).

Each context is executed in a subprocess with a controlled HOME/PATH, so the
tests prove the resulting environment instead of asserting on file contents:
POSIX login shells, the greetd Niri session wrapper (and the greetd config
that selects it), fish login shells, and the systemd user-environment
generator reading the shipped environment.conf.
"""

import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SNIPPET = ROOT / 'desktop/etc/profile.d/dux-localbin.sh'
WRAPPER = ROOT / 'desktop/usr/local/bin/dux-niri-session'
FISH_SNIPPET = ROOT / 'desktop/usr/share/fish/vendor_conf.d/dux-localbin.fish'
ENVIRONMENT_CONF = ROOT / 'desktop/usr/share/arch-niri-dux/environment.conf'
GREETD_CONFIG = ROOT / 'desktop/etc/greetd/config.toml'
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

    def run_session(self, home, path=START_PATH):
        """Execute the real wrapper with a stub niri; return (observed PATH, marker)."""
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            localbin = home / '.local/bin'
            localbin.mkdir(parents=True, exist_ok=True)
            marker = scratch / 'marker'
            probe = localbin / 'dux-path-probe'
            probe.write_text(f'#!/bin/sh\nprintf probe-ok > "{marker}"\n')
            probe.chmod(0o755)
            observed = scratch / 'observed-path'
            stubdir = scratch / 'stubbin'
            stubdir.mkdir()
            stub = stubdir / 'niri'
            stub.write_text('#!/bin/sh\n'
                            f'printf "%s" "$PATH" > "{observed}"\n'
                            'command -v dux-path-probe >/dev/null || exit 10\n'
                            'exec dux-path-probe\n')
            stub.chmod(0o755)
            env = {'HOME': str(home), 'PATH': f'{stubdir}:{path}'}
            result = subprocess.run([str(WRAPPER)], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file(), 'stub niri could not resolve the local wrapper')
            self.assertEqual(marker.read_text(), 'probe-ok')
            return observed.read_text(), str(stubdir)

    def test_niri_session_wrapper_resolves_local_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            observed, stubdir = self.run_session(home)
            self.assertEqual(entries(observed),
                             [stubdir] + entries(START_PATH) + [f'{home}/.local/bin'])

    def test_niri_session_wrapper_is_directly_executable(self):
        mode = WRAPPER.stat().st_mode
        self.assertTrue(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH),
                        'greetd must be able to exec the session wrapper')
        self.assertTrue(os.access(WRAPPER, os.X_OK))

    def test_greetd_config_selects_working_wrapper(self):
        config = tomllib.loads(GREETD_CONFIG.read_text())
        command = shlex.split(config['default_session']['command'])
        target = command[command.index('--command') + 1]
        resolved = ROOT / 'desktop' / target.lstrip('/')
        self.assertEqual(resolved, WRAPPER)
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            observed, _ = self.run_session(home)
            self.assertIn(f'{home}/.local/bin', entries(observed))

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

    def test_all_shell_contexts_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            expected = entries(START_PATH) + [f'{home}/.local/bin']
            shell_path = entries(self.run_sh(f'. "{PROFILE_SNIPPET}"; printf "%s" "$PATH"', home))
            self.assertEqual(shell_path, expected)
            observed, stubdir = self.run_session(home)
            self.assertEqual(entries(observed), [stubdir] + expected)
            if FISH:
                env = {'HOME': str(home), 'PATH': START_PATH}
                output = subprocess.run(
                    [FISH, '--no-config', '-c',
                     f'source "{FISH_SNIPPET}"; string join : $PATH'],
                    capture_output=True, text=True, env=env, check=True).stdout.strip()
                self.assertEqual(entries(output), expected)


if __name__ == '__main__':
    unittest.main()
