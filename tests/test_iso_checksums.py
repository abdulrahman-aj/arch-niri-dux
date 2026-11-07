import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import iso_checksums as iso
import release_key as key


TAG = 'v0.3.0-2'


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
        self.output = self.root / 'signed'
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.temp_patch = patch.object(tempfile, 'tempdir', str(self.scratch))
        self.temp_patch.start()
        self.addCleanup(self.temp_patch.stop)

    def sign(self, secret=None):
        iso.sign_checksums(self.root, TAG, self.sha256, self.output, self.secret if secret is None else secret)

    def friend_verifies(self):
        directory = self.output
        environment = dict(os.environ, GNUPGHOME=str(self.scratch))
        signature = subprocess.run(
            ['gpgv', '--keyring', str(self.root / 'keys/integration-signing.gpg'), 'SHA256SUMS.sig', 'SHA256SUMS'],
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

    def test_prepare_sign_and_friend_verification(self):
        iso.prepare(TAG, self.image, self.root / 'prepared')
        self.sign()
        (self.output / self.image.name).write_bytes(self.image.read_bytes())
        self.assertTrue(self.friend_verifies())
        iso.verify(self.root, self.output / 'SHA256SUMS', current_signer=True)
        self.assertEqual(list(self.scratch.iterdir()), [])
        with self.assertRaises(FileExistsError):
            iso.prepare(TAG, self.image, self.root / 'prepared')

    def test_tampered_iso_checksums_and_signature_are_rejected(self):
        self.sign()
        (self.output / self.image.name).write_bytes(self.image.read_bytes())
        for name in (self.image.name, 'SHA256SUMS', 'SHA256SUMS.sig'):
            with self.subTest(name=name):
                path = self.output / name
                original = path.read_bytes()
                path.write_bytes(b'tampered')
                self.assertFalse(self.friend_verifies())
                path.write_bytes(original)

    def test_malformed_input_is_rejected_before_signing(self):
        for tag in ('main', 'v0.3.0', 'v0.3.0-1\n', '../v0.3.0-1', 'v0.3.0-1;id'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                iso.sign_checksums(self.root, tag, self.sha256, self.output, self.secret)
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                iso.validate_release(tag)
        for value in ('a' * 63, 'A' * 64, '0' * 64 + '\n', '0' * 64 + '  injected.iso'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                iso.sign_checksums(self.root, TAG, value, self.output, self.secret)
        wrong_name = self.root / 'development.iso'
        wrong_name.write_bytes(b'wrong filename')
        with self.assertRaises(ValueError):
            iso.prepare(TAG, wrong_name, self.root / 'prepared')
        target = self.image.rename(self.root / 'original.iso')
        self.image.symlink_to(target)
        with self.assertRaises(ValueError):
            iso.prepare(TAG, self.image, self.root / 'prepared')
        self.assertFalse(self.output.exists())

    def test_import_and_signing_failures_remove_secret_home(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.sign(secret='not a key')
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.output = self.root / 'another-attempt'
        (self.root / 'keys/integration-signing.subkey').write_text('F' * 40 + '\n')
        with self.assertRaises(subprocess.CalledProcessError):
            self.sign()
        self.assertEqual(list(self.scratch.iterdir()), [])

    def checkout(self):
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()

        git('init', '-q')
        git('config', 'user.name', 'Disposable')
        git('config', 'user.email', 'disposable@example.invalid')
        git('commit', '-qm', 'Package release', '--allow-empty')
        source = git('rev-parse', 'HEAD')
        git('tag', TAG)
        git('commit', '-qm', 'Updated workflow on main', '--allow-empty')
        commit = git('rev-parse', 'HEAD')
        git('update-ref', 'refs/remotes/origin/main', commit)
        return git, source, commit

    def test_main_workflow_can_authorize_older_release_tag(self):
        git, source, commit = self.checkout()
        self.assertEqual(iso.validate_checkout(self.root, TAG, self.sha256, commit), source)
        with self.assertRaises(ValueError):
            iso.validate_checkout(self.root, TAG, self.sha256, 'f' * 40)
        with self.assertRaises(ValueError):
            iso.validate_checkout(self.root, TAG, self.sha256, commit, source_commit='f' * 40)
        git('commit', '-qm', 'Not yet on main', '--allow-empty')
        unmerged = git('rev-parse', 'HEAD')
        with self.assertRaises(subprocess.CalledProcessError):
            iso.validate_checkout(self.root, TAG, self.sha256, unmerged)
        git('tag', '-f', TAG)
        git('checkout', '--detach', commit)
        with self.assertRaises(subprocess.CalledProcessError):
            iso.validate_checkout(self.root, TAG, self.sha256, commit)

    def test_published_release_validation(self):
        for tag, draft, prerelease in (
            (TAG, False, False),
            (TAG, True, False),
            (TAG, False, True),
            ('v0.2.0-1', False, False),
        ):
            response = json.dumps(dict(tag_name=tag, draft=draft, prerelease=prerelease)).encode()
            with self.subTest(tag=tag, draft=draft, prerelease=prerelease):
                with patch.object(iso.subprocess, 'check_output', return_value=response):
                    if tag == TAG and not draft and not prerelease:
                        iso.validate_release(TAG)
                    else:
                        with self.assertRaises(ValueError):
                            iso.validate_release(TAG)
        with patch.object(iso.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'gh')):
            with self.assertRaises(subprocess.CalledProcessError):
                iso.validate_release(TAG)

    def test_cli_signs_from_main_and_rejects_tag_dispatch(self):
        _, source, commit = self.checkout()
        arguments = [
            'iso_checksums.py',
            'sign',
            '--root',
            str(self.root),
            '--tag',
            TAG,
            '--sha256',
            self.sha256,
            '--commit',
            commit,
            '--source-commit',
            source,
            '--output',
            str(self.output),
        ]
        environment = dict(
            GITHUB_REPOSITORY=iso.REPOSITORY,
            GITHUB_REF='refs/tags/' + TAG,
            PACKAGE_SIGNING_KEY=self.secret,
            GITHUB_STEP_SUMMARY=str(self.root / 'summary'),
        )
        with patch.dict(os.environ, environment), patch.object(sys, 'argv', arguments):
            with self.assertRaises(ValueError):
                iso.main()
        environment['GITHUB_REF'] = 'refs/heads/main'
        with patch.dict(os.environ, environment), patch.object(sys, 'argv', arguments):
            # Only the read-only GitHub lookup is replaced. Git validation and signing are real.
            with patch.object(iso, 'validate_release'):
                iso.main()
            self.assertNotIn('PACKAGE_SIGNING_KEY', os.environ)
        (self.output / self.image.name).write_bytes(self.image.read_bytes())
        self.assertTrue(self.friend_verifies())
        self.assertEqual(list(self.scratch.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
