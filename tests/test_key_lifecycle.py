import subprocess
from pathlib import Path
import unittest


class LifecycleTests(unittest.TestCase):
    def test_real_pacman_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(
            [
                'fakeroot',
                'python3',
                '-c',
                'from verification.key_lifecycle import qualify; from pathlib import Path; '
                f'qualify(Path({str(root / "desktop/usr/bin/arch-niri-dux-keyring-recover")!r}), fakeroot=False)',
            ],
            cwd=root / 'scripts',
            check=True,
        )
