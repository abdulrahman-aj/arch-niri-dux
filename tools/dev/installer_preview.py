#!/usr/bin/env python3
"""Boot a disposable installer UI preview. This never qualifies a change."""

import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from tools.workspace import state as workspace
from tools.workspace import resources
from tools.verification.vm import VM

ROOT = Path(__file__).resolve().parents[2]


def sync_installer(vm, directory):
    staging = directory / 'installer'
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(ROOT / 'installer', staging, ignore=shutil.ignore_patterns('__pycache__'))
    vm.command(
        'rm -rf /usr/local/lib/arch-niri-dux/installer; '
        'cp -a /run/verify/installer /usr/local/lib/arch-niri-dux/installer; '
        'systemctl restart getty@tty1.service'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'out/preview')
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--disk-gib', type=int, default=32)
    parser.add_argument('--ram-gib', type=int, default=4)
    args = parser.parse_args()
    resources.heavy_guard()
    if args.disk_gib < 1 or args.ram_gib < 1:
        parser.error('Disk and RAM sizes must be positive GiB values.')
    directory = args.directory.resolve()
    resources.socket_guard(directory)
    if resources.kept_running(directory):
        parser.error('Preview already running. Use vm_console preview-sync or poweroff first.')
    # A preview is disposable, but an earlier directory may contain valuable evidence.
    if directory.exists():
        parser.error('Choose a fresh --directory or remove the stopped preview directory.')
    images = sorted((ROOT / 'out').glob('*-development.iso'), key=lambda path: path.stat().st_mtime)
    iso = args.iso or (images[-1] if images else None)
    if iso is None or not iso.is_file():
        parser.error('Build a development ISO with make iso-development, or pass --iso.')
    resources.disk_guard(directory)
    directory.mkdir(parents=True)
    workspace.own(directory)
    (directory / 'installer-preview.json').write_text(json.dumps(dict(iso=str(iso.resolve()))))
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), f'{args.disk_gib}G'], check=True)
    vm = VM(directory, iso.resolve(), disk)
    vm.memory_mib = args.ram_gib * 1024
    vm.socket_directory = directory / '.sockets'
    vm.socket_directory.mkdir()
    kept = False
    try:
        vm.start(software_display=True)
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        vm.mount_probes()
        sync_installer(vm, directory)
        kept = True
        console = f'./scripts/in-dev-shell python3 -m tools.vm.console --directory {shlex.quote(str(directory))}'
        print('Preview only. Final installer qualification requires an unmodified ISO and make verify.')
        print(f'{console} preview-sync')
        print(f'{console} shot screen.png')
        print(f'{console} key ret')
        print(f'{console} poweroff')
    finally:
        if not kept:
            vm.stop()


if __name__ == '__main__':
    main()
