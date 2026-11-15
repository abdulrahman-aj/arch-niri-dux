"""GIT_DIR and friends never redirect test git helpers into another repo."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import git_isolation
from tests.git_isolation import check_git_output, run_git, sanitized_env
from tests.test_workspace import git_run, init_repo


class SanitizedEnvTests(unittest.TestCase):
    def test_redirects_are_dropped_and_the_rest_is_kept(self):
        env = dict.fromkeys(git_isolation.REDIRECTED, '/tmp/elsewhere') | {'PATH': 'keep'}
        cleaned = sanitized_env(env)
        for var in git_isolation.REDIRECTED:
            self.assertNotIn(var, cleaned)
        self.assertEqual(cleaned['PATH'], 'keep')
        self.assertIn('GIT_DIR', env)

    def test_explicit_env_is_scrubbed_too(self):
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp) / 'scratch'
            scratch.mkdir()
            run_git('init', '-q', cwd=scratch, check=True)
            run_git('config', 'user.email', 't@t', cwd=scratch, check=True)
            run_git('config', 'user.name', 't', cwd=scratch, check=True)
            run_git('commit', '-q', '--allow-empty', '-m', 'x', cwd=scratch, check=True)
            empty = Path(temp) / 'empty'
            empty.mkdir()
            # GIT_DIR points at a real repo, but it must not leak through.
            with self.assertRaises(subprocess.CalledProcessError):
                check_git_output('rev-parse', 'HEAD', cwd=empty, env={'GIT_DIR': str(scratch / '.git')}, text=True)


class HelperIsolationTests(unittest.TestCase):
    def untouched(self, scratch):
        listing = subprocess.run(
            ['git', '--git-dir', str(scratch), 'config', '--local', '--list'],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        refs = subprocess.run(
            ['git', '--git-dir', str(scratch), 'for-each-ref'],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return listing, refs, (scratch / 'index').exists()

    def test_helpers_ignore_a_bare_repo_in_git_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp) / 'scratch.git'
            subprocess.run(['git', 'init', '--quiet', '--bare', str(scratch)], check=True)
            before = self.untouched(scratch)
            poisoned = {
                'GIT_DIR': str(scratch),
                'GIT_WORK_TREE': str(scratch),
                'GIT_INDEX_FILE': str(scratch / 'index'),
                'GIT_OBJECT_DIRECTORY': str(scratch / 'objects'),
            }
            with patch.dict(os.environ, poisoned):
                repo = Path(temp) / 'fixture'
                repo.mkdir()
                init_repo(repo)
                head = git_run('rev-parse', 'HEAD', cwd=repo).stdout.strip()
                self.assertTrue((repo / '.git').is_dir())
                self.assertEqual(check_git_output('rev-parse', 'HEAD', cwd=repo, text=True).strip(), head)
            self.assertEqual(self.untouched(scratch), before)
            self.assertNotIn('user.email', before[0])


if __name__ == '__main__':
    unittest.main()
