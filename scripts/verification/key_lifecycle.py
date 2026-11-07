"""Disposable GnuPG and real pacman transactions shared by fast and VM qualification."""

import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile


def qualify(helper, *, fakeroot=True):
    recover = runpy.run_path(str(helper))['recover']
    with tempfile.TemporaryDirectory(prefix='dux-keylife-') as directory:
        work = Path(directory)
        signing = work / 'signing'
        client = work / 'client'
        signing.mkdir(mode=0o700)
        client.mkdir(mode=0o700)
        (client / 'pubring.gpg').touch()
        # Root pacman may try retrieving unknown signers. Fail locally, never query a public keyserver.
        (client / 'gpg.conf').write_text('keyserver hkp://127.0.0.1:9\nkeyserver-options timeout=1\n')
        homes = [signing, client]

        def gpg(home, *args, data=None):
            return subprocess.check_output(
                ['gpg', '--homedir', str(home), '--batch', '--yes', '--pinentry-mode', 'loopback', *args],
                input=data,
                stderr=subprocess.DEVNULL,
            )

        def sign(path, signer):
            gpg(signing, '--local-user', signer + '!', '--output', str(path) + '.sig', '--detach-sign', str(path))

        try:
            gpg(signing, '--passphrase', '', '--quick-gen-key', 'Disposable lifecycle', 'ed25519', 'cert', '0')

            def fingerprints():
                return [
                    line.split(':')[9]
                    for line in gpg(signing, '--with-colons', '--list-keys').decode().splitlines()
                    if line.startswith('fpr:')
                ]

            primary = fingerprints()[0]

            def add():
                gpg(signing, '--passphrase', '', '--quick-add-key', primary, 'ed25519', 'sign', '1d')
                return fingerprints()[-1]

            a = add()
            anchor = work / 'anchor.gpg'
            anchor.write_bytes(gpg(signing, '--export', primary))
            gpg(client, '--import', data=anchor.read_bytes())
            gpg(client, '--import-ownertrust', data=f'{primary}:6:\n'.encode())
            root = work / 'root'
            for name in ('db', 'cache'):
                (root / name).mkdir(parents=True)
            repository = work / 'repo'
            repository.mkdir()
            conf = work / 'pacman.conf'
            conf.write_text(
                '[options]\nArchitecture = auto\nSigLevel = Required TrustedOnly\n'
                f'[lifecycle]\nServer = file://{repository}\n'
            )
            pacman = (['fakeroot'] if fakeroot else []) + [
                'pacman',
                '--root',
                str(root),
                '--dbpath',
                str(root / 'db'),
                '--cachedir',
                str(root / 'cache'),
                '--logfile',
                str(root / 'log'),
                '--gpgdir',
                str(client),
                '--config',
                str(conf),
            ]

            def transaction(success):
                result = subprocess.run(pacman + ['-Syy', '--noconfirm'], capture_output=True, check=False, timeout=30)
                if result.returncode == 0:
                    result = subprocess.run(
                        pacman + ['-S', '--noconfirm', 'dux-lifecycle-fixture'],
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                assert (result.returncode == 0) == success, (result.stdout, result.stderr)

            def installed():
                return subprocess.check_output(pacman + ['-Q', 'dux-lifecycle-fixture'], text=True).split()[1]

            def release(version, package_signer, database_signer=None):
                payload = work / 'payload'
                payload.mkdir(exist_ok=True)
                (payload / '.PKGINFO').write_text(
                    f'pkgname = dux-lifecycle-fixture\npkgver = {version}\n'
                    'pkgdesc = Disposable fixture\nsize = 0\narch = any\n'
                )
                package = repository / f'dux-lifecycle-fixture-{version}-any.pkg.tar.zst'
                subprocess.run(['bsdtar', '--zstd', '-cf', str(package), '-C', str(payload), '.PKGINFO'], check=True)
                sign(package, package_signer)
                database = repository / 'lifecycle.db.tar.gz'
                subprocess.run(
                    ['repo-add', '--include-sigs', str(database), str(package)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                sign(database, database_signer or package_signer)
                alias = repository / 'lifecycle.db.sig'
                alias.unlink(missing_ok=True)
                shutil.copyfile(Path(str(database) + '.sig'), alias)

            def refresh(certificate, digest=None):
                recover(certificate, digest or hashlib.sha256(certificate.read_bytes()).hexdigest(), anchor, client)

            # A works; A-only clients cannot cross two missed certificate transitions.
            release('1-1', a)
            transaction(True)
            b = add()
            c = add()
            release('2-1', c)
            transaction(False)
            assert installed() == '1-1'
            certificate = work / 'latest.gpg'
            certificate.write_bytes(gpg(signing, '--export', primary))
            try:
                refresh(certificate, '0' * 64)
                raise AssertionError('Wrong digest accepted')
            except ValueError:
                pass
            transaction(False)
            refresh(certificate)
            refresh(certificate)
            transaction(True)
            assert installed() == '2-1'
            # Multiple valid signers coexist during routine overlap.
            release('3-1', b)
            transaction(True)
            assert installed() == '3-1'
            release('4-1', a)
            revoked_database = work / 'revoked-database'
            shutil.copytree(repository, revoked_database)
            release('5-1', a, c)
            revoked_package = work / 'revoked-package'
            shutil.copytree(repository, revoked_package)
            # Revoke A with the offline primary, preserving the binding and revocation.
            gpg(
                signing,
                '--command-fd',
                '0',
                '--status-fd',
                '2',
                '--edit-key',
                primary,
                data=b'key 1\nrevkey\ny\n1\nCompromised fixture\n\ny\nsave\n',
            )
            certificate.write_bytes(gpg(signing, '--export', primary))
            refresh(certificate)
            # Replay of an old certificate must not erase learned revocations.
            refresh(anchor)
            for attacker in (revoked_database, revoked_package):
                shutil.rmtree(repository)
                shutil.copytree(attacker, repository, symlinks=True)
                transaction(False)
                assert installed() == '3-1'
            signed = work / 'check'
            signed.write_text('replacement')
            sign(signed, c)
            subprocess.run(
                ['gpg', '--homedir', str(client), '--batch', '--verify', str(signed) + '.sig', str(signed)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            release('6-1', c)
            transaction(True)
            assert installed() == '6-1'
            return dict(
                skipped_two_transitions=True,
                wrong_digest_rejected=True,
                repeated_recovery=True,
                routine_overlap=True,
                revoked_database_rejected=True,
                revoked_package_rejected=True,
                revocation_persisted=True,
                replacement_accepted=True,
            )
        finally:
            for home in homes:
                subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'all'], check=False)


def verify_installed():
    subprocess.run(['systemd-detect-virt', '--vm'], check=True)
    result = qualify(Path('/usr/bin/arch-niri-dux-keyring-recover'), fakeroot=False)
    Path('/run/verify/key-lifecycle.json').write_text(json.dumps(result, indent=2) + '\n')
