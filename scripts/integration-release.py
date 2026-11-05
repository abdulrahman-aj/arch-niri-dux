#!/usr/bin/env python3
"""Sign the integration repository or fetch an authenticated release for an ISO."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verification._common import COMMIT_EXPR, FINGERPRINT_EXPR, RELEASES_URL, SHA256_EXPR, check_hex, load_module

build = load_module('integration_build', Path(__file__).with_name('build-integration.py'))
RELEASES = RELEASES_URL


def fingerprint(root):
    value = (root / 'keys/integration-signing.fingerprint').read_text().strip()
    check_hex(value, FINGERPRINT_EXPR, 'Invalid release key fingerprint.')
    return value


def verify(root, path, *, historical=False):
    if path.is_symlink() or Path(str(path) + '.sig').is_symlink():
        raise ValueError('Release assets must be regular files, not links.')
    result = subprocess.run(['gpgv', '--status-fd', '1', '--keyring',
                             str(root / 'keys/integration-signing.gpg'), str(path) + '.sig', str(path)],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    signatures = [line.split() for line in result.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    statuses = {line.split()[1] for line in result.stdout.splitlines() if line.startswith('[GNUPG:] ')}
    if statuses & {'REVKEYSIG', 'EXPSIG'} or ('EXPKEYSIG' in statuses and not historical):
        raise ValueError('Release signing key/signature is expired or revoked.')
    expected = fingerprint(root)
    if not any(expected in (line[2], line[-1]) for line in signatures):
        raise ValueError('Release signature does not belong to the pinned identity.')


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
        check_hex(entry['sha256'], SHA256_EXPR, 'Invalid package checksum.')
        if build.digest(directory / entry['filename']) != entry['sha256']:
            raise ValueError('Release package checksum mismatch.')
    for name, field in [('integration.tar', 'source_sha256'), ('integration.PKGBUILD', 'recipe_sha256')]:
        if build.digest(directory / name) != manifest[field]:
            raise ValueError(f'Release source checksum mismatch: {name}')
    if type(manifest['source_date_epoch']) is not int or manifest['source_date_epoch'] < 0:
        raise ValueError('Invalid source date epoch.')
    if manifest.get('version') != version:
        raise ValueError('Release version does not match the checkout.')
    if build.digest(root / 'packages/integration/PKGBUILD') != manifest['recipe_sha256']:
        raise ValueError('Recipe differs from the release; use iso-development for unreleased work.')
    with tempfile.TemporaryDirectory(prefix='integration-inputs-') as temporary:
        archive = build.prepare_source(root, Path(temporary), manifest['source_date_epoch'])
        if build.digest(archive) != manifest['source_sha256']:
            raise ValueError('Integration differs from the release; use iso-development for unreleased work.')
    return manifest


def download(url, destination):
    # These are our small config packages/sources, not the distribution closure.
    with urllib.request.urlopen(url, timeout=60) as source, destination.open('wb') as output:
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > 64 * 1024 * 1024:
                raise ValueError('Integration release asset exceeds 64 MiB.')
            output.write(chunk)


def fetch(root, destination, base_url=RELEASES):
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Release download destination must be empty.')
    destination.mkdir(parents=True, exist_ok=True)
    base = f'{base_url}/integration-v{build.version(root)}'
    for name in ('integration.manifest.json', 'integration.manifest.json.sig'):
        download(f'{base}/{name}', destination / name)
    verify(root, destination / 'integration.manifest.json')
    manifest = json.loads((destination / 'integration.manifest.json').read_text())
    expected = {f'{name}-{build.version(root)}-any.pkg.tar.zst' for name in build.NAMES}
    names = [entry['filename'] for entry in manifest['packages']]
    if len(names) != 2 or set(names) != expected:
        raise ValueError('Unexpected release asset names.')
    for name in (*names, 'integration.tar', 'integration.PKGBUILD'):
        download(f'{base}/{name}', destination / name)
        if name in expected:
            download(f'{base}/{name}.sig', destination / (name + '.sig'))
            verify(root, destination / name)
    validate_manifest(root, destination)


def sign(root, path):
    subprocess.run(['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--local-user', fingerprint(root),
                    '--output', str(path) + '.sig', '--detach-sign', str(path)], check=True)
    verify(root, path)


def publish(root, packages, history, destination, tag, commit):
    version = build.version(root)
    if tag != f'integration-v{version}':
        raise ValueError('Release tag/commit must identify this package version.')
    check_hex(commit, COMMIT_EXPR, 'Release tag/commit must identify this package version.')
    if not os.environ.get('GNUPGHOME'):
        raise ValueError('Signing requires a dedicated GNUPGHOME.')
    manifest = validate_manifest(root, packages)
    if manifest.get('source_commit', commit) != commit:
        raise ValueError('Published version belongs to a different commit; create a new version.')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Repository output must be empty.')
    destination.mkdir(parents=True, exist_ok=True)
    for old in history.glob('*.pkg.tar.zst'):
        if old.is_symlink() or not old.is_file():
            raise ValueError('Unexpected historical package link.')
        name = next((name for name in build.NAMES if re.fullmatch(
            re.escape(name) + r'-[0-9]+(?:\.[0-9]+)*-[0-9]+(?:\.[0-9]+)*-any\.pkg\.tar\.zst', old.name)), None)
        if name is None:
            raise ValueError('Unexpected historical package name.')
        verify(root, old, historical=True)
        old_version = old.name.removeprefix(name + '-').removesuffix('-any.pkg.tar.zst')
        newer = int(subprocess.check_output(['vercmp', version, old_version], text=True))
        if newer <= 0:
            raise ValueError('A release must be newer than every published integration package.')
        shutil.copyfile(old, destination / old.name)
        shutil.copyfile(Path(str(old) + '.sig'), destination / (old.name + '.sig'))
    current = []
    for entry in manifest['packages']:
        target = destination / entry['filename']
        shutil.copyfile(packages / entry['filename'], target)
        sign(root, target)
        current.append(target)
    database = destination / 'arch-niri-dux.db.tar.gz'
    subprocess.run(['repo-add', '--include-sigs', str(database), *map(str, current)], check=True)
    for suffix in ('db', 'files'):
        archive = destination / f'arch-niri-dux.{suffix}.tar.gz'
        sign(root, archive)
        # Pages artifacts disallow links. Serve the exact signed bytes as .db/.files.
        alias = destination / f'arch-niri-dux.{suffix}'
        alias.unlink(missing_ok=True)
        shutil.copyfile(archive, alias)
        shutil.copyfile(Path(str(archive) + '.sig'), Path(str(alias) + '.sig'))
    manifest.update(tag=tag, source_commit=commit)
    manifest_path = destination / 'integration.manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    for name in ('integration.tar', 'integration.PKGBUILD'):
        shutil.copyfile(packages / name, destination / name)
    sign(root, manifest_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    acquire = commands.add_parser('fetch')
    acquire.add_argument('root', type=Path)
    acquire.add_argument('destination', type=Path)
    release = commands.add_parser('publish')
    release.add_argument('root', type=Path)
    release.add_argument('packages', type=Path)
    release.add_argument('history', type=Path)
    release.add_argument('destination', type=Path)
    release.add_argument('--tag', required=True)
    release.add_argument('--commit', required=True)
    arguments = parser.parse_args()
    if arguments.command == 'fetch':
        fetch(arguments.root.resolve(), arguments.destination.resolve())
    else:
        publish(arguments.root.resolve(), arguments.packages.resolve(), arguments.history.resolve(),
                arguments.destination.resolve(), arguments.tag, arguments.commit)


if __name__ == '__main__':
    main()
