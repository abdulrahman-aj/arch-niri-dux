#!/usr/bin/env python3
"""Focused real-Linux fixture acceptance; serial-console fixture contract in ALONGSIDE.md."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from verify import VM, PASSWORD, installed_login

ROOT = Path(__file__).resolve().parents[1]


def old_linux_login(vm):
    vm.expect(r'login:', timeout=240)
    vm.send('tester\n')
    vm.expect(r'Password:')
    vm.send(PASSWORD + '\n')
    vm.expect(r'\x1b\[\?2004h', timeout=60)
    vm.command('test -f /etc/debian_version; test "$(cat /var/lib/alongside-sentinel)" = preserve-existing-linux')
    vm.send('sudo -i\n')
    vm.command('test "$(id -u)" = 0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if shutil.disk_usage(args.directory.parent).free < 20 * 1024**3:
        parser.error('Need 20 GiB free to leave at least 8 GiB headroom during this one Linux scenario.')
    args.directory.mkdir(mode=0o700)  # Never reuse evidence or fixture overlays.
    directory = args.directory.resolve()
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b',
                    str(args.fixture.resolve()), str(disk)], check=True)
    shutil.copyfile(args.firmware, directory / 'OVMF_VARS.fd')
    inputs = {}
    for name, path in [('iso', args.iso), ('fixture', args.fixture), ('firmware', args.firmware)]:
        with path.open('rb') as stream:
            inputs[name] = dict(path=str(path.resolve()), sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
    (directory / 'inputs.json').write_text(json.dumps(inputs, indent=2))
    vm = VM(directory, args.iso.resolve(), disk)
    milestones = []

    def passed(message):
        milestones.append(message)
        (directory / 'milestones.json').write_text(json.dumps(milestones, indent=2))
        print('PASS: ' + message, flush=True)

    try:
        vm.start(cdrom=False)
        old_linux_login(vm)
        passed('Real existing Debian boots before installation and sentinel is intact')
        vm.poweroff()
        vm.start()
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        for filename in ('install.py', 'configure-target.sh'):
            digest = hashlib.sha256((ROOT / 'installer' / filename).read_bytes()).hexdigest()
            vm.command(f'test "$(sha256sum /usr/local/lib/arch-niri-dux/installer/{filename} | cut -d " " -f 1)" = {digest}')
        # Plan through the ISO's own read-only inventory. The fixture must have
        # exactly one eligible region and one registered Debian bootloader.
        plan_script = '''import importlib.util,json,sys
p='/usr/local/lib/arch-niri-dux/installer/install.py'
s=importlib.util.spec_from_file_location('installer',p); m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
v=m.Settings('/dev/vda','tester','alongside','UTC','us','vm-verification-only','vm-verification-only',swap_gib=0,mode='alongside')
t=m.alongside_snapshot(v)
assert len(t['regions'])==1
boots=[n for n,b in t['boots'].items() if 'debian' in b['path'].lower()]
assert len(boots)==1
v.free_region=t['regions'][0];v.existing_boot=boots[0];v.validate()
c={k:getattr(v,k) for k in ('disk','username','hostname','timezone','keymap','passphrase','password','swap_gib','mode','free_region','existing_boot')}
open('/run/verify/install.json','w').write(json.dumps(c))
open('/run/verify/old-partitions.json','w').write(json.dumps(t['table']['partitions']))
'''
        vm.command('python -c ' + shlex.quote(plan_script))
        vm.command('chmod 600 /run/verify/install.json')
        parts = json.loads((directory / 'old-partitions.json').read_text())
        devices = ' '.join(shlex.quote(part['node']) for part in parts)
        vm.command(f'sha256sum {devices} > /run/verify/old-partitions.sha256', timeout=600)
        vm.command('efibootmgr -v > /run/verify/firmware-before.txt')
        vm.command('arch-niri-install --config /run/verify/install.json --confirm-disk /dev/vda', timeout=2400)
        vm.command('sha256sum -c /run/verify/old-partitions.sha256', timeout=600)
        vm.command('efibootmgr -v > /run/verify/firmware-after.txt')
        passed('Every byte of every existing partition survives installation')
        vm.poweroff()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('test -f /etc/arch-release; test "$(findmnt -no FSTYPE /)" = btrfs')
        passed('Default firmware path boots the new encrypted installation')
        # Show the menu on its next boot, then choose the appended existing OS.
        vm.command('sed -i "1i timeout: 30" /boot/limine.conf')
        vm.poweroff()
        vm.start(cdrom=False)
        time.sleep(4)
        vm.key('esc')
        vm.key('end')
        vm.screenshot('limine-existing-os')
        vm.key('ret')
        old_linux_login(vm)
        passed('Existing Debian boots through Limine and retains its sentinel')
        vm.poweroff()
        (directory / 'SUCCESS').write_text('Linux alongside acceptance passed\n')
    finally:
        vm.stop()


if __name__ == '__main__':
    main()
