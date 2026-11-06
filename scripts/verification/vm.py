"""QEMU control, console transport and login for disposable acceptance VMs."""

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import struct
import time
import tempfile

PASSWORD = 'vm-verification-only'


class VM:
    def __init__(self, directory, iso, disk):
        self.directory = directory
        self.iso = iso
        self.disk = disk
        self.optical_interface = 'usb'
        self.graphics = 'virtio-vga-gl'
        self.disk_interface = 'virtio'
        self.network_model = 'virtio-net-pci'
        self.extra_args = []
        self.allow_reboot = False
        self.min_free_bytes = 0
        self.socket_directory = None
        self.process = None
        self.serial = None
        self.qmp = None
        self.transcript = ''
        self.read_cursor = 0
        self.log = (directory / 'serial.log').open('a')

    def control_path(self, name):
        # Linux limits Unix socket paths to 107 bytes, including deep worktrees.
        if self.socket_directory is None:
            self.socket_directory = tempfile.TemporaryDirectory(prefix='dux-vm-')
        return Path(self.socket_directory.name) / name

    def connect(self, name):
        path = self.control_path(name)
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

    def start(self, cdrom=True, network=False, software_display=False):
        sockets = self.control_path('serial.sock').parent
        firmware = Path(os.environ.get('OVMF_DIR', '/usr/share/edk2/x64'))
        pairs = [('OVMF_CODE.4m.fd', 'OVMF_VARS.4m.fd'), ('OVMF_CODE.fd', 'OVMF_VARS.fd')]
        pair = next(((firmware / code, firmware / variables) for code, variables in pairs
                     if (firmware / code).is_file() and (firmware / variables).is_file()), None)
        if pair is None:
            raise RuntimeError(f'OVMF code and variable-store firmware missing under {firmware}')
        code, template = pair
        variables = self.directory / 'OVMF_VARS.fd'
        if not variables.exists():
            shutil.copyfile(template, variables)
        render_node = Path(os.environ.get('QEMU_RENDER_NODE', '/dev/dri/renderD128'))
        if not software_display and not os.access(render_node, os.R_OK | os.W_OK):
            raise RuntimeError(f'Niri VM verification needs an accessible GPU render node: {render_node}')
        command = ['qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host',
                   '-m', '8192', '-smp', '4', '-display',
                   'none' if software_display else f'egl-headless,rendernode={render_node}',
                   '-vga', 'none', '-device', 'VGA' if software_display else self.graphics,
                   '-drive', f'if=pflash,format=raw,readonly=on,file={code}',
                   '-drive', f'if=pflash,format=raw,file={variables}',
                   '-drive', f'if=none,id=target-disk,format=qcow2,file={self.disk}',
                   '-device', ('virtio-blk-pci,drive=target-disk,bootindex=2' if self.disk_interface == 'virtio'
                               else 'ide-hd,drive=target-disk,bus=ide.0,bootindex=2'),
                   '-nic', f'user,model={self.network_model}' if network else 'none',
                   '-virtfs', f'local,path={self.directory},mount_tag=verify,security_model=none,id=verify',
                   '-serial', f'unix:{sockets}/serial.sock,server=on,wait=off',
                   '-qmp', f'unix:{sockets}/qmp.sock,server=on,wait=off']
        if not self.allow_reboot:
            command += ['-no-reboot']
        if cdrom:
            media_type = ',media=cdrom' if self.optical_interface == 'sata' else ''
            command += ['-drive', f'if=none,id=install-media,format=raw,readonly=on{media_type},file={self.iso}']
            if self.optical_interface == 'sata':
                command += ['-device', 'ide-cd,drive=install-media,bus=ide.1,bootindex=1']
            else:
                command += ['-device', 'qemu-xhci', '-device', 'usb-storage,drive=install-media,bootindex=1']
        command += self.extra_args
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
            if shutil.disk_usage(self.directory).free < self.min_free_bytes:
                raise RuntimeError('VM stopped before breaching reserved host disk headroom.')
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
        payload = f"(\nset -e\n{command}\n)\nverify_status=$?; printf '\\n{marker}:%s\\n' \"$verify_status\""
        self.send('bash -c ' + shlex.quote(payload) + '\n')
        match, output = self.expect(r'\r?\n' + marker + r':([0-9]+)\r?\n', timeout)
        if match.group(1) != '0':
            raise RuntimeError(f'Guest command failed ({match.group(1)}): {command}\n{output[-3000:]}')
        return output

    def mount_probes(self):
        self.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')

    def probe(self, module, *arguments, timeout=120):
        command = ['python', '-m', 'verification.' + module, *map(str, arguments)]
        return self.command('cd /run/verify && ' + shlex.join(command), timeout=timeout)

    def screenshot(self, name):
        path = self.directory / (name + '.png')
        try:
            self.qmp_command('screendump', {'filename': str(path), 'format': 'png'})
        except RuntimeError as error:
            detail = error.args[0]
            if not isinstance(detail, dict) or detail.get('desc') != 'no surface':
                raise
            # VirGL scanout can remain in a GPU texture with no QMP surface.
            # The desktop probe separately requires a real Niri screenshot.
            path.with_suffix('.unavailable.txt').write_text('QEMU GL backend has no CPU-readable surface.\n')
            return None
        return path

    def key(self, name):
        self.chord(name)

    def chord(self, *names):
        self.qmp_command('send-key', {'keys': [{'type': 'qcode', 'data': name} for name in names], 'hold-time': 50})

    def click(self, x, y):
        self.qmp_command('input-send-event', {'events': [
            {'type': 'abs', 'data': {'axis': 'x', 'value': round(x * 32767)}},
            {'type': 'abs', 'data': {'axis': 'y', 'value': round(y * 32767)}},
        ]})
        self.qmp_command('input-send-event', {'events': [{'type': 'btn', 'data': {'down': True, 'button': 'left'}}]})
        time.sleep(0.15)
        self.qmp_command('input-send-event', {'events': [{'type': 'btn', 'data': {'down': False, 'button': 'left'}}]})

    def type_graphical(self, value):
        for character in value:
            if character.islower() or character.isdigit():
                self.key(character)
            elif character == '-':
                self.key('minus')
            else:
                raise ValueError('Unsupported verification keyboard character.')
            time.sleep(0.08)

    def poweroff(self, timeout=180):
        self.send('systemctl poweroff\n')
        self.wait_exit(timeout)

    def wait_exit(self, timeout=180):
        deadline = time.monotonic() + timeout
        # Drain the console while waiting: shutdown can otherwise block when
        # the Unix socket's receive buffer fills with service/terminal output.
        while self.process.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError('VM did not exit; inspect serial.log.')
            try:
                data = self.serial.recv(65536)
                if data:
                    self.log.write(data.decode(errors='replace'))
                    self.log.flush()
                else:
                    self.process.wait(timeout=15)
            except socket.timeout:
                pass
        if self.process.returncode != 0:
            raise RuntimeError(f'QEMU exited abnormally during shutdown: {self.process.returncode}')

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
        if self.socket_directory is not None:
            self.socket_directory.cleanup()
            self.socket_directory = None
        if self.process is not None and self.process.returncode not in (None, 0):
            raise RuntimeError(f'QEMU exited abnormally: {self.process.returncode}')


