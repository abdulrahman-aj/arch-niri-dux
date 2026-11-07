#!/usr/bin/env python3
"""Provision the protected CI signing subkey and an encrypted offline recovery copy."""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


REPOSITORY = 'abdulrahman-aj/arch-niri-dux'


def gpg(home, *arguments, data=None):
    return subprocess.check_output(
        ['gpg', '--homedir', str(home), '--batch', '--yes', '--pinentry-mode', 'loopback', *arguments], input=data
    )


def identity(home):
    listing = gpg(home, '--with-colons', '--list-secret-keys').decode()
    keys = [line for line in listing.splitlines() if line.startswith('sec:')]
    fingerprints = [line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:')]
    if len(keys) != 1 or len(fingerprints) != 2:
        raise ValueError('Expected exactly one certification key and one signing subkey.')
    return fingerprints[0]


def provision(root, passphrase, backup, restore=None):
    public = root / 'keys/integration-signing.gpg'
    fingerprint = root / 'keys/integration-signing.fingerprint'
    if restore is None and (public.exists() or fingerprint.exists() or backup.exists()):
        raise ValueError('Refusing to replace an existing release identity or recovery copy.')
    with tempfile.TemporaryDirectory(prefix='dux-release-key-') as directory:
        home = Path(directory)
        home.chmod(0o700)
        try:
            if restore is None:
                gpg(
                    home,
                    '--passphrase',
                    '',
                    '--quick-generate-key',
                    'Arch Niri Dux package releases <noreply@abdulrahman-aj.github.io>',
                    'rsa3072',
                    'cert',
                    '0',
                )
                listing = gpg(home, '--with-colons', '--list-secret-keys').decode()
                primary = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
                gpg(home, '--passphrase', '', '--quick-add-key', primary, 'rsa3072', 'sign', '2y')
                # The primary stays off CI. Its only durable private copy is encrypted
                # with the owner's recovery password, separately from GitHub secrets.
                secret = gpg(home, '--export-secret-keys', primary)
                encrypted = gpg(
                    home, '--passphrase-file', str(passphrase), '--symmetric', '--cipher-algo', 'AES256', data=secret
                )
                backup.parent.mkdir(parents=True, exist_ok=True)
                with backup.open('xb') as output:
                    os.chmod(backup, 0o600)
                    output.write(encrypted)
            else:
                secret = gpg(home, '--passphrase-file', str(passphrase), '--decrypt', str(restore))
                gpg(home, '--import', data=secret)
            primary = identity(home)
            if restore is not None and fingerprint.read_text().strip() != primary:
                raise ValueError('Recovery key does not match the checked-in release identity.')
            key = gpg(home, '--export', primary)
            ci = gpg(home, '--armor', '--export-secret-subkeys', primary)
            public.parent.mkdir(parents=True, exist_ok=True)
            public.write_bytes(key)
            fingerprint.write_text(primary + '\n')
            # Only a signing subkey (primary secret stub) enters the protected environment.
            subprocess.run(
                ['gh', 'secret', 'set', 'PACKAGE_SIGNING_KEY', '--repo', REPOSITORY, '--env', 'integration-release'],
                input=ci,
                check=True,
            )
            print(f'CI signing configured for {primary}. Recovery copy: {restore or backup}')
        finally:
            subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'all'], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--passphrase-file', type=Path, required=True)
    parser.add_argument('--backup', type=Path, default=Path('out/integration-recovery.gpg'))
    parser.add_argument('--restore', type=Path)
    arguments = parser.parse_args()
    if not arguments.passphrase_file.read_bytes().strip():
        parser.error('Recovery password must not be empty.')
    provision(
        Path(__file__).resolve().parents[1],
        arguments.passphrase_file.resolve(),
        arguments.backup.resolve(),
        arguments.restore.resolve() if arguments.restore else None,
    )


if __name__ == '__main__':
    main()
