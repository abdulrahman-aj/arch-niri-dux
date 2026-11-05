"""Shared builders/probes helpers: hashing, validation, VM strings, probe dispatch.

Imported two ways, both resolve to this file:
- inside the package: ``from ._common import ...`` (guest and unit tests);
- from ``scripts/*.py`` entry points: ``sys.path.insert(0, <scripts dir>)`` then
  ``from verification._common import ...`` (host runners, guest staging, spec loads).
"""

import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

# 9p share that stages host evidence and guest probes.
VERIFY_MOUNT = 'mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify'


def mount_verify(tolerant=False):
    """Guest shell to (re)mount the 9p evidence share."""
    if tolerant:
        return VERIFY_MOUNT + ' || mountpoint -q /run/verify'
    return VERIFY_MOUNT


def digest(path):
    """SHA256 of a file, the single implementation for builders and finalizers."""
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


# Hex formats keep their domain case: GPG fingerprints print uppercase,
# git commits and package checksums print lowercase.
FINGERPRINT_EXPR = r'[A-F0-9]{40}'
COMMIT_EXPR = r'[a-f0-9]{40}'
SHA256_EXPR = r'[a-f0-9]{64}'


def check_hex(value, expression, message):
    if not re.fullmatch(expression, value):
        raise ValueError(message)


def load_module(name, path):
    """Import a hyphenated script (PKGBUILD-style names are not importable)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def dispatch(name, table):
    """Probe ``__main__`` entry point: ``action = sys.argv[1]`` without elif chains."""
    action = sys.argv[1]
    try:
        handler = table[action]
    except KeyError:
        raise SystemExit(f'Unknown {name} probe')
    handler()


class Milestones:
    """Ordered acceptance record with the same PASS console output as before."""

    def __init__(self, directory):
        self._directory = Path(directory)
        self._names = []

    def passed(self, message):
        self._names.append(message)
        (self._directory / 'milestones.json').write_text(json.dumps(self._names, indent=2))
        print('PASS: ' + message, flush=True)


PNG_TRAILER = b'\x00\x00\x00\x00IEND\xaeB\x60\x82'


def png_ready(path):
    """True once a screenshot file exists and its PNG stream is complete."""
    path = Path(path)
    return path.is_file() and path.read_bytes().endswith(PNG_TRAILER)


def png_dimensions(path):
    data = Path(path).read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', 'Not a PNG screenshot.'
    assert data[12:16] == b'IHDR', 'Screenshot PNG has no IHDR.'
    width, height = struct.unpack('>II', data[16:24])
    assert width > 0 and height > 0, 'Rendered screenshot has no dimensions.'
    return width, height


# Host disk guards: 9 GiB keeps the running VM safe, 8 GiB is the install reserve.
MIN_FREE_BYTES = 9 * 1024**3
HOST_HEADROOM_BYTES = 8 * 1024**3


# Encrypted target inspection from the live ISO: open, mount @, mount ESP.
def install_open(encrypted, esp):
    return (f'cryptsetup open --key-file /run/verify/key {encrypted} dux-root; '
            'mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; '
            f'mount {esp} /mnt/verify/boot')


INSTALL_CLOSE = 'umount -R /mnt/verify; cryptsetup close dux-root'


def private_dir(path, **kwargs):
    """Create a mode-0700 directory (evidence, keys, staged fixtures)."""
    path = Path(path)
    path.mkdir(mode=0o700, **kwargs)
    return path


# Repository layout literals shared by builders (shell entry points keep
# their own copies: no Python runtime exists where they run).
PACKAGES_TARGET = 'packages/packages.target'
REPOSITORY = 'abdulrahman-aj/arch-niri-dux'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases/download'
GEO_MIRROR = 'https://geo.mirror.pkgbuild.com'
OMARCHY_MIRROR = 'https://pkgs.omarchy.org/stable'

# Single makepkg debug toggle shared by real builds and upgrade fixtures.
MAKEPKG_CONFIG = 'source /etc/makepkg.conf\nOPTIONS+=(!debug)\n'
