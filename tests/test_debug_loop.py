"""Kept-VM debugging loop: attach failures, socket dirs, console dispatch."""

from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verification import vm as verify
import verify_fast
import vm_console


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
                with self.assertRaisesRegex(RuntimeError, 'verify-fast --keep'):
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


class ConsoleDispatchTests(unittest.TestCase):
    def run_console(self, *argv):
        with patch.object(sys, 'argv', ['vm_console', *argv]), patch.object(vm_console, 'VM') as factory:
            factory.return_value.command.return_value = ''
            factory.return_value.probe.return_value = ''
            try:
                vm_console.main()
            except SystemExit as stop:
                return stop.code, factory.return_value
            return 0, factory.return_value

    def test_missing_vm_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / '.sockets').mkdir()
            argv = ['vm_console', '--directory', root, 'sh', 'true']
            with patch.object(vm_console.VM, 'attach', side_effect=RuntimeError('no kept VM')):
                with patch.object(sys, 'argv', argv):
                    with self.assertRaises(SystemExit) as stop:
                        vm_console.main()
            self.assertEqual(stop.exception.code, 1)

    def test_verb_mistakes_are_usage_errors(self):
        for argv in (['--directory', 'out/fast', 'sh'], ['--directory', 'out/fast', 'click', '2', '0.5']):
            with self.subTest(argv=argv):
                code, machine = self.run_console(*argv)
                self.assertEqual(code, 2)
                machine.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
