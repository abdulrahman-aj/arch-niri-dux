from pathlib import Path
import re
import socket
import secrets
import subprocess
import tempfile
import unittest

from scripts.verification import vm as verify


class VMCommandTests(unittest.TestCase):
    def test_control_transport_works_with_deep_evidence_paths_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / ('deep-worktree-' * 10)
            evidence.mkdir()
            machine = verify.VM(evidence, None, evidence / 'unused.qcow2')
            address = machine.control_path('test.sock')
            try:
                with socket.socket(socket.AF_UNIX) as server, socket.socket(socket.AF_UNIX) as client:
                    server.bind(str(address))
                    server.listen(1)
                    client.connect(str(address))
                    connection, _ = server.accept()
                    with connection:
                        payload = secrets.token_bytes(16)
                        connection.sendall(payload)
                        self.assertEqual(client.recv(len(payload)), payload)
                machine.stop()
                with socket.socket(socket.AF_UNIX) as client:
                    with self.assertRaises(FileNotFoundError):
                        client.connect(str(address))
            finally:
                machine.stop()
                machine.log.close()

    def test_login_waits_for_terminal_input_before_commands_and_sudo_shell(self):
        class LoginTerminal:
            ready = False
            root_requested = False
            root_ready = False

            def expect(self, pattern, timeout=120):
                if re.search(pattern, '\x1b[?2004h'):
                    self.ready = True
                    self.root_ready = self.root_requested

            def send(self, value):
                # Login/PAM and sudo can flush input before the shell reader
                # starts. A prompt-independent login still needs this barrier.
                self.ready = False
                if value.startswith('sudo -n bash'):
                    self.root_requested = True

            def command(self, command):
                if not self.ready:
                    raise AssertionError('The command was sent before the terminal could read it.')
                self.ready = False

        terminal = LoginTerminal()
        verify.installed_login(terminal)
        self.assertTrue(terminal.root_ready)

    def run_command(self, command):
        with tempfile.TemporaryDirectory() as temporary:
            vm = verify.VM(Path(temporary), Path('unused.iso'), Path('unused.qcow2'))
            output = []

            def send(value):
                result = subprocess.run(
                    ['bash', '-c', value], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
                )
                output.append(result.stdout)

            # VM.command supplies timeout by keyword; the test responds immediately.
            def expect(pattern, timeout):  # noqa: ARG001
                result = ''.join(output)
                match = re.search(pattern, result)
                if match is None:
                    self.fail('Guest command exited without producing its completion status.')
                return match, result

            vm.send = send
            vm.expect = expect
            try:
                return vm.command(command)
            finally:
                vm.log.close()

    def test_explicit_successful_exit_still_produces_completion(self):
        self.run_command('for attempt in 1 2; do true && exit 0; done; exit 1')

    def test_explicit_failure_is_reported_without_waiting_for_a_missing_marker(self):
        with self.assertRaisesRegex(RuntimeError, r'Guest command failed \(7\)'):
            self.run_command('exit 7')

    def test_command_output_and_quotes_survive_transport(self):
        self.assertIn("quoted ' result", self.run_command('printf "quoted \' result\\n"'))

    def test_failed_check_cannot_be_hidden_by_later_success(self):
        with self.assertRaisesRegex(RuntimeError, r'Guest command failed \(1\)'):
            self.run_command('false; true')

    def test_abnormal_vm_exit_is_a_verification_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            vm = verify.VM(Path(temporary), None, Path('unused.qcow2'))
            vm.process = subprocess.Popen(['bash', '-c', 'exit 7'])
            vm.process.wait()
            try:
                with self.assertRaisesRegex(RuntimeError, 'QEMU exited abnormally: 7'):
                    vm.stop()
            finally:
                vm.log.close()


if __name__ == '__main__':
    unittest.main()


class VMDiskBudgetTests(unittest.TestCase):
    def test_low_host_space_stops_a_guest_wait_before_more_work(self):
        from unittest.mock import patch
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temporary:
            vm = verify.VM(Path(temporary), None, Path('unused.qcow2'))
            vm.min_free_bytes = 8 * 1024**3
            try:
                with patch.object(verify.shutil, 'disk_usage', return_value=SimpleNamespace(free=1024)):
                    with self.assertRaisesRegex(RuntimeError, 'headroom'):
                        vm.expect('never', timeout=1)
            finally:
                vm.log.close()
