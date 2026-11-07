"""Windows fixture guest reporting over QEMU's host-only user network."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import threading
import time
import shutil
import subprocess

from verification.vm import VM
from xml.sax.saxutils import escape

PORT = 18769


class WindowsVM(VM):
    """Keep the same TPM state while restarting its emulator for each QEMU run."""

    def __init__(self, directory, iso, disk, tpm_source=None):
        super().__init__(directory, iso, disk)
        self.optical_interface = 'sata'
        self.graphics = 'VGA'
        self.disk_interface = 'ide'
        self.network_model = 'e1000e'
        self.min_free_bytes = 9 * 1024**3
        self.tpm_process = None
        self.tpm_state = directory / 'tpm'
        if tpm_source is not None:
            shutil.copytree(tpm_source, self.tpm_state)
        else:
            self.tpm_state.mkdir(exist_ok=True)

    def stop_tpm(self):
        if self.tpm_process is not None:
            if self.tpm_process.poll() is None:
                self.tpm_process.terminate()
            self.tpm_process.wait(timeout=10)
            self.tpm_process = None

    def start(self, cdrom=True, network=False, software_display=False):
        self.stop_tpm()
        control = self.control_path('tpm.sock')
        control.unlink(missing_ok=True)
        self.tpm_process = subprocess.Popen(
            [
                'swtpm',
                'socket',
                '--tpm2',
                '--tpmstate',
                f'dir={self.tpm_state}',
                '--ctrl',
                f'type=unixio,path={control}',
                '--flags',
                'not-need-init',
            ]
        )
        for _attempt in range(100):
            if control.exists():
                break
            if self.tpm_process.poll() is not None:
                raise RuntimeError('TPM emulator exited before its control socket appeared.')
            time.sleep(0.1)
        tpm_args = [
            '-chardev',
            f'socket,id=chrtpm,path={control}',
            '-tpmdev',
            'emulator,id=tpm0,chardev=chrtpm',
            '-device',
            'tpm-tis,tpmdev=tpm0',
        ]
        saved = self.extra_args
        self.extra_args = saved + tpm_args
        try:
            super().start(cdrom=cdrom, network=network, software_display=software_display)
        finally:
            self.extra_args = saved

    def stop(self):
        try:
            super().stop()
        finally:
            self.stop_tpm()


class BootReports:
    def __init__(self, directory, token):
        self.directory = directory
        self.token = token
        self.reports = queue.Queue()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != '/' + owner.token:
                    self.send_error(403)
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if length > 65536:
                    self.send_error(413)
                    return
                report = json.loads(self.rfile.read(length))
                if (
                    'Windows 11' not in report.get('caption', '')
                    or int(report.get('build', '0')) < 22000
                    or report.get('sentinel') != 'preserve-existing-windows'
                ):
                    self.send_error(422)
                    return
                owner.reports.put(report)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait(self, vm, name, timeout=1200, install=False):
        deadline = time.monotonic() + timeout
        last_key = 0
        last_screenshot = 0
        while time.monotonic() < deadline:
            if shutil.disk_usage(self.directory).free < 9 * 1024**3:
                raise RuntimeError('Stopped Windows VM before breaching 8 GiB host headroom.')
            if time.monotonic() - last_screenshot > 30:
                vm.screenshot(name + '-progress')
                last_screenshot = time.monotonic()
            try:
                report = self.reports.get(timeout=1)
                (self.directory / (name + '.json')).write_text(json.dumps(report, indent=2))
                vm.screenshot(name)
                vm.wait_exit(timeout=180)
                return report
            except queue.Empty:
                if vm.process.poll() is not None:
                    raise RuntimeError('Windows VM exited without reporting a successful real OS boot.')
                # The Windows optical boot loader requires a key on first boot.
                if install and time.monotonic() - last_key > 1 and deadline - time.monotonic() > timeout - 12:
                    vm.key('ret')
                    last_key = time.monotonic()
        vm.screenshot(name + '-timeout')
        raise TimeoutError('Windows did not report; inspect QEMU screenshot and setup evidence.')

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def powershell_script(token):
    return f"""$ErrorActionPreference = 'Stop'
