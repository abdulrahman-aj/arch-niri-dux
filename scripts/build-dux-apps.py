#!/usr/bin/env python3
"""Build the reviewed Dux app recipes and add them to an integration package directory."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ARCH = 'x86_64'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_recipe(root, recipe):
    name = recipe['name']
    if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', name):
        raise ValueError('Invalid Dux app package name')
    source = root / 'packages/aur' / name
    if not re.fullmatch(r'[a-f0-9]{40}', recipe['commit']):
        raise ValueError('Invalid pinned AUR commit')
    if not {'PKGBUILD', '.SRCINFO'}.issubset(recipe['files']):
        raise ValueError('Recipe is missing its build or dependency metadata')
    for filename, expected in recipe['files'].items():
        file = source / filename
        if not file.resolve().is_relative_to(source.resolve()) or digest(file) != expected:
            raise ValueError(f'Reviewed recipe changed: {file}')
    return source


def version(source):
    """Read the package version from the hash-pinned .SRCINFO without running the recipe."""
    fields = dict(re.findall(r'^\t(pkgver|pkgrel|epoch) = (\S+)$', (source / '.SRCINFO').read_text(), re.MULTILINE))
    if 'epoch' in fields or not all(re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', fields.get(key, ''))
                                    for key in ('pkgver', 'pkgrel')):
        raise ValueError(f'Expected a numeric pkgver/pkgrel without epoch: {source}')
    return f'{fields["pkgver"]}-{fields["pkgrel"]}'


def recipes(root):
    """Return the reviewed recipes with their validated source directory and package filename."""
    declared = json.loads((root / 'packages/aur-recipes.json').read_text())
    if len({recipe['name'] for recipe in declared}) != len(declared):
        raise ValueError('Duplicate Dux app recipe.')
    result = []
    for recipe in declared:
        source = validate_recipe(root, recipe)
        result.append((recipe, source, f'{recipe["name"]}-{version(source)}-{ARCH}.pkg.tar.zst'))
    return result


def build(root, workspace, destination):
    manifest_path = destination / 'integration.manifest.json'
    manifest = json.loads(manifest_path.read_text())
    config = workspace / 'makepkg.conf'
    # Binary-only recipes do not need Arch's default split debug packages.
    config.write_text('source /etc/makepkg.conf\nOPTIONS+=(!debug)\n')
    apps = []
    for recipe, source, filename in recipes(root):
        work = workspace / recipe['name']
        work.mkdir()
        # Copy only reviewed files; never execute stray files from a working tree.
        for name in recipe['files']:
            shutil.copy2(source / name, work / name)
        environment = os.environ | {'PKGDEST': str(destination),
                                    'SOURCE_DATE_EPOCH': str(recipe['source_date_epoch'])}
        # These recipes only repackage pinned upstream binaries. Runtime deps are
        # resolved from the signed closure or offline repository at install time.
        subprocess.run(['makepkg', '--config', str(config), '--nodeps', '--noconfirm', '--cleanbuild', '--force'],
                       cwd=work, env=environment, check=True)
        package = destination / filename
        if not package.is_file():
            raise ValueError(f'Expected {filename} from the reviewed recipe')
        apps.append(dict(name=recipe['name'], version=version(source), filename=filename,
                         sha256=digest(package), recipe=recipe))
    manifest['apps'] = apps
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('destination', type=Path, help='Output of build-integration.py')
    arguments = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run package builds as an unprivileged user.')
    arguments.workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dux-apps-', dir=arguments.workspace) as directory:
        build(arguments.root.resolve(), Path(directory), arguments.destination.resolve())


if __name__ == '__main__':
    main()
