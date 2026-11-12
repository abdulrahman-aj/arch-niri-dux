#!/usr/bin/env python3
"""Pin Arch to one archive date, and fetch its verified packages for an ISO.

`resolve` runs on the host (`make lock`) and refreshes packages/archive-date,
the two-entry Omarchy pin, and the vendored Dux Niri files. `fetch` runs in
the builder VM and never changes the pins: it downloads the requested package
lists with pacman and checks each signature against keys/trusted-packagers.gpg.
"""

import argparse
import base64
import concurrent.futures
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

from verification.digest import digest


DOWNLOAD_TIMEOUT = 60
DOWNLOAD_ATTEMPTS = 4
ARCHIVE = 'https://archive.archlinux.org/repos'
ARCH_REPOS = ('core', 'extra')
OMARCHY_FILES_URL = 'https://pkgs.omarchy.org/stable/x86_64/'
# Only these two Omarchy packages are reviewed inputs; anything else is refused.
OMARCHY_NAMES = ('limine-mkinitcpio-hook', 'limine-snapper-sync')
# Upstream file suffix -> tree path. The tag matches the pinned dux-shell.
VENDORED = (
    ('niri.kdl', 'desktop/usr/share/arch-niri-dux/niri/config.kdl'),
    ('niri-binds.kdl', 'desktop/usr/share/arch-niri-dux/niri/dms/binds.kdl'),
    ('niri-colors.kdl', 'desktop/usr/share/arch-niri-dux/niri/dms/colors.kdl'),
    ('niri-layout.kdl', 'desktop/usr/share/arch-niri-dux/niri/dms/layout.kdl'),
    ('niri-alttab.kdl', 'desktop/usr/share/arch-niri-dux/niri/dms/alttab.kdl'),
    ('niri-input.kdl', 'desktop/usr/share/arch-niri-dux/niri/dms/input.kdl'),
)
DATE_PATTERN = re.compile(r'\d{4}/\d{2}/\d{2}\n')


def fetch_url(url, timeout=DOWNLOAD_TIMEOUT, attempts=DOWNLOAD_ATTEMPTS):
    """Return url's bytes, retrying transient failures."""
    last_error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(2**attempt)
    raise RuntimeError(f'Failed to fetch {url} after {attempts} attempts: {last_error}') from last_error


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


def archive_date(root):
    date = (root / 'packages/archive-date').read_text()
    if not DATE_PATTERN.fullmatch(date):
        raise ValueError('Pinned archive date must look like YYYY/MM/DD')
    return date.strip()


def omarchy_pin(root):
    entries = json.loads((root / 'packages/omarchy-pin.json').read_text())
    names = {entry['name'] for entry in entries}
    if names - set(OMARCHY_NAMES) or len(entries) != len(names):
        raise ValueError('Omarchy pin holds only the two reviewed packages')
    for entry in entries:
        validate_entry(entry)
    return sorted(entries, key=lambda e: e['name'])


def resolve_date(argument):
    """Return the requested archive date, or the latest available one."""
    if argument is not None:
        if not DATE_PATTERN.fullmatch(argument + '\n'):
            raise ValueError('Archive date must look like YYYY/MM/DD')
        candidates = [argument]
    else:
        today = datetime.date.today()
        candidates = [(today - datetime.timedelta(days=offset)).strftime('%Y/%m/%d') for offset in range(8)]
    for date in candidates:
        try:
            with urllib.request.urlopen(f'{ARCHIVE}/{date}/core/os/x86_64/core.db', timeout=DOWNLOAD_TIMEOUT):
                return date
        except urllib.error.HTTPError as exc:
            # Only a missing snapshot advances to the previous day. Any other
            # failure aborts instead of silently pinning an older date.
            if exc.code != 404:
                raise
            if argument is not None:
                raise
    raise ValueError('No Arch archive snapshot is available for the last 8 days')


def database_entries(database):
    """Map each database package filename to its desc fields."""
    return {fields['FILENAME'][0]: fields for fields in read_database(database)}


