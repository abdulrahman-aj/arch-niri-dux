"""Streaming SHA-256 for build inputs and verification evidence."""

# Build scripts import this from verification/ too: stage_probes copies this
# package whole into guests, so a neutral home would need its own staging.

import hashlib


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
