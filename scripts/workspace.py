"""Checkout-owned build and verification storage, guards, and garbage collection."""

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
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


PROTECTED_BRANCHES = ('main', 'master')
BUSY_CMD_HINTS = ('qemu-system', 'verify', 'build-iso', 'package_build', 'vm_console', 'serve_build_state')


def linked_worktrees(checkout=ROOT):
    """Linked worktrees from `git worktree list --porcelain`; [] when git fails.

    Returns dicts with path, head, branch (short name or None when detached),
    and is_main (first entry owns the git dir).
    """
    try:
        result = subprocess.run(
            ['git', '-C', str(checkout), 'worktree', 'list', '--porcelain'],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    worktrees = []
    current = None
    for line in result.stdout.splitlines():
        if line.startswith('worktree '):
            if current is not None:
                worktrees.append(current)
            current = {'path': Path(line[len('worktree ') :]), 'head': '', 'branch': None, 'is_main': not worktrees}
        elif line.startswith('HEAD ') and current is not None:
            current['head'] = line[len('HEAD ') :].strip()
        elif line == 'detached' and current is not None:
            current['branch'] = None
        elif line.startswith('branch ') and current is not None:
            ref = line[len('branch ') :].strip()
            current['branch'] = ref[len('refs/heads/') :] if ref.startswith('refs/heads/') else ref
    if current is not None:
        worktrees.append(current)
    return worktrees


def _status_entry_outside_out(line):
    """True when a `git status --porcelain` line touches anything outside out/."""
    body = line[3:] if len(line) > 3 else ''
    parts = [part.strip().strip('"') for part in body.split(' -> ')] if ' -> ' in body else [body.strip().strip('"')]
    return any(part != 'out' and not part.startswith('out/') for part in parts)


def worktree_status_outside_out(worktree):
    """Porcelain lines outside out/, or None when git status fails."""
    try:
        result = subprocess.run(
            ['git', '-C', str(worktree), 'status', '--porcelain'],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line and _status_entry_outside_out(line)]


def build_lock_busy(worktree):
    """True when another process holds the worktree's ISO build lock (see build-iso)."""
    lock = build_state(worktree) / 'build.lock'
    if not lock.is_file():
        return False
    try:
        with lock.open('r') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream, fcntl.LOCK_UN)
            return False
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return True
        return True


def kept_vm_busy(worktree):
    """True when a kept VM answers under the worktree's out/ (see kept_running)."""
    out = Path(worktree) / 'out'
    if not out.is_dir():
        return False
    evidence_dirs = [child for child in out.iterdir() if child.is_dir() and not child.is_symlink()]
    verification = out / 'verify'
    if verification.is_dir():
        evidence_dirs.extend(child for child in verification.iterdir() if child.is_dir() and not child.is_symlink())
    return any(kept_running(directory) for directory in evidence_dirs)


def proc_busy(worktree, proc_root='/proc'):
    """True when a build/verify-flavoured process runs with the worktree in cwd or cmdline."""
    try:
        anchor = Path(worktree).resolve()
    except OSError:
        return True
    self_pid = str(os.getpid())
    try:
        pids = [entry for entry in os.listdir(proc_root) if entry.isdigit()]
    except OSError:
        return True
    for pid in pids:
        if pid == self_pid:
            continue
        try:
            cwd = Path(proc_root, pid, 'cwd').resolve()
        except OSError:
            continue
        try:
            raw = Path(proc_root, pid, 'cmdline').read_bytes().decode(errors='replace')
        except OSError:
            continue
        cmdline = raw.replace('\0', ' ').strip()
        if not cmdline or not any(hint in cmdline for hint in BUSY_CMD_HINTS):
            continue
        if cwd == anchor or anchor in cwd.parents or str(anchor) in cmdline:
            return True
    return False


def worktree_busy(worktree):
    """True with a reason when a build or verify is running in the worktree."""
    if build_lock_busy(worktree):
        return True, 'ISO build holds build.lock'
    if kept_vm_busy(worktree):
        return True, 'kept VM still answers under out/'
    if proc_busy(worktree):
        return True, 'build/verify process still runs in it'
    return False, ''


def fetch_origin(checkout=ROOT):
    """Best-effort `git fetch origin main` for fresh ancestry; False on failure."""
    try:
        result = subprocess.run(
            ['git', '-C', str(checkout), 'fetch', 'origin', 'main', '--quiet'],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f'Warning: fetch origin failed ({error}); using stale origin/main.')
        return False
    if result.returncode != 0:
        print('Warning: fetch origin failed; using stale origin/main.')
        return False
    return True


