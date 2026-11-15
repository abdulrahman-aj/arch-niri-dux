"""Accepted verification fixture discovery and relocation."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tools.workspace.state import ROOT, MARKER, cache_root, registered_paths


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


def newest_accepted_fixture(registry=None, checkout=ROOT):
    """Newest accepted ancestor of HEAD, falling back to an unrecorded fixture."""
    fallback = None
    for directory in accepted_fixtures(registry):
        try:
            inputs = json.loads((directory / 'inputs.json').read_text())
        except FileNotFoundError:
            inputs = {}
        commit = inputs.get('checkout', {}).get('commit')
        if not commit:
            if fallback is None:
                fallback = directory
            continue
        result = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', commit, 'HEAD'],
            cwd=checkout,
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return directory / 'disk.qcow2'
        reason = 'not an ancestor of HEAD' if result.returncode == 1 else 'commit unavailable for ancestry check'
        print(f'Skipping fixture {directory}: {commit} is {reason}.', file=sys.stderr)
    if fallback is not None:
        print(f'Warning: using fixture {fallback} without a recorded checkout commit.', file=sys.stderr)
        return fallback / 'disk.qcow2'
    return None


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
