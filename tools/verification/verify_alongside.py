#!/usr/bin/env python3
"""Real Windows/Linux alongside acceptance; fixture contracts in ALONGSIDE.md."""

import argparse
from pathlib import Path
import sys
from tools.verification.scenarios import alongside


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os', choices=['linux', 'windows'], default='linux')
    parser.add_argument('--windows-token', type=Path)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    return alongside.run_alongside(args, parser)


if __name__ == '__main__':
    sys.exit(main())
