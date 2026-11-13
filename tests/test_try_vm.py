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
        with self.assertRaises(SystemExit):
            try_vm.refuse_if_agent({'T3CODE_THREAD_ID': 'thread-1'})
        # Regression: make try refused to run from T3's integrated terminal.
        try_vm.refuse_if_agent({'T3CODE_PROJECT_ROOT': '/w', 'AGENT_HEAVY_JOBS': 'deny'})

    def test_build_stage_follows_traced_commands(self):
        stages = try_vm.BUILD_STAGES[try_vm.DEVELOPMENT]
        self.assertEqual(stages[0][0], 'Download the Arch bootstrap image')
        self.assertEqual(try_vm.build_stage(stages, ''), 0)
        # The host-side download runs before the VM boots; its end is a plain
        # host log line, not a traced guest command.
        self.assertEqual(try_vm.build_stage(stages, '', 'Starting the builder VM'), 1)
        log = '+ python /source/scripts/package_lock.py fetch /source /build/payload/repo\nfetched 700 packages\n'
        self.assertEqual(try_vm.build_stage(stages, log, 'Starting the builder VM'), 2)
        # Untraced output that mentions a later stage does not advance it.
        self.assertEqual(try_vm.build_stage(stages, 'installing archiso and mkarchiso\n'), 0)
        log += '+ runuser -u nobody -- python /source/scripts/package_build.py /source\n'
        self.assertEqual(try_vm.build_stage(stages, log, 'Starting the builder VM'), 3)
        # Stages never move backwards between incremental reads.
        self.assertEqual(try_vm.build_stage(stages, '+ echo done\n', current=4), 4)
        fractions = [try_vm.build_fraction(stages, index, 10**6) for index in range(len(stages))]
        self.assertEqual(fractions, sorted(fractions))
        self.assertLess(fractions[-1], 1)

    def test_bootstrap_byte_fraction_follows_part_file(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(try_vm.bootstrap_byte_fraction(state=temp))
            part = Path(temp) / 'archlinux-2026.10.01-x86_64.iso.part'
            part.write_bytes(b'x' * 100)
            self.assertAlmostEqual(try_vm.bootstrap_byte_fraction(state=temp), 100 / try_vm.BOOTSTRAP_ISO_BYTES)
            part.write_bytes(b'')
            self.assertEqual(try_vm.bootstrap_byte_fraction(state=temp), 0.0)
            part.write_bytes(b'x' * 100)
            with patch.object(try_vm, 'BOOTSTRAP_ISO_BYTES', 50):
                # A stale size constant caps instead of overshooting the bar.
                self.assertEqual(try_vm.bootstrap_byte_fraction(state=temp), 1.0)

    def test_log_tail_ignores_previous_build_and_follows_rewrites(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'guest-build.log'
            log.write_text('+ mkarchiso -v\n')
            os.utime(log, (1000, 1000))
            tail = try_vm.LogTail(log, since=2000)
            self.assertEqual(tail.read(), '')
            self.assertFalse(tail.active)
            log.write_text('+ python /source/scripts/package_lock.py fetch\n  0  0  0  0\npartial')
            self.assertIn('package_lock.py', tail.read())
            self.assertEqual(tail.lines, ['+ python /source/scripts/package_lock.py fetch'])
            with log.open('a') as handle:
                handle.write(' line done\n')
            self.assertEqual(tail.read(), 'partial line done\n')
            log.write_text('+ fresh\n')
            self.assertEqual(tail.read(), '+ fresh\n')

    def test_low_disk_warns_before_new_vm_prompts(self):
        with tempfile.TemporaryDirectory() as state:
            with (
                patch.object(try_vm, 'VM_SPACE', 2**60),
                patch('try_vm.gum_style') as style,
                patch('try_vm.gum_confirm', return_value=False),
                patch('try_vm.gum_input') as text,
            ):
                self.assertIsNone(try_vm.create_flow(base=state))
                style.assert_called_once()
                text.assert_not_called()

    def test_name_validation(self):
        for good in ('default', 'a', 'my-vm_1.2', 'new', '123abc'):
            self.assertTrue(try_vm.valid_name(good))
        for bad in ('', '../evil', 'a/b', ' name', '-lead', '123', '1.5'):
            self.assertFalse(try_vm.valid_name(bad))

    def test_disk_size_refuses_below_what_the_installer_needs(self):
        # 4 GiB RAM needs 35 GiB (32 floor vs 4+2 swap + 29 reserve); 8 GiB RAM needs 39.
        with patch('try_vm.gum_input', side_effect=['32', '35']):
            with patch('builtins.print') as printed:
                self.assertEqual(try_vm.ask_disk_size(4), 35)
            refusal = '\n'.join(call.args[0] for call in printed.call_args_list)
            self.assertIn('35 GiB', refusal)
            self.assertIn('automatic swap', refusal)
        with patch('try_vm.gum_input', side_effect=['38', '39']):
            self.assertEqual(try_vm.ask_disk_size(8), 39)
        with patch('try_vm.gum_input', side_effect=['']):
            self.assertEqual(try_vm.ask_disk_size(4), try_vm.DEFAULT_DISK_GIB)
        with patch('try_vm.gum_input', return_value=None):
            self.assertIsNone(try_vm.ask_disk_size(4))
        with patch('try_vm.gum_input', side_effect=['abc', '0', '48']):
            self.assertEqual(try_vm.ask_disk_size(4), 48)

    def test_disk_minimum_comes_from_the_installer_constant(self):
        import installer.storage

        # A bigger installer floor raises the prompt's floor with it: no copied literal.
        with (
            patch.object(installer.storage, 'MIN_DISK_BYTES', 64 * installer.storage.GIB),
            patch('try_vm.gum_input', side_effect=['48', '64']),
            patch('builtins.print'),
        ):
            self.assertEqual(try_vm.ask_disk_size(4), 64)

    def test_ask_name_rejects_reserved_and_bad_names(self):
        with patch('try_vm.gum_input', side_effect=['new', '123', 'my vm', 'ok-name']):
            self.assertEqual(try_vm.ask_name(), 'ok-name')
        with patch('try_vm.gum_input', side_effect=['']):
            self.assertEqual(try_vm.ask_name(), try_vm.DEFAULT_NAME)
        with patch('try_vm.gum_input', return_value=None):
            self.assertIsNone(try_vm.ask_name())

    def test_list_vms_ignores_junk_directories(self):
        with tempfile.TemporaryDirectory() as state:
            (Path(state) / 'out').mkdir()
            real = Path(state) / 'demo'
            real.mkdir()
            (real / 'disk.qcow2').write_bytes(b'disk')
            self.assertEqual([path.name for path in try_vm.list_vms(state)], ['demo'])

    def test_size_and_source_prompts(self):
        with patch('try_vm.gum_input', side_effect=['0', '-3', 'abc', '16']):
            self.assertEqual(try_vm.ask_positive_int('RAM', 8), 16)
        with patch('try_vm.gum_input', side_effect=['']):
            self.assertEqual(try_vm.ask_positive_int('Disk', 48), 48)
        with patch('try_vm.gum_input', return_value=None):
            self.assertIsNone(try_vm.ask_positive_int('RAM', 8))
        with patch('try_vm.gum_choose', return_value=try_vm.SOURCE_LABELS[try_vm.RELEASE]):
            self.assertEqual(try_vm.ask_source(), try_vm.RELEASE)
        with patch('try_vm.gum_choose', return_value=try_vm.SOURCE_LABELS[try_vm.DEVELOPMENT]):
            self.assertEqual(try_vm.ask_source(), try_vm.DEVELOPMENT)
        with patch('try_vm.gum_choose', return_value=None):
            self.assertIsNone(try_vm.ask_source())

    def test_require_gum(self):
        with patch('shutil.which', return_value=None):
            with self.assertRaises(SystemExit):
                try_vm.require_gum()
        with (
            patch('shutil.which', return_value='/nix/store/gum'),
            patch('sys.stdin.isatty', return_value=False),
        ):
            with self.assertRaises(SystemExit):
                try_vm.require_gum()
        with (
            patch('shutil.which', return_value='/nix/store/gum'),
            patch('sys.stdin.isatty', return_value=True),
            patch('sys.stdout.isatty', return_value=False),
        ):
            with self.assertRaises(SystemExit):
                try_vm.require_gum()
        with (
            patch('shutil.which', return_value='/nix/store/gum'),
            patch('sys.stdin.isatty', return_value=True),
            patch('sys.stdout.isatty', return_value=True),
        ):
            self.assertIsNone(try_vm.require_gum())

    def test_gum_missing_returns_from_menu(self):
        with (
            patch('try_vm.require_gum'),
            patch('try_vm.gum_style'),
            patch('try_vm.subprocess.run', side_effect=OSError('no such file')),
        ):
            with self.assertRaises(try_vm.GumMissingError):
                try_vm.gum_choose('Pick:', ['a'])
        with tempfile.TemporaryDirectory() as state:
            with (
                patch('try_vm.require_gum'),
                patch('try_vm.gum_style'),
                patch('try_vm.gum_choose', side_effect=try_vm.GumMissingError(try_vm.GUM_MISSING)),
            ):
                try_vm.interactive(base=state)

    def test_iso_selection_by_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'arch-niri-dux-2026.10.01-x86_64.iso').write_bytes(b'release')
            (Path(temp) / 'arch-niri-dux-2026.11.01-x86_64-development.iso').write_bytes(b'dev')
            with patch.dict(os.environ, {'TRY_OUT_DIR': temp}):
                self.assertTrue(try_vm.iso_for_kind(try_vm.RELEASE).name.endswith('.iso'))
                self.assertFalse(try_vm.iso_for_kind(try_vm.RELEASE).name.endswith('-development.iso'))
                self.assertTrue(try_vm.iso_for_kind(try_vm.DEVELOPMENT).name.endswith('-development.iso'))

    def test_ensure_iso_and_staleness(self):
        commit_time = int(
            subprocess.check_output(['git', '-C', str(ROOT), 'log', '-1', '--format=%ct'], text=True).strip()
        )
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'TRY_OUT_DIR': temp}):
                with patch('try_vm.gum_confirm', return_value=False):
                    with self.assertRaises(SystemExit):
                        try_vm.ensure_iso(try_vm.DEVELOPMENT)
                iso = Path(temp) / 'x-development.iso'
                iso.write_bytes(b'data')
                os.utime(iso, (commit_time - 100, commit_time - 100))
                self.assertTrue(try_vm.checkout_newer_than(iso))
                os.utime(iso, (commit_time, commit_time))
                self.assertFalse(try_vm.checkout_newer_than(iso))
                self.assertEqual(try_vm.ensure_iso(try_vm.DEVELOPMENT), iso.resolve())

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

            real_popen = subprocess.Popen

            def fake_popen(argv, **_kwargs):
                self.assertEqual(argv[0], 'qemu-system-x86_64')
                return real_popen(['true'])

            with (
                patch.dict(os.environ, {'TRY_OUT_DIR': temp}),
                patch('shutil.which', return_value='/usr/bin/qemu-img'),
                patch('try_vm.firmware', return_value=(Path(temp) / 'CODE', Path(temp) / 'OVMF_VARS.fd')),
                patch('os.access', return_value=True),
                patch('try_vm.subprocess.run', side_effect=fake_run),
                patch('try_vm.subprocess.Popen', side_effect=fake_popen),
                patch('try_vm.check_new_vm_space', return_value=True),
                patch('try_vm.require_gum'),
                patch('try_vm.gum_style'),
                patch('try_vm.gum_choose', side_effect=['New VM', try_vm.SOURCE_LABELS[try_vm.RELEASE], None]),
                patch('try_vm.gum_input', side_effect=['demo', '16', '60']),
                patch('try_vm.gum_confirm', return_value=True),
            ):
                try_vm.interactive(base=state)
                choices = try_vm.read_choices(Path(state) / 'demo')
                self.assertEqual(choices, {'ram_gib': 16, 'disk_gib': 60, 'source': try_vm.RELEASE})
                self.assertTrue((Path(state) / 'demo' / 'disk.qcow2').is_file())
            with (
                patch('try_vm.require_gum'),
                patch('try_vm.gum_style'),
                patch('try_vm.gum_choose', side_effect=['Delete a VM', 'demo', None]),
                patch('try_vm.gum_confirm', return_value=True),
            ):
                try_vm.interactive(base=state)
                self.assertFalse((Path(state) / 'demo').exists())

    def test_interactive_quit_and_cancel(self):
        with tempfile.TemporaryDirectory() as state:
            with (
                patch('try_vm.require_gum'),
                patch('try_vm.gum_style'),
                patch('try_vm.gum_choose', return_value=None) as choose,
                patch('try_vm.gum_input') as text,
                patch('try_vm.gum_confirm') as confirm,
            ):
                try_vm.interactive(base=state)
                choose.assert_called_once()
                text.assert_not_called()
                confirm.assert_not_called()
                self.assertEqual(try_vm.list_vms(state), [])

    def test_delete_kept_without_confirm(self):
        with tempfile.TemporaryDirectory() as state:
            directory = Path(state) / 'demo'
            directory.mkdir()
            (directory / 'disk.qcow2').write_bytes(b'disk')
            with patch('try_vm.gum_confirm', return_value=False):
                try_vm.delete_vm('demo', base=state)
            self.assertTrue(directory.is_dir())

    def test_delete_picker_cancel_keeps_vm(self):
        with tempfile.TemporaryDirectory() as state:
            directory = Path(state) / 'demo'
            directory.mkdir()
            (directory / 'disk.qcow2').write_bytes(b'disk')
            with (
                patch('try_vm.require_gum'),
                patch('try_vm.gum_style'),
                patch('try_vm.gum_choose', side_effect=['Delete a VM', None, None]),
                patch('try_vm.gum_confirm') as confirm,
            ):
                try_vm.interactive(base=state)
                confirm.assert_not_called()
            self.assertTrue(directory.is_dir())


if __name__ == '__main__':
    unittest.main()
