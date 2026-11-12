#!/usr/bin/env python3
"""Build our owning packages with Arch's makepkg."""

import argparse
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from verification.digest import digest


import package_lock


NAMES = {'arch-niri-dux', 'arch-niri-dux-baseline'}


def version(root):
    recipe = (root / 'packages/integration/PKGBUILD').read_text()
    values = []
    for name in ('pkgver', 'pkgrel'):
        match = re.search(rf'^{name}=([0-9]+(?:\.[0-9]+)*)$', recipe, re.MULTILINE)
        if not match:
            raise ValueError(f'Expected a numeric literal {name} in the package recipe.')
        values.append(match[1])
    return '-'.join(values)


def prepare_source(root, workspace, epoch):
    """Make a bounded, deterministic input archive, independent of Git metadata."""
    # The release manifest records the Omarchy pin projection too, so the pin
    # and the package lists it is filtered by are hashed build inputs.
    sources = [
        root / 'packages/packages.target',
        root / 'packages/omarchy-pin.json',
        root / 'packages/dux-apps-recipes.json',
        root / 'archiso/packages.live',
    ]
    sources.extend(sorted((root / 'desktop').rglob('*')))
    signing_key = root / 'keys/integration-signing.gpg'
    if signing_key.is_file():
        fingerprint = (root / 'keys/integration-signing.fingerprint').read_text().strip()
        if not re.fullmatch(r'[A-F0-9]{40}', fingerprint):
            raise ValueError('Invalid integration signing fingerprint.')
        sources.append(signing_key)
    archive = workspace / 'integration.tar'
    with tarfile.open(archive, 'w') as output:
        for path in sources:
            if path.is_symlink():
                raise ValueError(f'Unexpected package source symlink: {path}')
            if not path.is_file() or path == root / 'desktop/README.md':
                continue
            if path == signing_key:
                name = 'desktop/usr/share/pacman/keyrings/arch-niri-dux.gpg'
            else:
                name = str(path.relative_to(root))
            info = output.gettarinfo(str(path), name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = epoch
            info.mode = 0o644
            with path.open('rb') as stream:
                output.addfile(info, stream)
        if signing_key.is_file():
            data = f'{fingerprint}:6:\n'.encode()
            info = tarfile.TarInfo('desktop/usr/share/pacman/keyrings/arch-niri-dux-trusted')
            info.size, info.mtime, info.mode = len(data), epoch, 0o644
            output.addfile(info, io.BytesIO(data))
    recipe = root / 'packages/integration/PKGBUILD'
    # The input is our checkout, not downloaded upstream code. Record its exact
    # hash in both makepkg's checksum and the artifact provenance.
    (workspace / 'PKGBUILD').write_text(recipe.read_text() + f"\nsha256sums=('{digest(archive)}')\n")
    return archive


def build_packages(root, workspace, destination, epoch):
    archive = prepare_source(root, workspace, epoch)
    environment = os.environ | {'PKGDEST': str(destination), 'SOURCE_DATE_EPOCH': str(epoch)}
    config = workspace / 'makepkg.conf'
    config.write_text('source /etc/makepkg.conf\nOPTIONS+=(!debug)\n')
    command = ['makepkg', '--config', str(config)]
    # Only local text/config inputs; dependencies are installed by pacstrap from
    # the ISO's verified repository, not fetched during this build.
    subprocess.run(
        command + ['--nodeps', '--noconfirm', '--cleanbuild', '--force'], cwd=workspace, env=environment, check=True
    )
    packages = subprocess.check_output(
        command + ['--packagelist'], cwd=workspace, env=environment, text=True
    ).splitlines()
    result = []
    for filename in packages:
        path = Path(filename)
        name = subprocess.check_output(['pacman', '-Qp', '--quiet', str(path)], text=True).strip()
        if name not in NAMES or path.parent != destination or not path.name.endswith('.pkg.tar.zst'):
            raise ValueError(f'Unexpected package: {path}')
        result.append(dict(name=name, filename=path.name, sha256=digest(path)))
    if {entry['name'] for entry in result} != NAMES or len(result) != len(NAMES):
        raise ValueError('Expected both of our packages.')
    provenance = dict(
        version=version(root),
        source_sha256=digest(archive),
        recipe_sha256=digest(root / 'packages/integration/PKGBUILD'),
        source_date_epoch=epoch,
        packages=sorted(result, key=lambda entry: entry['name']),
        omarchy=package_lock.release_omarchy(root),
    )
    shutil.copyfile(archive, destination / 'integration.tar')
    shutil.copyfile(root / 'packages/integration/PKGBUILD', destination / 'integration.PKGBUILD')
    (destination / 'integration.manifest.json').write_text(json.dumps(provenance, indent=2) + '\n')


def build(root, workspace, destination, epoch):
    if os.geteuid() == 0:
        raise ValueError('Run package builds as an unprivileged user.')
    workspace.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='integration-', dir=workspace) as directory:
        build_packages(root, Path(directory), destination, epoch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--epoch', required=True, type=int)
    arguments = parser.parse_args()
    if arguments.epoch < 0:
        parser.error('Source date epoch must be nonnegative.')
    build(arguments.root.resolve(), arguments.workspace.resolve(), arguments.destination.resolve(), arguments.epoch)


if __name__ == '__main__':
    main()
