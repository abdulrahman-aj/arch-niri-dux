#!/usr/bin/env python3
"""Run the ISO in a UEFI VM without a network device; retain evidence locally."""

import argparse
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

    def start(self, cdrom=True, network=False):
        for name in ('serial.sock', 'qmp.sock'):
            (self.directory / name).unlink(missing_ok=True)
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
        if not os.access(render_node, os.R_OK | os.W_OK):
            raise RuntimeError(f'Niri VM verification needs an accessible GPU render node: {render_node}')
        command = ['qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host',
                   '-m', '8192', '-smp', '4', '-display', f'egl-headless,rendernode={render_node}',
                   '-vga', 'none', '-device', 'virtio-vga-gl',
                   '-drive', f'if=pflash,format=raw,readonly=on,file={code}',
                   '-drive', f'if=pflash,format=raw,file={variables}',
                   '-drive', f'if=virtio,format=qcow2,file={self.disk}',
                   '-nic', 'user,model=virtio-net-pci' if network else 'none', '-no-reboot',
                   '-virtfs', f'local,path={self.directory},mount_tag=verify,security_model=none,id=verify',
                   '-serial', f'unix:{self.directory}/serial.sock,server=on,wait=off',
                   '-qmp', f'unix:{self.directory}/qmp.sock,server=on,wait=off']
        if cdrom:
            command += ['-device', 'qemu-xhci',
                        '-drive', f'if=none,id=install-media,format=raw,readonly=on,file={self.iso}',
                        '-device', 'usb-storage,drive=install-media,bootindex=1']
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
        payload = f"(\nset -e\n{command}\n)\nverify_status=$?; printf '\\n{marker}:%s\\n' \"$verify_status\""
        self.send('bash -c ' + shlex.quote(payload) + '\n')
        match, output = self.expect(r'\r?\n' + marker + r':([0-9]+)\r?\n', timeout)
        if match.group(1) != '0':
            raise RuntimeError(f'Guest command failed ({match.group(1)}): {command}\n{output[-3000:]}')
        return output

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
        self.qmp_command('send-key', {'keys': [{'type': 'qcode', 'data': name}], 'hold-time': 50})

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
        if self.process is not None and self.process.returncode not in (None, 0):
            raise RuntimeError(f'QEMU exited abnormally: {self.process.returncode}')