def dux_shell_version(extra_db):
    for fields in read_database(extra_db):
        if fields.get('NAME') == ['dux-shell']:
            return fields['VERSION'][0]
    raise ValueError('Pinned archive date has no dux-shell')


def pinned_closure(date, requested, required, workdir, cache_dir=None):
    """Resolve requested names against the pinned date with pacman.

    Returns the pacman command prefix, for downloading into cache_dir, and the
    closure entries with each database's SHA256 and detached signature.
    `required` names must appear verbatim; provisions and soname deps from the
    Omarchy pin resolve to real package names, so callers check only their
    direct list.
    """
    config = workdir / 'pacman.conf'
    sections = ''.join(f'[{repo}]\nServer = {ARCHIVE}/{date}/{repo}/os/x86_64\n' for repo in ARCH_REPOS)
    cache = f'CacheDir = {cache_dir}\n' if cache_dir else ''
    # fetch checks every package with gpgv against the repository keyring,
    # which is also the install keyring, so pacman's own check would only add
    # a dependency on the bootstrap ISO's keyring.
    config.write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n{cache}{sections}')
    # An empty local database makes pacman print and download the whole
    # closure instead of skipping what the live builder already installed.
    dbpath = workdir / 'db'
    (dbpath / 'local').mkdir(parents=True)
    (dbpath / 'sync').mkdir()
    # Writing the databases directly, like `pacman -Sy`, works without root.
    for repo in ARCH_REPOS:
        (dbpath / 'sync' / f'{repo}.db').write_bytes(fetch_url(f'{ARCHIVE}/{date}/{repo}/os/x86_64/{repo}.db'))
    pacman = ['pacman', '--noconfirm', '--config', str(config), '--dbpath', str(dbpath)]
    result = subprocess.check_output([*pacman, '-Sp', '--print-format', '%n|%v|%f|%r', *requested], text=True)
    metadata = {}
    for repo in ARCH_REPOS:
        for fields in read_database(dbpath / 'sync' / f'{repo}.db'):
            metadata[fields['FILENAME'][0]] = fields
    entries = []
    for line in result.splitlines():
        if line.count('|') != 3:
            continue
        name, version, filename, repo = line.split('|')
        fields = metadata[filename]
        entries.append(
            dict(
                name=name,
                version=version,
                filename=filename,
                sha256=fields['SHA256SUM'][0],
                url=f'{ARCHIVE}/{date}/{repo}/os/x86_64/{filename}',
                repository=repo,
                signature=fields['PGPSIG'][0],
            )
        )
    if not set(required).issubset({e['name'] for e in entries}):
        raise ValueError('Incomplete resolution from the pinned archive date')
    return pacman, entries


def vendor_desktop(root, tag, contents):
    """Write the vendored files and the version reference for one Dux tag."""
    for (_, path), body in zip(VENDORED, contents, strict=True):
        (root / path).write_bytes(body)
    version = tag.lstrip('v')
    pkgbuild = root / 'packages/integration/PKGBUILD'
    old = pkgbuild.read_text()
    new, count = re.subn(r"'dux-shell>=[\d.]+'", f"'dux-shell>={version}'", old)
    if count == 0:
        raise ValueError('Integration recipe has no dux-shell floor to refresh')
    pkgbuild.write_text(new)


def omarchy_arch_deps(pin):
    """Return the pinned Omarchy packages' Arch dependencies from the pin.

    The two hooks install from the offline repository, so their Arch
    dependencies must be part of the downloaded closure. The pin records the
    dependencies at lock time, so fetch needs no Omarchy database.
    """
    names = {entry['name'] for entry in pin}
    deps = set()
    for entry in pin:
        deps.update(entry['depends'])
    return deps - names


def release_omarchy(root):
    """Return the pinned Omarchy packages our signed repository carries.

    `make lock` is the only thing that moves the pin; this projects that same
    reviewed source into release metadata instead of a second list. Entries
    outside the requested package lists are not published, mirroring fetch.
    """
    pin = omarchy_pin(root)
    requested = set(requested_packages(root))
    return sorted(
        (
            dict(name=entry['name'], version=entry['version'], filename=entry['filename'], sha256=entry['sha256'])
            for entry in pin
            if entry['name'] in requested
        ),
        key=lambda entry: entry['name'],
    )


