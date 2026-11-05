#!/usr/bin/env python3
"""Freeze a pacman-resolved closure and validate every payload before ISO assembly."""
import base64
import concurrent.futures
import hashlib
import json
import pathlib
import subprocess
import sys
import tarfile
import urllib.request


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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


def main():
    root, dest = map(pathlib.Path, sys.argv[1:3])
    # Reviewed local builds have recipe/source hashes, not distribution signatures.
    # Keep them outside the signed binary closure and add them after verification.
    aur_names = {entry['name'] for entry in json.loads((root / 'packages/aur-recipes.json').read_text())}
    for source in json.loads((root / 'packages/desktop-sources.json').read_text()):
        if digest(root / source['path']) != source['sha256']:
            raise ValueError(f"Upstream desktop asset changed: {source['path']}")
    requested = set()
    for file in ('packages/packages.target', 'archiso/packages.live'):
        requested.update(line.split('#')[0].strip() for line in (root / file).read_text().splitlines())
    requested.discard('')
    requested.difference_update(aur_names)
    lock = root / 'packages/closure.lock.json'
    if not lock.exists():
        metadata = {}
        for db in pathlib.Path('/var/lib/pacman/sync').glob('*.db'):
            with tarfile.open(db) as archive:
                for member in archive:
                    if not member.name.endswith('/desc'):
                        continue
                    data = archive.extractfile(member).read().decode()
                    fields = {}
                    for section in data.strip().split('\n\n'):
                        key, *values = section.splitlines()
                        fields[key.strip('%')] = values
                    metadata[fields['FILENAME'][0]] = fields
        subprocess.run(['mkdir', '-p', '/build/resolve/local', '/build/resolve/sync'], check=True)
        subprocess.run(['cp', '-a', '/var/lib/pacman/sync/.', '/build/resolve/sync/'], check=True)
        result = subprocess.check_output(['pacman', '-Sp', '--noconfirm', '--dbpath', '/build/resolve', '--print-format', '%n|%v|%f|%r', *sorted(requested)], text=True)
        entries = []
        for line in result.splitlines():
            if line.count('|') != 3:
                continue
            name, version, filename, repo = line.split('|')
            info = metadata[filename]
            url = ('https://pkgs.omarchy.org/stable/x86_64/' if repo == 'omarchy' else f'https://geo.mirror.pkgbuild.com/{repo}/os/x86_64/') + filename
            if 'PGPSIG' not in info:
                signature_path = pathlib.Path('/cache') / (filename + '.sig')
                signature = signature_path.read_bytes() if signature_path.exists() else urllib.request.urlopen(url + '.sig', timeout=60).read()
                info['PGPSIG'] = [base64.b64encode(signature).decode()]
            entries.append(dict(name=name, version=version, filename=filename, sha256=info['SHA256SUM'][0], url=url, repository=repo, signature=info['PGPSIG'][0]))
        if not requested.issubset({e['name'] for e in entries}):
            raise ValueError('Incomplete resolution')
        lock.write_text(json.dumps({'schema': 1, 'requested': sorted(requested), 'packages': sorted(entries, key=lambda e: e['name'])}, indent=2) + '\n')
    payload = json.loads(lock.read_text())
    if sorted(requested) != payload['requested']:
        raise ValueError('Package lists changed; explicitly refresh the closure lock')
    dest.mkdir(parents=True, exist_ok=True)
    def fetch(entry):
        validate_entry(entry)
        package = dest / entry['filename']
        cached = pathlib.Path('/cache') / entry['filename']
        if not package.exists() or digest(package) != entry['sha256']:
            if cache_matches(cached, entry['sha256']):
                subprocess.run(['cp', str(cached), str(package)], check=True)
            else:
                print('Downloading', entry['filename'], flush=True)
                for attempt in range(4):
                    try:
                        try:
                            urllib.request.urlretrieve(entry['url'], str(package))
                        except Exception:
                            if entry['repository'] not in ('core', 'extra'):
                                raise
                            name = entry['name']
                            archive = f"https://archive.archlinux.org/packages/{name[0]}/{name}/{entry['filename']}"
                            urllib.request.urlretrieve(archive, str(package))
                        break
                    except Exception:
                        if attempt == 3:
                            raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, payload['packages']))
    for entry in payload['packages']:
        package = dest / entry['filename']
        if digest(package) != entry['sha256']:
            raise ValueError(f'Package checksum mismatch: {package}')
        signature = package.with_name(package.name + '.sig')
        signature.write_bytes(base64.b64decode(entry['signature'], validate=True))
        subprocess.run(['gpgv', '--keyring', '/state/trusted-packagers.gpg', str(signature), str(package)], check=True)
    prune_payload(dest, payload['packages'])
    # A changed lock also needs a fresh database, not repo-add's merged history.
    for path in dest.glob('offline.*'):
        path.unlink()


if __name__ == '__main__':
    main()
