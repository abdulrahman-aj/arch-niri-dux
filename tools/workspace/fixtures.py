"""Accepted verification fixture discovery and relocation."""

import hashlib
import os
from pathlib import Path
import shutil

from tools.workspace.state import MARKER, cache_root, registered_paths


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
