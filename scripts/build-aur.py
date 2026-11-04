#!/usr/bin/env python3
"""Build the reviewed binary-only AUR recipes inside the disposable builder."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_recipe(root, recipe):
    name = recipe['name']
    if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', name):
        raise ValueError('Invalid AUR package name')
    source = root / 'packages/aur' / name
    if not re.fullmatch(r'[a-f0-9]{40}', recipe['commit']):
        raise ValueError('Invalid pinned AUR commit')
    if not {'PKGBUILD', '.SRCINFO'}.issubset(recipe['files']):
        raise ValueError('Recipe is missing its build or dependency metadata')
    for name, expected in recipe['files'].items():
        file = source / name
        if not file.resolve().is_relative_to(source.resolve()) or digest(file) != expected:
            raise ValueError(f'Reviewed recipe changed: {file}')
    return source


def main():
    root, destination, manifest_path = map(Path, sys.argv[1:4])
    if not os.path.ismount('/source') or not os.path.ismount('/build'):
        raise SystemExit('Run only inside the isolated ISO builder.')
    workspace = Path('/build/aur')
    artifacts = workspace / 'packages'
    artifacts.mkdir(parents=True, exist_ok=True)
    config = workspace / 'makepkg.conf'
    # Binary-only recipes do not need Arch's default split debug packages.
    config.write_text('source /etc/makepkg.conf\nOPTIONS+=(!debug)\n')
    subprocess.run(['chown', 'nobody:nobody', str(artifacts)], check=True)
    packages = []
    for recipe in json.loads((root / 'packages/aur-recipes.json').read_text()):
        source = validate_recipe(root, recipe)
        work = workspace / recipe['name']
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        # Copy only reviewed files; never execute stray files from a working tree.
        for filename in recipe['files']:
            shutil.copy2(source / filename, work / filename)
        subprocess.run(['chown', '-R', 'nobody:nobody', str(work)], check=True)
        environment = ['env', f'PKGDEST={artifacts}',
                       f'SOURCE_DATE_EPOCH={recipe["source_date_epoch"]}']
        # These recipes only repackage pinned upstream binaries. Runtime deps are
        # resolved from the combined offline repository before any disk erasure.
        subprocess.run(['runuser', '-u', 'nobody', '--', *environment, 'makepkg', '--config', str(config),
                        '--nodeps', '--noconfirm', '--cleanbuild', '--force'], cwd=work, check=True)
        package_list = subprocess.check_output(['runuser', '-u', 'nobody', '--',
                                              *environment, 'makepkg', '--config', str(config), '--packagelist'],
                                             cwd=work, text=True).splitlines()
        if len(package_list) != 1:
            raise ValueError('Expected one binary package from each reviewed recipe')
        package = Path(package_list[0])
        if package.parent != artifacts or not package.name.endswith('.pkg.tar.zst'):
            raise ValueError('Unexpected AUR build output')
        shutil.copy2(package, destination / package.name)
        packages.append(dict(name=recipe['name'], filename=package.name,
                             sha256=digest(package), recipe=recipe))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(packages, indent=2) + '\n')


if __name__ == '__main__':
    main()
