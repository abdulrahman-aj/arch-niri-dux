import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublisherCheckoutTests(unittest.TestCase):
    def test_container_owned_checkout_passes_ancestry_guard_without_global_trust_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / 'checkout'
            subprocess.run(['git', 'init', '--quiet', str(checkout)], check=True)
            subprocess.run(['git', '-C', str(checkout), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet',
                            '--allow-empty', '-m', 'Release fixture'], check=True)
            commit = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
            subprocess.run(['git', '-C', str(checkout), 'update-ref', 'refs/remotes/origin/main', commit], check=True)
            commands = root / 'bin'
            commands.mkdir()
            gh = commands / 'gh'
            gh.write_text('#!/bin/sh\ntouch "$PUBLISHER_MARKER"\nexit 17\n')
            gh.chmod(0o755)
            environment = os.environ | {
                'PATH': str(commands) + ':' + os.environ['PATH'], 'GIT_CONFIG_GLOBAL': '/dev/null',
                'GIT_CONFIG_NOSYSTEM': '1', 'GITHUB_REF_NAME': 'integration-v0.1.0-2',
                'GITHUB_SHA': commit, 'GITHUB_REPOSITORY': 'abdulrahman-aj/arch-niri-dux',
                'PUBLISHER_MARKER': str(root / 'reached-release-api'),
            }
            environment.pop('SUDO_UID', None)
            # Match Actions: the publisher runs as root, but checkout files have
            # another UID. Fakeroot changes metadata only within this temp fixture.
            result = subprocess.run(['fakeroot', 'bash', '-c',
                                     'chown -R 1234 "$1"; '
                                     'if git -C "$1" status >"$1/control.log" 2>&1; then exit 90; fi; '
                                     'exec bash "$2" "$1" "$1/packages" "$1/history" "$1/output"',
                                     'bash', str(checkout), str(ROOT / 'scripts/publish-integration')],
                                    env=environment, text=True, capture_output=True)
            self.assertIn('dubious ownership', (checkout / 'control.log').read_text())
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertTrue((root / 'reached-release-api').is_file())


if __name__ == '__main__':
    unittest.main()
