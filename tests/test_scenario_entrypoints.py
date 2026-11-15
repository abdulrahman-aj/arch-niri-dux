"""Run the real CLI guards and parsers in fresh interpreters before VM work."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from textwrap import dedent
import unittest

from tests.fake_commands import mark
from tests.git_isolation import sanitized_env


class ScenarioEntrypointTests(unittest.TestCase):
    def test_parse_and_dispatch(self):
        alongside = ['--iso', 'image.iso', '--fixture', 'fixture.qcow2', '--firmware', 'vars.fd', '--directory', 'run']
        cases = [
            ('session', [], {'slow': False, 'fixture': None}),
            (
                'session',
                ['--slow', '--swap-gib', '0', '--keymap', 'de'],
                {'slow': True, 'swap_gib': 0, 'keymap': 'de'},
            ),
            ('session', ['--install-only'], {'install_only': True, 'fixture': None}),
            ('session', ['--fixture'], {'fixture': 'auto'}),
            (
                'session',
                ['--fixture', 'accepted', '--kernel-package', 'linux.pkg.tar.zst', '--keep'],
                {'fixture': 'accepted', 'kernel_package': 'linux.pkg.tar.zst', 'keep': True},
            ),
            ('verify_release', ['--tag', 'v0.3.2-1'], {'tag': 'v0.3.2-1', 'repository_url': None}),
            (
                'verify_release',
                ['--tag', 'v0.3.2-1', '--repository-url', 'http://localhost:8000/repo'],
                {'repository_url': 'http://localhost:8000/repo'},
            ),
            ('verify_alongside', alongside, {'os': 'linux', 'fixture': 'fixture.qcow2'}),
            (
                'verify_alongside',
                alongside + ['--os', 'windows', '--windows-token', 'report-token'],
                {'os': 'windows', 'windows_token': 'report-token'},
            ),
            ('verify_hibernate', ['--directory', 'accepted'], {'directory': 'accepted'}),
        ]
        program = dedent("""\
            import argparse
            import importlib
            import json
            import runpy
            import sys

            real_parse_args = argparse.ArgumentParser.parse_args
            def observe_parsed_args(self, args=None, namespace=None):
                # Observe the real parser's namespace, then stop before any VM work.
                parsed = real_parse_args(self, args, namespace)
                print(json.dumps(vars(parsed), default=str))
                raise SystemExit(0)
            argparse.ArgumentParser.parse_args = observe_parsed_args
            cli = sys.argv[1]
            for name in ('full', 'fixture', 'release', 'alongside', 'hibernate'):
                module = importlib.import_module('tools.verification.scenarios.' + name)
                assert callable(getattr(module, 'run_' + name))
            sys.argv = [cli, *sys.argv[2:]]
            runpy.run_module('tools.verification.' + cli, run_name='__main__')
            """)
        for cli, arguments, expected in cases:
            with self.subTest(cli=cli, arguments=arguments):
                result = subprocess.run(
                    [sys.executable, '-c', program, cli, *arguments],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                parsed = json.loads(result.stdout)
                for name, value in expected.items():
                    self.assertEqual(parsed[name], value)

    def test_dispatch_reaches_run_alongside(self):
        # verify_alongside has no cheap mode-mismatch error: run_alongside
        # checks host space and creates the overlay before validating its
        # arguments. Drive it with a fake qemu-img on a restricted PATH and
        # observe the overlay artifact plus the controlled windows-token
        # failure, so dispatch past the parser is actually exercised.
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as bindir:
            root = Path(temp)
            iso = root / 'image.iso'
            fixture = root / 'fixture.qcow2'
            firmware = root / 'vars.fd'
            for path in (iso, fixture, firmware):
                path.write_bytes(b'fixture-bytes')
            directory = root / 'run'
            case_bin = Path(bindir) / 'bin'
            case_bin.mkdir()
            qemu_img = case_bin / 'qemu-img'
            qemu_img.write_text(
                '#!/bin/sh\n# Fake qemu-img create: touch the overlay target (last argument).\n'
                'for target in "$@"; do :; done\n: > "$target"\n'
            )
            qemu_img.chmod(0o755)
            marker = mark(qemu_img, case_bin / 'qemu-img-ran')
            # checkout_state shells out to git for the evidence inputs.
            (case_bin / 'git').symlink_to(shutil.which('git'))
            program = dedent("""\
                import runpy
                import shutil
                import sys

                # disk_guard still runs its real check; only the capacity is
                # faked at the stdlib boundary so the test needs no host space.
                real_disk_usage = shutil.disk_usage
                def ample_disk_usage(path):
                    return real_disk_usage(path)._replace(free=100 * 1024**3)
                shutil.disk_usage = ample_disk_usage
                sys.argv = ['verify_alongside', *sys.argv[1:]]
                runpy.run_module('tools.verification.verify_alongside', run_name='__main__')
                """)
            result = subprocess.run(
                [
                    sys.executable,
                    '-c',
                    program,
                    '--os',
                    'windows',
                    '--iso',
                    str(iso),
                    '--fixture',
                    str(fixture),
                    '--firmware',
                    str(firmware),
                    '--directory',
                    str(directory),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=sanitized_env() | {'PATH': str(case_bin), 'XDG_CACHE_HOME': str(root / 'cache')},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn('Windows requires the provisioned fixture report-token file.', result.stderr)
            self.assertTrue((directory / 'disk.qcow2').is_file())
            self.assertTrue(marker.is_file())

    def test_dispatch_reaches_the_matching_scenario(self):
        # Mode-mismatched flags still parse, so each scenario rejects them with
        # its own error only when the CLI dispatched to the real run_*.
        # verify_alongside has no such case: run_alongside checks host space
        # and creates a disk before it validates its arguments, so it is
        # covered by test_dispatch_reaches_run_alongside instead.
        cases = [
            ('session', ['--kernel-package', 'extra.pkg.tar.zst'], '--kernel-package needs --fixture.'),
            ('session', ['--fixture', '--slow'], '--slow is for full runs'),
            (
                'verify_release',
                ['--tag', 'v0.3.2-1', '--repository-url', 'not a url'],
                '--repository-url must be an HTTP(S) URL',
            ),
            ('verify_hibernate', ['--directory', 'accepted'], 'Run full make verify first'),
        ]
        program = dedent("""\
            import runpy
            import sys

            cli = sys.argv[1]
            sys.argv = [cli, *sys.argv[2:]]
            runpy.run_module('tools.verification.' + cli, run_name='__main__')
            """)
        for cli, arguments, message in cases:
            with self.subTest(cli=cli, arguments=arguments):
                result = subprocess.run(
                    [sys.executable, '-c', program, cli, *arguments],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)

    def test_invalid_arguments_fail_before_dispatch(self):
        for cli in ('session', 'verify_release', 'verify_alongside', 'verify_hibernate'):
            with self.subTest(cli=cli):
                result = subprocess.run(
                    [sys.executable, '-m', 'tools.verification.' + cli, '--no-such-option'],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
