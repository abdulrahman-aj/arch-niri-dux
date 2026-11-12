#!/usr/bin/env python3
"""Sign the integration repository or fetch an authenticated release for an ISO."""

import argparse
import base64
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

from verification.digest import digest


import package_build as build
import dux_apps as apps
import package_lock as lock

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


def history_boundary(root):
    boundary = json.loads((root / 'keys/integration-history-start.json').read_text())
    if not re.fullmatch(r'v[0-9]+(?:\.[0-9]+)*-[0-9]+(?:\.[0-9]+)*', boundary['tag']):
        raise ValueError('Invalid history boundary tag.')
    if boundary['tag'][1:] != boundary['packages']['arch-niri-dux']:
        raise ValueError('History boundary tag must match the integration package floor.')
    names = build.NAMES | {recipe['name'] for recipe, _, _ in apps.recipes(root)}
    if set(boundary['packages']) != names:
        raise ValueError('History boundary must cover every package.')
    for version in boundary['packages'].values():
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*-[0-9]+(?:\.[0-9]+)*', version):
            raise ValueError('Invalid history boundary version.')
    return boundary


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
    base = f'{base_url}/v{build.version(root)}'
    for name in ('integration.manifest.json', 'integration.manifest.json.sig'):
        download(f'{base}/{name}', destination / name)
    verify(root, destination / 'integration.manifest.json')
    manifest = json.loads((destination / 'integration.manifest.json').read_text())
    expected = {f'{name}-{build.version(root)}-any.pkg.tar.zst' for name in build.NAMES}
    names = [entry['filename'] for entry in manifest['packages']]
    expected |= {filename for _, _, filename in apps.recipes(root)}
    names += [entry['filename'] for entry in manifest.get('apps', [])]
    # The pinned boot packages are release assets too, so a publish retry
    # reuses these exact bytes instead of Omarchy's moving-tip URL.
    expected |= {entry['filename'] for entry in manifest.get('omarchy', [])}
    names += [entry['filename'] for entry in manifest.get('omarchy', [])]
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError('Unexpected release asset names.')
    for name in (*names, 'integration.tar', 'integration.PKGBUILD'):
        download(f'{base}/{name}', destination / name)
        if name in expected:
            download(f'{base}/{name}.sig', destination / (name + '.sig'))
            verify(root, destination / name)
    for entry in manifest.get('omarchy', []):
        if digest(destination / entry['filename']) != entry['sha256']:
            raise ValueError('Omarchy package checksum mismatch.')
    validate_manifest(root, destination)


def sign(root, path):
    subprocess.run(
        [
            'gpg',
            '--batch',
            '--yes',
            '--pinentry-mode',
            'loopback',
            '--local-user',
            signing_subkey(root) + '!',
            '--output',
            str(path) + '.sig',
            '--detach-sign',
            str(path),
        ],
        check=True,
    )
    verify(root, path, current_signer=True)


def contents(path):
    """Package metadata and payload digests, without fields that vary between identical rebuilds."""

    def member(name):
        return subprocess.check_output(['bsdtar', '-xOf', str(path), name])

    # Comments name the makepkg/fakeroot versions. .BUILDINFO lists the build
    # environment, and the .MTREE digests of both metadata files follow from that.
    info = [line for line in member('.PKGINFO').decode().splitlines() if not line.startswith('#')]
    tree = [
        line
        for line in gzip.decompress(member('.MTREE')).decode().splitlines()
        if not line.startswith(('./.BUILDINFO ', './.PKGINFO '))
    ]
    return info, tree


