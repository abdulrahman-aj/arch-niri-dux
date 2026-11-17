#!/usr/bin/env python3
"""Verify package artifacts and fetch an authenticated release for an ISO."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

from tools.digest import digest
from tools.packages import integration as build
from tools.packages import dux_apps as apps
from tools.packages import inputs as lock

RELEASES = 'https://github.com/abdulrahman-aj/arch-niri-dux/releases/download'


def fingerprint(root):
    value = (root / 'keys/integration-signing.fingerprint').read_text().strip()
    if not re.fullmatch(r'[A-F0-9]{40}', value):
        raise ValueError('Invalid release key fingerprint.')
    return value


def signing_subkey(root):
    value = (root / 'keys/integration-signing.subkey').read_text().strip()
    if not re.fullmatch(r'[A-F0-9]{40}', value):
        raise ValueError('Invalid signing subkey fingerprint.')
    return value


def verify(root, path, *, historical=False, current_signer=False):
    if path.is_symlink() or Path(str(path) + '.sig').is_symlink():
        raise ValueError('Release assets must be regular files, not links.')
    result = subprocess.run(
        [
            'gpgv',
            '--status-fd',
            '1',
            '--keyring',
            str(root / 'keys/integration-signing.gpg'),
            str(path) + '.sig',
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    signatures = [line.split() for line in result.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    statuses = {line.split()[1] for line in result.stdout.splitlines() if line.startswith('[GNUPG:] ')}
    if statuses & {'REVKEYSIG', 'EXPSIG'} or ('EXPKEYSIG' in statuses and not historical):
        raise ValueError('Release signing key/signature is expired or revoked.')
    expected = fingerprint(root)
    if not any(expected in (line[2], line[-1]) for line in signatures):
        raise ValueError('Release signature does not belong to the pinned identity.')
    if current_signer and not any(line[2] == signing_subkey(root) for line in signatures):
        raise ValueError('Release signature does not use the selected signing subkey.')


def validate_manifest(root, directory):
    if any(path.is_symlink() for path in directory.iterdir()):
        raise ValueError('Release inputs must not contain links.')
    manifest = json.loads((directory / 'integration.manifest.json').read_text())
    version = build.version(root)
    entries = manifest['packages']
    if len(entries) != 2 or {entry['name'] for entry in entries} != build.NAMES:
        raise ValueError('Release must contain exactly integration and baseline packages.')
    for entry in entries:
        if entry['filename'] != f'{entry["name"]}-{version}-any.pkg.tar.zst':
            raise ValueError('Unexpected release package filename or version.')
        if not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']):
            raise ValueError('Invalid package checksum.')
        if digest(directory / entry['filename']) != entry['sha256']:
            raise ValueError('Release package checksum mismatch.')
    for name, field in [('integration.tar', 'source_sha256'), ('integration.PKGBUILD', 'recipe_sha256')]:
        if digest(directory / name) != manifest[field]:
            raise ValueError(f'Release source checksum mismatch: {name}')
    if type(manifest['source_date_epoch']) is not int or manifest['source_date_epoch'] < 0:
        raise ValueError('Invalid source date epoch.')
    if manifest.get('version') != version:
        raise ValueError('Release version does not match the checkout.')
    expected = lock.release_omarchy(root)
    if manifest.get('omarchy') != expected:
        raise ValueError('Release Omarchy packages differ from the reviewed pin; run make lock for unreleased work.')
    recipes = {recipe['name']: (recipe, filename) for recipe, _, filename in apps.recipes(root)}
    if (
        not isinstance(manifest.get('apps'), list)
        or len(manifest['apps']) != len(recipes)
        or {entry['name'] for entry in manifest['apps']} != set(recipes)
    ):
        raise ValueError('Release must contain exactly the reviewed Dux apps.')
    for entry in manifest['apps']:
        recipe, filename = recipes[entry['name']]
        if entry['recipe'] != recipe or entry['filename'] != filename:
            raise ValueError('Dux app recipe differs from the release; use iso-development for unreleased work.')
        if not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']) or digest(directory / filename) != entry['sha256']:
            raise ValueError('Dux app package checksum mismatch.')
    if digest(root / 'packages/integration/PKGBUILD') != manifest['recipe_sha256']:
        raise ValueError('Recipe differs from the release; use iso-development for unreleased work.')
    with tempfile.TemporaryDirectory(prefix='integration-inputs-') as temporary:
        archive = build.prepare_source(root, Path(temporary), manifest['source_date_epoch'])
        if digest(archive) != manifest['source_sha256']:
            raise ValueError('Integration differs from the release; use iso-development for unreleased work.')
    return manifest


# The caps bound a download before its signature can be checked. Our own
# packages and sources are small; the largest pinned Omarchy package,
# nvidia-580xx-utils, is 276 MiB.
ASSET_LIMIT = 64 * 1024 * 1024
OMARCHY_ASSET_LIMIT = 512 * 1024 * 1024


def download(url, destination, limit=ASSET_LIMIT):
    with urllib.request.urlopen(url, timeout=60) as source, destination.open('wb') as output:
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError(f'Integration release asset exceeds {limit // (1024 * 1024)} MiB.')
            output.write(chunk)


def fetch(root, destination, base_url=RELEASES):
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Release download destination must be empty.')
    destination.mkdir(parents=True, exist_ok=True)
    base = f'{base_url}/v{build.version(root)}'
    for name in ('integration.manifest.json', 'integration.manifest.json.sig'):
        download(f'{base}/{name}', destination / name)
    verify(root, destination / 'integration.manifest.json')
    manifest = json.loads((destination / 'integration.manifest.json').read_text())
    expected = {f'{name}-{build.version(root)}-any.pkg.tar.zst' for name in build.NAMES}
    names = [entry['filename'] for entry in manifest['packages']]
    expected |= {filename for _, _, filename in apps.recipes(root)}
    names += [entry['filename'] for entry in manifest.get('apps', [])]
    # The pinned Omarchy packages are release assets too, so a publish retry
    # reuses these exact bytes instead of Omarchy's moving-tip URL.
    omarchy = {entry['filename'] for entry in manifest.get('omarchy', [])}
    expected |= omarchy
    names += [entry['filename'] for entry in manifest.get('omarchy', [])]
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError('Unexpected release asset names.')
    for name in (*names, 'integration.tar', 'integration.PKGBUILD'):
        download(f'{base}/{name}', destination / name, OMARCHY_ASSET_LIMIT if name in omarchy else ASSET_LIMIT)
        if name in expected:
            download(f'{base}/{name}.sig', destination / (name + '.sig'))
            verify(root, destination / name)
    for entry in manifest.get('omarchy', []):
        if digest(destination / entry['filename']) != entry['sha256']:
            raise ValueError('Omarchy package checksum mismatch.')
    validate_manifest(root, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    acquire = commands.add_parser('fetch')
    acquire.add_argument('root', type=Path)
    acquire.add_argument('destination', type=Path)
    arguments = parser.parse_args()
    fetch(arguments.root.resolve(), arguments.destination.resolve())


if __name__ == '__main__':
    main()
