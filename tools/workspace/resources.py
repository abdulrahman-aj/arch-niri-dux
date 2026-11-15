"""Host resource guards, storage measurements and VM liveness."""

import fcntl
import os
from pathlib import Path
import shutil
import socket

from tools.workspace.state import ROOT, MARKER, build_state, cache_root, downloads_root, registered_paths

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
        from tools.verification.vm import wait_for_memory

        wait_for_memory()
        return BUILDER_MIB, None
    available = available_memory() // 1024**2
    mib = max(BUILDER_MIN_MIB, min(BUILDER_MIB, (available - MEMORY_HEADROOM_MIB) // 512 * 512))
    if available < BUILDER_MIN_MIB + MEMORY_HEADROOM_MIB:
        effect = 'That is below what a build needs, so the system will swap heavily. Close big apps first.'
    elif available - mib < MEMORY_HEADROOM_MIB:
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


def worktree_busy(worktree):
    """(busy, reason) when the ISO build lock is held or a kept VM answers."""
    lock = build_state(worktree) / 'build.lock'
    if lock.is_file():
        try:
            with lock.open() as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True, 'ISO build holds build.lock'
    evidence = []
    for directory in registered_paths(cache_root() / 'arch-niri-dux-states'):
        marker = directory / MARKER
        try:
            if marker.is_file() and marker.read_text().strip() == str(Path(worktree).resolve()):
                evidence.append(directory)
        except FileNotFoundError:
            continue
    out = Path(worktree) / 'out'
    if out.is_dir():
        evidence += [child for child in out.iterdir() if child.is_dir() and not child.is_symlink()]
        verification = out / 'verify'
        if verification.is_dir():
            evidence.extend(child for child in verification.iterdir() if child.is_dir() and not child.is_symlink())
    if any(kept_running(directory) for directory in evidence):
        return True, 'kept VM still answers'
    return False, ''
