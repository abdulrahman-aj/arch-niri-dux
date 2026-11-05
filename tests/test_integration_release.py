import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from tests.helpers import ROOT, load_script

release = load_script('release', ROOT / 'scripts/integration-release.py')


class SignedReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='dux-signature-test-')
        cls.home = Path(cls.temporary.name) / 'gnupg'
        cls.home.mkdir(mode=0o700)
        cls.previous_home = os.environ.get('GNUPGHOME')
        os.environ['GNUPGHOME'] = str(cls.home)
        cls.gpg('--passphrase', '', '--quick-generate-key', 'Disposable integration test', 'ed25519', 'cert', '1d')
        listing = cls.gpg('--with-colons', '--list-keys').decode()
        cls.fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        cls.gpg('--passphrase', '', '--quick-add-key', cls.fingerprint, 'ed25519', 'sign', '1d')
        cls.public = cls.gpg('--export', cls.fingerprint)

    @classmethod
    def gpg(cls, *arguments):
        return subprocess.check_output(['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', *arguments],
                                       stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['gpgconf', '--kill', 'all'], check=True)
        if cls.previous_home is None:
            os.environ.pop('GNUPGHOME')
        else:
            os.environ['GNUPGHOME'] = cls.previous_home
        cls.temporary.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dux-release-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ('packages/integration', 'desktop/usr/share', 'keys'):
            (self.root / name).mkdir(parents=True)
        (self.root / 'keys/integration-signing.gpg').write_bytes(self.public)
        (self.root / 'keys/integration-signing.fingerprint').write_text(self.fingerprint + '\n')
        (self.root / 'packages/packages.target').write_text('base\n')
        (self.root / 'desktop/usr/share/example').write_text('Packaged defaults\n')
        (self.root / 'packages/integration/PKGBUILD').write_text('pkgver=0.1.0\npkgrel=1\n')

    def packages(self, destination):
        destination.mkdir()
        archive = release.build.prepare_source(self.root, destination, 1234)
        shutil.copyfile(self.root / 'packages/integration/PKGBUILD', destination / 'integration.PKGBUILD')
        version = release.build.version(self.root)
        entries = []
        for name in sorted(release.build.NAMES):
            metadata = destination / '.PKGINFO'
            metadata.write_text(f'pkgname = {name}\npkgver = {version}\npkgdesc = Test fixture\n'
                                'builddate = 1234\npackager = Test\nsize = 0\narch = any\n')
            filename = f'{name}-{version}-any.pkg.tar.zst'
            subprocess.run(['bsdtar', '--zstd', '-cf', str(destination / filename), '-C', str(destination),
                            '.PKGINFO'], check=True)
            entries.append(dict(name=name, filename=filename, sha256=release.build.digest(destination / filename)))
        (destination / '.PKGINFO').unlink()
        (destination / 'PKGBUILD').unlink()
        manifest = dict(version=version, source_date_epoch=1234, source_sha256=release.build.digest(archive),
                        recipe_sha256=release.build.digest(destination / 'integration.PKGBUILD'), packages=entries)
        (destination / 'integration.manifest.json').write_text(json.dumps(manifest))
        return destination

    def publish(self, packages, history, output, commit='a' * 40):
        release.publish(self.root, packages, history, output,
                        'integration-v' + release.build.version(self.root), commit)

    def test_signed_release_roundtrip_tampering_and_identity(self):
        packages = self.packages(self.root / 'packages-built')
        signed = self.root / 'releases/integration-v0.1.0-1'
        self.publish(packages, self.root / 'history', signed)
        release.fetch(self.root, self.root / 'fetched', (self.root / 'releases').as_uri())
        for path in signed.glob('*.pkg.tar.zst'):
            self.assertEqual(path.read_bytes(), (self.root / 'fetched' / path.name).read_bytes())
        with self.assertRaisesRegex(ValueError, 'must be empty'):
            release.fetch(self.root, self.root / 'fetched', (self.root / 'releases').as_uri())
        for name in ('integration.manifest.json', 'arch-niri-dux-0.1.0-1-any.pkg.tar.zst',
                     'integration.tar', 'integration.PKGBUILD'):
            path = signed / name
            original = path.read_bytes()
            path.write_bytes(original + b'tampered')
            with self.subTest(asset=name), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                release.fetch(self.root, self.root / ('fetched-' + name), (self.root / 'releases').as_uri())
            path.write_bytes(original)
        (self.root / 'keys/integration-signing.fingerprint').write_text('B' * 40)
        with self.assertRaisesRegex(ValueError, 'pinned identity'):
            release.verify(self.root, signed / 'integration.manifest.json')

    def test_history_is_retained_but_database_only_selects_latest_and_pages_has_no_links(self):
        old = self.root / 'old'
        self.publish(self.packages(self.root / 'first'), self.root / 'empty', old)
        recipe = self.root / 'packages/integration/PKGBUILD'
        recipe.write_text('pkgver=0.1.0\npkgrel=2\n')
        current = self.root / 'current'
        self.publish(self.packages(self.root / 'second'), old, current)
        self.assertTrue((current / 'arch-niri-dux-0.1.0-1-any.pkg.tar.zst').is_file())
        self.assertFalse(any(path.is_symlink() for path in current.iterdir()))
        with tarfile.open(current / 'arch-niri-dux.db') as database:
            self.assertEqual({name.split('/')[0] for name in database.getnames()},
                             {'arch-niri-dux-0.1.0-2', 'arch-niri-dux-baseline-0.1.0-2'})
        for suffix in ('db', 'files'):
            alias = current / f'arch-niri-dux.{suffix}'
            self.assertEqual(alias.read_bytes(), (current / (alias.name + '.tar.gz')).read_bytes())
            release.verify(self.root, alias)
        with self.assertRaisesRegex(ValueError, 'newer'):
            self.publish(self.root / 'second', current, self.root / 'replay')

    def test_changed_sources_and_commit_cannot_reuse_a_release(self):
        packages = self.packages(self.root / 'built')
        signed = self.root / 'signed'
        self.publish(packages, self.root / 'history', signed)
        with self.assertRaisesRegex(ValueError, 'different commit'):
            self.publish(signed, self.root / 'empty', self.root / 'retry', commit='b' * 40)
        (self.root / 'desktop/usr/share/example').write_text('Unreleased edit')
        with self.assertRaisesRegex(ValueError, 'differs from the release'):
            release.validate_manifest(self.root, packages)

    def test_pacman_installs_and_upgrades_from_signed_repo_and_rejects_tampered_database(self):
        first = self.root / 'first'
        self.publish(self.packages(self.root / 'v1'), self.root / 'empty', first)
        target = self.root / 'target'
        for name in ('db', 'cache', 'gnupg'):
            (target / name).mkdir(parents=True, mode=0o700)
        keyring = ['gpg', '--homedir', str(target / 'gnupg'), '--batch']
        try:
            subprocess.run(keyring + ['--import'], input=self.public, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(keyring + ['--import-ownertrust'], input=f'{self.fingerprint}:6:\n'.encode(),
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            conf = target / 'pacman.conf'

            def select(repository):
                conf.write_text('[options]\nArchitecture = auto\nSigLevel = Required TrustedOnly\n'
                                f'[arch-niri-dux]\nServer = file://{repository}\n')

            # Fakeroot supplies only UID/filesystem metadata inside this temp root;
            # pacman's database downloads, GPG verification and transactions are real.
            pacman = ['fakeroot', 'pacman', '--root', str(target), '--dbpath', str(target / 'db'),
                      '--logfile', str(target / 'log'), '--cachedir', str(target / 'cache'),
                      '--gpgdir', str(target / 'gnupg'), '--config', str(conf)]
            select(first)
            subprocess.run(pacman + ['-Syu', '--noconfirm', *sorted(release.build.NAMES)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (self.root / 'packages/integration/PKGBUILD').write_text('pkgver=0.1.0\npkgrel=2\n')
            second = self.root / 'second'
            self.publish(self.packages(self.root / 'v2'), first, second)
            select(second)
            database = second / 'arch-niri-dux.db'
            original = database.read_bytes()
            database.write_bytes(original + b'tampered')
            failed = subprocess.run(pacman + ['-Syyu', '--noconfirm'], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('signature', failed.stderr.lower())
            installed = subprocess.check_output(pacman + ['-Q'], text=True)
            self.assertIn('arch-niri-dux 0.1.0-1', installed)
            database.write_bytes(original)
            subprocess.run(pacman + ['-Syyu', '--noconfirm'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            installed = subprocess.check_output(pacman + ['-Q'], text=True)
            self.assertIn('arch-niri-dux 0.1.0-2', installed)
            self.assertIn('arch-niri-dux-baseline 0.1.0-2', installed)
        finally:
            subprocess.run(['gpgconf', '--homedir', str(target / 'gnupg'), '--kill', 'all'], check=True)

    def test_expired_historical_subkey_survives_replacement_under_same_identity(self):
        home = self.root / 'expired-key'
        home.mkdir(mode=0o700)
        gpg = ['gpg', '--homedir', str(home), '--batch', '--yes', '--pinentry-mode', 'loopback',
               '--passphrase', '']

        def run(*arguments):
            return subprocess.check_output(gpg + list(arguments), stderr=subprocess.DEVNULL)

        try:
            run('--faked-system-time', '1577836800', '--quick-gen-key', 'Historical fixture', 'ed25519', 'cert', '0')
            listing = run('--with-colons', '--list-keys').decode()
            primary = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
            run('--faked-system-time', '1577836800', '--quick-add-key', primary, 'ed25519', 'sign', '1d')
            payload = self.root / 'old-package'
            payload.write_text('Historical package')
            run('--faked-system-time', '1577836860', '--local-user', primary, '--detach-sign', str(payload))
            (self.root / 'keys/integration-signing.fingerprint').write_text(primary)
            (self.root / 'keys/integration-signing.gpg').write_bytes(run('--export', primary))
            with self.assertRaisesRegex(ValueError, 'expired'):
                release.verify(self.root, payload)
            release.verify(self.root, payload, historical=True)
            run('--quick-add-key', primary, 'ed25519', 'sign', '1d')
            (self.root / 'keys/integration-signing.gpg').write_bytes(run('--export', primary))
            release.verify(self.root, payload, historical=True)
            new = self.root / 'new-package'
            new.write_text('New package')
            run('--local-user', primary, '--detach-sign', str(new))
            release.verify(self.root, new)
        finally:
            subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'all'], check=True)


if __name__ == '__main__':
    unittest.main()
