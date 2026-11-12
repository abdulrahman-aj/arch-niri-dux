import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import try_vm


def gpt_head(with_esp=True):
    data = bytearray(34 * 512)
    data[510:512] = b'\x55\xaa'
    data[512:520] = b'EFI PART'
    struct.pack_into('<QII', data, 512 + 72, 2, 128, 128)
    if with_esp:
        data[2 * 512 : 2 * 512 + 16] = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b').bytes_le
    return bytes(data)


class TryVmTests(unittest.TestCase):
    def test_agent_guard_refuses_required_markers(self):
        for marker in ('T3CODE_THREAD_ID', 'CLAUDECODE', 'AI_AGENT'):
            with self.subTest(marker=marker), self.assertRaises(SystemExit) as context:
                try_vm.refuse_if_agent({marker: '1'})
            self.assertIn('verify-fast', str(context.exception))

    def test_agent_guard_refuses_observed_worker_markers(self):
        observed = {
            'AGENT': '1',
            'OPENCODE': '1',
            'OPENCODE_SESSION_ID': 'ses_test',
            'OPENCODE_TERMINAL': '1',
            'T3CODE_HOME': '/home/u/.t3',
            'T3CODE_LAN': 'false',
            'T3_BOOT_SERVICE_UNIT': 't3code.service',
            'T3_SERVICE_LAUNCHER_CONTEXT': '{}',
        }
        for marker, value in observed.items():
            with self.subTest(marker=marker), self.assertRaises(SystemExit):
                try_vm.refuse_if_agent({marker: value})

    def test_agent_guard_refuses_codex_prefix(self):
        with self.assertRaises(SystemExit):
            try_vm.refuse_if_agent({'CODEX_SANDBOX': 'seatbelt'})

    def test_agent_guard_passes_clean_human_env(self):
        try_vm.refuse_if_agent({'HOME': '/home/u', 'PATH': '/usr/bin', 'DISPLAY': ':0'})

    def test_name_validation(self):
        for good in ('default', 'a', 'my-vm_1.2'):
            self.assertTrue(try_vm.valid_name(good), good)
        for bad in ('', '../evil', 'a/b', ' name', '-lead'):
            self.assertFalse(try_vm.valid_name(bad), bad)

    def test_newest_iso_picks_lexical_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / 'arch-niri-dux-2026.11.01-x86_64.iso'
            second = Path(temp) / 'arch-niri-dux-2026.11.07-x86_64.iso'
            first.write_bytes(b'a')
            second.write_bytes(b'b')
            with patch.dict(os.environ, {'TRY_OUT_DIR': temp}):
                self.assertEqual(try_vm.newest_iso(), second)
                self.assertEqual(try_vm.iso_version(second), 'arch-niri-dux-2026.11.07-x86_64')

    def test_gpt_probe(self):
        self.assertTrue(try_vm.has_esp_gpt(gpt_head(True)))
        self.assertFalse(try_vm.has_esp_gpt(gpt_head(False)))
        self.assertFalse(try_vm.has_esp_gpt(bytes(34 * 512)))

    def test_disk_is_installed_prefers_probe_over_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / 'disk.raw'
            raw.write_bytes(gpt_head(True))
            self.assertTrue(try_vm.disk_is_installed(raw, {'installed': False}))
            raw.write_bytes(bytes(34 * 512))
            self.assertFalse(try_vm.disk_is_installed(raw, {'installed': True}))

    def test_qemu_command_iso_selection(self):
        base = dict(code='/fw/CODE', variables='/vm/VARS', disk='/vm/disk.qcow2')
        first = try_vm.build_qemu_command(iso='/out/a.iso', **base)
        self.assertIn('/out/a.iso', ' '.join(str(part) for part in first))
        self.assertIn('bootindex=1', ' '.join(str(part) for part in first))
        later = try_vm.build_qemu_command(**base)
        self.assertNotIn('install-media', ' '.join(str(part) for part in later))
        offline = try_vm.build_qemu_command(**base)
        self.assertIn('user,model=virtio-net-pci', ' '.join(str(part) for part in offline))
        off = try_vm.build_qemu_command(offline=True, **base)
        self.assertIn('-nic none', ' '.join(str(part) for part in off))
        headless = try_vm.build_qemu_command(display='none', **base)
        self.assertIn('-display none', ' '.join(str(part) for part in headless))

    def test_choose_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            meminfo = Path(temp) / 'meminfo'
            meminfo.write_text('MemAvailable:      24000000 kB\n')
            self.assertEqual(try_vm.choose_memory(None, str(meminfo), False), 8)
            meminfo.write_text('MemAvailable:       7000000 kB\n')
            with patch('builtins.print') as printer:
                self.assertEqual(try_vm.choose_memory(None, str(meminfo), False), 4)
                printer.assert_called_once()
            meminfo.write_text('MemAvailable:       1000000 kB\n')
            with self.assertRaises(SystemExit):
                try_vm.choose_memory(None, str(meminfo), False)

    def test_create_and_delete_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as state:
            with patch.dict(os.environ, {'TRY_OUT_DIR': temp}):
                self.assertIsNone(try_vm.newest_iso())
            with (
                patch('shutil.which', return_value='/usr/bin/qemu-img'),
                patch('try_vm.firmware', return_value=(Path('/fw/CODE'), Path(temp) / 'TEMPLATE')),
                patch('os.access', return_value=True),
            ):
                (Path(temp) / 'TEMPLATE').write_bytes(b'vars')
                with patch('subprocess.run') as runner:
                    directory = try_vm.create_vm('demo', 48, base=state)
                    self.assertTrue(directory / 'disk.qcow2' and directory / 'meta.json')
                    self.assertTrue(runner.called)
                meta = try_vm.load_meta(directory)
                self.assertEqual(meta['disk_size_gib'], 48)
                self.assertFalse(meta['installed'])
                self.assertEqual(try_vm.list_vms(base=state), [directory])
                self.assertTrue(try_vm.delete_vm('demo', assume_yes=True, base=state))
                self.assertFalse(directory.exists())


if __name__ == '__main__':
    unittest.main()
