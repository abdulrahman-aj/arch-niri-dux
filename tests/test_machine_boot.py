import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from installer import install as installer
from scripts.verification import boot
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

    def test_luks_pbkdf_rejects_the_old_pinned_memory(self):
        old = 'PBKDF:      argon2id\n  Time cost:       4\n  Memory:          262144\n  Threads:         4\n'
        with self.assertRaises(AssertionError):
            boot.assert_luks_pbkdf(old)

    def test_luks_pbkdf_accepts_cryptsetup_defaults(self):
        new = 'PBKDF:      argon2id\n  Time cost:       4\n  Memory:          1048576\n  Threads:         4\n'
        self.assertEqual(boot.assert_luks_pbkdf(new), [1048576])
        with self.assertRaises(AssertionError):
            boot.assert_luks_pbkdf('PBKDF:      pbkdf2\n  Memory:          1048576\n')


if __name__ == '__main__':
    unittest.main()
