"""Checkout-owned paths, ownership markers and the storage registry."""

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKER = '.arch-niri-dux-owner'


def cache_root():
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))


def build_state(checkout=ROOT):
    if os.environ.get('BUILD_STATE'):
        return Path(os.environ['BUILD_STATE']).resolve()
    identity = hashlib.sha256(os.fsencode(checkout.resolve())).hexdigest()[:16]
    return cache_root() / f'arch-niri-dux-build-{identity}'


def downloads_root():
    return cache_root() / 'arch-niri-dux-downloads'


def own(directory, checkout=ROOT):
    directory = directory.resolve()
    owner = checkout.resolve()
    marker = directory / MARKER
    if not marker.exists() and any(directory.iterdir()):
        raise SystemExit(f'Refusing to claim nonempty unowned directory: {directory}')
    if marker.exists() and marker.read_text().strip() != str(owner):
        raise SystemExit(f'State belongs to another checkout: {directory}')
    marker.write_text(str(owner) + '\n')
    registry = cache_root() / 'arch-niri-dux-states'
    registry.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.fsencode(directory)).hexdigest()
    (registry / key).write_text(str(directory) + '\n')


def registered_paths(registry):
    """Absolute directories recorded in a states registry."""
    paths = []
    if registry.is_dir():
        for record in registry.iterdir():
            if record.is_file() and not record.is_symlink():
                value = record.read_text().strip()
                if Path(value).is_absolute():
                    paths.append(Path(value))
    return paths