def wait_for_password_screen(vm, name):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        path = vm.screenshot(name)
        if path is not None:
            text = subprocess.check_output(
                ['tesseract', str(path), 'stdout', '--psm', '11'],
                text=True, stderr=subprocess.DEVNULL)
            if 'passphrase' in text.lower():
                path.with_suffix('.txt').write_text(text)
                return
        time.sleep(1)
    raise TimeoutError('The disk-password prompt did not appear on screen.')


def click_screen_text(vm, path, phrase):
    # Chromium's share chooser can appear later or move with window geometry.
    # Locate its visible label instead of clicking a guessed screen coordinate.
    output = subprocess.check_output(
        ['tesseract', str(path), 'stdout', '--psm', '11', 'tsv'],
        text=True, stderr=subprocess.DEVNULL)
    words = [row for row in csv.DictReader(output.splitlines(), delimiter='\t')
             if row.get('text', '').strip()]
    target = phrase.casefold().split()
    width, height = struct.unpack('>II', path.read_bytes()[16:24])
    for index in range(len(words) - len(target) + 1):
        match = words[index:index + len(target)]
        if [word['text'].casefold() for word in match] != target:
            continue
        if len({(word['block_num'], word['par_num'], word['line_num']) for word in match}) != 1:
            continue
        left = min(int(word['left']) for word in match)
        right = max(int(word['left']) + int(word['width']) for word in match)
        top = min(int(word['top']) for word in match)
        bottom = max(int(word['top']) + int(word['height']) for word in match)
        vm.click((left + right) / 2 / width, (top + bottom) / 2 / height)
        return True
    return False


def installed_login(vm, keyboard_unlock=False, screenshot_tag=None):
    if keyboard_unlock:
        wait_for_password_screen(vm, screenshot_tag + '-prompt')
        vm.type_graphical('wrong-passphrase-retry')
        vm.key('ret')
        # Let LUKS finish its intentionally expensive password check, then
        # require the password screen again before attempting the correct key.
        time.sleep(3)
        wait_for_password_screen(vm, screenshot_tag + '-retry')
        vm.type_graphical(PASSWORD)
        vm.key('ret')
    else:
        vm.expect(r'(?i)(passphrase|password).*?:', timeout=180)
        if screenshot_tag is not None:
            wait_for_password_screen(vm, screenshot_tag)
        vm.send(PASSWORD + '\n')
    vm.expect(r'login:', timeout=180)
    vm.send('tester\n')
    vm.expect(r'Password:')
    vm.send(PASSWORD + '\n')
    # Login/PAM can flush queued input while restoring the terminal. Both
    # Bash and Fish enable bracketed paste when their interactive reader starts.
    input_ready = r'\x1b\[\?2004h'
    vm.expect(input_ready, timeout=60)
    vm.command('test "$(id -u)" = 1000')
    vm.expect(input_ready, timeout=60)
    # Personal setup may change the login shell or grant passwordless sudo.
    # Completion markers establish readiness without depending on either prompt.
    vm.command('printf "%s\\n" ' + shlex.quote(PASSWORD) + ' | sudo -S -p "" -v')
    vm.expect(input_ready, timeout=60)
    vm.send('sudo -n bash --noprofile --norc\n')
    vm.expect(input_ready, timeout=60)
    vm.command('test "$(id -u)" = 0')


def graphical_login(vm):
    vm.probe('desktop', 'greeter')
    # Pinned upstream's first-login user picker, on the VM's single display.
    vm.click(0.5, 0.5)
    time.sleep(0.3)
    vm.click(0.5, 0.443)
    time.sleep(0.3)
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.command('for attempt in $(seq 1 60); do pgrep -u tester -x niri >/dev/null && exit 0; sleep 1; done; exit 1', timeout=90)



def stage_probes(directory):
    """Copy one complete probe package so new scenarios cannot be omitted."""
    source = Path(__file__).parent
    shutil.copytree(source, directory / 'verification',
                    ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
    builder = source.parent / 'build-integration.py'
    shutil.copyfile(builder, directory / builder.name)
    paths = sorted(source.glob('*.py')) + [builder]
    return {str(path.relative_to(source.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}