def merged_prs(branch, checkout=ROOT):
    """Merged PRs for a branch via gh, or None when gh is unavailable/fails."""
    try:
        result = subprocess.run(
            ['gh', 'pr', 'list', '--state', 'merged', '--head', branch, '--json', 'number,title', '--limit', '10'],
            cwd=str(checkout),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        records = json.loads(result.stdout or '[]')
    except json.JSONDecodeError:
        return None
    if not isinstance(records, list):
        return None
    return records


def open_prs(branch, checkout=ROOT):
    """Open PRs for a branch via gh; [] when gh is unavailable/fails."""
    try:
        result = subprocess.run(
            ['gh', 'pr', 'list', '--state', 'open', '--head', branch, '--json', 'number,title', '--limit', '10'],
            cwd=str(checkout),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        records = json.loads(result.stdout or '[]')
    except json.JSONDecodeError:
        return []
    return records if isinstance(records, list) else []


def is_ancestor(branch, checkout=ROOT, base='origin/main'):
    """True/False via merge-base; None when the comparison itself fails."""
    try:
        result = subprocess.run(
            ['git', '-C', str(checkout), 'merge-base', '--is-ancestor', branch, base],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def merge_info(branch, checkout=ROOT):
    """(merged, number, title, method) for a branch: gh first, ancestry fallback.

    Squash merges are not ancestors of origin/main, so gh is the primary
    check and ancestry only catches direct pushes.
    """
    prs = merged_prs(branch, checkout)
    if prs:
        first = prs[0]
        return True, first.get('number'), first.get('title', ''), 'gh'
    if is_ancestor(branch, checkout):
        return True, None, '', 'ancestry'
    return False, None, '', 'none'


def fixture_version_str(directory):
    """Release version beside a fixture, or 'unknown' without a manifest."""
    manifest = Path(directory) / 'release-manifest.json'
    try:
        version = json.loads(manifest.read_text()).get('version')
    except (OSError, json.JSONDecodeError, AttributeError):
        return 'unknown'
    return str(version) if version else 'unknown'


def is_proven_old_fixture(directory):
    """True only when a manifest proves the fixture predates 0.3."""
    manifest = Path(directory) / 'release-manifest.json'
    try:
        version = json.loads(manifest.read_text()).get('version')
    except (OSError, json.JSONDecodeError, AttributeError):
        return False
    if not version:
        return False
    numeric = str(version).split('-', 1)[0]
    try:
        parts = tuple(int(piece) for piece in numeric.split('.'))
    except ValueError:
        return False
    return parts < (0, 3)


def plan_worktree_gc(checkout=ROOT):
    """Split linked worktrees into removals and skips, plus a fixture move if needed.

    A worktree is removable only when it is not the runner, not the main
    worktree, not detached, not on a protected branch, clean outside out/,
    idle, and merged into origin/main. The newest accepted fixture moves to
    the runner's out/verify when it lives inside a worktree about to go.
    """
    runner = Path(checkout).resolve()
    removals = []
    skipped = []
    for entry in linked_worktrees(checkout):
        path = entry['path']
        try:
            resolved = path.resolve()
        except OSError:
            skipped.append({'path': path, 'branch': entry['branch'], 'reason': 'path does not resolve'})
            continue
        branch = entry['branch']
        if resolved == runner:
            skipped.append({'path': path, 'branch': branch, 'reason': 'current checkout (running gc)'})
            continue
        if entry['is_main']:
            skipped.append({'path': path, 'branch': branch, 'reason': 'main worktree'})
            continue
        if branch is None:
            skipped.append({'path': path, 'branch': branch, 'reason': 'detached HEAD'})
            continue
        if branch in PROTECTED_BRANCHES:
            skipped.append({'path': path, 'branch': branch, 'reason': f'protected branch {branch}'})
            continue
        outside = worktree_status_outside_out(path)
        if outside is None:
            skipped.append({'path': path, 'branch': branch, 'reason': 'git status failed'})
            continue
        if outside:
            preview = ', '.join(outside[:3])
            skipped.append({'path': path, 'branch': branch, 'reason': f'dirty worktree ({preview})'})
            continue
        busy, reason = worktree_busy(path)
        if busy:
            skipped.append({'path': path, 'branch': branch, 'reason': reason})
            continue
        merged, number, title, method = merge_info(branch, checkout)
        if not merged:
            opened = open_prs(branch, checkout)
            if opened:
                first = opened[0]
                reason = f'unmerged branch (open PR #{first.get("number")} {first.get("title", "")})'.rstrip()
            else:
                reason = 'unmerged branch (no merged PR)'
            skipped.append({'path': path, 'branch': branch, 'reason': reason})
            continue
        state = build_state(path)
        removals.append(
            {
                'path': path,
                'resolved': resolved,
                'branch': branch,
                'pr_number': number,
                'pr_title': title,
                'method': method,
                'worktree_size': tree_size(path),
                'build_state': state,
                'build_size': tree_size(state),
            }
        )
    fixture_move = None
    fixtures = accepted_fixtures()
    newest = fixtures[0] if fixtures else None
    if newest is not None and not is_proven_old_fixture(newest):
        try:
            newest_resolved = newest.resolve()
        except OSError:
            newest_resolved = None
        if newest_resolved is not None:
            inside = [
                item
                for item in removals
                if newest_resolved == item['resolved'] or item['resolved'] in newest_resolved.parents
            ]
            if inside:
                target_dir = runner / 'out' / 'verify'
                target = target_dir / newest_resolved.name
                counter = 1
                while target.exists():
                    counter += 1
                    target = target_dir / f'{newest_resolved.name}-gc-{counter}'
                fixture_move = {
                    'src': newest_resolved,
                    'dst': target,
                    'size': tree_size(newest_resolved),
                    'version': fixture_version_str(newest_resolved),
                }
    return removals, skipped, fixture_move


def move_fixture(src, dst, new_owner):
    """Move an accepted workspace and re-point its marker and registry at new_owner."""
    src = Path(src).resolve()
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    old_key = hashlib.sha256(os.fsencode(src)).hexdigest()
    shutil.move(str(src), str(dst))
    resolved = dst.resolve()
    (resolved / MARKER).write_text(str(Path(new_owner).resolve()) + '\n')
    registry = cache_root() / 'arch-niri-dux-states'
    registry.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.fsencode(resolved)).hexdigest()
    (registry / key).write_text(str(resolved) + '\n')
    (registry / old_key).unlink(missing_ok=True)
    return resolved


def gc_worktrees(checkout, dry_run):
    """Remove worktrees of merged PRs, preserving the newest fixture first."""
    removals, skipped, fixture_move = plan_worktree_gc(checkout)
    verb = 'Would remove' if dry_run else 'Removing'
    move_verb = 'Would move' if dry_run else 'Moving'
    freed_worktrees = 0
    if fixture_move is not None:
        print(
            f'{move_verb} fixture: {fixture_move["src"]} -> {fixture_move["dst"]} '
            f'({fixture_move["size"] / 1024**3:.2f} GiB, version {fixture_move["version"]})'
        )
    for item in removals:
        if item['pr_number'] is not None:
            pr = f'PR #{item["pr_number"]} {item["pr_title"]}'.rstrip()
        else:
            pr = 'merged by ancestry'
        print(
            f'{verb} worktree: {item["path"]} (branch {item["branch"]}, {pr} via {item["method"]}; '
            f'worktree {item["worktree_size"] / 1024**3:.2f} GiB, build state {item["build_size"] / 1024**3:.2f} GiB)'
        )
        freed_worktrees += item['worktree_size']
    for item in skipped:
        print(f'Skipped worktree: {item["path"]} (branch {item["branch"] or "detached"}): {item["reason"]}')
    if fixture_move is not None:
        freed_worktrees -= fixture_move['size']
    if removals:
        print(f'{"Would free" if dry_run else "Freeing"} {freed_worktrees / 1024**3:.2f} GiB from worktrees')
        if dry_run:
            orphaned_build = sum(item['build_size'] for item in removals)
            if orphaned_build:
                print(
                    f'Would orphan build states of removed checkouts ({orphaned_build / 1024**3:.2f} GiB), '
                    'freed by the existing cleanup below'
                )
    if dry_run:
        return removals, skipped, fixture_move
    if fixture_move is not None:
        try:
            move_fixture(fixture_move['src'], fixture_move['dst'], checkout)
        except OSError as error:
            print(f'Fixture move failed ({error}); keeping its worktree.')
            doomed = {
                item['resolved']
                for item in removals
                if fixture_move['src'] == item['resolved'] or item['resolved'] in fixture_move['src'].parents
            }
            removals = [item for item in removals if item['resolved'] not in doomed]
    for item in removals:
        result = subprocess.run(
            ['git', '-C', str(checkout), 'worktree', 'remove', str(item['path'])],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            print(f'Worktree remove failed: {item["path"]} ({"; ".join(detail[:2])})')
    return removals, skipped, fixture_move


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
        if not args.dry_run:
            fetch_origin(ROOT)
        gc_worktrees(ROOT, args.dry_run)
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
