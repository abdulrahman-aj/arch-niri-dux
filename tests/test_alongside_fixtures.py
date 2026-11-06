"""Acceptance fixture contracts: fail closed on a boot without OS evidence."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('alongside_windows', Path(__file__).resolve().parents[1] / 'scripts/alongside_windows.py')
windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows)


class WindowsFixtureTests(unittest.TestCase):
    def test_unattended_install_keeps_a_small_esp_and_unallocated_region(self):
        root = ET.fromstring(windows.unattended())
        ns = {'u': 'urn:schemas-microsoft-com:unattend'}
        self.assertIn('create partition efi size=100', windows.DISKPART)
        self.assertIn('create partition primary size=24576', windows.DISKPART)
        self.assertNotIn('extend', windows.DISKPART)
        command = root.findtext('.//u:CommandLine', namespaces=ns)
        self.assertLess(len(command), 1024)
        self.assertEqual(command, r'cmd /c C:\setup-verify.cmd')
        ET.fromstring(windows.deployment_unattended())
        self.assertIn('dism /Apply-Image', windows.DEPLOY)
        self.assertIn('bcdboot W:\\Windows /s S: /f UEFI', windows.DEPLOY)
        self.assertIn('AlongsideBootEvidence', windows.SETUP_VERIFY)
        self.assertIn('powercfg /h off', windows.SETUP_VERIFY)
        self.assertIn('Get-CimInstance Win32_OperatingSystem', windows.powershell_script('fixture-token'))

    def test_reports_require_secret_route_real_windows11_build_and_sentinel(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(windows, 'PORT', 0):
            reports = windows.BootReports(Path(directory), 'fixture-token')
            self.addCleanup(reports.close)
            port = reports.server.server_port
            valid = dict(caption='Microsoft Windows 11 Enterprise LTSC Evaluation', build='26100', sentinel='preserve-existing-windows')
            for token, body, status in [('wrong', valid, 403), ('fixture-token', valid | {'caption': 'EFI stub'}, 422),
                                        ('fixture-token', valid | {'build': '19045'}, 422),
                                        ('fixture-token', valid | {'sentinel': 'missing'}, 422)]:
                request = urllib.request.Request(f'http://127.0.0.1:{port}/{token}', data=json.dumps(body).encode())
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertEqual(caught.exception.code, status)
            request = urllib.request.Request(f'http://127.0.0.1:{port}/fixture-token', data=json.dumps(valid).encode())
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(reports.reports.get_nowait(), valid)

class WindowsTPMRestartTests(unittest.TestCase):
    def test_a_second_boot_restarts_the_emulator_and_retains_its_state_directory(self):
        import shutil
        import socket
        self.assertIsNotNone(shutil.which('swtpm'), 'Run tests through the project Nix shell.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vm = windows.WindowsVM(root, root / 'unused.iso', root / 'unused.qcow2')
            sentinel = vm.tpm_state / 'preserved-state-sentinel'
            sentinel.write_text('retain fixture state')
            pids = []

            # Match the keyword interface of VM.start in this test double.
            def boot(instance, cdrom=True, network=False, software_display=False):  # noqa: ARG001
                connection = socket.socket(socket.AF_UNIX)
                try:
                    connection.connect(str(instance.control_path('tpm.sock')))
                    pids.append(instance.tpm_process.pid)
                finally:
                    connection.close()

            try:
                with patch.object(windows.VM, 'start', new=boot):
                    vm.start()
                    # A QEMU shutdown also ends the connected TPM emulator.
                    vm.tpm_process.terminate()
                    vm.tpm_process.wait(timeout=10)
                    vm.start()
                self.assertEqual(len(set(pids)), 2)
                self.assertEqual(sentinel.read_text(), 'retain fixture state')
            finally:
                vm.stop()
                vm.log.close()