def publish(root, packages, history, destination, tag, commit):
    version = build.version(root)
    if tag != f'v{version}' or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Release tag/commit must identify this package version.')
    if not os.environ.get('GNUPGHOME'):
        raise ValueError('Signing requires a dedicated GNUPGHOME.')
    manifest = validate_manifest(root, packages)
    if manifest.get('source_commit', commit) != commit:
        raise ValueError('Published version belongs to a different commit; create a new version.')
    pin = {entry['name']: entry for entry in lock.omarchy_pin(root)}
    for entry in manifest.get('omarchy', []):
        # The exact bytes the pin reviewed, re-signed with our release key so
        # installed machines take them from our first-listed repository.
        source = pin[entry['name']]
        target = packages / entry['filename']
        if not lock.cache_matches(target, entry['sha256']):
            lock.download(source['url'], target, entry['sha256'])
        signature = target.with_name(target.name + '.sig')
        signature.write_bytes(base64.b64decode(source['signature'], validate=True))
        lock.verify_signature(root / 'keys/trusted-packagers.gpg', signature, target)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Repository output must be empty.')
    destination.mkdir(parents=True, exist_ok=True)
    app_entries = {entry['name']: entry for entry in manifest['apps']}
    versions = dict.fromkeys(build.NAMES, (version, 'any'))
    versions |= {name: (entry['version'], apps.ARCH) for name, entry in app_entries.items()}
    boundary = history_boundary(root)
    for name, (current_version, _) in versions.items():
        if int(subprocess.check_output(['vercmp', current_version, boundary['packages'][name]], text=True)) < 0:
            raise ValueError('Current package predates the history boundary.')
    reused = {}
    omarchy_names = {entry['name'] for entry in manifest.get('omarchy', [])}
    omarchy_pattern = (
        '(?:'
        + '|'.join(re.escape(name) for name in sorted(omarchy_names))
        + r')-[0-9]+(?:\.[0-9]+)*-[0-9]+(?:\.[0-9]+)*-x86_64\.pkg\.tar\.zst'
    )
    for old in history.glob('*.pkg.tar.zst'):
        if old.is_symlink() or not old.is_file():
            raise ValueError('Unexpected historical package link.')
        if omarchy_names and re.fullmatch(omarchy_pattern, old.name):
            # Only the pinned boot packages are served, like superseded Dux
            # apps; older files stay on GitHub Releases, if they were kept.
            verify(root, old, historical=True)
            continue
        name = next(
            (
                name
                for name, (_, arch) in versions.items()
                if re.fullmatch(
                    re.escape(name) + r'-[0-9]+(?:\.[0-9]+)*-[0-9]+(?:\.[0-9]+)*-' + arch + r'\.pkg\.tar\.zst', old.name
                )
            ),
            None,
        )
        if name is None:
            raise ValueError('Unexpected historical package name.')
        current_version, arch = versions[name]
        old_version = old.name.removeprefix(name + '-').removesuffix(f'-{arch}.pkg.tar.zst')
        if int(subprocess.check_output(['vercmp', old_version, boundary['packages'][name]], text=True)) < 0:
            raise ValueError('Historical package predates the history boundary.')
        verify(root, old, historical=True)
        newer = int(subprocess.check_output(['vercmp', current_version, old_version], text=True))
        if name in build.NAMES:
            if newer <= 0:
                raise ValueError('A release must be newer than every published integration package.')
        elif newer < 0:
            raise ValueError(f'A release must not downgrade the published {name}.')
        elif newer == 0:
            # Rebuilds are not byte-identical, and pacman caches by filename, so a
            # published version keeps its bytes. A recipe change needs a new pkgrel.
            if contents(old) != contents(packages / old.name):
                raise ValueError(f'{old.name} changed without a new pkgrel.')
            app_entries[name]['sha256'] = digest(old)
            reused[old.name] = old
            continue
        else:
            # pacman -Syu refreshes the database first. Retaining every app version
            # would exhaust the 1 GB Pages limit; GitHub Releases keep them.
            continue
        shutil.copyfile(old, destination / old.name)
        shutil.copyfile(Path(str(old) + '.sig'), destination / (old.name + '.sig'))
    current = []
    for entry in manifest['packages'] + manifest['apps']:
        target = destination / entry['filename']
        shutil.copyfile(reused.get(entry['filename'], packages / entry['filename']), target)
        # Re-sign reused bytes too: their old signature may be from an expired subkey.
        sign(root, target)
        current.append(target)
    for entry in manifest.get('omarchy', []):
        # Only the pinned version is served; GitHub Releases keep the history.
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


def history_tags(root, releases, tag):
    start = history_boundary(root)['tag'][1:]
    for page in json.loads(releases.read_text()):
        for release in page:
            previous = release['tag_name']
            if release['draft'] or release['prerelease'] or previous == tag:
                continue
            if not re.fullmatch(r'v[0-9]+(?:\.[0-9]+)*-[0-9]+(?:\.[0-9]+)*', previous):
                continue
            if int(subprocess.check_output(['vercmp', previous[1:], start], text=True)) >= 0:
                print(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    history = commands.add_parser('history-tags')
    history.add_argument('root', type=Path)
    history.add_argument('releases', type=Path)
    history.add_argument('--tag', required=True)
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
    if arguments.command == 'history-tags':
        history_tags(arguments.root, arguments.releases, arguments.tag)
    elif arguments.command == 'fetch':
        fetch(arguments.root.resolve(), arguments.destination.resolve())
    else:
        publish(
            arguments.root.resolve(),
            arguments.packages.resolve(),
            arguments.history.resolve(),
            arguments.destination.resolve(),
            arguments.tag,
            arguments.commit,
        )


if __name__ == '__main__':
    main()
