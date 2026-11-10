"""Kept-VM debugging loop: attach failures, socket dirs, console dispatch."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verification import vm as verify
import vm_console


def kept_machine(directory):
    machine = verify.VM(directory, None, directory / 'disk.qcow2')
    machine.socket_directory = directory / '.sockets'
    machine.socket_directory.mkdir(exist_ok=True)
    return machine


class KeptSocketTests(unittest.TestCase):
    def test_sockets_live_under_a_caller_chosen_directory(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            machine = kept_machine(directory)
            try:
                self.assertEqual(machine.control_path('qmp.sock'), directory / '.sockets' / 'qmp.sock')
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

    def test_missing_vm_exits_nonzero_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as root:
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

    def test_verbs_reach_the_matching_vm_method(self):
        code, machine = self.run_console('--directory', 'out/fast', 'sh', 'echo', 'hi')
        self.assertEqual(code, 0)
        machine.command.assert_called_once_with('echo hi')
        code, machine = self.run_console('--directory', 'out/fast', 'probe', 'dux_update', 'offer')
        self.assertEqual(code, 0)
        machine.probe.assert_called_once_with('dux_update', 'offer')
        machine.stop.assert_called()


if __name__ == '__main__':
    unittest.main()
