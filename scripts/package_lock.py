#!/usr/bin/env python3
"""Pin Arch to one archive date, and fetch its verified packages for an ISO.

`resolve` runs on the host (`make lock`) and refreshes packages/archive-date,
the two-entry Omarchy pin, and the vendored Dux Niri files. `fetch` runs in
the builder VM and never changes the pins: it refreshes archlinux-keyring from
the pinned date, then downloads the requested package lists with pacman.
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
            if exc.code != 404 or argument is not None:
                raise
        except Exception:
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


def closure_filenames(date, arch_requested):
    """Resolve the Arch closure to filename entries using the pinned date."""
    with tempfile.TemporaryDirectory(prefix='archive-resolve-') as directory:
        dbpath = pathlib.Path(directory)
        (dbpath / 'local').mkdir()
        (dbpath / 'sync').mkdir()
        config = dbpath / 'pacman.conf'
        sections = ''.join(f'[{repo}]\nServer = {ARCHIVE}/{date}/{repo}/os/x86_64\n' for repo in ARCH_REPOS)
        # Lock-time databases are fetched over HTTPS and only used for resolution.
        config.write_text(f'[options]\nArchitecture = x86_64\nSigLevel = Never\n{sections}')
        for repo in ARCH_REPOS:
            database = dbpath / 'sync' / f'{repo}.db'
            database.write_bytes(fetch_url(f'{ARCHIVE}/{date}/{repo}/os/x86_64/{repo}.db'))
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
                *arch_requested,
            ],
            text=True,
        )
    entries = []
    for line in result.splitlines():
        if line.count('|') != 3:
            continue
        name, version, filename, repo = line.split('|')
        entries.append(dict(name=name, version=version, filename=filename, repository=repo))
    if not set(arch_requested).issubset({e['name'] for e in entries}):
        raise ValueError('Incomplete resolution from the pinned archive date')
    return entries


def vendor_desktop(root, tag, contents):
    """Write the vendored files and the version references for one Dux tag."""
    for (_, path), body in zip(VENDORED, contents, strict=True):
        (root / path).write_bytes(body)
    version = tag.lstrip('v')
    readme = root / 'desktop/README.md'
    old = readme.read_text()
    new = re.sub(r'upstream Dux v[\d.]+ Niri integration', f'upstream Dux v{version} Niri integration', old)
    if new == old:
        raise ValueError('Desktop README has no Dux version to refresh')
    readme.write_text(new)
    pkgbuild = root / 'packages/integration/PKGBUILD'
    old = pkgbuild.read_text()
    new = re.sub(r"'dux-shell>=[\d.]+'", f"'dux-shell>={version}'", old)
    if new == old:
        raise ValueError('Integration recipe has no dux-shell floor to refresh')
    pkgbuild.write_text(new)


def omarchy_arch_deps(pin):
    """Return the pinned Omarchy packages' Arch dependencies.

    The two hooks install from the offline repository, so their Arch
    dependencies must be part of the downloaded closure. Matching the exact
    pinned versions fails closed when the pin goes stale.
    """
    database = pathlib.Path(tempfile.mkdtemp(prefix='omarchy-deps-')) / 'omarchy.db'
    try:
        database.write_bytes(fetch_url(f'{OMARCHY_FILES_URL}omarchy.db'))
        fields = database_entries(database)
    finally:
        shutil.rmtree(database.parent, ignore_errors=True)
    deps = set()
    for entry in pin:
        matches = [info for info in fields.values() if info.get('NAME') == [entry['name']]]
        if len(matches) != 1 or matches[0].get('VERSION') != [entry['version']]:
            raise ValueError(f'Omarchy pin is stale for {entry["name"]}; refresh it with `make lock`')
        deps.update(matches[0].get('DEPENDS', []))
    return deps - {entry['name'] for entry in pin}


def refresh_omarchy_pin():
    with tempfile.TemporaryDirectory(prefix='omarchy-pin-') as directory:
        database = pathlib.Path(directory) / 'omarchy.db'
        database.write_bytes(fetch_url(f'{OMARCHY_FILES_URL}omarchy.db'))
        fields = database_entries(database)
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
    pin = refresh_omarchy_pin()
    pin = [entry for entry in pin if entry['name'] in omarchy_requested]
    arch_requested = sorted(set(requested) - set(omarchy_requested) | omarchy_arch_deps(pin))
    closure_filenames(pinned, arch_requested)
    tag = 'v' + pkgver.split('-')[0]
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


def seed_cache(cache_dir, *sources):
    """Offer already-verified packages to pacman; unknown names are ignored."""
    for source in sources:
        if source is None:
            continue
        for path in pathlib.Path(source).glob('*.pkg.tar.zst'):
            target = cache_dir / path.name
            if not target.exists():
                try:
                    shutil.copyfile(path, target)
                except OSError:
                    continue


def refresh_keyring(date, keyring):
    """Install the pinned date's keyring so pacman verifies that date's packages.

    The package hash comes from the archive database over HTTPS, and its
    detached signature is checked against the repository keyring before pacman
    trusts it. Pacman then enforces signatures for everything it downloads.
    """
    with tempfile.TemporaryDirectory(prefix='archive-keyring-') as directory:
        database = pathlib.Path(directory) / 'core.db'
        database.write_bytes(fetch_url(f'{ARCHIVE}/{date}/core/os/x86_64/core.db'))
        fields = database_entries(database)
        matches = [info for info in fields.values() if info.get('NAME') == ['archlinux-keyring']]
        if len(matches) != 1:
            raise ValueError('Pinned archive date has no archlinux-keyring')
        info = matches[0]
        package = pathlib.Path(directory) / info['FILENAME'][0]
        download(f'{ARCHIVE}/{date}/core/os/x86_64/{info["FILENAME"][0]}', package, info['SHA256SUM'][0])
        signature = package.with_name(package.name + '.sig')
        signature.write_bytes(fetch_url(f'{ARCHIVE}/{date}/core/os/x86_64/{info["FILENAME"][0]}.sig'))
        subprocess.run(['gpgv', '--keyring', str(keyring), str(signature), str(package)], check=True)
        config = pathlib.Path(directory) / 'pacman.conf'
        config.write_text('[options]\nArchitecture = auto\nSigLevel = Never\n')
        subprocess.run(['pacman', '-U', '--noconfirm', '--config', str(config), str(package)], check=True)
    subprocess.run(['pacman-key', '--populate', 'archlinux'], check=True)


def fetch(root, dest, keyring, downloads, host_cache, cache_dir):
    root = pathlib.Path(root)
    date = archive_date(root)
    pin = [entry for entry in omarchy_pin(root) if entry['name'] in requested_packages(root)]
    requested = requested_packages(root)
    arch_requested = sorted(set(requested) - {entry['name'] for entry in pin} | omarchy_arch_deps(pin))
    dest.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (dest / 'arch-downloaded.json').unlink(missing_ok=True)
    refresh_keyring(date, keyring)
    config = pathlib.Path(tempfile.mkdtemp(prefix='archive-fetch-')) / 'pacman.conf'
    sections = ''.join(f'[{repo}]\nServer = {ARCHIVE}/{date}/{repo}/os/x86_64\n' for repo in ARCH_REPOS)
    config.write_text(
        f'[options]\nArchitecture = auto\nSigLevel = Required DatabaseOptional\nCacheDir = {cache_dir}\n{sections}'
    )
    # An empty local database makes pacman download the whole closure instead
    # of skipping what the live builder already installed.
    dbpath = pathlib.Path(tempfile.mkdtemp(prefix='archive-local-'))
    (dbpath / 'local').mkdir()
    (dbpath / 'sync').mkdir()
    try:
        seed_cache(cache_dir, downloads, host_cache)
        subprocess.run(
            ['pacman', '-Syw', '--noconfirm', '--config', str(config), '--dbpath', str(dbpath), *arch_requested],
            check=True,
        )
        result = subprocess.check_output(
            [
                'pacman',
                '-Sp',
                '--config',
                str(config),
                '--dbpath',
                str(dbpath),
                '--print-format',
                '%n|%v|%f|%r',
                *arch_requested,
            ],
            text=True,
        )
        expected = {}
        for repo in ARCH_REPOS:
            for fields in read_database(dbpath / 'sync' / f'{repo}.db'):
                expected[fields['FILENAME'][0]] = (fields['SHA256SUM'][0], fields['PGPSIG'][0])
    finally:
        shutil.rmtree(dbpath, ignore_errors=True)
        shutil.rmtree(config.parent, ignore_errors=True)
    closure = []
    for line in result.splitlines():
        if line.count('|') != 3:
            continue
        name, version, filename, repo = line.split('|')
        closure.append(dict(name=name, version=version, filename=filename, repository=repo))
    if not set(arch_requested).issubset({e['name'] for e in closure}):
        raise ValueError('Incomplete download from the pinned archive date')
    entries = []
    for item in closure:
        package = cache_dir / item['filename']
        if item['filename'] not in expected:
            raise ValueError(f'Downloaded package is not in the pinned databases: {item["filename"]}')
        sha256, signature = expected[item['filename']]
        checksum = digest(package)
        if checksum != sha256:
            raise ValueError(f'Package checksum mismatch: {item["filename"]}')
        place_verified(package, downloads / item['filename'])
        place_verified(package, dest / item['filename'])
        # The installer checks every package signature against the install
        # keyring. These bytes equal the archive's detached signatures.
        (dest / (item['filename'] + '.sig')).write_bytes(base64.b64decode(signature))
        entries.append(
            dict(
                name=item['name'],
                version=item['version'],
                filename=item['filename'],
                sha256=checksum,
                url=f'{ARCHIVE}/{date}/{item["repository"]}/os/x86_64/{item["filename"]}',
                repository=item['repository'],
                signature=signature,
            )
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda entry: fetch_package(entry, dest, downloads, host_cache), pin))
    for entry in pin:
        package = dest / entry['filename']
        if digest(package) != entry['sha256']:
            raise ValueError(f'Package checksum mismatch: {package}')
        signature = package.with_name(package.name + '.sig')
        signature.write_bytes(base64.b64decode(entry['signature'], validate=True))
        subprocess.run(['gpgv', '--keyring', str(keyring), str(signature), str(package)], check=True)
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
