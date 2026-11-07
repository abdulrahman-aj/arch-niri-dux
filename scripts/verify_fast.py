#!/usr/bin/env python3
"""Build and qualify integration packages on an overlay of an accepted 0.3+ VM."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from workspace import disk_guard, own

from verification.digest import digest
from verification.evidence import Evidence

from verification.vm import VM, graphical_login, installed_login, stage_probes

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, type=Path, help='disk.qcow2 from a full accepted make verify run')
    parser.add_argument('--directory', required=True, type=Path, help='New evidence directory')
    parser.add_argument(
        '--kernel-package', type=Path, help='Also reinstall this kernel archive in the same package transaction'
    )
    args = parser.parse_args()
    fixture = args.fixture.resolve()
    accepted = fixture.parent
    if not fixture.is_file() or not (accepted / 'ACCEPTANCE-PASSED').is_file():
        parser.error('Fixture must be a disk from a completed full acceptance run.')
    directory = args.directory.resolve()
    disk_guard(args.directory)
    evidence = Evidence(directory, parents=True)
    own(args.directory)
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
    try:
        vm.start(cdrom=False)
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
        vm.stop()


if __name__ == '__main__':
    main()
