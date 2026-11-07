"""Streaming SHA-256 for build inputs and verification evidence."""

import hashlib


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
