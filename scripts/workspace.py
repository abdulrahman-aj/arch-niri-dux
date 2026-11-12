"""Checkout-owned build and verification storage, guards, and garbage collection."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
MARKER = '.arch-niri-dux-owner'
# Agents' builds and VMs keep this margin because several streams share the
# build machine; one build alone needs far less (BUILD_SPACE, ISO_SPACE).
MIN_FREE = 30 * 1024**3
# Measured on a development build from an empty state (msi-dux, 2026-11-13):
# the state peaked at 7.3 GiB (the builder disk keeps it for the next build),
# the shared downloads cache holds 3.3 GiB, and the ISO is 2.7-2.9 GiB.
BUILD_SPACE = 8 * 1024**3
DOWNLOADS_SPACE = int(3.5 * 1024**3)
ISO_SPACE = 3 * 1024**3
BUILDER_MIB = 8192
# A development build with a 4 GiB builder succeeded (host RSS peaked at
# 4.5 GiB instead of 8.6 GiB), so humans with less memory get at least this.
BUILDER_MIN_MIB = 4096
MEMORY_HEADROOM_MIB = 1024

# Environment variables agent harnesses set in every command they run, and
# that jobs they start inherit. Match exact names only: T3's integrated
# terminal (a human) carries T3CODE_PROJECT_ROOT, and only the agents T3
# launches get T3CODE_THREAD_ID. A human may set CODEX_HOME or OPENCODE_CONFIG.
AGENT_MARKERS = (
    'T3CODE_THREAD_ID',  # every agent T3 launches
    'CLAUDECODE',  # Claude Code tool shells
    'CODEX_THREAD_ID',  # Codex tool shells
    'CODEX_CI',
    'OPENCODE',  # OpenCode tool shells
    'OPENCODE_SESSION_ID',
    'AI_AGENT',  # cross-harness convention (Claude Code, OpenCode)
    'AGENT',
)


def cache_root():
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))


def build_state(checkout=ROOT):
    if os.environ.get('BUILD_STATE'):
        return Path(os.environ['BUILD_STATE']).resolve()
    identity = hashlib.sha256(os.fsencode(checkout.resolve())).hexdigest()[:16]
    return cache_root() / f'arch-niri-dux-build-{identity}'


def agent_markers(env=None):
    """The agent markers set in env (default: this process); empty for a human."""
    env = os.environ if env is None else env
    return [name for name in AGENT_MARKERS if env.get(name)]


def heavy_guard(env=None):
    """AGENT_HEAVY_JOBS=deny keeps agents' builds and VMs off a machine; its human still runs them."""
    env = os.environ if env is None else env
    markers = agent_markers(env)
    if env.get('AGENT_HEAVY_JOBS') == 'deny' and markers:
        raise SystemExit(
            f'Heavy jobs are disabled for agents on this machine (AGENT_HEAVY_JOBS=deny; agent: {", ".join(markers)}); '
            'run this on your build machine.'
        )


def existing(path):
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    return path


def tree_size(path):
    """Allocated bytes under path; sparse disk images count only what they use."""
    path = Path(path)
    if not path.exists():
        return 0
    return sum(item.lstat().st_blocks * 512 for item in path.rglob('*') if not item.is_symlink())


def downloads_root():
    return cache_root() / 'arch-niri-dux-downloads'


def build_space_needed(state):
    """Free bytes one ISO build still needs beside out/: what earlier builds
    left in its state and the shared downloads cache counts toward the peak."""
    return max(0, BUILD_SPACE - tree_size(state)) + max(0, DOWNLOADS_SPACE - tree_size(downloads_root()))


def space_shortfalls(needs):
    """Filesystems without room for their summed needs, given (path, bytes, purpose) entries.
    Returns (path, needed, free, purposes) per short filesystem."""
    groups = {}
    for path, size, purpose in needs:
        path = existing(path)
        group = groups.setdefault(path.stat().st_dev, [path, 0, []])
        group[1] += size
        group[2].append(purpose)
    shortfalls = []
    for anchor, total, purposes in groups.values():
        free = shutil.disk_usage(anchor).free
        if free < total:
            shortfalls.append((anchor, total, free, purposes))
    return shortfalls


