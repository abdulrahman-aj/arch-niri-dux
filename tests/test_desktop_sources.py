"""Vendored desktop files must stay byte-identical to their recorded upstream."""

import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DesktopSourcesTests(unittest.TestCase):
    def test_vendored_files_match_recorded_hashes(self):
        for source in json.loads((ROOT / 'packages/desktop-sources.json').read_text()):
            with self.subTest(path=source['path']):
                actual = hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest()
                self.assertEqual(actual, source['sha256'], 'Upstream desktop asset changed')


if __name__ == '__main__':
    unittest.main()
