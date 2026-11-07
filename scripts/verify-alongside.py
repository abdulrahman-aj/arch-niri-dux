#!/usr/bin/env python3
"""Real Windows/Linux alongside acceptance; fixture contracts in ALONGSIDE.md."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from verification.vm import VM, PASSWORD, installed_login, stage_probes
from alongside_windows import WindowsVM, BootReports

ROOT = Path(__file__).resolve().parents[1]


def old_linux_login(vm):
    vm.expect(r'login:', timeout=240)
    vm.send('tester\n')
    vm.expect(r'Password:')
    vm.send(PASSWORD + '\n')
    vm.expect(r'\x1b\[\?2004h', timeout=60)
    vm.command('test -f /etc/debian_version; test "$(cat /var/lib/alongside-sentinel)" = preserve-existing-linux')
    vm.send('sudo -i\n')
    vm.expect(r'\x1b\[\?2004h', timeout=60)
    vm.command('test "$(id -u)" = 0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os', choices=['linux', 'windows'], default='linux')
    parser.add_argument('--windows-token', type=Path)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if shutil.disk_usage(args.directory.parent).free < 16 * 1024**3:
        parser.error('Need 16 GiB free for one scenario, including a 9 GiB stop threshold protecting 8 GiB headroom.')
    args.directory.mkdir(mode=0o700)  # Never reuse evidence or fixture overlays.
    directory = args.directory.resolve()
    disk = directory / 'disk.qcow2'
    subprocess.run(
        ['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(args.fixture.resolve()), str(disk)], check=True
    )
    shutil.copyfile(args.firmware, directory / 'OVMF_VARS.fd')
    inputs = {}
    for name, path in [('iso', args.iso), ('fixture', args.fixture), ('firmware', args.firmware)]:
        with path.open('rb') as stream:
            inputs[name] = dict(path=str(path.resolve()), sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
    (directory / 'inputs.json').write_text(json.dumps(inputs, indent=2))
    reports = None
    if args.os == 'windows':
        if args.windows_token is None:
            parser.error('Windows requires the provisioned fixture report-token file.')
        vm = WindowsVM(directory, args.iso.resolve(), disk, tpm_source=args.fixture.parent / 'tpm')
        reports = BootReports(directory, args.windows_token.read_text().strip())
    else:
        vm = VM(directory, args.iso.resolve(), disk)
    vm.min_free_bytes = 9 * 1024**3
    device = '/dev/sda' if reports else '/dev/vda'
    (directory / 'key').write_text(PASSWORD)
    (directory / 'key').chmod(0o600)
    stage_probes(directory)
    milestones = []

    def passed(message):
        milestones.append(message)
        (directory / 'milestones.json').write_text(json.dumps(milestones, indent=2))
        print('PASS: ' + message, flush=True)

    try:
        vm.start(cdrom=False, network=bool(reports))
        if reports:
            reports.wait(vm, 'windows-before')
        else:
            old_linux_login(vm)
            vm.poweroff()
        passed(f'Real existing {args.os} boots before installation and sentinel is intact')
        vm.start()
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        vm.mount_probes()
        for filename in ('install.py', 'configure-target.sh'):
            digest = hashlib.sha256((ROOT / 'installer' / filename).read_bytes()).hexdigest()
            vm.command(
                f'test "$(sha256sum /usr/local/lib/arch-niri-dux/installer/{filename} | cut -d " " -f 1)" = {digest}'
            )
        # Plan through the ISO's own read-only inventory. The fixture must have
        # exactly one eligible region and one registered Debian bootloader.
        plan_script = """import importlib.util,json,sys
