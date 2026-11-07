#!/usr/bin/env python3
"""Provision disposable real OS fixtures from official media; never touches host disks."""

import argparse
import json
from pathlib import Path

import subprocess
import secrets

from workspace import disk_guard

from alongside_windows import (
    WindowsVM,
    BootReports,
    unattended,
    deployment_unattended,
    powershell_script,
    DISKPART,
    DEPLOY,
    SETUP_VERIFY,
)

from verification.vm import VM


def linux(args):
    directory = args.directory.resolve()
    directory.mkdir(mode=0o700)
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'convert', '-O', 'qcow2', str(args.media), str(disk)], check=True)
    subprocess.run(['qemu-img', 'resize', str(disk), '64G'], check=True)
    vm = VM(directory, args.iso.resolve(), disk)
    vm.min_free_bytes = 9 * 1024**3
    try:
        vm.start()
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        vm.command('sgdisk -e /dev/vda')  # fixture preparation only, not installer
        vm.command(
            'mkdir -p /run/debian; mount /dev/vda1 /run/debian; mount /dev/vda15 /run/debian/boot/efi; test -f /run/debian/etc/debian_version'
        )
        vm.command(
            'arch-chroot /run/debian /usr/sbin/useradd -m -G sudo -s /bin/bash tester; '
            'echo tester:vm-verification-only | arch-chroot /run/debian /usr/sbin/chpasswd; '
            'mkdir -p /run/debian/etc/sudoers.d; '
            'echo "tester ALL=(ALL) NOPASSWD:ALL" > /run/debian/etc/sudoers.d/tester; '
            'chmod 440 /run/debian/etc/sudoers.d/tester; '
            'echo preserve-existing-linux > /run/debian/var/lib/alongside-sentinel; '
            'touch /run/debian/etc/growroot-disabled /run/debian/etc/cloud/cloud-init.disabled'
        )
        vm.command('test -f /run/debian/boot/efi/EFI/debian/shimx64.efi; umount -R /run/debian')
        vm.command(r"efibootmgr --create --disk /dev/vda --part 15 --label Debian --loader '\EFI\debian\shimx64.efi'")
        vm.poweroff()
        (directory / 'READY').write_text('Official Debian 13 GPT/UEFI fixture; existing partitions not resized\n')
    finally:
        vm.stop()


def windows(args):
    directory = args.directory.resolve()
    directory.mkdir(mode=0o700)
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '64G'], check=True)
    seed = directory / 'seed'
    seed.mkdir()
    token = secrets.token_hex(16)
    (directory / 'report-token').write_text(token)
    (seed / 'Autounattend.xml').write_text(deployment_unattended())
    (seed / 'installed-unattend.xml').write_text(unattended())
    (seed / 'diskpart.txt').write_text(DISKPART)
    (seed / 'deploy.cmd').write_text(DEPLOY)
    (seed / 'setup-verify.cmd').write_text(SETUP_VERIFY)
    (seed / 'verify-boot.ps1').write_text(powershell_script(token))
    seed_iso = directory / 'seed.iso'
    subprocess.run(
        ['xorriso', '-as', 'mkisofs', '-quiet', '-r', '-J', '-V', 'WINSEED', '-o', str(seed_iso), str(seed)], check=True
    )
    vm = WindowsVM(directory, args.media.resolve(), disk)
    vm.allow_reboot = True
    vm.extra_args = ['-drive', f'if=ide,index=2,format=raw,readonly=on,media=cdrom,file={seed_iso}']
    reports = BootReports(directory, token)
    try:
        vm.start(network=True)
        reports.wait(vm, 'windows-installed', install=True, timeout=1800)
        vm.stop()
        finish_windows(args)
    finally:
        vm.stop()
        reports.close()


def finish_windows(args):
    directory = args.directory.resolve()
    report = json.loads((directory / 'windows-installed.json').read_text())
    if report.get('sentinel') != 'preserve-existing-windows' or int(report['build']) < 22000:
        raise ValueError('Resume requires a successful real Windows 11 installation report.')
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'check', '-q', str(disk)], check=True)
    vm = WindowsVM(directory, args.iso.resolve(), disk)
    try:
        vm.start()
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        vm.command(
            r"efibootmgr --create --disk /dev/sda --part 1 --label 'Windows Boot Manager' --loader '\EFI\Microsoft\Boot\bootmgfw.efi'"
        )
        vm.poweroff()
        (directory / 'READY').write_text('Real Windows 11 Enterprise LTSC evaluation; 100 MiB existing ESP\n')
    finally:
        vm.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os', choices=['linux', 'windows'], required=True)
    parser.add_argument('--media', type=Path)
    parser.add_argument(
        '--finish-windows',
        action='store_true',
        help='Resume after a saved successful Windows installation report; never recreate the disk',
    )
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    disk_guard(args.directory)
    if args.finish_windows:
        if args.os != 'windows':
            parser.error('--finish-windows requires --os windows')
        finish_windows(args)
    else:
        if args.media is None:
            parser.error('--media is required for a new fixture')
        (linux if args.os == 'linux' else windows)(args)


if __name__ == '__main__':
    main()
