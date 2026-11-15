import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
from tools.dev import try_vm
from tests.git_isolation import check_git_output


GUM_SCRIPT = """#!/bin/sh
# Fake gum for tests: style always succeeds without consuming a reply;
# choose/input/confirm pop one "<exit>:<text>" line from $GUM_REPLIES.
if [ "$1" = "style" ]; then exit 0; fi
replies="$GUM_REPLIES"
line=$(head -n 1 "$replies")
tail -n +2 "$replies" > "$replies.tmp" && mv "$replies.tmp" "$replies"
code=${line%%:*}
text=${line#*:}
if [ "$code" = "$line" ]; then code="$line"; text=""; fi
if [ -n "$text" ]; then printf '%s' "$text"; fi
exit "$code"
"""

QEMU_IMG_SCRIPT = """#!/bin/sh
# Fake qemu-img create: touch the disk path (second-to-last arg) then exit 0.
set -- "$@"
count=$#
target=""
i=1
for arg in "$@"; do
    if [ "$i" -eq $((count - 1)) ]; then target="$arg"; fi
    i=$((i + 1))
done
if [ -n "$target" ]; then : > "$target"; fi
exit 0
"""

QEMU_SYSTEM_SCRIPT = """#!/bin/sh
exit 0
"""


def make_bin(directory, replies=(), extra=()):
    bindir = Path(directory) / 'bin'
    bindir.mkdir(parents=True, exist_ok=True)
    replies_path = Path(directory) / 'gum-replies'
    replies_path.write_text(''.join(f'{code}:{text}\n' for code, text in replies))
    (bindir / 'gum').write_text(GUM_SCRIPT)
    (bindir / 'gum').chmod(0o755)
    for name, body in extra:
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    # Only the fake gum's coreutils come from the host; every other name on
    # PATH is a fake, so a missing fake fails closed instead of running a
    # host tool like qemu-img or make.
    for name in ('head', 'tail', 'mv'):
        target = bindir / name
        if not target.exists():
            target.symlink_to(shutil.which(name))
    return bindir, replies_path


