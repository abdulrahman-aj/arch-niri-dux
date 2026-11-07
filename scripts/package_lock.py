#!/usr/bin/env python3
"""Resolve the signed binary closure lock, and fetch its verified packages for an ISO.

`resolve` runs on the host (`make lock`) and rewrites packages/closure.lock.json
from the current mirror databases. `fetch` runs in the builder VM and never
changes the lock.
"""

import argparse
import base64
import concurrent.futures
import json
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

from verification.digest import digest


DOWNLOAD_TIMEOUT = 60
DOWNLOAD_ATTEMPTS = 4
# Resolution order matters: pacman prefers the first repository with a package.
REPOSITORIES = {
    'core': 'https://geo.mirror.pkgbuild.com/core/os/x86_64/',
    'extra': 'https://geo.mirror.pkgbuild.com/extra/os/x86_64/',
    'omarchy': 'https://pkgs.omarchy.org/stable/x86_64/',
}


def download(url, dest, sha256, timeout=DOWNLOAD_TIMEOUT, attempts=DOWNLOAD_ATTEMPTS):
    """Place url's content at dest only once it matches sha256.

    The rename from a temporary file in dest's directory keeps dest complete
    for concurrent readers of a shared cache.
    """
    dest = pathlib.Path(dest)
    last_error = None
    for attempt in range(attempts):
        tmp_path = None
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                with tempfile.NamedTemporaryFile(delete=False, dir=dest.parent, prefix='.' + dest.name + '.') as tmp:
                    tmp_path = pathlib.Path(tmp.name)
                    shutil.copyfileobj(response, tmp)
            if digest(tmp_path) != sha256:
                raise ValueError(f'Checksum mismatch for {url}')
            tmp_path.replace(dest)
            return
        except Exception as exc:
            last_error = exc
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            if attempt + 1 >= attempts:
                break
            time.sleep(2**attempt)
    raise RuntimeError(f'Failed to download {url} after {attempts} attempts: {last_error}') from last_error


def read_database(path):
    """Return each package's desc fields from a pacman repository database."""
    packages = []
    with tarfile.open(path) as archive:
        for member in archive:
            if not member.name.endswith('/desc'):
                continue
            fields = {}
            for section in archive.extractfile(member).read().decode().strip().split('\n\n'):
                key, *values = section.splitlines()
                fields[key.strip('%')] = values
            packages.append(fields)
    return packages


def requested_packages(root):
    # Reviewed local builds have recipe/source hashes, not distribution signatures.
    # Keep them outside the signed binary closure and add them after verification.
    aur_names = {entry['name'] for entry in json.loads((root / 'packages/aur-recipes.json').read_text())}
    requested = set()
    for file in ('packages/packages.target', 'archiso/packages.live'):
        requested.update(line.split('#')[0].strip() for line in (root / file).read_text().splitlines())
    requested.discard('')
    return sorted(requested - aur_names)


def resolve(root):
    requested = requested_packages(root)
    with tempfile.TemporaryDirectory(prefix='closure-resolve-') as directory:
        dbpath = pathlib.Path(directory)
        (dbpath / 'local').mkdir()
        (dbpath / 'sync').mkdir()
        config = dbpath / 'pacman.conf'
        sections = ''.join(f'[{repo}]\nServer = {url.rstrip("/")}\n' for repo, url in REPOSITORIES.items())
        # Lock-time signatures are recorded, then enforced by gpgv at fetch time.
        config.write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n{sections}')
        metadata = {}
        for repo, url in REPOSITORIES.items():
            database = dbpath / 'sync' / f'{repo}.db'
            with urllib.request.urlopen(f'{url}{repo}.db', timeout=DOWNLOAD_TIMEOUT) as response:
                database.write_bytes(response.read())
            metadata.update({fields['FILENAME'][0]: fields for fields in read_database(database)})
        # An empty local database makes pacman print the whole closure.
        result = subprocess.check_output(
            [
                'pacman',
                '-Sp',
                '--noconfirm',
                '--config',
                str(config),
                '--dbpath',
                str(dbpath),
                '--print-format',
                '%n|%v|%f|%r',
                *requested,
            ],
            text=True,
        )
    entries = []
    for line in result.splitlines():
        if line.count('|') != 3:
            continue
        name, version, filename, repo = line.split('|')
        info = metadata[filename]
        url = REPOSITORIES[repo] + filename
        if 'PGPSIG' in info:
            signature = info['PGPSIG'][0]
        else:
            with urllib.request.urlopen(url + '.sig', timeout=DOWNLOAD_TIMEOUT) as response:
                signature = base64.b64encode(response.read()).decode()
        entries.append(
            dict(
                name=name,
                version=version,
                filename=filename,
                sha256=info['SHA256SUM'][0],
                url=url,
                repository=repo,
                signature=signature,
            )
        )
    if not set(requested).issubset({e['name'] for e in entries}):
        raise ValueError('Incomplete resolution')
    lock = {'schema': 1, 'requested': requested, 'packages': sorted(entries, key=lambda e: e['name'])}
    (root / 'packages/closure.lock.json').write_text(json.dumps(lock, indent=2) + '\n')