def refresh_omarchy_pin():
    database = fetch_url(f'{OMARCHY_FILES_URL}omarchy.db')
    with tempfile.TemporaryDirectory(prefix='omarchy-pin-') as directory:
        path = pathlib.Path(directory) / 'omarchy.db'
        path.write_bytes(database)
        fields = database_entries(path)
        entries = []
        for name in OMARCHY_NAMES:
            matches = [info for info in fields.values() if info.get('NAME') == [name]]
            if len(matches) != 1:
                raise ValueError(f'Omarchy repository has no single {name}')
            info = matches[0]
            signature = base64.b64encode(fetch_url(OMARCHY_FILES_URL + info['FILENAME'][0] + '.sig')).decode()
            entries.append(
                dict(
                    name=name,
                    version=info['VERSION'][0],
                    filename=info['FILENAME'][0],
                    sha256=info['SHA256SUM'][0],
                    url=OMARCHY_FILES_URL + info['FILENAME'][0],
                    repository='omarchy',
                    signature=signature,
                    depends=info.get('DEPENDS', []),
                )
            )
    return sorted(entries, key=lambda e: e['name'])


def resolve(root, date=None):
    root = pathlib.Path(root).resolve()
    pinned = resolve_date(date)
    with tempfile.TemporaryDirectory(prefix='archive-resolve-') as directory:
        extra_db = pathlib.Path(directory) / 'extra.db'
        extra_db.write_bytes(fetch_url(f'{ARCHIVE}/{pinned}/extra/os/x86_64/extra.db'))
        pkgver = dux_shell_version(extra_db)
    requested = requested_packages(root)
    omarchy_requested = sorted(set(requested) & set(OMARCHY_NAMES))
    pin = [entry for entry in refresh_omarchy_pin() if entry['name'] in omarchy_requested]
    direct_arch = set(requested) - set(omarchy_requested)
    arch_requested = sorted(direct_arch | omarchy_arch_deps(pin))
    # Fail fast on the host; fetch re-resolves authoritatively in the guest.
    with tempfile.TemporaryDirectory(prefix='archive-resolve-') as directory:
        pinned_closure(pinned, arch_requested, direct_arch, pathlib.Path(directory))
    tag = 'v' + pkgver.split('-')[0].split(':')[-1]
    base = f'https://raw.githubusercontent.com/duxlabs-dev/dux-shell/{tag}/core/internal/config/embedded/'
    suffixes = ('niri.kdl', 'niri-binds.kdl', 'niri-colors.kdl', 'niri-layout.kdl', 'niri-alttab.kdl', 'niri-input.kdl')
    contents = [fetch_url(base + suffix) for suffix in suffixes]
    # Every network read succeeded; now rewrite the pins and vendored files.
    vendor_desktop(root, tag, contents)
    (root / 'packages/omarchy-pin.json').write_text(json.dumps(pin, indent=2) + '\n')
    (root / 'packages/archive-date').write_text(pinned + '\n')


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
    # repo-add must see only this refresh's versions after an intentional update.
    expected = {entry['filename'] for entry in entries}
    for path in dest.glob('*.pkg.tar.zst'):
        if path.name not in expected:
            path.unlink()
            path.with_name(path.name + '.sig').unlink(missing_ok=True)


def place_verified(source, dest):
    """Copy a hash-verified file into place atomically for cache readers."""
    dest = pathlib.Path(dest)
    with tempfile.NamedTemporaryFile(delete=False, dir=dest.parent, prefix='.' + dest.name + '.') as tmp:
        with open(source, 'rb') as origin:
            shutil.copyfileobj(origin, tmp)
    pathlib.Path(tmp.name).replace(dest)


