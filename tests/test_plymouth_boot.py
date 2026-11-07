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


if __name__ == '__main__':
    unittest.main()
