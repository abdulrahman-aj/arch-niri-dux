#!/usr/bin/env python3
"""Build our owning integration and baseline packages with Arch's makepkg."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


NAMES = {'arch-niri-dux', 'arch-niri-dux-baseline'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare_source(root, workspace, epoch):
    """Make a bounded, deterministic input archive, independent of Git metadata."""
    sources = [root / 'packages/packages.target']
    sources.extend(sorted((root / 'desktop').rglob('*')))
    archive = workspace / 'integration.tar'
    with tarfile.open(archive, 'w') as output:
        for path in sources:
            if path.is_symlink():
                raise ValueError(f'Unexpected integration source symlink: {path}')
            if not path.is_file() or path == root / 'desktop/README.md':
                continue
            name = 'packages.target' if path == sources[0] else str(path.relative_to(root))
            info = output.gettarinfo(str(path), name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = epoch
            info.mode = 0o644
            with path.open('rb') as stream:
                output.addfile(info, stream)
    recipe = root / 'packages/integration/PKGBUILD'
    # The input is our checkout, not downloaded upstream code. Record its exact
    # hash in both makepkg's checksum and the artifact provenance.
    (workspace / 'PKGBUILD').write_text(
        recipe.read_text() + f"\nsha256sums=('{digest(archive)}')\n")
    return archive


def build_packages(root, workspace, destination, epoch):
    archive = prepare_source(root, workspace, epoch)
    environment = os.environ | {'PKGDEST': str(destination), 'SOURCE_DATE_EPOCH': str(epoch)}
    config = workspace / 'makepkg.conf'
    config.write_text('source /etc/makepkg.conf\nOPTIONS+=(!debug)\n')
    command = ['makepkg', '--config', str(config)]
    # Only local text/config inputs; dependencies are installed by pacstrap from
    # the ISO's verified repository, not fetched during this build.
    subprocess.run(command + ['--nodeps', '--noconfirm', '--cleanbuild', '--force'],
                   cwd=workspace, env=environment, check=True)
    packages = subprocess.check_output(command + ['--packagelist'], cwd=workspace,
                                       env=environment, text=True).splitlines()
    result = []
    for filename in packages:
        path = Path(filename)
        name = subprocess.check_output(['pacman', '-Qp', '--quiet', str(path)], text=True).strip()
        if name not in NAMES or path.parent != destination or not path.name.endswith('.pkg.tar.zst'):
            raise ValueError(f'Unexpected integration package: {path}')
        result.append(dict(name=name, filename=path.name, sha256=digest(path)))
    if {entry['name'] for entry in result} != NAMES or len(result) != len(NAMES):
        raise ValueError('Expected both the integration and baseline packages.')
    provenance = dict(source_sha256=digest(archive), recipe_sha256=digest(root / 'packages/integration/PKGBUILD'),
                      source_date_epoch=epoch, packages=sorted(result, key=lambda entry: entry['name']))
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
