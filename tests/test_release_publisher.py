import os
from pathlib import Path
import shutil
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
            subprocess.run(
                [
                    'git',
                    '-C',
                    str(checkout),
                    '-c',
                    'user.name=Fixture',
                    '-c',
                    'user.email=fixture@example.invalid',
                    'commit',
                    '--quiet',
                    '--allow-empty',
                    '-m',
                    'Release fixture',
                ],
                check=True,
            )
            commit = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
            subprocess.run(['git', '-C', str(checkout), 'update-ref', 'refs/remotes/origin/main', commit], check=True)
            commands = root / 'bin'
            commands.mkdir()
            gh = commands / 'gh'
            gh.write_text(f'#!{shutil.which("sh")}\ntouch "$PUBLISHER_MARKER"\nexit 17\n')
            gh.chmod(0o755)
            environment = os.environ | {
                'PATH': str(commands) + ':' + os.environ['PATH'],
                'GIT_CONFIG_GLOBAL': '/dev/null',
                'GIT_CONFIG_NOSYSTEM': '1',
                'GITHUB_REF_NAME': 'integration-v0.1.0-2',
                'LC_ALL': 'C',
                'GITHUB_SHA': commit,
                'GITHUB_REPOSITORY': 'abdulrahman-aj/arch-niri-dux',
                'PUBLISHER_MARKER': str(root / 'reached-release-api'),
            }
            environment.pop('SUDO_UID', None)
            # Match Actions: the publisher runs as root, but checkout files have
            # another UID. Fakeroot changes metadata only within this temp fixture.
            result = subprocess.run(
                [
                    'fakeroot',
                    'bash',
                    '-c',
                    'chown -R 1234 "$1"; '
                    'if git -C "$1" merge-base --is-ancestor "$GITHUB_SHA" origin/main '
                    '>"$1/control.log" 2>&1; then exit 90; fi; '
                    'exec bash "$2" "$1" "$1/packages" "$1/history" "$1/output"',
                    'bash',
                    str(checkout),
                    str(ROOT / 'scripts/publish-integration'),
                ],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertIn('dubious ownership', (checkout / 'control.log').read_text())
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertTrue((root / 'reached-release-api').is_file())


class PublisherScratchTests(unittest.TestCase):
    def test_retry_scratch_is_cleaned_on_success_and_failure_without_removing_input(self):
        for stage, expected in (('', 0), ('fetch', 23), ('publish', 24)):
            with self.subTest(failure_stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands, scratch, packages = root / 'bin', root / 'scratch', root / 'packages'
                for path in (commands, scratch, packages):
                    path.mkdir()
                (packages / 'keep').write_text('original input')
                # Exercise the real scratch lifecycle while GitHub and signing stay stubbed.
                stubs = {
                    'git': 'exit 0\n',
                    'gh': """[[ $1 == api ]] || exit 81
printf '%s\\n' '[[{"tag_name":"integration-v0.1.0-2","draft":false}]]'
""",
                    'python': """case "$2" in
    fetch)
        mkdir -p "$4"
        touch "$4/reused"
        [[ $FAIL_STAGE != fetch ]] || exit 23
        ;;
    publish)
        test -f "$4/reused"
        [[ $FAIL_STAGE != publish ]] || exit 24
        ;;
    *) exit 82 ;;
esac
""",
                }
                for name, body in stubs.items():
                    path = commands / name
                    path.write_text(f'#!{shutil.which("bash")}\nset -eu\n' + body)
                    path.chmod(0o755)
                environment = os.environ | {
                    'PATH': str(commands) + ':' + os.environ['PATH'],
                    'TMPDIR': str(scratch),
                    'GITHUB_REF_NAME': 'integration-v0.1.0-2',
                    'GITHUB_SHA': 'a' * 40,
                    'GITHUB_REPOSITORY': 'abdulrahman-aj/arch-niri-dux',
                    'FAIL_STAGE': stage,
                }
                result = subprocess.run(
                    [
                        'bash',
                        str(ROOT / 'scripts/publish-integration'),
                        str(root),
                        str(packages),
                        str(root / 'history'),
                        str(root / 'output'),
                    ],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(list(scratch.iterdir()), [])
                self.assertEqual((packages / 'keep').read_text(), 'original input')


if __name__ == '__main__':
    unittest.main()
