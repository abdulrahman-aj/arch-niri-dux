#!/usr/bin/env python3
"""Run offline UEFI acceptance, optionally checking published delivery afterward."""

import argparse
from pathlib import Path
import sys
from tools.verification.scenarios import full, fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--directory', type=Path, help='Fresh verification workspace; default under out/verify')
    parser.add_argument('--disk', type=Path, help='New disposable qcow2 path; default inside the workspace')
    parser.add_argument('--install-only', action='store_true', help='Debug only; does not produce acceptance success')
    parser.add_argument(
        '--slow',
        action='store_true',
        help='Include the slow Dux updater stage',
    )
    parser.add_argument('--swap-gib', type=int, help='Override Auto swap sizing; 0 disables swap and hibernation')
    parser.add_argument('--keymap', default='us', help='Console keymap for the installed system (default: us)')
    parser.add_argument(
        '--nvidia',
        choices=('open', '580xx'),
        help='Install this NVIDIA driver list without a card; the run never becomes a verify-fast fixture',
    )
    parser.add_argument(
        '--keep',
        action='store_true',
        help='On failure keep the guest running for debugging; the disk is still unlinked',
    )
    parser.add_argument(
        '--fixture',
        nargs='?',
        const='auto',
        help='Fixture mode: reuse an accepted disk.qcow2 instead of installing '
        '(PATH, or auto-discovers the newest accepted fixture)',
    )
    parser.add_argument(
        '--kernel-package',
        type=Path,
        help='Fixture mode only: also reinstall this kernel archive in the same transaction',
    )
    args = parser.parse_args()
    if args.fixture is not None:
        return fixture.run_fixture(args, parser)
    return full.run_full(args, parser)


if __name__ == '__main__':
    sys.exit(main())
