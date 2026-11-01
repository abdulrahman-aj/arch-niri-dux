import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('verify_vm', Path(__file__).resolve().parents[1] / 'scripts/verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VMCommandTests(unittest.TestCase):
    def run_command(self, command):
        with tempfile.TemporaryDirectory() as temporary:
            vm = verify.VM(Path(temporary), Path('unused.iso'), Path('unused.qcow2'))
            output = []
            def send(value):
                result = subprocess.run(['bash', '-c', value], text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, check=False)
                output.append(result.stdout)
            def expect(pattern, timeout):
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


if __name__ == '__main__':
    unittest.main()
