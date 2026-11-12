import sys
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_build as builder


class IntegrationSourceTests(unittest.TestCase):
    def test_archive_is_reproducible_and_keeps_source_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages/integration').mkdir(parents=True)
            (root / 'desktop/usr/share').mkdir(parents=True)
            (root / 'packages/integration/PKGBUILD').write_text('pkgname=example\n')
            (root / 'packages/packages.target').write_text('base\nniri\n')
            asset = root / 'desktop/usr/share/default.kdl'
            asset.write_text('binds { Mod+Return { spawn "xdg-terminal-exec"; } }\n')
            one = root / 'one'
            two = root / 'two'
            one.mkdir()
            two.mkdir()
            first = builder.prepare_source(root, one, 1234)
            os.utime(asset, (9000, 9000))
            asset.chmod(0o755)
            second = builder.prepare_source(root, two, 1234)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                self.assertEqual(archive.extractfile('desktop/usr/share/default.kdl').read(), asset.read_bytes())
                self.assertEqual(archive.extractfile('packages/packages.target').read(), b'base\nniri\n')
                self.assertTrue(all(entry.uid == entry.gid == 0 and entry.mtime == 1234 for entry in archive))

    def test_archive_refuses_linked_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'packages').mkdir()
            (root / 'packages/packages.target').write_text('base\n')
            (root / 'desktop').mkdir()
            (root / 'desktop/escape').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'source symlink'):
                builder.prepare_source(root, root, 1234)


if __name__ == '__main__':
    unittest.main()