def installed_login(vm):
    vm.expect(r'(?i)(passphrase|password).*?:', timeout=180)
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
    vm.command('python /run/verify/verify-guest.py greeter')
    # Pinned upstream's first-login user picker, on the VM's single display.
    vm.click(0.5, 0.5)
    time.sleep(0.3)
    vm.click(0.5, 0.443)
    time.sleep(0.3)
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.command('for attempt in $(seq 1 60); do pgrep -u tester -x niri >/dev/null && exit 0; sleep 1; done; exit 1', timeout=90)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--directory', type=Path, help='Fresh verification workspace; default under /tmp')
    parser.add_argument('--disk', type=Path, help='New disposable qcow2 path; default inside the workspace')
    parser.add_argument('--install-only', action='store_true', help='Debug only; does not produce acceptance success')
    parser.add_argument('--swap-gib', type=int, help='Override Auto swap sizing; 0 disables swap and hibernation')
    args = parser.parse_args()
    if args.swap_gib is not None and args.swap_gib < 0:
        parser.error('--swap-gib must be zero or a positive number of GiB.')
    images = sorted((ROOT / 'out').glob('*.iso'))
    iso = args.iso or (images[-1] if images else None)
    if iso is None or not iso.is_file():
        parser.error('Build an ISO first with make iso, or pass --iso PATH.')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('The VM verification requires access to /dev/kvm.')
    directory = args.directory or Path(tempfile.mkdtemp(prefix='dux-verify-', dir='/tmp'))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    disk = args.disk or directory / 'disk.qcow2'
    if disk.exists():
        parser.error('Use a fresh verification directory; refusing to overwrite an existing VM disk.')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '48G'], check=True)
    configuration = dict(disk='/dev/vda', username='tester', hostname='dux-verify',
                         timezone='UTC', keymap='us', passphrase=PASSWORD, password=PASSWORD)
    if args.swap_gib is not None:
        configuration['swap_gib'] = args.swap_gib
    config_file = directory / 'install.json'
    config_file.write_text(json.dumps(configuration))
    config_file.chmod(0o600)
    with iso.open('rb') as stream:
        iso_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    package_digest = hashlib.sha256((ROOT / 'out/packages.manifest.json').read_bytes()).hexdigest()
    (directory / 'inputs.json').write_text(json.dumps({
        'iso': str(iso.resolve()), 'iso_sha256': iso_digest,
        'package_manifest_sha256': package_digest,
    }, indent=2) + '\n')
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
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        # USB boot with enough RAM reproduces archiso's automatic-copy path.
        # The payload must stay on mounted media instead of disappearing.
        vm.command('grep -qw copytoram=n /proc/cmdline; mountpoint -q /run/archiso/bootmnt; test ! -e /run/archiso/copytoram/airootfs.sfs')
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('fastfetch --logo none --structure OS:Kernel')
        vm.command('test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "')
        vm.command(f'test "$(sha256sum /run/archiso/bootmnt/packages.manifest.json | cut -d " " -f 1)" = {package_digest}')
        for filename in ('install.py', 'configure-target.sh'):
            expected = hashlib.sha256((ROOT / 'installer' / filename).read_bytes()).hexdigest()
            vm.command(f'test "$(sha256sum /usr/local/lib/arch-niri-dux/installer/{filename} | cut -d " " -f 1)" = {expected}')
        installation_output = vm.command('arch-niri-install --config /run/verify/install.json --confirm-disk /dev/vda', timeout=2400)
        elapsed = re.search(r'INSTALLATION COMPLETE[^\n]*took ([0-9]+) seconds', installation_output)
        if elapsed is None:
            raise RuntimeError('Installer did not report its elapsed completion time.')
        (directory / 'install-time-seconds').write_text(elapsed.group(1) + '\n')
        passed('Offline encrypted installation completed')
        vm.command("cryptsetup open --key-file /run/verify/key /dev/vda2 dux-root; mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; mount /dev/vda1 /mnt/verify/boot")
        vm.command('python /run/verify/verify-guest.py prepare /mnt/verify', timeout=300)
        vm.command('umount -R /mnt/verify; cryptsetup close dux-root; sync')
        vm.stop()
        subprocess.run(['qemu-img', 'snapshot', '-c', 'pristine', str(disk)], check=True)
        if args.install_only:
            print('Install-only debug run complete. Full acceptance has NOT passed.', flush=True)
            return 0
        vm.start(cdrom=False)
        installed_login(vm)
        passed('UEFI boot from encrypted disk without installation ISO')
        vm.command('test -e /dev/mapper/dux-root; findmnt -n -o FSTYPE / | grep -x btrfs; test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "')
        vm.command('cat /proc/cmdline; ! grep -q /@snapshots/ /proc/cmdline; systemctl is-enabled greetd; systemctl is-active greetd; test -f /home/tester/.config/niri/config.kdl')
        vm.command('systemctl is-active NetworkManager; systemctl is-enabled bluetooth; command -v nvim chromium alacritty yay niri dms papers snapshot')
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify || mountpoint -q /run/verify')
        graphical_login(vm)
        vm.command('python /run/verify/verify-guest.py storage')
        passed('No-swap installation disables hibernation' if args.swap_gib == 0 else
               'Encrypted swap outside root snapshots; physical resume offset matches the kernel')
        vm.command('python /run/verify/verify-guest.py desktop', timeout=180)
        passed('Niri, Dux, terminal, Neovim and Chromium smoke checks')
        vm.command('python /run/verify/verify-guest.py dux-components', timeout=300)
        passed('GOP metrics, live file indexing, Calendar event create/read and Dux Files integration')
        vm.command('python /run/verify/verify-guest.py themes', timeout=300)
        passed('Live Dux light/dark changes reach Chromium, GTK settings, portals and Dux apps')
        vm.command('python /run/verify/verify-guest.py wallpaper', timeout=90)
        passed('Bloom material wallpaper is the fresh-user default and rendered')
        vm.command('python /run/verify/verify-guest.py wallpaper-preserved', timeout=180)
        passed('Real configure-desktop preserved an existing wallpaper choice')
        vm.command('python /run/verify/verify-guest.py pdf', timeout=180)
        passed('Papers is the default PDF viewer and rendered a sample document')
        vm.command('python /run/verify/verify-guest.py camera', timeout=300)
        passed('Snapshot preview verified with a synthetic PipeWire camera; no physical camera used')
        # Dismiss upstream's first-login What's New through its normal button.
        vm.click(0.57, 0.92)
        time.sleep(0.5)
        vm.command('python /run/verify/verify-guest.py focus-browser')
        vm.key('o')
        vm.command('python /run/verify/verify-guest.py file-chooser', timeout=90)
        vm.key('esc')
        vm.command('python /run/verify/verify-guest.py file-chooser-cancelled', timeout=90)
        passed('Chromium open-file request uses Dux Files and responds to cancellation')
        vm.command('python /run/verify/verify-guest.py focus-browser')
        vm.key('c')
        time.sleep(1)
        # Chromium first asks for the source type, then GNOME selects the
        # actual screen; Chromium finally confirms that selected source.
        vm.click(0.89, 0.284)
        vm.command('python /run/verify/verify-guest.py capture-picker', timeout=90)
        vm.click(0.725, 0.245)
        time.sleep(1)
        vm.click(0.935, 0.8)
        vm.command('python /run/verify/verify-guest.py capture', timeout=90)
        passed('Chromium screen sharing produced nonblank captured desktop content')
        vm.command('python /run/verify/verify-guest.py lock')
        vm.command('python /run/verify/verify-guest.py locked')
        vm.screenshot('03-locked')
        vm.type_graphical(PASSWORD)
        vm.key('ret')
        vm.command('for attempt in $(seq 1 30); do python /run/verify/verify-guest.py unlocked && exit 0; sleep 1; done; exit 1', timeout=60)
        passed('Screen lock and password unlock')
        vm.command('python /run/verify/verify-guest.py snapshots', timeout=300)
        passed('Automatic package snapshots created; recovery fixture prepared')
        vm.command('sync')
        vm.poweroff()
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('python /run/verify/verify-guest.py recovery', timeout=300)
        vm.screenshot('04-recovery')
        passed('Limine snapshot boot and restoration')
        vm.command('sync')
        vm.poweroff()
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('python /run/verify/verify-guest.py restored')
        vm.screenshot('05-restored')
        passed('Restored system rebooted normally and home was preserved')
        vm.stop()
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
