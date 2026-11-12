#!/usr/bin/env python3
"""Thin alias: fast is a fixture stage selection in verify.py.

Same behavior as the former verify_fast.py (fixture auto-discovery, --keep,
--directory defaults): every argument is passed through, with --fixture
defaulting to auto-discovery so this entry point always runs fixture mode.
"""

import sys

from verify import main


if __name__ == '__main__':
    if not any(arg == '--fixture' or arg.startswith('--fixture=') for arg in sys.argv[1:]):
        sys.argv.append('--fixture')
    sys.exit(main())
