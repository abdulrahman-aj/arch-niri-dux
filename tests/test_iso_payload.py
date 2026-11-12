import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from installer.install import verify_repository
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import iso_payload


class PayloadFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / 'repo'
        self.repository.mkdir()
        (self.root / 'packages').mkdir()
        (self.root / 'archiso').mkdir()
        (self.root / 'out').mkdir()
        self.output = self.root / 'out/packages.manifest.json'
        (self.root / 'packages/archive-date').write_text('2026/11/10\n')
        (self.root / 'packages/packages.target').write_text('base\nlimine-mkinitcpio-hook\nlimine-snapper-sync\n')
        (self.root / 'archiso/packages.live').write_text('# fixture live list\n')
        base = self.package('base')
        self.arch = [
            base
            | {
                'version': '1-1',
                'url': 'https://archive.example.invalid/' + base['filename'],
                'repository': 'core',
            }
        ]
        self.pin = [
            self.package(name, repository='omarchy')
            | {'version': '1-1', 'url': 'https://example.invalid/' + name, 'signature': 'producer-verified'}
            for name in ('limine-mkinitcpio-hook', 'limine-snapper-sync')
        ]
        own = [self.package(name) for name in ('arch-niri-dux', 'arch-niri-dux-baseline')]
        self.recipe = dict(
            name='example-bin', source_date_epoch=1234, files={'PKGBUILD': 'e' * 64, '.SRCINFO': 'f' * 64}
        )
        self.apps = [self.package('example-bin') | {'version': '1-1', 'recipe': self.recipe}]
        self.integration = dict(
            version='1-1',
            source_sha256='a' * 64,
            recipe_sha256='b' * 64,
            source_date_epoch=1234,
            packages=own,
            apps=self.apps,
            tag='v1-1',
            source_commit='c' * 40,
        )
        self.save()

    def package(self, name, directory=None, repository=None):
        directory = directory or self.repository
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            source = Path(temporary)
            (source / '.PKGINFO').write_text(
                f'pkgname = {name}\npkgver = 1-1\n'
                'pkgdesc = Fixture\nbuilddate = 1234\n'
                'packager = Fixture\nsize = 0\narch = any\n'
            )
            path = directory / f'{name}-1-1-any.pkg.tar.zst'
            subprocess.run(['bsdtar', '--zstd', '-cf', str(path), '-C', str(source), '.PKGINFO'], check=True)
        entry = dict(name=name, filename=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        if repository is not None:
            entry['repository'] = repository
        return entry

    def save(self):
        for path, value in [
            (self.root / 'packages/dux-apps-recipes.json', [self.recipe]),
            (self.root / 'packages/omarchy-pin.json', self.pin),
            (self.repository / 'arch-downloaded.json', self.arch),
            (self.repository / 'integration.manifest.json', self.integration),
        ]:
            path.write_text(json.dumps(value))

    def expected_manifest(self):
        return dict(
            schema=1,
            archive_date='2026/11/10',
            requested=['base', 'limine-mkinitcpio-hook', 'limine-snapper-sync'],
            packages=sorted(self.arch + self.pin, key=lambda e: e['name']),
            integration=self.integration,
        )

    def rejected(self, message):
        self.output.write_text('Previous complete manifest\n')
        with self.assertRaisesRegex(ValueError, message):
            iso_payload.finalize(self.root, self.repository)
        self.assertEqual(self.output.read_text(), 'Previous complete manifest\n')
        self.assertFalse((self.repository / 'SHA256SUMS').exists())

    def test_finalizes_all_origins_and_replaces_bootstrap_database(self):
        obsolete = self.package('obsolete', self.root / 'old')
        subprocess.run(
            ['repo-add', str(self.repository / 'offline.db.tar.gz'), str(self.root / 'old' / obsolete['filename'])],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        (self.repository / 'offline.db.tar.gz.old').write_text('Old bootstrap database')
        iso_payload.finalize(self.root, self.repository)
        combined = json.loads(self.output.read_text())
        self.assertEqual(combined, self.expected_manifest())
        self.assertFalse((self.repository / 'arch-downloaded.json').exists())
        verify_repository(self.repository)
        with tarfile.open(self.repository / 'offline.db') as database:
            descriptions = [
                database.extractfile(member).read().decode() for member in database if member.name.endswith('/desc')
            ]
        self.assertEqual(len(descriptions), 6)
        self.assertFalse(any('%NAME%\nobsolete\n' in text for text in descriptions))
        self.assertFalse((self.repository / 'offline.db.tar.gz.old').exists())
        listed = {line.split()[1] for line in (self.repository / 'SHA256SUMS').read_text().splitlines()}
        self.assertEqual(listed, {p.name for p in self.repository.glob('*.pkg.tar.zst')} | {'offline.db'})

    def test_bad_archive_date_is_rejected(self):
        (self.root / 'packages/archive-date').write_text('last-week\n')
        self.rejected('archive date')

    def test_unknown_pin_name_is_rejected(self):
        self.pin[0]['name'] = 'unreviewed-hook'
        self.save()
        self.rejected('two reviewed')

    def test_missing_package_stops_without_replacing_manifest(self):
        (self.repository / self.arch[0]['filename']).unlink()
        self.rejected('Missing packages')

    def test_unexpected_package_is_rejected_and_not_deleted(self):
        extra = self.package('stale')
        self.rejected('Unexpected packages')
        self.assertTrue((self.repository / extra['filename']).is_file())

    def test_corrupted_package_stops_without_replacing_manifest(self):
        path = self.repository / self.arch[0]['filename']
        path.write_bytes(path.read_bytes() + b'corruption')
        self.rejected('checksum mismatch')

    def test_metadata_name_must_match_the_actual_package_database(self):
        self.arch[0]['name'] = 'pretend'
        self.save()
        self.rejected('database inventory')

    def test_metadata_version_must_match_the_actual_package_database(self):
        self.arch[0]['version'] = '99-1'
        self.save()
        self.rejected('database inventory')

    def test_duplicate_package_names_are_rejected_across_origins(self):
        self.integration['packages'][0]['name'] = 'base'
        self.save()
        self.rejected('Duplicate package name')

    def test_duplicate_package_filenames_are_rejected_across_origins(self):
        self.integration['packages'][0]['filename'] = self.arch[0]['filename']
        self.save()
        self.rejected('Duplicate package filename')

    def test_missing_app_is_rejected_when_recipes_exist(self):
        self.integration['apps'] = []
        self.save()
        self.rejected('Dux app inventory')

    def test_stale_app_provenance_is_rejected(self):
        self.apps[0]['recipe'] = self.recipe | {'files': {'PKGBUILD': 'a' * 64, '.SRCINFO': 'f' * 64}}
        self.save()
        self.rejected('Dux app provenance')

    def test_unsafe_package_filename_is_rejected(self):
        self.arch[0]['filename'] = '../escape.pkg.tar.zst'
        self.save()
        self.rejected('Unsafe package filename')

    def test_package_symlink_is_rejected(self):
        path = self.repository / self.arch[0]['filename']
        moved = self.root / path.name
        path.rename(moved)
        path.symlink_to(moved)
        self.rejected('regular file')

    def test_another_repository_database_is_rejected(self):
        (self.repository / 'unexpected.db').write_text('Another database')
        self.rejected('Unexpected repository database')


if __name__ == '__main__':
    unittest.main()