$o = Get-CimInstance Win32_OperatingSystem
$s = (Get-Content C:\\alongside-sentinel -Raw).Trim()
$r = @{{ caption=$o.Caption; build=$o.BuildNumber; version=$o.Version; sentinel=$s; boot=[guid]::NewGuid().ToString(); partitions=@(Get-Partition | Select-Object PartitionNumber,Size,Offset,Type) }} | ConvertTo-Json -Depth 5
for ($i=0; $i -lt 120; $i++) {{
  try {{ Invoke-RestMethod -Method Post -Uri 'http://10.0.2.2:{PORT}/{token}' -ContentType 'application/json' -Body $r; shutdown.exe /s /t 3; exit 0 }}
  catch {{ Start-Sleep -Seconds 2 }}
}}
exit 1
"""


def unattended():
    command = r'cmd /c C:\setup-verify.cmd'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
<settings pass="specialize"><component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><ComputerName>alongside-win11</ComputerName></component>
<component name="Microsoft-Windows-SecureStartup-FilterDriver" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><PreventDeviceEncryption>true</PreventDeviceEncryption></component></settings>
<settings pass="oobeSystem"><component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale></component>
<component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
<OOBE><HideEULAPage>true</HideEULAPage><HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><ProtectYourPC>3</ProtectYourPC></OOBE>
<UserAccounts><LocalAccounts><LocalAccount wcm:action="add"><Name>tester</Name><Group>Administrators</Group><Password><Value>vm-verification-only</Value><PlainText>true</PlainText></Password></LocalAccount></LocalAccounts></UserAccounts>
<AutoLogon><Enabled>true</Enabled><LogonCount>2</LogonCount><Username>tester</Username><Password><Value>vm-verification-only</Value><PlainText>true</PlainText></Password></AutoLogon>
<FirstLogonCommands><SynchronousCommand wcm:action="add"><Order>1</Order><Description>Record real Windows boot and preserve sentinel</Description><CommandLine>{escape(command)}</CommandLine></SynchronousCommand></FirstLogonCommands>
</component></settings></unattend>"""


def deployment_unattended():
    command = r'cmd /c for %d in (D E F G H I J K L M N O P) do if exist %d:\deploy.cmd call %d:\deploy.cmd'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
<settings pass="windowsPE"><component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><RunSynchronous><RunSynchronousCommand wcm:action="add"><Order>1</Order><Path>{escape(command)}</Path></RunSynchronousCommand></RunSynchronous></component></settings></unattend>"""


DISKPART = """select disk 0
clean
convert gpt
create partition efi size=100
format quick fs=fat32 label=ExistingESP
assign letter=S
create partition msr size=16
create partition primary size=24576
format quick fs=ntfs label=Windows
assign letter=W
exit
"""

DEPLOY = r"""@echo off
wpeinit
for %%d in (D E F G H I J K L M N O P) do if exist %%d:\sources\install.wim set MEDIA=%%d:
if not defined MEDIA goto fail
echo DEPLOYING_REAL_WINDOWS11>COM1
diskpart /s "%~dp0diskpart.txt" || goto fail
dism /Apply-Image /ImageFile:%MEDIA%\sources\install.wim /Index:1 /ApplyDir:W:\ /Compact || goto fail
mkdir W:\Windows\Panther
copy "%~dp0installed-unattend.xml" W:\Windows\Panther\unattend.xml || goto fail
copy "%~dp0verify-boot.ps1" W:\verify-boot.ps1 || goto fail
copy "%~dp0setup-verify.cmd" W:\setup-verify.cmd || goto fail
echo preserve-existing-windows>W:\alongside-sentinel
bcdboot W:\Windows /s S: /f UEFI || goto fail
echo WINDOWS_IMAGE_APPLIED>COM1
wpeutil reboot
exit /b
:fail
echo WINDOWS_DEPLOY_FAILED>COM1
wpeutil shutdown
"""

SETUP_VERIFY = r"""@echo off
powercfg /h off
schtasks /Create /F /TN AlongsideBootEvidence /SC ONSTART /RU SYSTEM /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\verify-boot.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\verify-boot.ps1
"""
