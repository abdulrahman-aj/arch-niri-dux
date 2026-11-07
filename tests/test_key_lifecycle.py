import subprocess
from pathlib import Path
import unittest


class LifecycleTests(unittest.TestCase):
    def test_real_pacman_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(
            [
                'fakeroot',
                'python3',
                '-c',
                'from verification.key_lifecycle import qualify; from pathlib import Path; '
                f'qualify(Path({str(root / "desktop/usr/bin/arch-niri-dux-keyring-recover")!r}), fakeroot=False)',
            ],
            cwd=root / 'scripts',
            check=True,
        )

    def test_recovery_rejects_certificates_without_current_signing_authority(self):
        import hashlib
        import runpy
        import tempfile

        root = Path(__file__).resolve().parents[1]
        recover = runpy.run_path(str(root / 'desktop/usr/bin/arch-niri-dux-keyring-recover'))['recover']
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            home = work / 'gnupg'
            home.mkdir(mode=0o700)

            def gpg(*args):
                return subprocess.check_output(
                    [
                        'gpg',
                        '--homedir',
                        str(home),
                        '--batch',
                        '--yes',
                        '--pinentry-mode',
                        'loopback',
                        '--passphrase',
                        '',
                        *args,
                    ],
                    stderr=subprocess.DEVNULL,
                )

            try:
                gpg('--faked-system-time', '1577836800', '--quick-gen-key', 'Expired fixture', 'ed25519', 'cert', '0')
                primary = next(
                    line.split(':')[9]
                    for line in gpg('--with-colons', '--list-keys').decode().splitlines()
                    if line.startswith('fpr:')
                )
                certificate = work / 'certificate'
                anchor = work / 'anchor'
                anchor.write_bytes(gpg('--export', primary))
                samples = [b'malformed certificate', anchor.read_bytes()]
                gpg('--faked-system-time', '1577836800', '--quick-add-key', primary, 'ed25519', 'sign', '1d')
                samples.append(gpg('--export', primary))
                samples.append(gpg('--export-secret-keys', primary))
                gpg('--quick-gen-key', 'Unrelated fixture', 'ed25519', 'sign', '1d')
                samples.append(gpg('--export'))
                for data in samples:
                    with self.subTest(size=len(data)):
                        certificate.write_bytes(data)
                        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                            recover(certificate, hashlib.sha256(data).hexdigest(), anchor, work / 'unused-keyring')
            finally:
                subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'all'], check=True)