p='/usr/local/lib/arch-niri-dux/installer/install.py'
s=importlib.util.spec_from_file_location('installer',p); m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
v=m.Settings('DEVICE','tester','alongside','UTC','us','vm-verification-only','vm-verification-only',swap_gib=0,mode='alongside')
t=m.alongside_snapshot(v.disk)
assert len(t['regions'])==1
boots=[n for n,b in t['boots'].items() if 'BOOTPATH' in b['path'].lower()]
assert boots and len({(t['boots'][n]['guid'],t['boots'][n]['path'].lower()) for n in boots})==1
boots.sort()
v.free_region=t['regions'][0];v.existing_boot=boots[0];v.validate()
c={k:getattr(v,k) for k in ('disk','username','hostname','timezone','keymap','passphrase','password','swap_gib','mode','free_region','existing_boot')}
open('/run/verify/install.json','w').write(json.dumps(c))
open('/run/verify/old-partitions.json','w').write(json.dumps(t['table']['partitions']))
open('/run/verify/new-partitions.json','w').write(json.dumps(m.alongside_plan(v,t)))
"""
        plan_script = plan_script.replace('DEVICE', device).replace('BOOTPATH', 'microsoft' if reports else 'debian')
        vm.command('python -c ' + shlex.quote(plan_script))
        vm.command('chmod 600 /run/verify/install.json')
        parts = json.loads((directory / 'old-partitions.json').read_text())
        devices = ' '.join(shlex.quote(part['node']) for part in parts)
        vm.command(f'sha256sum {devices} > /run/verify/old-partitions.sha256', timeout=600)
        vm.command('efibootmgr -v > /run/verify/firmware-before.txt')
        vm.command(f'arch-niri-install --config /run/verify/install.json --confirm-disk {device}', timeout=2400)
        vm.command('sha256sum -c /run/verify/old-partitions.sha256', timeout=600)
        vm.command('efibootmgr -v > /run/verify/firmware-after.txt')
        passed('Every byte of every existing partition survives installation')
        plan = json.loads((directory / 'new-partitions.json').read_text())
        separator = 'p' if device[-1].isdigit() else ''
        esp, encrypted = [device + separator + str(part[0]) for part in plan]
        vm.command(
            f'cryptsetup open --key-file /run/verify/key {encrypted} dux-root; '
            'mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; '
            f'mount {esp} /mnt/verify/boot'
        )
        vm.command(
            'cd /run/verify && python -m verification.boot prepare /mnt/verify; '
            'cd /run/verify && python -m verification.boot serial-console-enable /mnt/verify; '
            'arch-chroot /mnt/verify limine-mkinitcpio',
            timeout=300,
        )
        vm.command(
            'umount -R /mnt/verify; cryptsetup close dux-root; '
            'cp /run/archiso/bootmnt/repo/linux-[0-9]*.pkg.tar.zst /run/verify/; sync'
        )
        vm.poweroff()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('test -f /etc/arch-release; test "$(findmnt -no FSTYPE /)" = btrfs')
        passed('Default firmware path boots the new encrypted installation')
        # Reinstall the pinned kernel: exercise actual package hooks and
        # snapshot generation rather than only editing the menu in place.
        vm.mount_probes()
        vm.command('pacman -U --noconfirm /run/verify/linux-[0-9]*.pkg.tar.zst', timeout=600)
        vm.command(
            'grep -A2 "^/Existing OS" /boot/limine.conf > /run/verify/chainload-after-update.txt; '
            'grep -q "path: guid(" /run/verify/chainload-after-update.txt; '
            'cp /boot/limine.conf /run/verify/limine-after-update.conf'
        )
        passed('Pinned kernel reinstall and snapshot hooks preserve the existing OS entry')
        vm.poweroff()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('test -f /etc/arch-release; test -e /dev/mapper/dux-root')
        passed('New encrypted installation boots after kernel package reinstall')
        # Explicit selection by entry path exercises the same chainloader as
        # manual menu selection without timing-dependent keyboard simulation.
        vm.command(
            'sed -i "1i default_entry: Existing OS (firmware '
            + json.loads((directory / 'install.json').read_text())['existing_boot'].upper()
            + ')\\ntimeout: 1\\nremember_last_entry: no" /boot/limine.conf'
        )
        vm.poweroff()
        vm.start(cdrom=False, network=bool(reports))
        if reports:
            reports.wait(vm, 'windows-after-update-chainload')
        else:
            old_linux_login(vm)
            vm.poweroff()
        passed(f'Existing {args.os} boots through Limine after package update and retains its sentinel')
        (directory / 'SUCCESS').write_text(f'{args.os} alongside acceptance passed\n')
    finally:
        vm.stop()
        if reports:
            reports.close()


if __name__ == '__main__':
    main()
