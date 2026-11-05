"""New first-party files must reach the real linters without editing a file list."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LintDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git('init', '--quiet')
        self.write('.gitignore', '/out/\n')

    def git(self, *args):
        subprocess.run(['git', *args], cwd=self.root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def shellcheck(self, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/lint-shell.py')],
                              cwd=cwd or self.root, text=True, capture_output=True)

    def test_new_shell_files_anywhere_are_checked_without_staging(self):
        for name, prefix in [('new directory/out/extensionless', '#!/usr/bin/env bash\n'),
                             ('portable', '#!/bin/sh\n'),
                             ('env-s', '#!/usr/bin/env -S bash -eu\n'),
                             ('--flag.sh', '# shellcheck shell=sh\n')]:
            with self.subTest(name=name):
                file = self.write(name, prefix + 'echo $missing\n')
                result = self.shellcheck()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SC2086', result.stdout)
                file.write_text(prefix + 'echo "checked"\n')
                self.assertEqual(self.shellcheck().returncode, 0)

    def test_ignored_vendored_deleted_and_external_sources_are_not_linted(self):
        self.write('out/generated.sh', 'broken shell !!!\n')
        self.write('packages/aur/upstream/PKGBUILD', 'broken shell !!!\n')
        deleted = self.write('deleted.sh', 'broken shell !!!\n')
        self.git('add', '--force', 'out/generated.sh', 'packages/aur', 'deleted.sh')
        deleted.unlink()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'external.sh'
            external.write_text('broken shell !!!\n')
            (self.root / 'external.sh').symlink_to(external)
            self.assertEqual(self.shellcheck().returncode, 0)

    def test_running_from_a_subdirectory_still_checks_the_repository(self):
        self.write('outside.sh', '#!/bin/sh\necho $missing\n')
        nested = self.root / 'nested'
        nested.mkdir()
        result = self.shellcheck(cwd=nested)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SC2086', result.stdout)

    def test_makepkg_context_still_catches_real_shell_errors(self):
        recipe = self.write('new package/PKGBUILD',
                            "pkgbase='example-app'\npkgname=(example-app)\npkgver=1\n"
                            'package() { mkdir -p "$pkgdir/usr"; echo "$srcdir"; }\n')
        self.assertEqual(self.shellcheck().returncode, 0)
        recipe.write_text(recipe.read_text() + 'echo $unquoted\n')
        result = self.shellcheck()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SC2086', result.stdout)

    def test_python_discovery_is_repository_wide_and_preserves_exclusions(self):
        shutil.copyfile(ROOT / 'ruff.toml', self.root / 'ruff.toml')
        file = self.write('new component/out/module.py', 'import unused_import\n')
        self.write('out/generated.py', 'invalid python !!!\n')
        self.write('packages/aur/upstream/module.py', 'invalid python !!!\n')
        command = ['ruff', 'check', '--no-cache', '.']
        result = subprocess.run(command, cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('F401', result.stdout)
        file.write_text('')
        result = subprocess.run(command, cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout)
