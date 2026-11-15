#!/usr/bin/env python3
"""Build the reviewed Dux app recipes and add them to an integration package directory."""

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from tools.digest import digest

ARCH = 'x86_64'


def validate_recipe(root, recipe):
    name = recipe['name']
    if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', name):
        raise ValueError('Invalid Dux app package name')
    source = root / 'packages/dux-apps' / name
    if not {'PKGBUILD', '.SRCINFO'}.issubset(recipe['files']):
        raise ValueError('Recipe is missing its build or dependency metadata')
    for filename, expected in recipe['files'].items():
        file = source / filename
        if not file.resolve().is_relative_to(source.resolve()) or digest(file) != expected:
            raise ValueError(f'Reviewed recipe changed: {file}')
    return source


def version_from_text(text, source='<srcinfo>'):
    """Parse pkgver-pkgrel from .SRCINFO text without running the recipe."""
    fields = dict(re.findall(r'^\t(pkgver|pkgrel|epoch) = (\S+)$', text, re.MULTILINE))
    if 'epoch' in fields or not all(
        re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', fields.get(key, '')) for key in ('pkgver', 'pkgrel')
    ):
        raise ValueError(f'Expected a numeric pkgver/pkgrel without epoch: {source}')
    return f'{fields["pkgver"]}-{fields["pkgrel"]}'


def version(source):
    """Read the package version from the hash-pinned .SRCINFO without running the recipe."""
    return version_from_text((source / '.SRCINFO').read_text(), source)


def recipes(root):
    """Return the reviewed recipes with their validated source directory and package filename."""
    declared = json.loads((root / 'packages/dux-apps-recipes.json').read_text())
    if len({recipe['name'] for recipe in declared}) != len(declared):
        raise ValueError('Duplicate Dux app recipe.')
    result = []
    for recipe in declared:
        source = validate_recipe(root, recipe)
        result.append((recipe, source, f'{recipe["name"]}-{version(source)}-{ARCH}.pkg.tar.zst'))
    return result


MAKEPKG_OPTIONS = ['--nodeps', '--noconfirm', '--cleanbuild', '--force']
MAKEPKG_CONFIG = 'source /etc/makepkg.conf\nOPTIONS+=(!debug)\n'


def cache_key(recipe, provenance):
    inputs = dict(recipe=recipe, architecture=ARCH, options=MAKEPKG_OPTIONS, config=MAKEPKG_CONFIG, builder=provenance)
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def builder_provenance():
    # Include the complete installed tool closure and makepkg configuration.
    configs = [Path('/etc/makepkg.conf'), *sorted(Path('/etc/makepkg.conf.d').glob('*.conf'))]
    return dict(
        packages=subprocess.check_output(['pacman', '-Q'], text=True),
        configs={str(path): digest(path) for path in configs},
        driver=digest(Path(__file__)),
        architecture=platform.machine(),
    )


def validate_package(package, recipe, source):
    info = subprocess.check_output(['bsdtar', '-xOf', str(package), '.PKGINFO'], text=True)
    fields = dict(line.split(' = ', 1) for line in info.splitlines() if ' = ' in line)
    expected = dict(pkgname=recipe['name'], pkgver=version(source), arch=ARCH)
    if any(fields.get(key) != value for key, value in expected.items()):
        raise ValueError(f'Unexpected package identity: {package}')
    subprocess.run(['bsdtar', '-tf', str(package)], check=True, stdout=subprocess.DEVNULL)


def restore_cache(entry, package, recipe, source):
    try:
        expected = (entry / 'sha256').read_text().strip()
        archive = entry / package.name
        if digest(archive) != expected:
            return False
        validate_package(archive, recipe, source)
        shutil.copyfile(archive, package)
        return True
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False


def build(root, workspace, destination, cache=None):
    manifest_path = destination / 'integration.manifest.json'
    manifest = json.loads(manifest_path.read_text())
    config = workspace / 'makepkg.conf'
    # Binary-only recipes do not need Arch's default split debug packages.
    config.write_text(MAKEPKG_CONFIG)
    provenance = builder_provenance() if cache else None
    apps = []
    for recipe, source, filename in recipes(root):
        package = destination / filename
        entry = cache / cache_key(recipe, provenance) if cache else None
        hit = entry is not None and restore_cache(entry, package, recipe, source)
        if hit:
            print(f'Dux app cache hit: {filename}', flush=True)
        else:
            build_recipe(recipe, source, filename, workspace, destination, config)
            validate_package(package, recipe, source)
            if entry is not None:
                cache.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.publish-', dir=cache) as temporary:
                    staging = Path(temporary) / 'entry'
                    staging.mkdir()
                    shutil.copyfile(package, staging / filename)
                    (staging / 'sha256').write_text(digest(package) + '\n')
                    # One builder per state is locked by build-iso.
                    if entry.exists():
                        shutil.rmtree(entry)
                    staging.rename(entry)
        apps.append(
            dict(name=recipe['name'], version=version(source), filename=filename, sha256=digest(package), recipe=recipe)
        )
    manifest['apps'] = apps
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


def build_recipe(recipe, source, filename, workspace, destination, config):
    work = workspace / recipe['name']
    work.mkdir()
    # Copy only reviewed files; never execute stray files from a working tree.
    for name in recipe['files']:
        shutil.copy2(source / name, work / name)
    # Do not let inherited makepkg overrides escape the cache key.
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'LOGNAME') if key in os.environ}
    environment.update(LANG='C.UTF-8', PKGDEST=str(destination), SOURCE_DATE_EPOCH=str(recipe['source_date_epoch']))
    # These recipes only repackage pinned upstream binaries. Runtime deps are
    # resolved from the signed closure or offline repository at install time.
    subprocess.run(
        ['makepkg', '--config', str(config), *MAKEPKG_OPTIONS],
        cwd=work,
        env=environment,
        check=True,
    )
    package = destination / filename
    if not package.is_file():
        raise ValueError(f'Expected {filename} from the reviewed recipe')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('root', type=Path)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('destination', type=Path, help='Output of tools.packages.integration')
    arguments = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run package builds as an unprivileged user.')
    arguments.workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dux-apps-', dir=arguments.workspace) as directory:
        build(
            arguments.root.resolve(),
            Path(directory),
            arguments.destination.resolve(),
            arguments.cache.resolve() if arguments.cache else None,
        )


if __name__ == '__main__':
    main()
