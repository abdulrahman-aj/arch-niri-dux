"""Check PATH preservation at the login and user-service entry points."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'desktop/etc/profile.d/dux-localbin.sh'
ENVIRONMENT = ROOT / 'desktop/usr/lib/environment.d/90-dux.conf'
GENERATOR = Path('/usr/lib/systemd/user-environment-generators/30-systemd-environment-d-generator')
SHELL = shutil.which('sh')
START_PATH = '/usr/local/bin:/usr/bin:/home/tester/.nix-profile/bin'


class SessionPathTests(unittest.TestCase):
    def test_login_profile_preserves_path_without_duplicates(self):
        with tempfile.TemporaryDirectory() as home:
            output = subprocess.check_output(
                [SHELL, '-c', '. "$1"; . "$1"; printf "%s" "$PATH"', 'sh', str(PROFILE)],
                env={'HOME': home, 'PATH': START_PATH},
                text=True,
            )
            self.assertEqual(output, START_PATH + ':' + home + '/.local/bin')

    @unittest.skipUnless(GENERATOR.is_file(), 'systemd environment generator unavailable')
    def test_user_service_environment_preserves_path(self):
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / '.config/environment.d'
            config.mkdir(parents=True)
            shutil.copyfile(ENVIRONMENT, config / '90-dux.conf')
            # The real generator also reads the host distro (Ubuntu's
            # 99-environment.conf resets PATH). Isolate our fixture by masking it.
            host_configs = {
                path.name
                for directory in (
                    '/etc/environment.d',
                    '/run/environment.d',
                    '/usr/local/lib/environment.d',
                    '/usr/lib/environment.d',
                )
                for path in Path(directory).glob('*.conf')
            }
            for name in host_configs - {'90-dux.conf'}:
                (config / name).symlink_to('/dev/null')
            output = subprocess.check_output(
                [str(GENERATOR)],
                text=True,
                env={'HOME': home, 'PATH': START_PATH, 'XDG_CONFIG_HOME': str(config.parent)},
            )
            path = next(line.removeprefix('PATH=') for line in output.splitlines() if line.startswith('PATH=')).split(
                ':'
            )
            existing = START_PATH.split(':')
            self.assertEqual([entry for entry in path if entry in existing], existing)
            self.assertEqual(path.count(home + '/.local/bin'), 1)


if __name__ == '__main__':
    unittest.main()
