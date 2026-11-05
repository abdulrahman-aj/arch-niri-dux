"""Behavior-owned acceptance scenarios and disposable VM controls."""

import sys


def dispatch(name, handlers):
    try:
        handler = handlers[sys.argv[1]]
    except KeyError:
        raise SystemExit(f'Unknown {name} probe')
    handler()
