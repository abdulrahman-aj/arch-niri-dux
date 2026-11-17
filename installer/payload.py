"""Offline payload integrity and signature preflight. Nothing here writes to a disk or the medium."""

import hashlib
from pathlib import Path
import re
import subprocess
import time

if __package__:
    from . import storage
else:
    import storage

run = storage.run

SHARE = Path('/usr/local/share/arch-niri-dux')
REPO = Path('/run/archiso/bootmnt/repo')


def package_list(name):
    """Return the names in one of the ISO's package lists, refusing anything that could be a pacman option."""
    lines = (SHARE / name).read_text().splitlines()
    packages = [package for line in lines if (package := line.split('#', 1)[0].strip())]
    if not packages or any(not re.fullmatch(r'[a-zA-Z0-9@+_.-]+', package) for package in packages):
        raise ValueError(f'Invalid or missing package list: {name}')
    return packages


def check_installation_clock():
    """Refuse when the system clock predates the ISO build; stale clocks mimic expired keys."""
    try:
        build_date = int((SHARE / 'iso-build-date').read_text().strip())
    except (OSError, ValueError):
        raise ValueError('The ISO build date is missing or invalid; refusing to install from this medium.')
    now = time.time()
    if now < build_date:
        iso = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(build_date))
        clock = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(now))
        raise ValueError(
            f'System clock ({clock}) is earlier than the ISO build date ({iso}). Fix the system clock and retry.'
        )


def verify_payload_signatures(config, database, repository, filenames):
    """Verify every resolved package file against the install keyring before the disk is touched.

    pacman --print resolves names only, so without this the first pacstrap is
    the first real signature check, after partitioning. Checking the files in
    place exercises the same keyring and expiry enforcement the pacstrap will
    use, which plain gpg verification would still accept. Nothing is copied,
    and the medium is never written.
    """
    try:
        run(
            'pacman',
            '--config',
            str(config),
            '--dbpath',
            str(database),
            '--upgrade',
            '--print',
            '--print-format',
            '%f',
            *(str(repository / name) for name in filenames),
        )
    except subprocess.CalledProcessError as error:
        raise ValueError(
            'Package signature preflight failed; the install keyring cannot verify the offline payload. '
            'A signing key may have expired or is missing; check the system clock and use a newer ISO.'
        ) from error


def verify_repository(repository):
    manifest = repository / 'SHA256SUMS'
    if not manifest.is_file():
        raise ValueError('Offline payload checksum manifest is missing.')
    listed = set()
    for line in manifest.read_text().splitlines():
        if not line:
            continue
        checksum, filename = line.split(maxsplit=1)
        filename = filename.lstrip('*')
        path = repository / filename
        if not re.fullmatch(r'[a-f0-9]{64}', checksum) or not path.resolve().is_relative_to(repository.resolve()):
            raise ValueError('Invalid offline payload manifest.')
        if not path.is_file():
            raise ValueError(f'Offline payload file is missing: {filename}')
        with path.open('rb') as stream:
            actual_checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual_checksum != checksum:
            raise ValueError(f'Offline payload checksum failed: {filename}')
        listed.add(filename)
    packages = {file.name for file in repository.glob('*.pkg.tar.*') if not file.name.endswith('.sig')}
    databases = {file.name for file in repository.glob('*.db')}
    if not packages or not databases or not (packages | databases).issubset(listed):
        raise ValueError('Offline manifest does not cover every package and repository database.')


def offline_pacman_config(repository, database):
    # The database is assembled on the builder, without a release signing key.
    # Require signatures on every package even when its metadata is rewritten.
    return (
        '[options]\nArchitecture = auto\nSigLevel = Required DatabaseNever TrustedOnly\n'
        'LocalFileSigLevel = Required TrustedOnly\n'
        f'GPGDir = {SHARE / "install-keyring"}\n'
        f'[{database}]\nServer = file://{repository}\n'
    )
