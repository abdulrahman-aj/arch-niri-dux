"""Checkout identity shared by ISO staleness checks and verification evidence."""

import hashlib
import subprocess


def source_identity(root):
    """HEAD plus tracked diff hash; must match tools/build/host.sh's .iso.source."""
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    diff = subprocess.check_output(['git', '-C', str(root), 'diff', 'HEAD', '--binary'])
    return f'{head}\n{hashlib.sha256(diff).hexdigest()}'


def checkout_state(root):
    commit, diff_hash = source_identity(root).splitlines()
    dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all']))
    return {'commit': commit, 'dirty': dirty, **({'diff_sha256': diff_hash} if dirty else {})}
