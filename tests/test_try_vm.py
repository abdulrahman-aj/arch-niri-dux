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

    def test_size_and_source_prompts(self):
        with patch('builtins.input', side_effect=['0', '-3', 'abc', '16']):
            self.assertEqual(try_vm.ask_positive_int('RAM', 8), 16)
        with patch('builtins.input', side_effect=['']):
            self.assertEqual(try_vm.ask_positive_int('Disk', 48), 48)
        with patch('builtins.input', side_effect=['3', '1']):
            self.assertEqual(try_vm.ask_source(), try_vm.RELEASE)
        with patch('builtins.input', side_effect=['']):
            self.assertEqual(try_vm.ask_source(), try_vm.DEVELOPMENT)

    def test_iso_selection_by_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'arch-niri-dux-2026.10.01-x86_64.iso').write_bytes(b'release')
            (Path(temp) / 'arch-niri-dux-2026.11.01-x86_64-development.iso').write_bytes(b'dev')
            with patch.dict(os.environ, {'TRY_OUT_DIR': temp}):
                self.assertTrue(try_vm.iso_for_kind(try_vm.RELEASE).name.endswith('.iso'))
                self.assertFalse(try_vm.iso_for_kind(try_vm.RELEASE).name.endswith('-development.iso'))
                self.assertTrue(try_vm.iso_for_kind(try_vm.DEVELOPMENT).name.endswith('-development.iso'))

    def test_choices_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'demo'
            directory.mkdir()
            self.assertEqual(
                try_vm.read_choices(directory),
                {'ram_gib': 8, 'disk_gib': 48, 'source': try_vm.DEVELOPMENT},
            )
            try_vm.write_choices(directory, 16, 60, try_vm.RELEASE)
            self.assertEqual(
                try_vm.read_choices(directory),
                {'ram_gib': 16, 'disk_gib': 60, 'source': try_vm.RELEASE},
            )
            (directory / 'config').write_text('ram_gib=nope\ndisk_gib=-4\nsource=bogus\n')
            self.assertEqual(
                try_vm.read_choices(directory),
                {'ram_gib': 8, 'disk_gib': 48, 'source': try_vm.DEVELOPMENT},
            )

    def test_create_open_delete_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as state:
            for name in ('test.iso', 'test-development.iso', 'OVMF_VARS.fd', 'CODE'):
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
                with patch('builtins.input', side_effect=['new', 'demo', '1', '16', '60', '']):
                    try_vm.interactive(base=state)
                choices = try_vm.read_choices(Path(state) / 'demo')
                self.assertEqual(choices, {'ram_gib': 16, 'disk_gib': 60, 'source': try_vm.RELEASE})
                self.assertTrue((Path(state) / 'demo' / 'disk.qcow2').is_file())
                with patch('builtins.input', side_effect=['delete demo', 'y', '']):
                    try_vm.interactive(base=state)
                self.assertFalse((Path(state) / 'demo').exists())


if __name__ == '__main__':
    unittest.main()
