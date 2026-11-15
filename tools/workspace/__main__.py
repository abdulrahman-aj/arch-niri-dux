"""Checkout-owned build and verification storage, guards, and garbage collection."""

import argparse
import os
import sys

from tools.workspace.state import ROOT, build_state, cache_root, own
from tools.workspace.resources import agent_markers, heavy_guard, builder_memory, disk_guard, human_build_guard
from tools.workspace.gc import gc_worktrees, collect


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
