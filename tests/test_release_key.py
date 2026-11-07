import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import release_key as key


class RecoveryTests(unittest.TestCase):
    def test_encrypted_backup_restores_identity_and_ci_only_has_signing_subkey(self):
        with tempfile.TemporaryDirectory(prefix='dux-recovery-test-') as directory:
            root = Path(directory)
            password = root / 'password'
            password.write_text('Disposable test password\n')
            backup = root / 'recovery.gpg'
            exported = []
            real_run = subprocess.run

            def record_upload(command, **kwargs):
                if command[:3] == ['gh', 'secret', 'set']:
                    exported.append(kwargs['input'])
                    return subprocess.CompletedProcess(command, 0)
                return real_run(command, **kwargs)

            with patch.object(key.subprocess, 'run', side_effect=record_upload):
                key.provision(root, password, backup, upload=True)
                fingerprint = (root / 'keys/integration-signing.fingerprint').read_text()
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(ValueError, 'Refusing to replace'):
                    key.provision(root, password, backup, upload=True)
                key.provision(root, password, backup, restore=backup, upload=True)
                self.assertEqual((root / 'keys/integration-signing.fingerprint').read_text(), fingerprint)
            self.assertEqual(len(exported), 2)
            home = root / 'ci'
            home.mkdir(mode=0o700)
            try:
                key.gpg(home, '--import', data=exported[0])
                listing = key.gpg(home, '--with-colons', '--list-secret-keys').decode()
                primary = next(line.split(':') for line in listing.splitlines() if line.startswith('sec:'))
                subkey = next(line.split(':') for line in listing.splitlines() if line.startswith('ssb:'))
                self.assertEqual(primary[14], '#', 'CI must not receive the certification secret')
                self.assertEqual(subkey[14], '+', 'CI must have the signing subkey')
                message = root / 'message'
                message.write_text('A release signed by CI\n')
                key.gpg(home, '--local-user', fingerprint.strip(), '--detach-sign', str(message))
                subprocess.run(
                    [
                        'gpgv',
                        '--keyring',
                        str(root / 'keys/integration-signing.gpg'),
                        str(message) + '.sig',
                        str(message),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            finally:
                real_run(['gpgconf', '--homedir', str(home), '--kill', 'all'], check=True)

    def test_multiple_subkeys_export_only_selected_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'offline'
            home.mkdir(mode=0o700)
            ci = Path(directory) / 'ci'
            ci.mkdir(mode=0o700)
            try:
                key.gpg(home, '--passphrase', '', '--quick-gen-key', 'Disposable selection', 'ed25519', 'cert', '0')
                primary = key.identity(home)
                for _ in range(2):
                    key.gpg(home, '--passphrase', '', '--quick-add-key', primary, 'ed25519', 'sign', '1d')
                subkeys = [record['fingerprint'] for record in key.records(home, True) if record['kind'] == 'ssb']
                key.gpg(ci, '--import', data=key.selected_secret(home, primary, subkeys[0]))
                key.gpg(ci, '--local-user', subkeys[0] + '!', '--detach-sign', data=b'disposable')
                with self.assertRaises(subprocess.CalledProcessError):
                    key.gpg(ci, '--local-user', subkeys[1] + '!', '--detach-sign', data=b'disposable')
                with self.assertRaises(subprocess.CalledProcessError):
                    key.gpg(ci, '--passphrase', '', '--quick-add-key', primary, 'ed25519', 'sign', '1d')
                with self.assertRaises(ValueError):
                    key.selected_secret(home, primary, 'F' * 40)
            finally:
                for path in (home, ci):
                    subprocess.run(['gpgconf', '--homedir', str(path), '--kill', 'all'], check=True)


if __name__ == '__main__':
    unittest.main()
