"""Accepted-fixture overlay qualification lifecycle."""

import json
from pathlib import Path
import shutil
import subprocess
import time
from tools.workspace.state import ROOT, own
from tools.workspace.resources import disk_guard, kept_running, socket_guard
from tools.workspace.fixtures import newest_accepted_fixture
from tools.digest import digest
from tools.verification.evidence import Evidence
from tools.verification import gate
from tools.vm.control import VM
from tools.verification.login import graphical_login, installed_login
from tools.verification.payload import stage_probes, probe, mount_probes


def run_fixture(args, parser):
    """Fixture mode (folded verify_fast.py): qualify new packages on an accepted overlay."""
    for flag in ('--iso', '--disk', '--install-only', '--swap-gib', '--slow'):
        attr = flag.lstrip('-').replace('-', '_')
        value = args.__dict__.get(attr)
        if value is not None and value is not False:
            parser.error(f'{flag} is for full runs; fixture mode uses --fixture [--kernel-package] [--keep].')
    raw = args.fixture
    fixture = newest_accepted_fixture() if raw is None or str(raw) == 'auto' else Path(raw).resolve()
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
    if args.keep:
        socket_guard(directory)
    if kept_running(directory):
        parser.error(f'Kept VM still running in {directory}; power it off first.')
    disk_guard(directory)
    evidence = Evidence(directory, parents=True)
    own(directory)
    for name in ('OVMF_VARS.fd', 'install.json'):
        shutil.copyfile(accepted / name, directory / name)
    if args.kernel_package:
        shutil.copyfile(args.kernel_package, directory / 'kernel.pkg.tar.zst')
    probe_hashes = stage_probes(directory)
    from tools.packages import integration as builder

    source = directory / 'integration-input'
    source.mkdir()
    epoch = int(subprocess.check_output(['git', 'log', '-1', '--format=%ct'], cwd=ROOT, text=True))
    builder.prepare_source(ROOT, source, epoch)
    # The legacy verify.py key pins the CLI, not the extracted lifecycle modules.
    evidence.inputs(
        {
            'kernel_sha256': digest(directory / 'kernel.pkg.tar.zst') if args.kernel_package else None,
            'fixture': str(fixture),
            'accepted_inputs': json.loads((accepted / 'inputs.json').read_text()),
            'source_sha256': digest(source / 'integration.tar'),
            'verification_scripts': probe_hashes | {'verify.py': digest(ROOT / 'tools/verification/session.py')},
        }
    )
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(fixture), str(disk)], check=True)
    passed = evidence.passed
    vm = VM(directory, None, disk)
    if args.keep:
        vm.socket_directory = directory / '.sockets'
        vm.socket_directory.mkdir(exist_ok=True)
    started = False
    try:
        vm.start(cdrom=False)
        started = True
        installed_login(vm)
        mount_probes(vm)
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
            'cd /run/verify && runuser -u tester -- python -m tools.packages.integration '
            f'/tmp/fast/source /tmp/fast/build /tmp/fast/packages --epoch {epoch}',
            timeout=180,
        )
        probe(vm, 'policy', 'record-boot')
        kernel = ' /run/verify/kernel.pkg.tar.zst' if args.kernel_package else ''
        vm.command(
            'pacman -U --noconfirm /tmp/fast/packages/*.pkg.tar.zst'
            + kernel
            + ' >/run/verify/boot-policy-transaction.log 2>&1; '
            'cp /tmp/fast/packages/integration.manifest.json /run/verify/fast-packages.json',
            timeout=300,
        )
        probe(vm, 'policy', 'upgraded-boot')
        vm.poweroff()
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        mount_probes(vm)
        graphical_login(vm)
        probe(vm, 'boot', 'plymouth-config')
        probe(vm, 'policy', 'check', timeout=180)
        probe(vm, 'desktop', 'niri-overrides', timeout=180)
        probe(vm, 'desktop', 'account-seeded', timeout=180)
        # Error gate on the final clean boot, before FAST-PASSED is written.
        # Deliberate-failure probes ran in earlier boots only.
        gate.collect(vm, directory)
        gate.check(directory)
        passed('Final boot journal and failed units contain only reviewed lines')
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
            console = f'./scripts/in-dev-shell python3 -m tools.vm.console --directory {directory}'
            print(f'VM kept running in {directory} (holds 8 GiB guest RAM until stopped).')
            print(f"  Shell:      {console} sh '<command>'")
            print(f'  Screenshot: {console} shot <name.png>')
            print(f'  Click/key:  {console} click <x_frac> <y_frac> | key <qcode>')
            print(f'  Probe:      {console} probe <module> <function>')
            print(f"  Files:      cp <src> {directory}/, then sh 'cp /run/verify/<name> <dest>'")
            print(f'  Stop:       {console} poweroff')
        else:
            vm.stop()
