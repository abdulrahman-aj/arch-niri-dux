from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
from scripts.verification import boot as guest


class PlymouthFallbackTests(unittest.TestCase):
    def test_serial_instrumentation_roundtrip_preserves_boot_and_recovery_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'etc/default').mkdir(parents=True)
            base = 'rd.luks.name=uuid=dux-root rw quiet splash resume_offset=99'
            defaults = root / 'etc/default/limine'
            configuration = f'KERNEL_CMDLINE[default]="{base}"\nMAX_SNAPSHOT_ENTRIES=12\n'
            defaults.write_text(configuration)
            guest.serial_console(True, root)
            guest.serial_console(True, root)
            self.assertEqual(guest.configured_cmdline(root).count('console=ttyS0,115200n8'), 1)
            guest.serial_console(False, root)
            self.assertEqual(guest.configured_cmdline(root), base.split())


if __name__ == '__main__':
    unittest.main()
