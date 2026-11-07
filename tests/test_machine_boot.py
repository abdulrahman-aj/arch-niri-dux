import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from installer import install as installer
from scripts.verification.boot import configured_cmdline


class MachineBootTests(unittest.TestCase):
    def test_firmware_deferral_roundtrip_preserves_resume_and_snapshot_policy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, 'TARGET', Path(directory)):
            root = Path(directory)
            for deferred in (True, False):
                installer.write_boot_config('luks-id', 'filesystem-id', '1234', defer_efi=deferred)
                cmdline = configured_cmdline(root)
                self.assertIn('rd.luks.name=luks-id=dux-root', cmdline)
                self.assertIn('resume=/dev/mapper/dux-root', cmdline)
                self.assertIn('resume_offset=1234', cmdline)
                self.assertNotIn('noresume', cmdline)
                # Exercise the shell settings as the consumer does, including +=.
                values = subprocess.check_output(
                    [
                        'bash',
                        '-c',
                        'source "$1"; printf \'["%s","%s","%s"]\' "$FS_UUID" "$SNAPSHOT_KERNEL_PARAMETERS" "${SKIP_UEFI:-}"',
                        'bash',
                        str(root / 'etc/default/limine'),
                    ],
                    text=True,
                )
                fs_uuid, snapshot_flags, skip = json.loads(values)
                self.assertEqual(fs_uuid, 'filesystem-id')
                self.assertIn('noresume', snapshot_flags.split())
                self.assertEqual(bool(skip), deferred)


if __name__ == '__main__':
    unittest.main()
