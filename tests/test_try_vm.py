import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import try_vm


class TryVmTests(unittest.TestCase):
    def test_agent_guard(self):
        for marker in ('CLAUDECODE', 'AI_AGENT', 'AGENT', 'CODEX_SANDBOX', 'OPENCODE_SESSION', 'T3CODE_HOME'):
            with self.subTest(marker=marker), self.assertRaises(SystemExit):
                try_vm.refuse_if_agent({marker: '1'})
        try_vm.refuse_if_agent({'HOME': '/home/u', 'PATH': '/usr/bin'})

    def test_name_validation(self):
        for good in ('default', 'a', 'my-vm_1.2'):
            self.assertTrue(try_vm.valid_name(good))
        for bad in ('', '../evil', 'a/b', ' name', '-lead'):
            self.assertFalse(try_vm.valid_name(bad))

    def test_create_open_delete_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as state:
            for name in ('test.iso', 'OVMF_VARS.fd', 'CODE'):
                (Path(temp) / name).write_bytes(b'data')

            def fake_run(argv, **_kwargs):
                if argv[0] == 'qemu-img':
                    Path(argv[-2]).write_bytes(b'disk')
                return subprocess.CompletedProcess(argv, 0)

            with (
                patch.dict(os.environ, {'TRY_OUT_DIR': temp}),
                patch('shutil.which', return_value='/usr/bin/qemu-img'),
                patch('try_vm.firmware', return_value=(Path(temp) / 'CODE', Path(temp) / 'OVMF_VARS.fd')),
                patch('os.access', return_value=True),
                patch('try_vm.subprocess.run', side_effect=fake_run),
            ):
                with patch('builtins.input', side_effect=['new', 'demo', '']):
                    try_vm.interactive(base=state)
                self.assertTrue((Path(state) / 'demo' / 'disk.qcow2').is_file())
                with patch('builtins.input', side_effect=['delete demo', 'y', '']):
                    try_vm.interactive(base=state)
                self.assertFalse((Path(state) / 'demo').exists())


if __name__ == '__main__':
    unittest.main()
