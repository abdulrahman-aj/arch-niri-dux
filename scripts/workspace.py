"""Checkout-owned build and verification storage, guards, and garbage collection."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
MARKER = '.arch-niri-dux-owner'
MIN_FREE = 30 * 1024**3


def cache_root():
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))


def build_state(checkout=ROOT):
    if os.environ.get('BUILD_STATE'):
        return Path(os.environ['BUILD_STATE']).resolve()
    identity = hashlib.sha256(os.fsencode(checkout.resolve())).hexdigest()[:16]
    return cache_root() / f'arch-niri-dux-build-{identity}'


def heavy_guard():
    if os.environ.get('AGENT_HEAVY_JOBS') == 'deny':
        raise SystemExit(
            'Heavy jobs are disabled on this machine (AGENT_HEAVY_JOBS=deny); run this on your build machine.'
        )


def disk_guard(path, minimum=MIN_FREE):
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    if free < minimum:
        raise SystemExit(f'Need {minimum / 1024**3:g} GiB free at {path}; have {free / 1024**3:.1f} GiB. Run make gc.')


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


def accepted_fixtures(registry=None):
    """Accepted full-verify workspaces, newest first: ACCEPTANCE-PASSED plus a disk."""
    registry = Path(registry) if registry is not None else cache_root() / 'arch-niri-dux-states'
    found = []
    if registry.is_dir():
        for record in registry.iterdir():
            if record.is_file() and not record.is_symlink():
                value = record.read_text().strip()
                if not Path(value).is_absolute():
                    continue
                directory = Path(value)
                if (directory / 'ACCEPTANCE-PASSED').is_file() and (directory / 'disk.qcow2').is_file():
                    found.append(directory)
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def newest_accepted_fixture(registry=None):
    """Newest accepted disk.qcow2, or None when no full run has passed."""
    fixtures = accepted_fixtures(registry)
    return fixtures[0] / 'disk.qcow2' if fixtures else None


def candidates(cache, checkout):
    paths = set(cache.glob('arch-niri-dux-build*'))
    verification = cache / 'arch-niri-dux-verification'
    if verification.is_dir():
        paths.update(verification.iterdir())
    evidence = checkout / 'out/verify'
    if evidence.is_dir():
        paths.update(evidence.iterdir())
    registry = cache / 'arch-niri-dux-states'
    if registry.is_dir():
        for record in registry.iterdir():
            if record.is_file() and not record.is_symlink():
                value = record.read_text().strip()
                if Path(value).is_absolute():
                    paths.add(Path(value))
    return sorted(paths)


def collect(cache, checkout, dry_run=False):
    freed = 0
    removed = []
    protected = [cache / 'arch-niri-dux-downloads', cache / 'arch-niri-dux-states']
    if os.environ.get('BUILD_STATE'):
        protected.append(build_state())
    for path in candidates(cache, checkout):
        if not path.exists():
            continue
        # Never follow links or delete a parent of shared storage or the checkout.
        if path.is_symlink() or not path.is_dir():
            print(f'Unowned, left alone: {path}')
            continue
        resolved = path.resolve()
        if any(
            resolved == p.resolve() or resolved in p.resolve().parents or p.resolve() in resolved.parents
            for p in [*protected, checkout]
        ):
            print(f'Protected, left alone: {path}')
            continue
        marker = path / MARKER
        if not marker.is_file() or marker.is_symlink():
            print(f'Unowned, left alone: {path}')
            continue
        owner = Path(marker.read_text().strip())
        if not owner.is_absolute():
            print(f'Unowned, left alone: {path}')
            continue
        if owner.exists():
            print(f'Kept, checkout exists: {path}')
            continue
        size = sum(item.lstat().st_blocks * 512 for item in path.rglob('*') if not item.is_symlink())
        print(f'{"Would delete" if dry_run else "Deleting"}: {path} ({size / 1024**3:.2f} GiB)')
        if not dry_run:
            shutil.rmtree(path)
        removed.append(path)
        freed += size
    print(f'{"Would free" if dry_run else "Freed"} {freed / 1024**3:.2f} GiB')
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['state', 'prepare-build', 'heavy-guard', 'gc'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.command == 'heavy-guard':
        heavy_guard()
    elif args.command == 'gc':
        collect(cache_root(), ROOT, args.dry_run)
    else:
        state = build_state()
        if args.command == 'prepare-build':
            heavy_guard()
            disk_guard(state)
            disk_guard(ROOT / 'out')
        state.mkdir(parents=True, exist_ok=True)
        if not os.environ.get('BUILD_STATE'):
            own(state)
        print(state)


if __name__ == '__main__':
    main()
