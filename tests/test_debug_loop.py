"""Kept-VM debugging loop: attach failures, socket dirs, fixture choice, console output."""

from pathlib import Path
import os
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
            old = root / 'old'
            old.mkdir()
            (old / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
            (old / 'disk.qcow2').write_bytes(b'old')
            new = root / 'new'
            new.mkdir()
            (new / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
            (new / 'disk.qcow2').write_bytes(b'new')
            broken = root / 'broken'
            broken.mkdir()
            (broken / 'disk.qcow2').write_bytes(b'no marker')
            registry = root / 'registry'
            registry.mkdir()
            for index, target in enumerate((old, new, broken)):
                (registry / str(index)).write_text(str(target) + '\n')
            (registry / 'gone').write_text(str(root / 'missing') + '\n')
            (registry / 'relative').write_text('relative/path\n')
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))
            os.utime(broken, (3000, 3000))
            self.assertEqual(workspace.newest_accepted_fixture(registry), new / 'disk.qcow2')
            self.assertEqual(workspace.accepted_fixtures(registry), [new, old])

    def test_no_accepted_fixture_is_none(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(workspace.newest_accepted_fixture(Path(temp) / 'missing'))
            self.assertEqual(workspace.accepted_fixtures(Path(temp) / 'missing'), [])


class CleanOutputTests(unittest.TestCase):
    def test_strips_marker_line_and_terminal_escapes(self):
        output = 'hello \x1b[32mworld\x1b[0m\r\necho done\r\n__VERIFY_abc123__:0\r\n'
        self.assertEqual(vm_console.clean_output(output), 'hello world\necho done')

    def test_keeps_quotes_and_dollar_expansion(self):
        output = 'it\'s "$HOME"\r\n__VERIFY_deadbeef00__:0\r\n'
        self.assertEqual(vm_console.clean_output(output), 'it\'s "$HOME"')


if __name__ == '__main__':
    unittest.main()
