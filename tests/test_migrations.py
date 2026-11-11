"""User migration order, markers, stop-on-failure and skel markers."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'desktop/usr/bin/arch-niri-dux-migrate'


def run(scripts, home, **changes):
    environment = os.environ | {
        'HOME': str(home),
        'XDG_STATE_HOME': str(home / 'state'),
        'ARCH_NIRI_DUX_MIGRATIONS_DIR': str(scripts),
    }
    return subprocess.run(['bash', str(RUNNER)], env=environment | changes, capture_output=True, text=True, check=False)


class UserMigrationsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir()
        self.home = self.root / 'home'

    def script(self, name, body):
        path = self.scripts / name
        path.write_text(body + '\n')
        return path

    def test_order_markers_and_stop_on_failure(self):
        log = self.root / 'log'
        self.script('20261106_01_first.sh', f'echo first >>{log}')
        self.script('20261106_03_last.sh', f'grep -qx second {log} && echo third >>{log}')
        self.script('20261106_02_failure.sh', f'echo second >>{log}\ntest -e {self.root}/ready')
        failed = run(self.scripts, self.home)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(log.read_text().splitlines(), ['first', 'second'])
        state = self.home / 'state/arch-niri-dux/migrations'
        self.assertTrue((state / '20261106_01_first.sh').is_file())
        self.assertFalse((state / '20261106_02_failure.sh').exists())
        (self.root / 'ready').touch()
        self.assertEqual(run(self.scripts, self.home).returncode, 0)
        self.assertEqual(log.read_text().splitlines(), ['first', 'second', 'second', 'third'])

    def test_pkgbuild_seeds_one_skel_marker_per_script(self):
        recipe = (ROOT / 'packages/integration/PKGBUILD').read_text()
        self.assertIn('etc/skel/.local/state/arch-niri-dux/migrations', recipe)
        self.script('20261106_01_example.sh', 'true')
        markers = self.root / 'markers'
        markers.mkdir()
        for script in sorted(self.scripts.glob('*.sh')):
            (markers / script.name).touch()
        self.assertEqual([path.name for path in sorted(markers.iterdir())], ['20261106_01_example.sh'])


if __name__ == '__main__':
    unittest.main()
