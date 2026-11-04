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
    old_file = Path('/usr/share/arch-niri-dux/niri/config.kdl')
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
        # A test-only release exercises removal of an obsolete owned file after
        # prepare() has generated the effective defaults from it.
        entrypoint = 'package_arch-niri-dux() {'
        assert recipe.count(entrypoint) == 1
        recipe = recipe.replace(entrypoint, entrypoint + '\n    rm "$srcdir/desktop/usr/share/arch-niri-dux/niri/config.kdl"')
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
        run('repo-add', str(packages / 'upgrade.db.tar.gz'), *map(str, packages.glob('*.pkg.tar.zst')))
        configuration = work / 'pacman.conf'
        # This local throwaway repository tests package semantics; production
        # signed publication is qualified separately with disposable keys.
        configuration.write_text('[options]\nArchitecture = auto\nSigLevel = Never\n'
                                 f'[upgrade]\nServer = file://{packages}\n')
        run('pacman', '--config', str(configuration), '-Syu', '--noconfirm')
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
        (evidence / 'integration-upgrade.json').write_text(json.dumps({
            'before': versions.splitlines(), 'after': updated.splitlines(),
            'personal_sha256': before, 'owned_file_added_and_removed': True,
            'baseline_addition_installed': True, 'baseline_can_be_removed_independently': True,
        }, indent=2) + '\n')


if __name__ == '__main__':
    main()
