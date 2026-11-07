import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import iso_checksums as iso
import release_key as key


TAG = 'v0.3.0-1'


class LocalRelease:
    """Replace only the remote asset store; signing and verification remain real."""

    def __init__(self, directory):
        self.directory = directory
        directory.mkdir()
        self.fail_upload = None

    def names(self):
        return {path.name for path in self.directory.iterdir()}

    def download(self, name, destination):
        shutil.copyfile(self.directory / name, destination)

    def upload(self, path):
        if path.name == self.fail_upload:
            raise RuntimeError('Interrupted upload')
        with (self.directory / path.name).open('xb') as stream:
            stream.write(path.read_bytes())


class IsoChecksumsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix='dux-iso-tests-')
        cls.keys = Path(cls.fixture.name)
        cls.home = cls.keys / 'offline'
        cls.home.mkdir(mode=0o700)
        key.gpg(cls.home, '--passphrase', '', '--quick-gen-key', 'Disposable ISO test', 'ed25519', 'cert', '0')
        cls.primary = key.identity(cls.home)
        for _ in range(2):
            key.gpg(cls.home, '--passphrase', '', '--quick-add-key', cls.primary, 'ed25519', 'sign', '1d')
        cls.signer = next(record['fingerprint'] for record in key.records(cls.home, True) if record['kind'] == 'ssb')
        cls.secret = key.selected_secret(cls.home, cls.primary, cls.signer).decode()
        cls.certificate = key.gpg(cls.home, '--export', cls.primary)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['gpgconf', '--homedir', str(cls.home), '--kill', 'all'], check=True)
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='dux-iso-case-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        keys = self.root / 'keys'
        keys.mkdir()
        (keys / 'integration-signing.gpg').write_bytes(self.certificate)
        (keys / 'integration-signing.fingerprint').write_text(self.primary + '\n')
        (keys / 'integration-signing.subkey').write_text(self.signer + '\n')
        self.image = self.root / iso.iso_name(TAG)
        self.image.write_bytes(b'Disposable ISO bytes, not a bootable image')
        self.sha256 = iso.digest(self.image)
        self.release = LocalRelease(self.root / 'release')
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.temp_patch = patch.object(tempfile, 'tempdir', str(self.scratch))
        self.temp_patch.start()
        self.addCleanup(self.temp_patch.stop)

    def publish(self, secret=None):
        iso.publish(self.root, TAG, self.sha256, self.release, self.secret if secret is None else secret)

    def friend_verifies(self):
        directory = self.release.directory
        environment = dict(os.environ, GNUPGHOME=str(self.scratch))
        signature = subprocess.run(
            ['gpgv', '--keyring', './integration-signing.gpg', 'SHA256SUMS.sig', 'SHA256SUMS'],
            cwd=directory,
            env=environment,
            capture_output=True,
            check=False,
        )
        if signature.returncode:
            return False
        result = subprocess.run(
            ['sha256sum', '--check', '--strict', 'SHA256SUMS'], cwd=directory, capture_output=True, check=False
        )
        return result.returncode == 0

    def test_prepare_and_friend_verification_and_identical_retry(self):
        iso.prepare(TAG, self.image, self.root / 'prepared')
        self.publish()
        shutil.copyfile(self.image, self.release.directory / self.image.name)
        self.assertTrue(self.friend_verifies())
        iso.verify(self.root, self.release.directory / 'SHA256SUMS', current_signer=True)
        before = {name: (self.release.directory / name).read_bytes() for name in iso.ASSETS}
        self.publish(secret='')
        self.assertEqual(before, {name: (self.release.directory / name).read_bytes() for name in iso.ASSETS})
        self.assertEqual(list(self.scratch.iterdir()), [])
        with self.assertRaises(FileExistsError):
            iso.prepare(TAG, self.image, self.root / 'prepared')

    def test_tampered_iso_checksums_and_signature_are_rejected(self):
        self.publish()
        shutil.copyfile(self.image, self.release.directory / self.image.name)
        for name in (self.image.name, 'SHA256SUMS', 'SHA256SUMS.sig'):
            with self.subTest(name=name):
                path = self.release.directory / name
                original = path.read_bytes()
                path.write_bytes(b'tampered')
                self.assertFalse(self.friend_verifies())
                path.write_bytes(original)

    def test_malformed_input_is_rejected_before_signing_or_upload(self):
        for tag in ('main', 'v0.3.0', 'v0.3.0-1\n', '../v0.3.0-1', 'v0.3.0-1;id'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                iso.publish(self.root, tag, self.sha256, self.release, self.secret)
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                iso.GitHubRelease(tag)
        for value in ('a' * 63, 'A' * 64, '0' * 64 + '\n', '0' * 64 + '  injected.iso'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                iso.publish(self.root, TAG, value, self.release, self.secret)
        wrong_name = self.root / 'development.iso'
        wrong_name.write_bytes(b'wrong filename')
        with self.assertRaises(ValueError):
            iso.prepare(TAG, wrong_name, self.root / 'prepared')
        target = self.image.rename(self.root / 'original.iso')
        self.image.symlink_to(target)
        with self.assertRaises(ValueError):
            iso.prepare(TAG, self.image, self.root / 'prepared')
        self.assertEqual(self.release.names(), set())

    def test_conflicts_never_overwrite_any_assets(self):
        self.publish()
        for name in iso.ASSETS:
            with self.subTest(name=name):
                path = self.release.directory / name
                original = path.read_bytes()
                path.write_bytes(b'conflicting published bytes')
                before = {asset: (self.release.directory / asset).read_bytes() for asset in iso.ASSETS}
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    self.publish()
                self.assertEqual(before, {asset: (self.release.directory / asset).read_bytes() for asset in iso.ASSETS})
                path.write_bytes(original)

    def test_partial_upload_can_resume_without_replacing_existing_assets(self):
        self.release.fail_upload = 'integration-signing.gpg'
        with self.assertRaises(RuntimeError):
            self.publish()
        signature = (self.release.directory / 'SHA256SUMS.sig').read_bytes()
        self.release.fail_upload = None
        self.publish(secret='')
        self.assertEqual((self.release.directory / 'SHA256SUMS.sig').read_bytes(), signature)
        self.assertEqual(self.release.names(), set(iso.ASSETS))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_existing_signature_must_use_selected_subkey(self):
        self.publish()
        other = next(
            record['fingerprint']
            for record in key.records(self.home, True)
            if record['kind'] == 'ssb' and record['fingerprint'] != self.signer
        )
        (self.root / 'keys/integration-signing.subkey').write_text(other + '\n')
        with self.assertRaises(ValueError):
            self.publish(secret='')

    def test_import_and_signing_failures_remove_secret_home(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish(secret='not a key')
        self.assertEqual(list(self.scratch.iterdir()), [])
        (self.root / 'keys/integration-signing.subkey').write_text('F' * 40 + '\n')
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(self.release.names(), set())

    def test_checkout_must_be_main_ancestor_and_match_tag(self):
        checkout = self.root / 'checkout'
        checkout.mkdir()

        def git(*args):
            return subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()

        git('init', '-q')
        git('config', 'user.name', 'Disposable')
        git('config', 'user.email', 'disposable@example.invalid')
        git('commit', '-qm', 'Initial', '--allow-empty')
        commit = git('rev-parse', 'HEAD')
        git('tag', TAG)
        with self.assertRaises(ValueError):
            iso.validate_checkout(checkout, TAG, self.sha256, commit)
        git('update-ref', 'refs/remotes/origin/main', commit)
        iso.validate_checkout(checkout, TAG, self.sha256, commit)
        with self.assertRaises(ValueError):
            iso.validate_checkout(checkout, TAG, self.sha256, 'f' * 40)
        git('commit', '-qm', 'Not yet on main', '--allow-empty')
        commit = git('rev-parse', 'HEAD')
        git('tag', '-f', TAG)
        with self.assertRaises(subprocess.CalledProcessError):
            iso.validate_checkout(checkout, TAG, self.sha256, commit)


if __name__ == '__main__':
    unittest.main()
