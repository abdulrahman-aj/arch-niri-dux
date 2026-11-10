#!/usr/bin/env python3
"""Build and qualify integration packages on an overlay of an accepted 0.3+ VM."""

import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time

from workspace import disk_guard, newest_accepted_fixture, own

from verification.digest import digest
from verification.evidence import Evidence

from verification.vm import VM, graphical_login, installed_login, stage_probes

ROOT = Path(__file__).resolve().parents[1]


def kept_running(directory):
    sock = directory / '.sockets' / 'qmp.sock'
    if not sock.exists():
        return False
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.settimeout(2)
        probe.connect(str(sock))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, help='disk.qcow2 from a full accepted run (default: newest accepted)')
    parser.add_argument('--directory', type=Path, help='New evidence directory (default: fresh out/fast-<timestamp>)')
    parser.add_argument(
        '--kernel-package', type=Path, help='Also reinstall this kernel archive in the same package transaction'
    )
    parser.add_argument(
        '--keep',
        action='store_true',
        help='Keep the VM running after the run (pass or fail) for interactive debugging',
    )
    args = parser.parse_args()
    fixture = args.fixture.resolve() if args.fixture else newest_accepted_fixture()
    if fixture is None:
        parser.error('No accepted fixture found; run make verify first or pass --fixture PATH.')
    accepted = fixture.parent
    if not fixture.is_file() or not (accepted / 'ACCEPTANCE-PASSED').is_file():
        parser.error('Fixture must be a disk from a completed full acceptance run.')
    if args.directory is None:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        directory = ROOT / 'out' / f'fast-{stamp}'
        suffix = 1
        while directory.exists():
            suffix += 1
            directory = ROOT / 'out' / f'fast-{stamp}-{suffix}'
    else:
        directory = args.directory.resolve()
    if args.keep and len(str(directory / '.sockets' / 'serial.sock')) > 107:
        parser.error('Kept-VM socket path exceeds the 107-byte Unix socket limit; use a shorter --directory.')
    if kept_running(directory):
        parser.error(f'Kept VM still running in {directory}; power it off first.')
    disk_guard(args.directory or ROOT / 'out')
    evidence = Evidence(directory, parents=True)
    own(directory)
    for name in ('OVMF_VARS.fd', 'install.json'):
        shutil.copyfile(accepted / name, directory / name)
    if args.kernel_package:
        shutil.copyfile(args.kernel_package, directory / 'kernel.pkg.tar.zst')
    probe_hashes = stage_probes(directory)
    import integration_build as builder

    source = directory / 'integration-input'
    source.mkdir()
    epoch = int(subprocess.check_output(['git', 'log', '-1', '--format=%ct'], cwd=ROOT, text=True))
    builder.prepare_source(ROOT, source, epoch)
    evidence.inputs(
        {
            'kernel_sha256': digest(directory / 'kernel.pkg.tar.zst') if args.kernel_package else None,
            'fixture': str(fixture),
            'accepted_inputs': json.loads((accepted / 'inputs.json').read_text()),
            'source_sha256': digest(source / 'integration.tar'),
            'verification_scripts': probe_hashes | {'verify_fast.py': digest(Path(__file__))},
        }
    )
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(fixture), str(disk)], check=True)
    vm = VM(directory, None, disk)
    if args.keep:
        vm.socket_directory = directory / '.sockets'
        vm.socket_directory.mkdir(exist_ok=True)
    started = False
    try:
        vm.start(cdrom=False)
        started = True
        installed_login(vm)
        vm.mount_probes()
        # Pre-0.3 installer-owned files need manual repair. This tier deliberately
        # refuses those fixtures instead of introducing a migration path.
        vm.command('pacman -Qo /etc/snapper/configs/root /usr/share/arch-niri-dux/niri/tail.kdl')
        vm.command(
            'mkdir -p /tmp/fast/source/packages/integration /tmp/fast/build /tmp/fast/packages; '
            'tar -xf /run/verify/integration-input/integration.tar -C /tmp/fast/source; '
            'cp /run/verify/integration-input/PKGBUILD /tmp/fast/source/packages/integration/; '
            'chown -R tester:tester /tmp/fast'
        )
        vm.command(
            f'runuser -u tester -- python /run/verify/integration_build.py '
            f'/tmp/fast/source /tmp/fast/build /tmp/fast/packages --epoch {epoch}',
            timeout=180,
        )
        vm.probe('policy', 'record-boot')
        kernel = ' /run/verify/kernel.pkg.tar.zst' if args.kernel_package else ''
        vm.command(
            'pacman -U --noconfirm /tmp/fast/packages/*.pkg.tar.zst'
            + kernel
            + ' >/run/verify/boot-policy-transaction.log 2>&1; '
            'cp /tmp/fast/packages/integration.manifest.json /run/verify/fast-packages.json',
            timeout=300,
        )
        vm.probe('policy', 'upgraded-boot')
        vm.poweroff()
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.mount_probes()
        graphical_login(vm)
        vm.probe('boot', 'plymouth-config')
        vm.probe('policy', 'check', timeout=180)
        vm.probe('desktop', 'niri-overrides', timeout=180)
        vm.probe('migrations', 'check', timeout=180)
        if not args.keep:
            vm.poweroff()
        (directory / 'FAST-PASSED').write_text(
            json.dumps(
                {
                    'fixture': str(fixture),
                    'source_sha256': digest(source / 'integration.tar'),
                    'kernel_in_transaction': bool(args.kernel_package),
                    'checks': [
                        'ALPM boot policy regeneration',
                        'package upgrade',
                        'Niri and Dux bindings',
                        'presets',
                        'greetd login and unlocked keyring',
                        'systemd drop-ins',
                    ],
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        if args.keep and started and vm.process is not None and vm.process.poll() is None:
            # The VM outlives this process: sockets stay under the evidence
            # directory, so vm_console.py can attach. Wanted especially on
            # failure, which is when the live guest is most useful.
            console = f'./scripts/build-in-dev python3 scripts/vm_console.py --directory {directory}'
            print(f'VM kept running in {directory} (holds 8 GiB guest RAM until stopped).')
            print(f"  Shell:      {console} sh '<command>'")
            print(f'  Screenshot: {console} shot <name.png>')
            print(f'  Click/key:  {console} click <x_frac> <y_frac> | key <qcode>')
            print(f'  Probe:      {console} probe <module> <function>')
            print(f"  Files:      cp <src> {directory}/, then sh 'cp /run/verify/<name> <dest>'")
            print(f'  Stop:       {console} poweroff')
        else:
            vm.stop()


if __name__ == '__main__':
    main()
