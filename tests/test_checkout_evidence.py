"""Checkout identity: clean, dirty, staged, untracked and ignored states."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.workspace.source import checkout_state
from tests.git_isolation import check_git_output


class CheckoutEvidenceTests(unittest.TestCase):
    def test_clean_dirty_and_ignored_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args):
                return check_git_output(*args, cwd=root)

            git('init', '-q')
            (root / '.gitignore').write_text('out/\n')
            (root / 'tracked').write_bytes(b'original\x00bytes')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Initial')
            clean = {'commit': git('rev-parse', 'HEAD').decode().strip(), 'dirty': False}
            self.assertEqual(checkout_state(root), clean)
            (root / 'out').mkdir()
            (root / 'out/log').touch()
            self.assertEqual(checkout_state(root), clean)
            (root / 'tracked').write_bytes(b'changed\x00bytes')
            expected = hashlib.sha256(git('diff', 'HEAD', '--binary')).hexdigest()
            self.assertEqual(checkout_state(root), clean | {'dirty': True, 'diff_sha256': expected})
            git('add', 'tracked')
            self.assertEqual(checkout_state(root)['diff_sha256'], expected)
            git('reset', '--hard', 'HEAD')
            (root / 'untracked').touch()
            self.assertEqual(
                checkout_state(root), clean | {'dirty': True, 'diff_sha256': hashlib.sha256(b'').hexdigest()}
            )
