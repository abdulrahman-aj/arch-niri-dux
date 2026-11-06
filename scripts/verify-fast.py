#!/usr/bin/env python3
"""Build and qualify integration packages on an overlay of an accepted 0.3+ VM."""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from verification.policy import SYSTEM_UNITS, USER_UNITS
from verification.vm import VM, graphical_login, installed_login, stage_probes

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, type=Path,
                        help='disk.qcow2 from a full accepted make verify run')
    parser.add_argument('--directory', required=True, type=Path, help='New evidence directory')
    args = parser.parse_args()
    fixture = args.fixture.resolve()
    accepted = fixture.parent
    if not fixture.is_file() or not (accepted / 'ACCEPTANCE-PASSED').is_file():
        parser.error('Fixture must be a disk from a completed full acceptance run.')
    directory = args.directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ('OVMF_VARS.fd', 'install.json'):
        shutil.copyfile(accepted / name, directory / name)
    probe_hashes = stage_probes(directory)
    spec = importlib.util.spec_from_file_location('builder', ROOT / 'scripts/build-integration.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = directory / 'integration-input'
    source.mkdir()
    epoch = int(subprocess.check_output(['git', 'log', '-1', '--format=%ct'], cwd=ROOT, text=True))
    builder.prepare_source(ROOT, source, epoch)
    (directory / 'inputs.json').write_text(json.dumps({
        'fixture': str(fixture), 'accepted_inputs': json.loads((accepted / 'inputs.json').read_text()),
        'source_sha256': builder.digest(source / 'integration.tar'),
        'verification_scripts': probe_hashes | {'verify-fast.py': builder.digest(Path(__file__))},
    }, indent=2) + '\n')
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2',
                    '-b', str(fixture), str(disk)], check=True)
    vm = VM(directory, None, disk)
    try:
        vm.start(cdrom=False)
        installed_login(vm)
        vm.mount_probes()
        # Pre-0.3 installer-owned files need manual repair. This tier deliberately
        # refuses those fixtures instead of introducing a migration path.
        vm.command('pacman -Qo /etc/snapper/configs/root /usr/share/arch-niri-dux/niri/tail.kdl')
        vm.command('mkdir -p /tmp/fast/source/packages/integration /tmp/fast/build /tmp/fast/packages; '
                   'tar -xf /run/verify/integration-input/integration.tar -C /tmp/fast/source; '
                   'mv /tmp/fast/source/packages.target /tmp/fast/source/packages/; '
                   'cp /run/verify/integration-input/PKGBUILD /tmp/fast/source/packages/integration/; '
                   'chown -R tester:tester /tmp/fast')
        vm.command(f'runuser -u tester -- python /run/verify/build-integration.py '
                   f'/tmp/fast/source /tmp/fast/build /tmp/fast/packages --epoch {epoch}', timeout=180)
        vm.command('pacman -U --noconfirm /tmp/fast/packages/*.pkg.tar.zst; '
                   'cp /tmp/fast/packages/integration.manifest.json /run/verify/fast-packages.json; '
                   'systemctl preset ' + ' '.join(SYSTEM_UNITS)
                   + ' snapper-timeline.timer; systemctl --global preset '
                   + ' '.join(USER_UNITS), timeout=300)
        vm.command('limine-mkinitcpio', timeout=300)
        vm.poweroff()
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.mount_probes()
        graphical_login(vm)
        vm.probe('boot', 'plymouth-config')
        vm.probe('policy', 'check', timeout=180)
        vm.probe('desktop', 'niri-overrides', timeout=180)
        vm.poweroff()
        (directory / 'FAST-PASSED').write_text(json.dumps({
            'fixture': str(fixture), 'source_sha256': builder.digest(source / 'integration.tar'),
            'checks': ['package upgrade', 'Niri and Dux bindings', 'presets',
                       'greetd login and unlocked keyring', 'systemd drop-ins'],
        }, indent=2) + '\n')
    finally:
        vm.stop()


if __name__ == '__main__':
    main()
