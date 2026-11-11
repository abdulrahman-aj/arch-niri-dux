#!/usr/bin/env python3
"""Finish an ISO's offline repository and inventory after all producers complete."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from package_lock import DATE_PATTERN, OMARCHY_NAMES, read_database, requested_packages, validate_entry
from verification.digest import digest


def _manifest(root, repository):
    date = (root / 'packages/archive-date').read_text()
    if not DATE_PATTERN.fullmatch(date):
        raise ValueError('Pinned archive date must look like YYYY/MM/DD')
    downloaded = repository / 'arch-downloaded.json'
    try:
        arch = json.loads(downloaded.read_text())
    finally:
        # The payload directory ships on the ISO; provenance lives in the manifest.
        downloaded.unlink(missing_ok=True)
    pin = json.loads((root / 'packages/omarchy-pin.json').read_text())
    if {entry['name'] for entry in pin} - set(OMARCHY_NAMES) or len(pin) != len({e['name'] for e in pin}):
        raise ValueError('Omarchy pin holds only the two reviewed packages.')
    for entry in pin:
        validate_entry(entry)
    integration = json.loads((repository / 'integration.manifest.json').read_text())
    recipes = json.loads((root / 'packages/aur-recipes.json').read_text())
    apps = integration.get('apps', [])
    expected = {recipe['name']: recipe for recipe in recipes}
    if (
        len(expected) != len(recipes)
        or len(apps) != len(expected)
        or {entry['name'] for entry in apps} != set(expected)
    ):
        raise ValueError('Dux app inventory does not match the declared recipes.')
    if any(entry['recipe'] != expected[entry['name']] for entry in apps):
        raise ValueError('Dux app provenance does not match the declared recipes.')
    return dict(
        schema=1,
        archive_date=date.strip(),
        requested=requested_packages(root),
        packages=sorted(arch + pin, key=lambda e: e['name']),
        integration=integration,
    )


def _inventory(manifest, repository):
    inventory = {}
    filenames = set()
    for entries, version in (
        (manifest['packages'], None),
        (manifest['integration']['packages'], manifest['integration']['version']),
        (manifest['integration'].get('apps', []), None),
    ):
        for entry in entries:
            name, filename, checksum = entry['name'], entry['filename'], entry['sha256']
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or not filename.endswith('.pkg.tar.zst')
            ):
                raise ValueError(f'Unsafe package filename: {filename}')
            if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9@+_.-]*', name):
                raise ValueError(f'Invalid package name: {name}')
            if not isinstance(checksum, str) or not re.fullmatch(r'[a-f0-9]{64}', checksum):
                raise ValueError(f'Invalid package checksum: {filename}')
            if name in inventory:
                raise ValueError(f'Duplicate package name: {name}')
            if filename in filenames:
                raise ValueError(f'Duplicate package filename: {filename}')
            inventory[name] = (filename, checksum, entry.get('version', version))
            filenames.add(filename)
    if not inventory:
        raise ValueError('Empty package inventory.')
    actual = {path.name for path in repository.glob('*.pkg.tar.*') if not path.name.endswith('.sig')}
    if missing := filenames - actual:
        raise ValueError(f'Missing packages: {sorted(missing)}')
    if unexpected := actual - filenames:
        raise ValueError(f'Unexpected packages: {sorted(unexpected)}')
    for filename, checksum, _ in inventory.values():
        path = repository / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Package must be a regular file: {filename}')
        if digest(path) != checksum:
            raise ValueError(f'Package checksum mismatch: {filename}')
    if any(path.name != 'offline.db' for path in repository.glob('*.db')):
        raise ValueError('Unexpected repository database.')
    return inventory


def _database_inventory(database):
    inventory = {}
    for fields in read_database(database):
        name = fields['NAME'][0]
        if name in inventory:
            raise ValueError('Duplicate package in repository database inventory.')
        inventory[name] = (fields['FILENAME'][0], fields['SHA256SUM'][0], fields['VERSION'][0])
    return inventory


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text)
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def finalize(root, repository):
    manifest = _manifest(root, repository)
    inventory = _inventory(manifest, repository)
    # Build a fresh database: merging the bootstrap database can retain entries
    # whose package files are no longer part of the completed payload.
    with tempfile.TemporaryDirectory(prefix='iso-payload-', dir=repository.parent) as directory:
        staging = Path(directory)
        database = staging / 'offline.db.tar.gz'
        packages = [repository / filename for filename, _, _ in sorted(inventory.values())]
        subprocess.run(['repo-add', str(database), *map(str, packages)], check=True)
        actual = _database_inventory(database)
        if set(actual) != set(inventory) or any(
            actual[name][:2] != entry[:2] or (entry[2] is not None and actual[name][2] != entry[2])
            for name, entry in inventory.items()
        ):
            raise ValueError('Repository database inventory does not match the declared packages.')
        checksums = ''.join(f'{checksum}  {filename}\n' for filename, checksum, _ in sorted(inventory.values()))
        checksums += f'{digest(database)}  offline.db\n'
        # No final metadata is replaced until every package and database entry
        # agrees. A failed build must not publish a new inventory.
        for path in repository.glob('offline.*'):
            path.unlink()
        for path in staging.iterdir():
            path.replace(repository / path.name)
        _write(repository / 'SHA256SUMS', checksums)
        _write(root / 'out/packages.manifest.json', json.dumps(manifest, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('repository', type=Path)
    arguments = parser.parse_args()
    finalize(arguments.root.resolve(), arguments.repository.resolve())


if __name__ == '__main__':
    main()
