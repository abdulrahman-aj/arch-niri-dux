"""Run the real CLI guards and parsers in fresh interpreters before VM work."""

import json
from pathlib import Path
import subprocess
import sys
from textwrap import dedent
import unittest


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

    def test_dispatch_reaches_the_matching_scenario(self):
        # Mode-mismatched flags still parse, so each scenario rejects them with
        # its own error only when the CLI dispatched to the real run_*.
        # verify_alongside has no case: run_alongside checks host space and creates a disk before it
        # validates its arguments, so it has no cheap mode-mismatch error to observe.
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
