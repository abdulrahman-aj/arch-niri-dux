"""Fresh installation acceptance lifecycle."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from tools.workspace.state import ROOT, own
from tools.workspace.resources import disk_guard, kept_running, socket_guard
from tools.workspace.fixtures import newest_accepted_fixture
from tools.digest import digest
from tools.verification.evidence import Evidence
from tools.verification import gate
from tools.vm.control import VM
from tools.verification.constants import PASSWORD
from tools.verification.login import graphical_login, installed_login
from tools.verification.payload import stage_probes, probe, mount_probes

from tools.verification.stages import full_stages, guest_alive, run_stages
from .install import run_install, prepare_integration_inputs


def run_full(args, parser):
    if args.kernel_package is not None:
        parser.error('--kernel-package needs --fixture.')
    if args.swap_gib is not None and args.swap_gib < 0:
        parser.error('--swap-gib must be zero or a positive number of GiB.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.keymap):
        parser.error('--keymap must be a plain console keymap name.')
    # The ISO just built is the newest file. Name order would pick a release ISO
    # over a newer development ISO of the same date ('.iso' sorts after '-development').
    images = sorted((ROOT / 'out').glob('*.iso'), key=lambda path: path.stat().st_mtime)
    iso = args.iso or (images[-1] if images else None)
    if iso is None or not iso.is_file():
        parser.error('Build an ISO first with make iso, or pass --iso PATH.')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('The VM verification requires access to /dev/kvm.')
    disk_guard(args.directory or ROOT / 'out/verify')
    if args.disk:
        disk_guard(args.disk)
    directory = args.directory
    if directory is None:
        # Runs hold multi-gigabyte disks, and /tmp is often RAM-backed tmpfs.
        (ROOT / 'out/verify').mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='dux-verify-', dir=ROOT / 'out/verify'))
    evidence = Evidence(directory, parents=True, exist_ok=True)
    own(directory)
    if args.keep:
        socket_guard(directory)
    if kept_running(directory):
        parser.error(f'Kept VM still running in {directory}; power it off first.')
    disk = args.disk or directory / 'disk.qcow2'
    if disk.exists():
        parser.error('Use a fresh verification directory; refusing to overwrite an existing VM disk.')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '48G'], check=True)
    configuration = dict(
        disk='/dev/vda',
        username='tester',
        hostname='dux-verify',
        timezone='UTC',
        keymap=args.keymap,
        passphrase=PASSWORD,
        password=PASSWORD,
    )
    if args.swap_gib is not None:
        configuration['swap_gib'] = args.swap_gib
    config_file = directory / 'install.json'
    config_file.write_text(json.dumps(configuration))
    config_file.chmod(0o600)
    iso_digest = digest(iso)
    package_digest = digest(ROOT / 'out/packages.manifest.json')
    probe_hashes = stage_probes(directory)
    # The legacy verify.py key pins the CLI, not the extracted lifecycle modules.
    probe_hashes['verify.py'] = digest(ROOT / 'tools/verification/session.py')
    stages = full_stages(args)
    evidence.inputs(
        {
            'iso': str(iso.resolve()),
            'iso_sha256': iso_digest,
            'package_manifest_sha256': package_digest,
            'verification_scripts': probe_hashes,
            'stages': [name for name, _ in stages],
        }
    )
    evidence.key(PASSWORD)
    prepare_integration_inputs(directory)
    vm = VM(directory, iso.resolve(), disk)
    succeeded = False
    if args.keep:
        vm.socket_directory = directory / '.sockets'
        vm.socket_directory.mkdir(exist_ok=True)
    passed = evidence.passed
    try:
        run_install(vm, passed, evidence, directory, package_digest)
        subprocess.run(['qemu-img', 'snapshot', '-c', 'pristine', str(disk)], check=True)
        if args.install_only:
            print('Install-only debug run complete. Full acceptance has NOT passed.', flush=True)
            succeeded = True
            return 0
        print(f'Stages: {", ".join(name for name, _ in stages)}', flush=True)
        failures, skipped = run_stages(vm, passed, stages)
        if failures or skipped:
            lines = [f'{name}: {error}' for name, error in failures.items()]
            lines += [f'{name}: skipped' for name in skipped]
            (directory / 'FAILURE.txt').write_text('\n'.join(lines) + '\n')
            for line in lines:
                print(f'FAILED: {line}', file=sys.stderr)
            print(
                f'Verification failed: {len(failures)} stage(s) failed, {len(skipped)} skipped\nEvidence: {directory}',
                file=sys.stderr,
            )
            return 1
        # Error gate: one final clean boot, scanned before acceptance is written.
        # Deliberate-failure probes (firewall containment, signature rejection)
        # ran in earlier boots; journalctl -b sees only this boot.
        if not guest_alive(vm):
            vm.start(cdrom=False)
            installed_login(vm)
            mount_probes(vm)
        graphical_login(vm)
        gate.collect(vm, directory)
        gate.check(directory)
        passed('Final boot journal and failed units contain only reviewed lines')
        vm.poweroff()
        (directory / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
        print(f'Acceptance passed. Evidence: {directory}', flush=True)
        succeeded = True
        return 0
    except Exception as error:
        try:
            if vm.process and vm.process.poll() is None and vm.screenshot('failure') is None:
                probe(vm, 'desktop', 'failure-screenshot', timeout=15)
        except Exception:
            pass
        (directory / 'FAILURE.txt').write_text(str(error) + '\n')
        print(f'Verification failed: {error}\nEvidence: {directory}', file=sys.stderr)
        return 1
    finally:
        # A failed or interrupted run's disk can never become a fixture:
        # unlink it (QEMU holds it open; the space frees at poweroff).
        kept = args.keep and not succeeded and vm.process is not None and vm.process.poll() is None
        if not succeeded:
            disk.unlink(missing_ok=True)
        if kept:
            console = f'./scripts/in-dev-shell python3 -m tools.vm.console --directory {directory}'
            print(f'VM kept running in {directory} (sockets under {directory}/.sockets).')
            print(f"  Shell:  {console} sh '<command>'")
            print(f'  Stage:  {console} stage <module>')
            print(f'  Stop:   {console} poweroff')
        else:
            vm.stop()
            if not succeeded and newest_accepted_fixture() is not None:
                print("Next: make verify-fast ARGS='--keep' (auto-finds the newest accepted fixture)", file=sys.stderr)
        if succeeded and args.keep:
            shutil.rmtree(directory / '.sockets', ignore_errors=True)
