from pathlib import Path
import subprocess
import sys
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
            ('tools/packages/example.py', 'import tools'),
            ('tools/build/example.py', 'import tools'),
            ('tools/packages/example.py', 'from ..verification import guest'),
            ('tools/build/example.py', 'from tools import release'),
        ):
            with self.subTest(path=path, source=source):
                self.assertTrue(list(violations(Path(path), source)))

    def test_shared_digest_and_local_imports_are_allowed(self):
        source = 'from tools.digest import digest\nfrom . import inputs\nimport pathlib'
        self.assertEqual(list(violations(Path('tools/packages/example.py'), source)), [])


class ArtifactImportTests(unittest.TestCase):
    def test_artifact_consumer_does_not_load_publication_or_probes(self):
        subprocess.run(
            [
                sys.executable,
                '-c',
                'import sys; import tools.packages.artifacts; '
                'forbidden = [name for name in sys.modules '
                'if name.startswith(("tools.release", "tools.verification"))]; '
                'assert not forbidden, forbidden',
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
        )
