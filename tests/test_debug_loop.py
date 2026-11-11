"""Kept-VM debugging loop: attach failures, socket dirs, fixture choice, console output."""

from pathlib import Path
import contextlib
import io
import os
import pty
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verification import vm as verify
import verify_fast
import vm_console
import workspace


def kept_machine(directory):
    machine = verify.VM(directory, None, directory / 'disk.qcow2')
    machine.socket_directory = directory / '.sockets'
    machine.socket_directory.mkdir(exist_ok=True)
    return machine


class KeptSocketTests(unittest.TestCase):
    def test_stop_leaves_caller_chosen_socket_dir(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            machine = kept_machine(directory)
            try:
                machine.stop()
                self.assertTrue((directory / '.sockets').is_dir())
            finally:
                machine.log.close()

    def test_attach_fails_loudly_without_a_kept_vm(self):
        with tempfile.TemporaryDirectory() as root:
            machine = kept_machine(Path(root))
            try:
                with self.assertRaises(RuntimeError):
                    machine.attach()
            finally:
                machine.log.close()


class AttachedConsoleTests(unittest.TestCase):
    def test_expect_times_out_without_a_qemu_process(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            machine = verify.VM(directory, None, directory / 'disk.qcow2')
            reader, writer = socket.socketpair()
            reader.settimeout(0.25)
            machine.serial = reader
            machine.process = None
            try:
                with self.assertRaises(TimeoutError):
                    machine.expect('never-matches', timeout=0.5)
            finally:
                writer.close()
                reader.close()
                machine.serial = None
                machine.log.close()

    def test_wait_exit_returns_on_serial_eof_without_a_qemu_process(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            machine = verify.VM(directory, None, directory / 'disk.qcow2')
            reader, writer = socket.socketpair()
            reader.settimeout(0.25)
            machine.serial = reader
            machine.process = None
            writer.close()
            try:
                machine.wait_exit(timeout=5)
            finally:
                reader.close()
                machine.serial = None
                machine.log.close()


class KeptRunningGuardTests(unittest.TestCase):
    def test_live_qmp_socket_counts_as_running(self):
        with tempfile.TemporaryDirectory() as root:
            sockets = Path(root) / '.sockets'
            sockets.mkdir()
            listener = socket.socket(socket.AF_UNIX)
            try:
                listener.bind(str(sockets / 'qmp.sock'))
                listener.listen(1)
                self.assertTrue(verify_fast.kept_running(Path(root)))
            finally:
                listener.close()

    def test_stale_socket_file_counts_as_not_running(self):
        with tempfile.TemporaryDirectory() as root:
            sockets = Path(root) / '.sockets'
            sockets.mkdir()
            stale = socket.socket(socket.AF_UNIX)
            stale.bind(str(sockets / 'qmp.sock'))
            stale.close()
            try:
                self.assertFalse(verify_fast.kept_running(Path(root)))
            finally:
                (sockets / 'qmp.sock').unlink(missing_ok=True)

    def test_missing_sockets_counts_as_not_running(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(verify_fast.kept_running(Path(root)))


class FixtureSelectionTests(unittest.TestCase):
    def test_picks_newest_valid_and_ignores_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / 'registry'
            registry.mkdir()
            self.assertIsNone(workspace.newest_accepted_fixture(registry))
            old = root / 'old'
            old.mkdir()
            (old / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
            (old / 'disk.qcow2').write_bytes(b'old')
            new = root / 'new'
            new.mkdir()
            (new / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
            (new / 'disk.qcow2').write_bytes(b'new')
            # A failed run's disk is unlinked, so a marker alone must never select it.
            unlinked = root / 'unlinked'
            unlinked.mkdir()
            (unlinked / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
            for index, target in enumerate((old, new, unlinked)):
                (registry / str(index)).write_text(str(target) + '\n')
            (registry / 'gone').write_text(str(root / 'missing') + '\n')
            (registry / 'relative').write_text('relative/path\n')
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))
            os.utime(unlinked, (3000, 3000))
            self.assertEqual(workspace.newest_accepted_fixture(registry), new / 'disk.qcow2')
            self.assertEqual(workspace.accepted_fixtures(registry), [new, old])


class CleanOutputTests(unittest.TestCase):
    def test_drops_markers_and_escapes_but_keeps_shell_text(self):
        raw = (
            "bash -c '(\r\n> echo hi\r\n"
            "> verify_status=$?; printf '\"'\"'\\n__VERIFY_abc123__:%s\\n'\"'\"' \"$verify_status\"'\r\n"
            'hi "$HOME" \x1b[32mok\x1b[0m\r\n__VERIFY_abc123__:0\r\n'
        )
        cleaned = vm_console.clean_output(raw)
        self.assertIn("bash -c '(", cleaned)
        self.assertIn('hi "$HOME" ok', cleaned)
        self.assertNotIn('__VERIFY_', cleaned)
        self.assertNotIn('\x1b', cleaned)


class StubVM:
    """Stand-in for verification.vm.VM: echoes each script back with a completion marker."""

    def __init__(self):
        self.scripts = []
        self.attach_error = None
        self.socket_directory = None
        self.log = io.StringIO()

    def attach(self):
        if self.attach_error is not None:
            raise self.attach_error

    def command(self, script):
        self.scripts.append(script)
        return script + '\r\n__VERIFY_ab12cd34__:0\r\n'

    def stop(self):
        pass


class ConsoleCase(unittest.TestCase):
    """Run vm_console.main against StubVM; assert stdout and exit codes."""

    def setUp(self):
        self.vm = StubVM()
        self._real_vm = vm_console.VM
        vm_console.VM = self._stub

    def tearDown(self):
        vm_console.VM = self._real_vm

    def _stub(self, *args):
        return self.vm

    def kept_argv(self, temp, *verb):
        (Path(temp) / '.sockets').mkdir()
        return ['vm_console', '--directory', temp, *verb]

    def run_main(self, argv, stdin):
        showed = io.StringIO()
        old_argv, old_stdin = sys.argv, sys.stdin
        sys.argv, sys.stdin = argv, stdin
        try:
            with contextlib.redirect_stdout(showed):
                vm_console.main()
        finally:
            sys.argv, sys.stdin = old_argv, old_stdin
        return showed.getvalue()


class StdinScriptTests(ConsoleCase):
    def test_sh_reads_script_from_stdin(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self.run_main(self.kept_argv(temp, 'sh'), io.StringIO('echo "it\'s $HOME"\n'))
            self.assertEqual(self.vm.scripts, ['echo "it\'s $HOME"\n'])
            self.assertIn("it's $HOME", output)
            self.assertNotIn('__VERIFY_', output)

    def test_attach_failure_exits_nonzero(self):
        self.vm.attach_error = RuntimeError('no kept VM')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(SystemExit) as stop:
                self.run_main(self.kept_argv(temp, 'sh', 'true'), io.StringIO(''))
            self.assertEqual(stop.exception.code, 1)

    def test_empty_stdin_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(SystemExit) as stop:
                self.run_main(self.kept_argv(temp, 'sh'), io.StringIO('  \n'))
            self.assertEqual(stop.exception.code, 2)

    def test_tty_stdin_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as temp:
            argv = self.kept_argv(temp, 'sh')
            primary, replica = pty.openpty()
            try:
                with os.fdopen(replica, 'w') as stdin:
                    with self.assertRaises(SystemExit) as stop:
                        self.run_main(argv, stdin)
                self.assertEqual(stop.exception.code, 2)
            finally:
                os.close(primary)


if __name__ == '__main__':
    unittest.main()
