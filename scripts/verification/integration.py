#!/usr/bin/env python3
"""Guest-only integration upgrade, baseline dependency and config preservation check."""

import hashlib
import json
from pathlib import Path
import pwd
import re
import subprocess
import tempfile

from .guest import run
from .vm import installed_login


def output(*args, **kwargs):
    try:
        return subprocess.check_output(args, text=True, **kwargs).strip()
    except subprocess.CalledProcessError as error:
        print(error.output, flush=True)
        raise


def verify_upgrade():
    evidence = Path('/run/verify')
    if not (evidence / 'install.json').is_file():
        raise SystemExit('Requires the disposable installation verification fixture.')
    subprocess.run(['systemd-detect-virt', '--vm'], check=True)
    user = pwd.getpwnam('tester')
    prefix = ['runuser', '-u', user.pw_name, '--', 'env', '-u', 'XDG_CONFIG_HOME',
              '-u', 'XDG_STATE_HOME', '-u', 'XDG_DATA_HOME', f'HOME={user.pw_dir}']
    versions = output('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline')
    personal = [Path(user.pw_dir) / '.config' / name for name in
                ('niri/config.kdl', 'niri/dms/binds.kdl', 'xdg-terminals.list')]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in personal}
    old_file = Path('/usr/share/arch-niri-dux/niri/dms/layout.kdl')
    new_file = Path('/usr/share/arch-niri-dux/upgrade-fixture')
    fake_name = 'dux-verification-app'
    assert old_file.is_file() and not new_file.exists()
    with tempfile.TemporaryDirectory(prefix='dux-upgrade-', dir='/tmp') as directory:
        work = Path(directory)
        source = work / 'source'
        source.mkdir()
        output('tar', '-xf', str(evidence / 'integration-input/integration.tar'), '-C', str(source))
        recipe = (evidence / 'integration-input/PKGBUILD').read_text()
        recipe, changes = re.subn(r'^pkgrel=(\d+)$', lambda match: f'pkgrel={int(match[1]) + 1}',
                                 recipe, flags=re.MULTILINE)
        assert changes == 1
        # Remove a seed template only in this disposable release. The running
        # session uses its user-owned copy, so deletion cannot break recovery.
        entrypoint = 'package_arch-niri-dux() {'
        assert recipe.count(entrypoint) == 1
        recipe = recipe.replace(entrypoint, entrypoint + f'\n    rm "$srcdir/desktop{old_file}"')
        recipe_path = source / 'packages/integration/PKGBUILD'
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text(recipe)
        (source / 'desktop/usr/share/arch-niri-dux/upgrade-fixture').write_text('Integration upgrade verified\n')
        session = source / 'desktop/usr/share/arch-niri-dux/session.kdl'
        session.write_text(session.read_text() + '\n// Verified package update\n')
        target_list = source / 'packages/packages.target'
        target_list.parent.mkdir(parents=True, exist_ok=True)
        (source / 'packages.target').rename(target_list)
        target_list.write_text(target_list.read_text() + fake_name + '\n')
        fake = work / 'app'
        fake.mkdir()
        (fake / 'PKGBUILD').write_text(
            f'pkgname={fake_name}\npkgver=1\npkgrel=1\narch=(any)\n'
            'pkgdesc="Disposable dependency fixture"\n'
            'package() { install -Dm644 /dev/null "$pkgdir/usr/share/dux-verification-app"; }\n')
        (fake / 'makepkg.conf').write_text('source /etc/makepkg.conf\nOPTIONS+=(!debug)\n')
        output('chown', '-R', f'{user.pw_uid}:{user.pw_gid}', str(work))
        packages = work / 'packages'
        output(*prefix, 'python', str(evidence / 'build-integration.py'), str(source), str(work / 'build'),
            str(packages), '--epoch', '1234')
        output(*prefix, 'env', f'PKGDEST={packages}', 'makepkg', '--config', str(fake / 'makepkg.conf'),
            '--nodeps', '--noconfirm', cwd=fake)
        signing = work / 'signing'
        keyring = work / 'keyring'
        signing.mkdir(mode=0o700)
        keyring.mkdir(mode=0o700)
        gpg = ['gpg', '--homedir', str(signing), '--batch', '--yes', '--pinentry-mode', 'loopback']
        output(*gpg, '--passphrase', '', '--quick-gen-key', 'Disposable VM update fixture', 'ed25519', 'sign', '1d')
        listing = output(*gpg, '--with-colons', '--list-keys')
        fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        public = work / 'public.gpg'
        output(*gpg, '--output', str(public), '--export', fingerprint)
        output('gpg', '--homedir', str(keyring), '--batch', '--import', str(public))
        subprocess.run(['gpg', '--homedir', str(keyring), '--batch', '--import-ownertrust'],
                       input=f'{fingerprint}:6:\n', text=True, check=True)
        for package in packages.glob('*.pkg.tar.zst'):
            output(*gpg, '--detach-sign', str(package))
        database = packages / 'upgrade.db.tar.gz'
        output('repo-add', '--include-sigs', str(database), *map(str, packages.glob('*.pkg.tar.zst')))
        output(*gpg, '--detach-sign', str(database))
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
        assert output('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline') == versions
        database.write_bytes(original)
        # Force refresh after the deliberately rejected database download.
        upgrade_output = output('pacman', '--config', str(configuration), '-Syyu', '--noconfirm')
        assert 'Refreshing Arch Niri Dux release signing keys' in upgrade_output, upgrade_output
        updated = output('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline')
        assert updated != versions
        assert new_file.read_text() == 'Integration upgrade verified\n' and not old_file.exists()
        assert 'Verified package update' in Path('/usr/share/arch-niri-dux/session.kdl').read_text()
        output('pacman', '-Q', fake_name)
        output('pacman', '-R', '--noconfirm', 'arch-niri-dux-baseline')
        output('pacman', '-Q', 'arch-niri-dux', fake_name)
        output('pacman', '-R', '--noconfirm', fake_name)
        output('pacman', '--config', str(configuration), '-S', '--noconfirm', 'arch-niri-dux-baseline')
        for path in personal:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before[str(path)], path
        output(*prefix, 'niri', 'validate', '--config', str(personal[0]))
        output('pacman', '-Dk')
        for home in (signing, keyring):
            output('gpgconf', '--homedir', str(home), '--kill', 'all')
        (evidence / 'integration-upgrade.json').write_text(json.dumps({
            'before': versions.splitlines(), 'after': updated.splitlines(),
            'personal_sha256': before, 'owned_file_added_and_removed': True,
            'baseline_addition_installed': True, 'baseline_can_be_removed_independently': True,
            'signed_packages_and_database': True, 'tampered_database_rejected': True,
            'installed_keyring_refresh_hook_ran': True,
            'restoration': {'files_present': [str(old_file)], 'files_absent': [str(new_file)],
                            'packages_absent': [fake_name]},
        }, indent=2) + '\n')