def gum_env(bindir, replies_path):
    return {'PATH': str(bindir), 'GUM_REPLIES': str(replies_path)}


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
        log = '+ python -m tools.packages.inputs fetch /source /build/payload/repo\nfetched 700 packages\n'
        self.assertEqual(try_vm.build_stage(stages, log, 'Starting the builder VM'), 2)
        # Untraced output that mentions a later stage does not advance it.
        self.assertEqual(try_vm.build_stage(stages, 'installing archiso and mkarchiso\n'), 0)
        log += '+ runuser -u nobody -- python -m tools.packages.integration /source\n'
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
            log.write_text('+ python -m tools.packages.inputs fetch\n  0  0  0  0\npartial')
            self.assertIn('tools.packages.inputs', tail.read())
            self.assertEqual(tail.lines, ['+ python -m tools.packages.inputs fetch'])
            with log.open('a') as handle:
                handle.write(' line done\n')
            self.assertEqual(tail.read(), 'partial line done\n')
            log.write_text('+ fresh\n')
            self.assertEqual(tail.read(), '+ fresh\n')

    def test_low_disk_warns_before_new_vm_prompts(self):
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as tmp:
            bindir, replies = make_bin(tmp, [(0, ''), (1, '')])
            with (
                patch.object(try_vm, 'VM_SPACE', 2**60),
                patch.dict(os.environ, gum_env(bindir, replies)),
                patch('sys.stdin.isatty', return_value=True),
                patch('sys.stdout.isatty', return_value=True),
            ):
                self.assertIsNone(try_vm.create_flow(base=state_dir))
                self.assertEqual(try_vm.list_vms(state_dir), [])

    def test_name_validation(self):
        for good in ('default', 'a', 'my-vm_1.2', 'new', 'delete', '123', '1.5', '123abc'):
            self.assertTrue(try_vm.valid_name(good))
        for bad in ('', '../evil', 'a/b', ' name', '-lead'):
            self.assertFalse(try_vm.valid_name(bad))

    def test_parse_vm_name(self):
        self.assertEqual(try_vm.parse_vm_name('ok-name'), 'ok-name')
        self.assertEqual(try_vm.parse_vm_name(''), try_vm.DEFAULT_NAME)
        self.assertEqual(try_vm.parse_vm_name('   '), try_vm.DEFAULT_NAME)
        for bad in ('my vm', '../evil', '-lead'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                try_vm.parse_vm_name(bad)

    def test_parse_positive_int(self):
        self.assertEqual(try_vm.parse_positive_int('16', 8), 16)
        self.assertEqual(try_vm.parse_positive_int('', 48), 48)
        self.assertEqual(try_vm.parse_positive_int('   ', 48), 48)
        for bad in ('0', '-3', 'abc', '1.5'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                try_vm.parse_positive_int(bad, 8)

    def test_parse_disk_size(self):
        # 4 GiB RAM needs 35 GiB (32 floor vs 4+2 swap + 29 reserve); 8 GiB RAM needs 39.
        self.assertEqual(try_vm.parse_disk_size('35', 4), 35)
        self.assertEqual(try_vm.parse_disk_size('', 4), try_vm.DEFAULT_DISK_GIB)
        self.assertEqual(try_vm.parse_disk_size('', 32), 63)
        self.assertEqual(try_vm.parse_disk_size('48', 4), 48)
        with self.assertRaises(ValueError):
            try_vm.parse_disk_size('32', 4)
        with self.assertRaises(ValueError):
            try_vm.parse_disk_size('38', 8)
        self.assertEqual(try_vm.parse_disk_size('39', 8), 39)
        for bad in ('abc', '0', '-1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                try_vm.parse_disk_size(bad, 4)
        # The refusal names the floor and the swap arithmetic.
        try:
            try_vm.parse_disk_size('32', 4)
            self.fail('expected refusal')
        except ValueError as error:
            message = str(error)
            self.assertIn('35 GiB', message)
            self.assertIn('automatic swap', message)

    def test_disk_minimum_comes_from_the_installer_constant(self):
        import installer.storage

        # A bigger installer floor raises the prompt's floor with it: no copied literal.
        with patch.object(installer.storage, 'MIN_DISK_BYTES', 64 * installer.storage.GIB):
            minimum, _, _ = try_vm.disk_size_floor(4)
            self.assertEqual(minimum, 64)
            self.assertEqual(try_vm.parse_disk_size('64', 4), 64)
            with self.assertRaises(ValueError):
                try_vm.parse_disk_size('48', 4)

    def test_source_choice_through_gum(self):
        for choice, expected in (
            (try_vm.RELEASE, try_vm.RELEASE),
            (try_vm.DEVELOPMENT, try_vm.DEVELOPMENT),
            ('bogus', None),
            ('', None),
        ):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as tmp:
                bindir, replies = make_bin(tmp, [(0, try_vm.SOURCE_LABELS.get(choice, choice))])
                with patch.dict(os.environ, gum_env(bindir, replies)):
                    self.assertEqual(try_vm.ask_source(), expected)

    def test_ask_name_roundtrip_through_real_gum(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir, replies = make_bin(tmp, [(0, 'my vm'), (0, 'ok-name')])
            with patch.dict(os.environ, gum_env(bindir, replies)):
                with patch('builtins.print'):
                    self.assertEqual(try_vm.ask_name(), 'ok-name')
            bindir2, replies2 = make_bin(tmp, [(0, '')])
            # Empty answer means the default; tested separately to keep reply files simple.
            with patch.dict(os.environ, gum_env(bindir2, replies2)):
                self.assertEqual(try_vm.ask_name(), try_vm.DEFAULT_NAME)

    def test_ask_positive_int_roundtrip_through_real_gum(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir, replies = make_bin(tmp, [(0, '0'), (0, 'abc'), (0, '16')])
            with patch.dict(os.environ, gum_env(bindir, replies)):
                with patch('builtins.print'):
                    self.assertEqual(try_vm.ask_positive_int('RAM', 8), 16)

    def test_ask_disk_size_roundtrip_through_real_gum(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir, replies = make_bin(tmp, [(0, '32'), (0, '35')])
            with patch.dict(os.environ, gum_env(bindir, replies)):
                with patch('builtins.print'):
                    self.assertEqual(try_vm.ask_disk_size(4), 35)

    def test_input_prompt_cancel_returns_none(self):
        # Gum exits nonzero when the user backs out of an input prompt.
        for ask, arguments in (
            (try_vm.ask_name, ()),
            (try_vm.ask_positive_int, ('RAM', 8)),
            (try_vm.ask_disk_size, (4,)),
        ):
            with self.subTest(ask=ask.__name__), tempfile.TemporaryDirectory() as tmp:
                bindir, replies = make_bin(tmp, [(1, '')])
                with patch.dict(os.environ, gum_env(bindir, replies)):
                    self.assertIsNone(ask(*arguments))

    def test_list_vms_ignores_junk_directories(self):
        with tempfile.TemporaryDirectory() as state:
            (Path(state) / 'out').mkdir()
            real = Path(state) / 'demo'
            real.mkdir()
            (real / 'disk.qcow2').write_bytes(b'disk')
            self.assertEqual([path.name for path in try_vm.list_vms(state)], ['demo'])

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

    def test_gum_transport_reports_missing_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / 'empty-bin'
            empty.mkdir()
            with patch.dict(os.environ, {'PATH': str(empty)}):
                with self.assertRaises(try_vm.GumMissingError):
                    try_vm.gum_choose('Pick:', ['a'])

    def test_gum_missing_returns_from_menu(self):
        def missing(argv, **_kwargs):
            if len(argv) > 1 and argv[1] == 'style':
                return subprocess.CompletedProcess(argv, 0, stdout='')
            raise OSError('no such file')

        with (
            patch('shutil.which', return_value='/fake/gum'),
            patch('sys.stdin.isatty', return_value=True),
            patch('sys.stdout.isatty', return_value=True),
            patch('tools.dev.try_vm.subprocess.run', side_effect=missing),
        ):
            with self.assertRaises(try_vm.GumMissingError):
                try_vm.gum_choose('Pick:', ['a'])
        with tempfile.TemporaryDirectory() as state:
            with (
                patch('shutil.which', return_value='/fake/gum'),
                patch('sys.stdin.isatty', return_value=True),
                patch('sys.stdout.isatty', return_value=True),
                patch('tools.dev.try_vm.subprocess.run', side_effect=missing),
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

    def test_checkout_changes_require_rebuild_regardless_of_timestamps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def git(*args, **kwargs):
                return check_git_output(*args, cwd=root, text=True, **kwargs).strip()

            git('init', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            (root / '.gitignore').write_text('*.iso*\n')
            tracked = root / 'tracked'
            tracked.write_text('base')
            git('add', '.')
            git('commit', '-m', 'base')
            old = git('rev-parse', 'HEAD')
            tracked.write_text('new')
            git('commit', '-am', 'new')
            iso = root / 'test.iso'
            iso.write_bytes(b'iso')
            source = iso.with_suffix('.iso.source')
            with patch.object(try_vm, 'ROOT', root):
                self.assertTrue(try_vm.checkout_changed_since(iso))
                source.write_text(try_vm.source_identity(try_vm.ROOT))
                self.assertFalse(try_vm.checkout_changed_since(iso))
                # Untracked files (notes, scratch) never make the ISO stale.
                (root / 'untracked').touch()
                self.assertFalse(try_vm.checkout_changed_since(iso))
                tracked.write_text('dirty')
                self.assertTrue(try_vm.checkout_changed_since(iso))
                # An ISO built from the dirty tree is current until the tree changes again.
                source.write_text(try_vm.source_identity(try_vm.ROOT))
                self.assertFalse(try_vm.checkout_changed_since(iso))
                tracked.write_text('dirtier')
                self.assertTrue(try_vm.checkout_changed_since(iso))
                git('checkout', '--', 'tracked')
                git('checkout', old)
                self.assertTrue(try_vm.checkout_changed_since(iso))

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
        with (
            tempfile.TemporaryDirectory() as temp,
            tempfile.TemporaryDirectory() as state,
            tempfile.TemporaryDirectory() as tmp,
        ):
            for name in ('test.iso', 'test-development.iso', 'OVMF_CODE.fd', 'OVMF_VARS.fd'):
                (Path(temp) / name).write_bytes(b'data')
            release_label = try_vm.SOURCE_LABELS[try_vm.RELEASE]
            bindir, replies = make_bin(
                tmp,
                [(0, 'New VM'), (0, 'demo'), (0, release_label), (0, '16'), (0, '60'), (1, '')],
                extra=[('qemu-img', QEMU_IMG_SCRIPT), ('qemu-system-x86_64', QEMU_SYSTEM_SCRIPT)],
            )
            ovmf = Path(temp)
            with (
                patch.dict(
                    os.environ,
                    {'TRY_OUT_DIR': temp, 'OVMF_DIR': str(ovmf), **gum_env(bindir, replies)},
                ),
                patch.object(try_vm, 'VM_SPACE', 1024),
                patch('sys.stdin.isatty', return_value=True),
                patch('sys.stdout.isatty', return_value=True),
                patch('os.access', return_value=True),
            ):
                try_vm.interactive(base=state)
                choices = try_vm.read_choices(Path(state) / 'demo')
                self.assertEqual(choices, {'ram_gib': 16, 'disk_gib': 60, 'source': try_vm.RELEASE})
                self.assertTrue((Path(state) / 'demo' / 'disk.qcow2').is_file())
            with tempfile.TemporaryDirectory() as tmp2:
                bindir2, replies2 = make_bin(tmp2, [(0, 'Delete a VM'), (0, 'demo'), (0, '')])
                with (
                    patch.dict(os.environ, gum_env(bindir2, replies2)),
                    patch('sys.stdin.isatty', return_value=True),
                    patch('sys.stdout.isatty', return_value=True),
                ):
                    try_vm.interactive(base=state)
                    self.assertFalse((Path(state) / 'demo').exists())

    def test_interactive_quit_without_vms(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as tmp:
            bindir, replies = make_bin(tmp, [(1, '')])
            with (
                patch.dict(os.environ, gum_env(bindir, replies)),
                patch('sys.stdin.isatty', return_value=True),
                patch('sys.stdout.isatty', return_value=True),
            ):
                try_vm.interactive(base=state)
                self.assertEqual(try_vm.list_vms(state), [])

    def test_delete_kept_without_confirm(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as tmp:
            directory = Path(state) / 'demo'
            directory.mkdir()
            (directory / 'disk.qcow2').write_bytes(b'disk')
            bindir, replies = make_bin(tmp, [(1, '')])
            with patch.dict(os.environ, gum_env(bindir, replies)):
                try_vm.delete_vm('demo', base=state)
            self.assertTrue(directory.is_dir())

    def test_delete_picker_cancel_keeps_vm(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as tmp:
            directory = Path(state) / 'demo'
            directory.mkdir()
            (directory / 'disk.qcow2').write_bytes(b'disk')
            bindir, replies = make_bin(tmp, [(0, 'Delete a VM'), (1, ''), (1, '')])
            with (
                patch.dict(os.environ, gum_env(bindir, replies)),
                patch('sys.stdin.isatty', return_value=True),
                patch('sys.stdout.isatty', return_value=True),
            ):
                try_vm.interactive(base=state)
            self.assertTrue(directory.is_dir())


if __name__ == '__main__':
    unittest.main()