def fetch_package(entry, dest, downloads, host_cache):
    """Copy one pinned Omarchy package into dest from a cache, downloading on a miss.

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
            download(entry['url'], source, entry['sha256'])
    shutil.copyfile(source, package)


def verify_signature(keyring, signature, package):
    """Check a detached signature against the repository keyring.

    The installer trusts only this keyring, so a signer missing here would
    fail the install; fail the build instead and name the key to review.
    """
    result = subprocess.run(
        ['gpgv', '--keyring', str(keyring), str(signature), str(package)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        key = re.search(r'key ([0-9A-F]{16,40})', result.stderr)
        signer = key.group(1) if key else 'unknown'
        raise ValueError(
            f'Signature check failed for {package.name} (signer {signer}): {result.stderr.strip()}\n'
            f'Review the signer and add it to keys/trusted-packagers.gpg'
        )


def fetch(root, dest, keyring, downloads, host_cache, cache_dir):
    root = pathlib.Path(root)
    date = archive_date(root)
    requested = requested_packages(root)
    pin = [entry for entry in omarchy_pin(root) if entry['name'] in requested]
    direct_arch = set(requested) - {entry['name'] for entry in pin}
    arch_requested = sorted(direct_arch | omarchy_arch_deps(pin))
    dest.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (dest / 'arch-downloaded.json').unlink(missing_ok=True)
    workdir = pathlib.Path(tempfile.mkdtemp(prefix='archive-fetch-'))
    try:
        pacman, entries = pinned_closure(date, arch_requested, direct_arch, workdir, cache_dir)
        # Offer pacman only this closure's verified files from earlier builds.
        for entry in entries:
            target = cache_dir / entry['filename']
            if cache_matches(target, entry['sha256']):
                continue
            for source in (downloads, host_cache):
                if source is not None and cache_matches(source / entry['filename'], entry['sha256']):
                    place_verified(source / entry['filename'], target)
                    break
        subprocess.run([*pacman, '-Sw', *arch_requested], check=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    for entry in entries:
        package = cache_dir / entry['filename']
        if digest(package) != entry['sha256']:
            raise ValueError(f'Package checksum mismatch: {entry["filename"]}')
        for target in (downloads / entry['filename'], dest / entry['filename']):
            if not cache_matches(target, entry['sha256']):
                place_verified(package, target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda entry: fetch_package(entry, dest, downloads, host_cache), pin))
    for entry in entries + pin:
        package = dest / entry['filename']
        if digest(package) != entry['sha256']:
            raise ValueError(f'Package checksum mismatch: {package}')
        signature = package.with_name(package.name + '.sig')
        signature.write_bytes(base64.b64decode(entry['signature'], validate=True))
        verify_signature(keyring, signature, package)
    prune_payload(dest, entries + pin)
    (dest / 'arch-downloaded.json').write_text(json.dumps(sorted(entries, key=lambda e: e['name']), indent=2) + '\n')
    # A changed refresh also needs a fresh database, not repo-add's merged history.
    for path in dest.glob('offline.*'):
        path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve_parser = commands.add_parser('resolve', help='refresh the archive date, Omarchy pin and vendored files')
    resolve_parser.add_argument('root', type=pathlib.Path)
    resolve_parser.add_argument('--date', default=None, help='archive date as YYYY/MM/DD (default: latest available)')
    fetch_parser = commands.add_parser('fetch', help='fetch and verify the pinned packages')
    fetch_parser.add_argument('root', type=pathlib.Path)
    fetch_parser.add_argument('dest', type=pathlib.Path)
    fetch_parser.add_argument('--keyring', type=pathlib.Path, required=True)
    fetch_parser.add_argument('--downloads', type=pathlib.Path, required=True, help='shared verified download cache')
    fetch_parser.add_argument('--host-cache', type=pathlib.Path, help='read-only pacman package cache')
    fetch_parser.add_argument('--cache-dir', type=pathlib.Path, required=True, help="pacman's download cache")
    arguments = parser.parse_args()
    if arguments.command == 'resolve':
        resolve(arguments.root.resolve(), date=arguments.date)
    else:
        fetch(
            arguments.root,
            arguments.dest,
            arguments.keyring,
            arguments.downloads,
            arguments.host_cache,
            arguments.cache_dir,
        )


if __name__ == '__main__':
    main()