def build_needs(state):
    """(path, bytes, purpose) entries for the free space one ISO build needs."""
    return [
        (state, build_space_needed(state), 'the ISO build (builder disk, kept for rebuilds, and package downloads)'),
        (ROOT / 'out', ISO_SPACE, 'the finished ISO in out/'),
    ]


def human_build_guard(state):
    shortfalls = space_shortfalls(build_needs(state))
    if shortfalls:
        path, need, free, purposes = shortfalls[0]
        raise SystemExit(
            f'{path} has {free / 1024**3:.1f} GiB free but needs {need / 1024**3:.1f} GiB for '
            f'{" and ".join(purposes)}. Free some space (make gc removes builds of deleted checkouts).'
        )


def builder_memory(env=None):
    """(MiB for the builder VM, warning or None). Agents wait for memory other
    streams on the shared build machine release; humans start at once with a
    VM that fits what is available."""
    if agent_markers(env):
        from verification.vm import wait_for_memory

        wait_for_memory()
        return BUILDER_MIB, None
    available = available_memory() // 1024**2
    mib = max(BUILDER_MIN_MIB, min(BUILDER_MIB, (available - MEMORY_HEADROOM_MIB) // 512 * 512))
    if available - mib < MEMORY_HEADROOM_MIB:
        effect = 'The build still runs, but the system may swap and slow down. Closing big apps helps.'
    elif mib < BUILDER_MIB:
        effect = 'The build works with less; closing big apps first is optional.'
    else:
        return mib, None
    return mib, (
        f'Memory is tight: {available / 1024:.1f} GiB is available, so the builder VM gets '
        f'{mib / 1024:g} GiB instead of {BUILDER_MIB / 1024:g}.\n{effect}'
    )


def available_memory():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise SystemExit('MemAvailable missing from /proc/meminfo')


def disk_guard(path, minimum=MIN_FREE):
    path = existing(path)
    free = shutil.disk_usage(path).free
    if free < minimum:
        raise SystemExit(f'Need {minimum / 1024**3:g} GiB free at {path}; have {free / 1024**3:.1f} GiB. Run make gc.')


def socket_guard(directory):
    """Refuse kept-VM layouts past the 107-byte Unix socket path limit."""
    if len(str(Path(directory) / '.sockets' / 'serial.sock')) > 107:
        raise SystemExit('Kept-VM socket path exceeds the 107-byte Unix socket limit; use a shorter --directory.')


def kept_running(directory):
    """True when a kept VM's QMP socket answers in the evidence directory."""
    sock = Path(directory) / '.sockets' / 'qmp.sock'
    if not sock.exists():
        return False
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.settimeout(2)
        probe.connect(str(sock))
        return True
    except OSError:
        return False
    finally:
        probe.close()


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


def accepted_fixtures(registry=None):
    """Accepted full-verify workspaces, newest first: ACCEPTANCE-PASSED plus a disk."""
    registry = Path(registry) if registry is not None else cache_root() / 'arch-niri-dux-states'
    entries = []
    for directory in registered_paths(registry):
        try:
            if (directory / 'ACCEPTANCE-PASSED').is_file() and (directory / 'disk.qcow2').is_file():
                entries.append((directory.stat().st_mtime, directory))
        except OSError:
            continue
    return [directory for _, directory in sorted(entries, reverse=True)]


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
    paths.update(registered_paths(registry))
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
    parser.add_argument('command', choices=['state', 'prepare-build', 'builder-memory', 'heavy-guard', 'gc'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.command == 'heavy-guard':
        heavy_guard()
    elif args.command == 'builder-memory':
        mib, warning = builder_memory()
        if warning:
            print(warning, file=sys.stderr)
        print(mib)
    elif args.command == 'gc':
        collect(cache_root(), ROOT, args.dry_run)
    else:
        state = build_state()
        if args.command == 'prepare-build':
            heavy_guard()
            if agent_markers():
                disk_guard(state)
                disk_guard(ROOT / 'out')
            else:
                human_build_guard(state)
        state.mkdir(parents=True, exist_ok=True)
        if not os.environ.get('BUILD_STATE'):
            own(state)
        print(state)


if __name__ == '__main__':
    main()
