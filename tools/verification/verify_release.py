#!/usr/bin/env python3
"""Release acceptance: install the release ISO, boot once, check online delivery."""

import argparse
from pathlib import Path
import sys
from tools.verification.scenarios import release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True, help='Package release tag, e.g. v0.3.2-1')
    parser.add_argument('--repository-url', help='Disposable rehearsal repository; skips ISO authorization staging')
    parser.add_argument('--iso', type=Path, help='Release ISO (default: build it)')
    parser.add_argument('--directory', type=Path, help='Fresh workspace')
    args = parser.parse_args()
    return release.run_release(args, parser)


if __name__ == '__main__':
    sys.exit(main())
