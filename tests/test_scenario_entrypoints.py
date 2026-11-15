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
            ('session', 'full', [], {'slow': False, 'fixture': None}),
            (
                'session',
                'full',
                ['--slow', '--swap-gib', '0', '--keymap', 'de'],
                {'slow': True, 'swap_gib': 0, 'keymap': 'de'},
            ),
            ('session', 'full', ['--install-only'], {'install_only': True, 'fixture': None}),
            ('session', 'fixture', ['--fixture'], {'fixture': 'auto'}),
            (
                'session',
                'fixture',
                ['--fixture', 'accepted', '--kernel-package', 'linux.pkg.tar.zst', '--keep'],
                {'fixture': 'accepted', 'kernel_package': 'linux.pkg.tar.zst', 'keep': True},
            ),
            ('verify_release', 'release', ['--tag', 'v0.3.2-1'], {'tag': 'v0.3.2-1', 'repository_url': None}),
            (
                'verify_release',
                'release',
                ['--tag', 'v0.3.2-1', '--repository-url', 'http://localhost:8000/repo'],
                {'repository_url': 'http://localhost:8000/repo'},
            ),
            ('verify_alongside', 'alongside', alongside, {'os': 'linux', 'fixture': 'fixture.qcow2'}),
            (
                'verify_alongside',
                'alongside',
                alongside + ['--os', 'windows', '--windows-token', 'report-token'],
                {'os': 'windows', 'windows_token': 'report-token'},
            ),
            ('verify_hibernate', 'hibernate', ['--directory', 'accepted'], {'directory': 'accepted'}),
        ]
        program = dedent("""\
            import importlib
            import json
            import runpy
            import sys

            cli = sys.argv[1]
            def dispatch(name):
                def run(args, parser):
                    print(json.dumps({'scenario': name, 'args': vars(args)}, default=str))
                    return 0
                return run
            for name in ('full', 'fixture', 'release', 'alongside', 'hibernate'):
                module = importlib.import_module('tools.verification.scenarios.' + name)
                assert callable(getattr(module, 'run_' + name))
                setattr(module, 'run_' + name, dispatch(name))
            sys.argv = [cli, *sys.argv[2:]]
            runpy.run_module('tools.verification.' + cli, run_name='__main__')
            """)
        for cli, scenario, arguments, expected in cases:
            with self.subTest(cli=cli, arguments=arguments):
                result = subprocess.run(
                    [sys.executable, '-c', program, cli, *arguments],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                dispatched = json.loads(result.stdout)
                self.assertEqual(dispatched['scenario'], scenario)
                for name, value in expected.items():
                    self.assertEqual(dispatched['args'][name], value)

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
