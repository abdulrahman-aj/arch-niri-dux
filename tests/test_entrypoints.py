"""Run host CLI guards in fresh interpreters, before expensive VM acceptance."""

import ast
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def entrypoints(root):
    # Include Makefile modules even when a missing guard would make --help a no-op.
    modules = set(re.findall(r'python3? -m ((?:tools|installer)\.[\w.]+)', (root / 'Makefile').read_text()))
    for directory in ('tools', 'installer'):
        for path in (root / directory).rglob('*.py'):
            # Guest probes dispatch actions, not CLI arguments, and require a guest.
            if 'probes' in path.relative_to(root).parts:
                continue
            tree = ast.parse(path.read_text())
            if any(
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and ast.dump(node.test) == ast.dump(ast.parse("__name__ == '__main__'", mode='eval').body)
                for node in tree.body
            ):
                name = '.'.join(path.relative_to(root).with_suffix('').parts)
                modules.add(name.removesuffix('.__main__'))
    return sorted(modules)


class EntrypointTests(unittest.TestCase):
    def test_help_in_fresh_processes(self):
        commands = [[sys.executable, '-m', name, '--help'] for name in entrypoints(ROOT)]
        # The installed launcher uses script-style sibling imports as well.
        commands.append([sys.executable, 'installer/install.py', '--help'])
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(
                    command, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False
                )
                self.assertEqual(result.returncode, 0, f'{command}:\n{result.stdout}{result.stderr}')
