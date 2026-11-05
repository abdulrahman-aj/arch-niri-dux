from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
from scripts.verification import boot as guest


class PlymouthFallbackTests(unittest.TestCase):
    def test_serial_instrumentation_roundtrip_preserves_boot_and_recovery_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'etc/kernel').mkdir(parents=True)
            (root / 'etc/default').mkdir(parents=True)
            base = 'rd.luks.name=uuid=dux-root rw quiet splash resume_offset=99'
            cmdline = root / 'etc/kernel/cmdline'
            defaults = root / 'etc/default/limine'
            cmdline.write_text(base + '\n')
            configuration = f'KERNEL_CMDLINE[default]="{base}"\nMAX_SNAPSHOT_ENTRIES=12\n'
            defaults.write_text(configuration)
            guest.serial_console(True, root)
            guest.serial_console(True, root)
            self.assertEqual(cmdline.read_text().split().count('console=ttyS0,115200n8'), 1)
            guest.serial_console(False, root)
            self.assertEqual(cmdline.read_text().strip(), base)
            self.assertEqual(defaults.read_text(), configuration)

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
