"""Cached packages must match their build inputs and survive artifact validation."""

import copy
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.packages import dux_apps as apps
from tools.digest import digest


class AppCacheTests(unittest.TestCase):
    def test_each_build_input_invalidates_the_key(self):
        recipe = dict(
            name='sample',
            source_date_epoch=1,
            files={'PKGBUILD': 'recipe-hash', '.SRCINFO': 'metadata-and-source-hashes'},
        )
        provenance = dict(packages='makepkg 1', configs={'config': 'hash'}, driver='builder-hash')
        original = apps.cache_key(recipe, provenance)
        for changed in (
            dict(source_date_epoch=2),
            dict(files=recipe['files'] | {'PKGBUILD': 'new'}),
            dict(files=recipe['files'] | {'.SRCINFO': 'new-source-hashes'}),
        ):
            self.assertNotEqual(original, apps.cache_key(recipe | changed, provenance))
        for field in provenance:
            changed = copy.deepcopy(provenance)
            changed[field] = 'changed'
            self.assertNotEqual(original, apps.cache_key(recipe, changed))
        self.assertNotEqual(original, apps.cache_key(recipe, provenance | {'architecture': 'aarch64'}))

    def test_restore_rejects_damage_and_wrong_package_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / 'cache'
            entry.mkdir()
            source = root / 'recipe'
            source.mkdir()
            (source / '.SRCINFO').write_text('\tpkgver = 1\n\tpkgrel = 1\n')
            output = root / 'sample-1-1-x86_64.pkg.tar.zst'
            archive = entry / output.name
            recipe = dict(name='sample')
            for name in ('sample', 'impostor'):
                (source / '.PKGINFO').write_text(f'pkgname = {name}\npkgver = 1-1\narch = x86_64\n')
                subprocess.run(['bsdtar', '--zstd', '-cf', str(archive), '-C', str(source), '.PKGINFO'], check=True)
                (entry / 'sha256').write_text(digest(archive))
                self.assertEqual(apps.restore_cache(entry, output, recipe, source), name == 'sample')
                if name == 'sample':
                    self.assertEqual(digest(output), digest(archive))
                    archive.write_bytes(b'damaged archive')
                    self.assertFalse(apps.restore_cache(entry, output, recipe, source))
            archive.unlink()
            self.assertFalse(apps.restore_cache(entry, output, recipe, source))


if __name__ == '__main__':
    unittest.main()
