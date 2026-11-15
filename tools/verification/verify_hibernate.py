#!/usr/bin/env python3
"""Verify genuine memory resume on an already accepted, disposable VM disk."""

import argparse
from pathlib import Path
import sys
from tools.verification.scenarios import hibernate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='Completed make verify workspace')
    args = parser.parse_args()
    return hibernate.run_hibernate(args, parser)


if __name__ == '__main__':
    sys.exit(main())
