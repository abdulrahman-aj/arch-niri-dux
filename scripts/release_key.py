#!/usr/bin/env python3
"""Provision the protected CI signing subkey and an encrypted offline recovery copy."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile


REPOSITORY = 'abdulrahman-aj/arch-niri-dux'


def gpg(home, *arguments, data=None):
    return subprocess.check_output(
        ['gpg', '--homedir', str(home), '--batch', '--yes', '--pinentry-mode', 'loopback', *arguments], input=data
    )


def records(home, secret=False):
    listing = gpg(home, '--with-colons', '--list-secret-keys' if secret else '--list-keys').decode()
    result = []
    for line in listing.splitlines():
        fields = line.split(':')
        if fields[0] in ('pub', 'sub', 'sec', 'ssb'):
            result.append(
                dict(
                    kind=fields[0],
                    validity=fields[1],
                    expires=fields[6],
                    capabilities=fields[11],
                    secret=fields[14] if len(fields) > 14 else '',
                )
            )
        elif fields[0] == 'fpr':
            result[-1]['fingerprint'] = fields[9]
    return result


def identity(home):
    keys = [key for key in records(home, secret=True) if key['kind'] == 'sec']
    if len(keys) != 1:
        raise ValueError('Expected exactly one certification key.')
    return keys[0]['fingerprint']


def selected_secret(home, primary, selected):
    import time

    if not isinstance(selected, str) or not re.fullmatch(r'[A-F0-9]{40}', selected):
        raise ValueError('Select a full signing-subkey fingerprint explicitly.')
    keys = records(home, secret=True)
    usable = [
        key
        for key in keys
        if key['kind'] == 'ssb'
        and key['fingerprint'] == selected
        and key['validity'] not in ('r', 'e', 'd', 'i')
        and 's' in key['capabilities']
        and key['secret'] == '+'
        and (not key['expires'] or int(key['expires']) > time.time())
    ]
    if not usable:
        raise ValueError('Selected signing subkey is unusable or absent from the recovery backup.')
    ci = gpg(home, '--armor', '--export-secret-subkeys', selected + '!')
    with tempfile.TemporaryDirectory(prefix='dux-ci-check-') as directory:
        isolated = Path(directory)
        isolated.chmod(0o700)
        try:
            gpg(isolated, '--import', data=ci)
            exported = records(isolated, secret=True)
            available = [key['fingerprint'] for key in exported if key['secret'] == '+']
            if available != [selected] or identity(isolated) != primary:
                raise ValueError('CI export contains unexpected private material.')
        finally:
            subprocess.run(['gpgconf', '--homedir', str(isolated), '--kill', 'all'], check=False)
    return ci


def provision(root, passphrase, backup, restore=None, *, signer=None, upload=False):
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
            else:
                secret = gpg(home, '--passphrase-file', str(passphrase), '--decrypt', str(restore))
                gpg(home, '--import', data=secret)
            primary = identity(home)
            if restore is not None and fingerprint.read_text().strip() != primary:
                raise ValueError('Recovery key does not match the checked-in release identity.')
            if public.exists():
                # Preserve public updates when restoring an older private backup.
                gpg(home, '--import', str(public))
            pin = root / 'keys/integration-signing.subkey'
            if signer is None:
                signer = pin.read_text().strip() if pin.exists() else None
            if signer is None and restore is None:
                signer = next((key['fingerprint'] for key in records(home, True) if key['kind'] == 'ssb'), None)
            ci = selected_secret(home, primary, signer)
            if restore is None or backup != restore:
                secret = gpg(home, '--export-secret-keys', primary)
                encrypted = gpg(
                    home, '--passphrase-file', str(passphrase), '--symmetric', '--cipher-algo', 'AES256', data=secret
                )
                backup.parent.mkdir(parents=True, exist_ok=True)
                try:
                    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError as error:
                    raise ValueError('Refusing to replace an existing recovery copy.') from error
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(encrypted)
            public.parent.mkdir(parents=True, exist_ok=True)
            public.write_bytes(gpg(home, '--export', primary))
            fingerprint.write_text(primary + '\n')
            pin.write_text(signer + '\n')
            if upload:
                subprocess.run(
                    [
                        'gh',
                        'secret',
                        'set',
                        'PACKAGE_SIGNING_KEY',
                        '--repo',
                        REPOSITORY,
                        '--env',
                        'integration-release',
                    ],
                    input=ci,
                    check=True,
                )
            print(f'Prepared signer {signer}. Recovery copy: {backup}')
            return ci
        finally:
            subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'all'], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--passphrase-file', type=Path, required=True)
    parser.add_argument('--backup', type=Path, default=Path('out/integration-recovery.gpg'))
    parser.add_argument('--restore', type=Path)
    parser.add_argument('--signer', help='Full signing-subkey fingerprint')
    parser.add_argument('--upload', action='store_true', help='Replace the protected GitHub secret')
    arguments = parser.parse_args()
    if not arguments.passphrase_file.read_bytes().strip():
        parser.error('Recovery password must not be empty.')
    provision(
        Path(__file__).resolve().parents[1],
        arguments.passphrase_file.resolve(),
        arguments.backup.resolve(),
        arguments.restore.resolve() if arguments.restore else None,
        signer=arguments.signer,
        upload=arguments.upload,
    )


if __name__ == '__main__':
    main()