def cache_matches(path, expected):
    try:
        return digest(path) == expected
    except OSError:
        # Cache reuse is optional; never change permissions on the host cache.
        return False


def validate_entry(entry):
    if pathlib.Path(entry['filename']).name != entry['filename']:
        raise ValueError('Unsafe package filename')
    if len(entry['sha256']) != 64 or any(c not in '0123456789abcdef' for c in entry['sha256']):
        raise ValueError('Invalid package digest')
    if not entry['url'].startswith('https://'):
        raise ValueError('Package source must use HTTPS')
    if not entry['signature']:
        raise ValueError('Unsigned package')


def prune_payload(dest, entries):
    # repo-add must see only this lock's versions after an intentional refresh.
    expected = {entry['filename'] for entry in entries}
    for path in dest.glob('*.pkg.tar.zst'):
        if path.name not in expected:
            path.unlink()
            path.with_name(path.name + '.sig').unlink(missing_ok=True)


def fetch_package(entry, dest, downloads, host_cache):
    """Copy one locked package into dest from a cache, downloading on a miss.

    Downloads land in the shared cache only after their hash matches, so a cache
    hit is a verified, complete file.
    """
    validate_entry(entry)
    package = dest / entry['filename']
    if cache_matches(package, entry['sha256']):
        return
    source = downloads / entry['filename']
    if not cache_matches(source, entry['sha256']):
        host_package = host_cache / entry['filename'] if host_cache else None
        if host_package and cache_matches(host_package, entry['sha256']):
            source = host_package
        else:
            print('Downloading', entry['filename'], flush=True)
            try:
                download(entry['url'], source, entry['sha256'])
            except Exception:
                if entry['repository'] not in ('core', 'extra'):
                    raise
                name = entry['name']
                download(
                    f'https://archive.archlinux.org/packages/{name[0]}/{name}/{entry["filename"]}',
                    source,
                    entry['sha256'],
                )
    shutil.copyfile(source, package)


def fetch(root, dest, keyring, downloads, host_cache):
    payload = json.loads((root / 'packages/closure.lock.json').read_text())
    if requested_packages(root) != payload['requested']:
        raise ValueError('Package lists changed; refresh the closure lock with `make lock`')
    dest.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda entry: fetch_package(entry, dest, downloads, host_cache), payload['packages']))
    for entry in payload['packages']:
        package = dest / entry['filename']
        if digest(package) != entry['sha256']:
            raise ValueError(f'Package checksum mismatch: {package}')
        signature = package.with_name(package.name + '.sig')
        signature.write_bytes(base64.b64decode(entry['signature'], validate=True))
        subprocess.run(['gpgv', '--keyring', str(keyring), str(signature), str(package)], check=True)
    prune_payload(dest, payload['packages'])
    # A changed lock also needs a fresh database, not repo-add's merged history.
    for path in dest.glob('offline.*'):
        path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve_parser = commands.add_parser('resolve', help='rewrite the closure lock from current mirrors')
    resolve_parser.add_argument('root', type=pathlib.Path)
    fetch_parser = commands.add_parser('fetch', help='fetch and verify the locked packages')
    fetch_parser.add_argument('root', type=pathlib.Path)
    fetch_parser.add_argument('dest', type=pathlib.Path)
    fetch_parser.add_argument('--keyring', type=pathlib.Path, required=True)
    fetch_parser.add_argument('--downloads', type=pathlib.Path, required=True, help='shared verified download cache')
    fetch_parser.add_argument('--host-cache', type=pathlib.Path, help='read-only pacman package cache')
    arguments = parser.parse_args()
    if arguments.command == 'resolve':
        resolve(arguments.root.resolve())
    else:
        fetch(arguments.root, arguments.dest, arguments.keyring, arguments.downloads, arguments.host_cache)


if __name__ == '__main__':
    main()
