#!/usr/bin/env python3
"""Guest-only integration upgrade, baseline dependency and config preservation check."""

import hashlib
import json
from pathlib import Path
import pwd
import re
import subprocess
import tempfile


def run(*args, **kwargs):
    try:
        return subprocess.check_output(args, text=True, **kwargs).strip()
    except subprocess.CalledProcessError as error:
        print(error.output, flush=True)
        raise


def main():
    evidence = Path('/run/verify')
    if not (evidence / 'install.json').is_file():
        raise SystemExit('Requires the disposable installation verification fixture.')
    subprocess.run(['systemd-detect-virt', '--vm'], check=True)
    user = pwd.getpwnam('tester')
    prefix = ['runuser', '-u', user.pw_name, '--', 'env', '-u', 'XDG_CONFIG_HOME',
              '-u', 'XDG_STATE_HOME', '-u', 'XDG_DATA_HOME', f'HOME={user.pw_dir}']
    versions = run('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline')
    personal = [Path(user.pw_dir) / '.config' / name for name in
                ('niri/config.kdl', 'niri/dms/binds.kdl', 'xdg-terminals.list')]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in personal}
    old_file = Path('/usr/share/arch-niri-dux/niri/dms/layout.kdl')
    new_file = Path('/usr/share/arch-niri-dux/upgrade-fixture')
    assert old_file.is_file() and not new_file.exists()
    with tempfile.TemporaryDirectory(prefix='dux-upgrade-', dir='/tmp') as directory:
        work = Path(directory)
        source = work / 'source'
        source.mkdir()
        run('tar', '-xf', str(evidence / 'integration-input/integration.tar'), '-C', str(source))
        recipe = (evidence / 'integration-input/PKGBUILD').read_text()
        recipe, changes = re.subn(r'^pkgrel=(\d+)$', lambda match: f'pkgrel={int(match[1]) + 1}',
                                 recipe, flags=re.MULTILINE)
        assert changes == 1
        # Remove a seed template only in this disposable release. The running
        # session uses its user-owned copy, so deletion cannot break recovery.
        entrypoint = 'package_arch-niri-dux() {'
        assert recipe.count(entrypoint) == 1
        recipe = recipe.replace(entrypoint, entrypoint + '\n    rm "$srcdir/desktop/usr/share/arch-niri-dux/niri/dms/layout.kdl"')
        recipe_path = source / 'packages/integration/PKGBUILD'
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text(recipe)
        (source / 'desktop/usr/share/arch-niri-dux/upgrade-fixture').write_text('Integration upgrade verified\n')
        session = source / 'desktop/usr/share/arch-niri-dux/session.kdl'
        session.write_text(session.read_text() + '\n// Verified package update\n')
        target_list = source / 'packages/packages.target'
        target_list.parent.mkdir(parents=True, exist_ok=True)
        (source / 'packages.target').rename(target_list)
        target_list.write_text(target_list.read_text() + 'dux-verification-app\n')
        fake = work / 'app'
        fake.mkdir()
        (fake / 'PKGBUILD').write_text(
            'pkgname=dux-verification-app\npkgver=1\npkgrel=1\narch=(any)\n'
            'pkgdesc="Disposable dependency fixture"\n'
            'package() { install -Dm644 /dev/null "$pkgdir/usr/share/dux-verification-app"; }\n')
        (fake / 'makepkg.conf').write_text('source /etc/makepkg.conf\nOPTIONS+=(!debug)\n')
        run('chown', '-R', f'{user.pw_uid}:{user.pw_gid}', str(work))
        packages = work / 'packages'
        run(*prefix, 'python', str(evidence / 'build-integration.py'), str(source), str(work / 'build'),
            str(packages), '--epoch', '1234')
        run(*prefix, 'env', f'PKGDEST={packages}', 'makepkg', '--config', str(fake / 'makepkg.conf'),
            '--nodeps', '--noconfirm', cwd=fake)
        signing = work / 'signing'
        keyring = work / 'keyring'
        signing.mkdir(mode=0o700)
        keyring.mkdir(mode=0o700)
        gpg = ['gpg', '--homedir', str(signing), '--batch', '--yes', '--pinentry-mode', 'loopback']
        run(*gpg, '--passphrase', '', '--quick-gen-key', 'Disposable VM update fixture', 'ed25519', 'sign', '1d')
        listing = run(*gpg, '--with-colons', '--list-keys')
        fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        public = work / 'public.gpg'
        run(*gpg, '--output', str(public), '--export', fingerprint)
        run('gpg', '--homedir', str(keyring), '--batch', '--import', str(public))
        subprocess.run(['gpg', '--homedir', str(keyring), '--batch', '--import-ownertrust'],
                       input=f'{fingerprint}:6:\n', text=True, check=True)
        for package in packages.glob('*.pkg.tar.zst'):
            run(*gpg, '--detach-sign', str(package))
        database = packages / 'upgrade.db.tar.gz'
        run('repo-add', '--include-sigs', str(database), *map(str, packages.glob('*.pkg.tar.zst')))
        run(*gpg, '--detach-sign', str(database))
        (packages / 'upgrade.db.sig').symlink_to('upgrade.db.tar.gz.sig')
        configuration = work / 'pacman.conf'
        cache = work / 'cache'
        cache.mkdir()
        # The disposable signing identity never touches installed trust. The ISO's
        # production keyring bootstrap and refresh hook are exercised separately.
        # Keep fixture signatures out of the system cache, which survives restore.
        configuration.write_text('[options]\nArchitecture = auto\nSigLevel = Required TrustedOnly\n'
                                 f'GPGDir = {keyring}\nCacheDir = {cache}\n'
                                 f'[upgrade]\nServer = file://{packages}\n')
        original = database.read_bytes()
        database.write_bytes(original + b'tampered')
        rejected = subprocess.run(['pacman', '--config', str(configuration), '-Syu', '--noconfirm'],
                                  capture_output=True, text=True)
        assert rejected.returncode != 0 and 'signature' in rejected.stderr.lower(), rejected
        assert run('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline') == versions
        database.write_bytes(original)
        # Force refresh after the deliberately rejected database download.
        upgrade_output = run('pacman', '--config', str(configuration), '-Syyu', '--noconfirm')
        assert 'Refreshing Arch Niri Dux release signing keys' in upgrade_output, upgrade_output
        updated = run('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline')
        assert updated != versions
        assert new_file.read_text() == 'Integration upgrade verified\n' and not old_file.exists()
        assert 'Verified package update' in Path('/usr/share/arch-niri-dux/session.kdl').read_text()
        run('pacman', '-Q', 'dux-verification-app')
        run('pacman', '-R', '--noconfirm', 'arch-niri-dux-baseline')
        run('pacman', '-Q', 'arch-niri-dux', 'dux-verification-app')
        run('pacman', '-R', '--noconfirm', 'dux-verification-app')
        run('pacman', '--config', str(configuration), '-S', '--noconfirm', 'arch-niri-dux-baseline')
        for path in personal:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before[str(path)], path
        run(*prefix, 'niri', 'validate', '--config', str(personal[0]))
        run('pacman', '-Dk')
        for home in (signing, keyring):
            run('gpgconf', '--homedir', str(home), '--kill', 'all')
        (evidence / 'integration-upgrade.json').write_text(json.dumps({
            'before': versions.splitlines(), 'after': updated.splitlines(),
            'personal_sha256': before, 'owned_file_added_and_removed': True,
            'baseline_addition_installed': True, 'baseline_can_be_removed_independently': True,
            'signed_packages_and_database': True, 'tampered_database_rejected': True,
            'installed_keyring_refresh_hook_ran': True,
        }, indent=2) + '\n')


if __name__ == '__main__':
    main()
