"""Garbage collection decisions and deletion for owned storage and worktrees."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from tools.workspace.state import ROOT, MARKER, build_state, registered_paths
from tools.workspace.resources import kept_running, tree_size, worktree_busy
from tools.workspace.fixtures import accepted_fixtures, move_fixture

PROTECTED_BRANCHES = ('main', 'master')


def linked_worktrees(checkout=ROOT):
    """Linked worktrees from `git worktree list --porcelain`.

    Returns dicts with path, branch (short name or None when detached),
    and is_main (first entry owns the git dir).
    """
    result = subprocess.run(
        ['git', '-C', str(checkout), 'worktree', 'list', '--porcelain'],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    worktrees = []
    current = None
    for line in result.stdout.splitlines():
        if line.startswith('worktree '):
            current = {'path': Path(line[len('worktree ') :]), 'branch': None, 'is_main': not worktrees}
            worktrees.append(current)
        elif line.startswith('branch ') and current is not None:
            ref = line[len('branch ') :].strip()
            current['branch'] = ref[len('refs/heads/') :] if ref.startswith('refs/heads/') else ref
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


def merge_info(branch, checkout=ROOT):
    """(merged, number, title, method) for a branch; merged None means can't tell.

    A merged PR proves only commits through its head. If gh fails, use origin/main ancestry.
    """
    try:
        listed = subprocess.run(
            [
                'gh',
                'pr',
                'list',
                '--state',
                'merged',
                '--head',
                branch,
                '--json',
                'number,title,headRefOid',
                '--limit',
                '1',
            ],
            cwd=str(checkout),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        listed = None
    if listed is not None and listed.returncode == 0:
        records = json.loads(listed.stdout or '[]')
        if records:
            record = records[0]
            head = record.get('headRefOid')
            if not head:
                return None, None, '', 'none'
            ancestor = subprocess.run(
                ['git', '-C', str(checkout), 'merge-base', '--is-ancestor', branch, head],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            merged = ancestor.returncode == 0 if ancestor.returncode in (0, 1) else None
            return merged, record.get('number'), record.get('title', ''), 'gh'
        return False, None, '', 'gh'
    ancestor = subprocess.run(
        ['git', '-C', str(checkout), 'merge-base', '--is-ancestor', branch, 'origin/main'],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if ancestor.returncode in (0, 1):
        return ancestor.returncode == 0, None, '', 'ancestry'
    return None, None, '', 'none'


def plan_worktree_gc(checkout=ROOT):
    """Removals, skips, and a fixture move for linked worktrees.

    Removable: not the runner, main, detached, or protected; clean outside
    out/; idle; merged. The newest accepted fixture moves into the runner's
    out/verify when a removal would take it away.
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
            reason = 'current checkout (running gc)'
        elif entry['is_main']:
            reason = 'main worktree'
        elif branch is None:
            reason = 'detached HEAD'
        elif branch in PROTECTED_BRANCHES:
            reason = f'protected branch {branch}'
        else:
            reason = ''
        if reason:
            skipped.append({'path': path, 'branch': branch, 'reason': reason})
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
        if merged is None:
            skipped.append({'path': path, 'branch': branch, 'reason': "can't tell whether merged"})
            continue
        if not merged:
            skipped.append({'path': path, 'branch': branch, 'reason': 'not merged'})
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
    if fixtures:
        newest = fixtures[0].resolve()
        if any(newest == item['resolved'] or item['resolved'] in newest.parents for item in removals):
            target_dir = runner / 'out' / 'verify'
            target = target_dir / newest.name
            counter = 1
            while target.exists():
                counter += 1
                target = target_dir / f'{newest.name}-gc-{counter}'
            fixture_move = {'src': newest, 'dst': target, 'size': tree_size(newest)}
    return removals, skipped, fixture_move


def gc_worktrees(checkout, dry_run):
    """Remove worktrees of merged PRs, preserving the newest fixture first."""
    removals, skipped, fixture_move = plan_worktree_gc(checkout)
    verb = 'Would remove' if dry_run else 'Removing'
    move_verb = 'Would move' if dry_run else 'Moving'
    freed_worktrees = 0
    if fixture_move is not None:
        print(
            f'{move_verb} fixture: {fixture_move["src"]} -> {fixture_move["dst"]} ({fixture_move["size"] / 1024**3:.2f} GiB)'
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
        if dry_run and (orphaned := sum(item['build_size'] for item in removals)):
            print(
                f'Would orphan build states of removed checkouts ({orphaned / 1024**3:.2f} GiB), '
                'freed by the existing cleanup below'
            )
    if dry_run:
        return removals, skipped, fixture_move
    if fixture_move is not None:
        try:
            move_fixture(fixture_move['src'], fixture_move['dst'], checkout)
        except OSError as error:
            print(f'Fixture move failed ({error}); keeping its worktree.')
            removals = [
                item
                for item in removals
                if item['resolved'] != fixture_move['src'] and item['resolved'] not in fixture_move['src'].parents
            ]
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
    running = [path.resolve() for path in registered_paths(cache / 'arch-niri-dux-states') if kept_running(path)]
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
        if kept_running(path) or any(resolved == vm or resolved in vm.parents for vm in running):
            print(f'Kept, VM still answers: {path}')
            continue
        size = sum(item.lstat().st_blocks * 512 for item in path.rglob('*') if not item.is_symlink())
        print(f'{"Would delete" if dry_run else "Deleting"}: {path} ({size / 1024**3:.2f} GiB)')
        if not dry_run:
            shutil.rmtree(path)
        removed.append(path)
        freed += size
    print(f'{"Would free" if dry_run else "Freed"} {freed / 1024**3:.2f} GiB')
    return removed
