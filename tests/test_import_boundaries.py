from pathlib import Path
import unittest

from tools.dev.lint_imports import violations


class ImportBoundaryTests(unittest.TestCase):
    def test_forbidden_imports_fail_including_relative_and_from_forms(self):
        for path, source in (
            ('tools/packages/example.py', 'import tools.release.key'),
            ('tools/packages/example.py', 'from tools import verification'),
            ('tools/packages/example.py', 'from ..build import payload'),
            ('tools/build/example.py', 'from tools.release import key'),
            ('installer/example.py', 'import tools.digest'),
        ):
            with self.subTest(path=path, source=source):
                self.assertTrue(list(violations(Path(path), source)))

    def test_shared_digest_and_local_imports_are_allowed(self):
        source = 'from tools.digest import digest\nfrom . import inputs\nimport pathlib'
        self.assertEqual(list(violations(Path('tools/packages/example.py'), source)), [])
