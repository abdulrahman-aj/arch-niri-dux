"""Run git from tests without inheriting the caller's git environment.

A `git rebase --exec` run exports GIT_DIR, and any test that builds a
temporary repo inherits it: git then reads and writes the real repo
instead of the fixture (reconfiguring it, adding branches, moving
refs). Every git invocation from tests goes through this module, which
drops the variables that redirect git elsewhere.
"""

import os
import subprocess

REDIRECTED = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY')


def sanitized_env(env=None):
    """Caller environment with git's directory redirects removed."""
    merged = dict(os.environ) if env is None else dict(env)
    for var in REDIRECTED:
        merged.pop(var, None)
    return merged


def run_git(*args, cwd, check=False, **kwargs):
    """subprocess.run for `git`, immune to inherited GIT_DIR and friends."""
    kwargs['env'] = sanitized_env(kwargs.get('env'))
    return subprocess.run(['git', *args], cwd=str(cwd), check=check, **kwargs)


def check_git_output(*args, cwd, **kwargs):
    """subprocess.check_output for `git`, immune to inherited GIT_DIR and friends."""
    kwargs['env'] = sanitized_env(kwargs.get('env'))
    return subprocess.check_output(['git', *args], cwd=str(cwd), **kwargs)
