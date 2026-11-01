#!/usr/bin/env python3
"""Run the ISO in a UEFI VM without a network device; retain evidence locally."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'vm-verification-only'


class VM:
    def __init__(self, directory, iso, disk):
        self.directory = directory
        self.iso = iso
        self.disk = disk
        self.process = None
        self.serial = None
        self.qmp = None
        self.transcript = ''
        self.read_cursor = 0
        self.log = (directory / 'serial.log').open('a')

    def connect(self, name):
        path = self.directory / name
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                connection = socket.socket(socket.AF_UNIX)
                connection.connect(str(path))
                connection.settimeout(0.25)
                return connection
            except (FileNotFoundError, ConnectionRefusedError):
                connection.close()
                if self.process.poll() is not None:
                    raise RuntimeError('QEMU exited; inspect qemu.log.')
                time.sleep(0.1)
        raise TimeoutError(f'QEMU socket did not appear: {path}')

    def start(self, cdrom=True):
        for name in ('serial.sock', 'qmp.sock'):
            (self.directory / name).unlink(missing_ok=True)
        firmware = Path(os.environ.get('OVMF_DIR', '/usr/share/edk2/x64'))
        code = firmware / 'OVMF_CODE.4m.fd'
        variables = self.directory / 'OVMF_VARS.fd'
        if not variables.exists():
            shutil.copyfile(firmware / 'OVMF_VARS.4m.fd', variables)
        command = ['qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host',
                   '-m', '4096', '-smp', '4', '-display', 'none', '-vga', 'virtio',
                   '-drive', f'if=pflash,format=raw,readonly=on,file={code}',
                   '-drive', f'if=pflash,format=raw,file={variables}',
                   '-drive', f'if=virtio,format=qcow2,file={self.disk}',
                   '-nic', 'none', '-no-reboot',
                   '-virtfs', f'local,path={self.directory},mount_tag=verify,security_model=none,id=verify',
                   '-serial', f'unix:{self.directory}/serial.sock,server=on,wait=off',
                   '-qmp', f'unix:{self.directory}/qmp.sock,server=on,wait=off']
        if cdrom:
            command += ['-cdrom', str(self.iso), '-boot', 'order=d']
        (self.directory / ('qemu-install.json' if cdrom else 'qemu-boot.json')).write_text(json.dumps(command, indent=2))
        self.process = subprocess.Popen(command, stdout=(self.directory / 'qemu.log').open('a'), stderr=subprocess.STDOUT)
        self.serial = self.connect('serial.sock')
        qmp_socket = self.connect('qmp.sock')
        qmp_socket.settimeout(30)
        self.qmp = qmp_socket.makefile('rwb', buffering=0)
        self.qmp.readline()
        self.qmp_command('qmp_capabilities')
        self.transcript = ''
        self.read_cursor = 0

    def qmp_command(self, command, arguments=None):
        self.qmp.write(json.dumps({'execute': command, 'arguments': arguments or {}}).encode() + b'\n')
        while True:
            result = json.loads(self.qmp.readline())
            if 'error' in result:
                raise RuntimeError(result['error'])
            if 'return' in result:
                return result['return']

    def send(self, value):
        self.serial.sendall(value.encode())

    def expect(self, pattern, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = re.search(pattern, self.transcript[self.read_cursor:])
            if match:
                beginning = self.read_cursor
                self.read_cursor += match.end()
                return match, self.transcript[beginning:self.read_cursor]
            try:
                data = self.serial.recv(65536)
                if not data:
                    raise RuntimeError('VM serial connection closed unexpectedly.')
                text = data.decode(errors='replace')
                self.transcript += text
                self.log.write(text)
                self.log.flush()
            except socket.timeout:
                if self.process.poll() is not None:
                    raise RuntimeError('QEMU exited; inspect serial.log and qemu.log.')
        raise TimeoutError(f'VM did not produce {pattern!r}; inspect serial.log.')

    def command(self, command, timeout=120):
        marker = '__VERIFY_' + secrets.token_hex(8) + '__'
        payload = f"{command}\nverify_status=$?; printf '\\n{marker}:%s\\n' \"$verify_status\""
        self.send('bash -c ' + shlex.quote(payload) + '\n')
        match, output = self.expect(r'\r?\n' + marker + r':([0-9]+)\r?\n', timeout)
        if match.group(1) != '0':
            raise RuntimeError(f'Guest command failed ({match.group(1)}): {command}\n{output[-3000:]}')
        return output

    def screenshot(self, name):
        path = self.directory / (name + '.png')
        self.qmp_command('screendump', {'filename': str(path), 'format': 'png'})
        return path

    def key(self, name):
        self.qmp_command('send-key', {'keys': [{'type': 'qcode', 'data': name}], 'hold-time': 50})

    def type_graphical(self, value):
        for character in value:
            if character.islower() or character.isdigit():
                self.key(character)
            elif character == '-':
                self.key('minus')
            else:
                raise ValueError('Unsupported verification keyboard character.')
            time.sleep(0.08)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.serial:
            self.serial.close()
        if self.qmp:
            self.qmp.close()


def installed_login(vm):
    vm.expect(r'(?i)(passphrase|password).*?:', timeout=180)
    vm.send(PASSWORD + '\n')
    vm.expect(r'login:', timeout=180)
    vm.send('tester\n')
    vm.expect(r'Password:')
    vm.send(PASSWORD + '\n')
    vm.command('test "$(id -u)" = 1000')
    vm.send('sudo -s\n')
    vm.expect(r'(?i)password.*:')
    vm.send(PASSWORD + '\n')
    vm.command('test "$(id -u)" = 0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--directory', type=Path, help='Fresh verification workspace; default under /tmp')
    parser.add_argument('--install-only', action='store_true', help='Debug only; does not produce acceptance success')
    args = parser.parse_args()
    images = sorted((ROOT / 'out').glob('*.iso'))
    iso = args.iso or (images[-1] if images else None)
    if iso is None or not iso.is_file():
        parser.error('Build an ISO first with make iso, or pass --iso PATH.')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('The VM verification requires access to /dev/kvm.')
    directory = args.directory or Path(tempfile.mkdtemp(prefix='dux-verify-', dir='/tmp'))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (directory / 'disk.qcow2').exists():
        parser.error('Use a fresh verification directory; refusing to overwrite an existing VM disk.')
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '48G'], check=True)
    configuration = dict(disk='/dev/vda', username='tester', hostname='dux-verify',
                         timezone='UTC', keymap='us', passphrase=PASSWORD, password=PASSWORD)
    config_file = directory / 'install.json'
    config_file.write_text(json.dumps(configuration))
    config_file.chmod(0o600)
    (directory / 'key').write_text(PASSWORD)
    (directory / 'key').chmod(0o600)
    shutil.copyfile(Path(__file__).with_name('verify-guest.py'), directory / 'verify-guest.py')
    vm = VM(directory, iso.resolve(), disk)
    milestones = []
    def passed(name):
        milestones.append(name)
        (directory / 'milestones.json').write_text(json.dumps(milestones, indent=2))
        print('PASS: ' + name, flush=True)
    try:
        print(f'VM evidence: {directory}', flush=True)
        vm.start()
        match, _ = vm.expect(r'(root@|login:)', timeout=180)
        if match.group(1) == 'login:':
            vm.send('root\n')
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "')
        vm.command('arch-niri-install --config /run/verify/install.json --confirm-disk /dev/vda', timeout=2400)
        passed('Offline encrypted installation completed')
        vm.command("cryptsetup open --key-file /run/verify/key /dev/vda2 dux-root; mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; mount /dev/vda1 /mnt/verify/boot")
        vm.command('python /run/verify/verify-guest.py prepare /mnt/verify', timeout=300)
        vm.command('umount -R /mnt/verify; cryptsetup close dux-root; sync')
        vm.stop()
        if args.install_only:
            print('Install-only debug run complete. Full acceptance has NOT passed.', flush=True)
            return 0
        vm.start(cdrom=False)
        installed_login(vm)
        passed('UEFI boot from encrypted disk without installation ISO')
        vm.command('test -e /dev/mapper/dux-root; findmnt -n -o FSTYPE / | grep -x btrfs; test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "')
        vm.command('systemctl is-active NetworkManager bluetooth; command -v nvim chromium alacritty yay niri dms')
        vm.screenshot('01-greeter')
        vm.type_graphical('tester')
        vm.key('ret')
        time.sleep(0.5)
        vm.type_graphical(PASSWORD)
        vm.key('ret')
        vm.command('for attempt in $(seq 1 60); do pgrep -u tester -x niri >/dev/null && exit 0; sleep 1; done; exit 1', timeout=90)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify || mountpoint -q /run/verify')
        vm.command('python /run/verify/verify-guest.py desktop', timeout=180)
        vm.screenshot('02-desktop-apps')
        passed('Niri, Dux, terminal, Neovim and Chromium smoke checks')
        vm.command('python /run/verify/verify-guest.py lock')
        vm.command('python /run/verify/verify-guest.py locked')
        vm.screenshot('03-locked')
        vm.type_graphical(PASSWORD)
        vm.key('ret')
        vm.command('for attempt in $(seq 1 30); do python /run/verify/verify-guest.py unlocked && exit 0; sleep 1; done; exit 1', timeout=60)
        passed('Screen lock and password unlock')
        vm.command('python /run/verify/verify-guest.py snapshots', timeout=300)
        passed('Automatic package snapshots created; recovery fixture prepared')
        vm.command('sync; systemctl poweroff')
        vm.process.wait(timeout=60)
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('python /run/verify/verify-guest.py recovery', timeout=300)
        vm.screenshot('04-recovery')
        passed('Limine snapshot boot and restoration')
        vm.command('sync; systemctl poweroff')
        vm.process.wait(timeout=60)
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('python /run/verify/verify-guest.py restored')
        vm.screenshot('05-restored')
        passed('Restored system rebooted normally and home was preserved')
        (directory / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
        print(f'Acceptance passed. Evidence: {directory}', flush=True)
        return 0
    except Exception as error:
        try:
            if vm.process and vm.process.poll() is None:
                vm.screenshot('failure')
        except Exception:
            pass
        (directory / 'FAILURE.txt').write_text(str(error) + '\n')
        print(f'Verification failed: {error}\nEvidence: {directory}', file=sys.stderr)
        return 1
    finally:
        vm.stop()


if __name__ == '__main__':
    sys.exit(main())
