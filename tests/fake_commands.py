"""Fakes that prove they ran.

A fake that never runs fails silently when the real command succeeds:
in one case a fake gh never made it onto PATH and the real gh answered
instead. Every fake whose output matters touches a marker first, and
the test asserts the marker, so a missing fake fails instead of
passing quietly.
"""

import shlex
from pathlib import Path


def mark(fake, marker):
    """Touch `marker` as the first command of the fake executable at `fake`."""
    fake, marker = Path(fake), Path(marker)
    head, rest = fake.read_text().split('\n', 1)
    fake.write_text(f'{head}\n: > {shlex.quote(str(marker))}\n{rest}')
    return marker
