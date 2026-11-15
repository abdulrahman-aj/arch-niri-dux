import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.git_isolation import check_git_output, run_git, sanitized_env
from tests.fake_commands import mark


ROOT = Path(__file__).resolve().parents[1]


class PublisherCheckoutTests(unittest.TestCase):
    def test_container_owned_checkout_passes_ancestry_guard_without_global_trust_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / 'checkout'
            run_git('init', '--quiet', str(checkout), cwd=root, check=True)
            run_git(
                '-c',
                'user.name=Fixture',
                '-c',
                'user.email=fixture@example.invalid',
                'commit',
                '--quiet',
                '--allow-empty',
                '-m',
                'Release fixture',
                cwd=checkout,
                check=True,
            )
            commit = check_git_output('rev-parse', 'HEAD', cwd=checkout, text=True).strip()
            run_git('update-ref', 'refs/remotes/origin/main', commit, cwd=checkout, check=True)
            commands = root / 'bin'
            commands.mkdir()
            gh = commands / 'gh'
            gh.write_text(f'#!{shutil.which("sh")}\nexit 17\n')
            gh.chmod(0o755)
            mark(gh, root / 'reached-release-api')
            # Only these host utilities stay reachable: real git must prove
            # the dubious-ownership failure, and the rest is what publish.sh
            # needs. A missing gh fake fails closed instead of reaching a
            # real gh that could touch GitHub releases.
            for name in ('bash', 'chown', 'fakeroot', 'git', 'jq', 'mkdir', 'mktemp', 'rm'):
                target = commands / name
                if not target.exists():
                    target.symlink_to(shutil.which(name))
            environment = sanitized_env(
                os.environ
                | {
                    'PATH': str(commands),
                    'GIT_CONFIG_GLOBAL': '/dev/null',
                    'GIT_CONFIG_NOSYSTEM': '1',
                    'GITHUB_REF_NAME': 'v0.1.0-2',
                    'LC_ALL': 'C',
                    'GITHUB_SHA': commit,
                    'GITHUB_REPOSITORY': 'abdulrahman-aj/arch-niri-dux',
                }
            )
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
                    str(ROOT / 'tools/release/publish.sh'),
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
printf '%s\\n' '[[{"tag_name":"v0.1.0-2","draft":false}]]'
""",
                    'python': """shift\ncase "$2" in
    history-tags) ;;
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
                    mark(path, root / 'stub-ran')
                # Only these host utilities stay reachable; the stubs cover
                # git, gh and python, so a missing stub fails closed instead
                # of reaching a real gh or a real fetch.
                for name in ('bash', 'jq', 'mkdir', 'mktemp', 'rm', 'touch'):
                    target = commands / name
                    if not target.exists():
                        target.symlink_to(shutil.which(name))
                environment = sanitized_env(
                    os.environ
                    | {
                        'PATH': str(commands),
                        'TMPDIR': str(scratch),
                        'GITHUB_REF_NAME': 'v0.1.0-2',
                        'GITHUB_SHA': 'a' * 40,
                        'GITHUB_REPOSITORY': 'abdulrahman-aj/arch-niri-dux',
                        'FAIL_STAGE': stage,
                    }
                )
                result = subprocess.run(
                    [
                        'bash',
                        str(ROOT / 'tools/release/publish.sh'),
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
                self.assertTrue((root / 'stub-ran').is_file())
                self.assertEqual(list(scratch.iterdir()), [])
                self.assertEqual((packages / 'keep').read_text(), 'original input')


if __name__ == '__main__':
    unittest.main()
