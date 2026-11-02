import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plymouth_guest', ROOT / 'scripts/verify-guest.py')
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)


class PlymouthFallbackTests(unittest.TestCase):
    def test_splash_fallback_toggle_preserves_quiet_splash_and_luks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'etc/kernel').mkdir(parents=True)
            (root / 'etc/default').mkdir(parents=True)
            base = ('rd.luks.name=uuid=dux-root root=/dev/mapper/dux-root '
                    'rootflags=subvol=@ rw quiet splash resume=/dev/mapper/dux-root resume_offset=99')
            (root / 'etc/kernel/cmdline').write_text(base + '\n')
            (root / 'etc/default/limine').write_text(
                f'KERNEL_CMDLINE[default]="{base}"\n'
                '# Recovery guidance: plymouth.enable=0 is available\n')
            guest.splash_fallback(True, root)
            disabled = (root / 'etc/kernel/cmdline').read_text().split()
            self.assertIn('plymouth.enable=0', disabled)
            self.assertIn('quiet', disabled)
            self.assertIn('splash', disabled)
            self.assertIn('resume=/dev/mapper/dux-root', disabled)
            self.assertIn('plymouth.enable=0', (root / 'etc/default/limine').read_text())
            # Disabling twice stays idempotent.
            guest.splash_fallback(True, root)
            self.assertEqual((root / 'etc/kernel/cmdline').read_text().split().count('plymouth.enable=0'), 1)
            guest.splash_fallback(False, root)
            enabled = (root / 'etc/kernel/cmdline').read_text().split()
            self.assertNotIn('plymouth.enable=0', enabled)
            self.assertIn('quiet', enabled)
            self.assertIn('splash', enabled)
            defaults = (root / 'etc/default/limine').read_text()
            self.assertIn(f'KERNEL_CMDLINE[default]="{base}"', defaults)
            self.assertIn('# Recovery guidance: plymouth.enable=0 is available', defaults)
            self.assertNotIn('KERNEL_CMDLINE[default]+=" plymouth.enable=0"', defaults)


if __name__ == '__main__':
    unittest.main()