def assert_restored(root=Path('/'), evidence=Path('/run/verify')):
    """Check the fixture's own recorded expectations after snapshot restoration."""
    expected = json.loads((evidence / 'integration-upgrade.json').read_text())
    actual = output('pacman', '-Q', 'arch-niri-dux', 'arch-niri-dux-baseline').splitlines()
    assert actual == expected['before'], (actual, expected['before'])
    restoration = expected['restoration']
    for name in restoration['files_present']:
        assert (root / name.lstrip('/')).is_file(), name
    for name in restoration['files_absent']:
        assert not (root / name.lstrip('/')).exists(), name
    for name in restoration['packages_absent']:
        assert subprocess.run(['pacman', '-Q', name], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode != 0, name




def published_repository():
    evidence = Path('/run/verify')
    assert (evidence / 'install.json').is_file(), 'Requires the disposable installation fixture.'
    run('systemd-detect-virt', '--vm')
    repository = Path('/etc/pacman.d/arch-niri-dux.conf').read_text()
    assert 'SigLevel = Required TrustedOnly' in repository
    assert 'Server = https://abdulrahman-aj.github.io/arch-niri-dux/x86_64' in repository
    personal = [Path('/home/tester/.config') / name for name in
                ('niri/config.kdl', 'niri/dms/binds.kdl', 'xdg-terminals.list')]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in personal}
    manifest = json.loads((evidence / 'release-manifest.json').read_text())
    hashes = {}
    with tempfile.TemporaryDirectory(prefix='dux-published-repository-') as directory:
        work = Path(directory)
        cache = work / 'cache'
        cache.mkdir()
        configuration = work / 'pacman.conf'
        # Only our repository participates; do not pull unrelated Arch updates
        # into this release qualification. Default installed GPG trust is used.
        configuration.write_text('[options]\nArchitecture = auto\n'
                                 f'CacheDir = {cache}\n' + repository)
        run('pacman', '--config', str(configuration), '-Syyu', '--noconfirm')
        # Force downloads into an empty cache even when this ISO already has
        # the current version. Package identity must match the signed ISO input.
        run('pacman', '--config', str(configuration), '-S', '--noconfirm',
            'arch-niri-dux', 'arch-niri-dux-baseline')
        for package in manifest['packages']:
            payload = cache / package['filename']
            hashes[package['name']] = hashlib.sha256(payload.read_bytes()).hexdigest()
            assert hashes[package['name']] == package['sha256'], package['name']
    for path in personal:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before[str(path)], path
    run('pacman', '-Dk')
    (evidence / 'published-repository.json').write_text(json.dumps({
        'version': manifest['version'], 'package_sha256': hashes, 'personal_sha256': before,
        'offline_trust_used': True, 'database_and_packages_required_signed': True,
        'https_delivery': True,
    }, indent=2) + '\n')


def verify_published(vm, passed):
    vm.start(cdrom=False, network=True)
    installed_login(vm)
    vm.mount_probes()
    vm.probe('integration', 'published-repository', timeout=300)
    passed('Published HTTPS repository and packages verify against offline trust and exact release hashes')
    vm.poweroff()
    vm.stop()


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'published-repository':
        published_repository()
    elif sys.argv[1] == 'upgrade':
        verify_upgrade()
    else:
        raise SystemExit('Unknown integration probe')
