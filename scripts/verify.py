#!/usr/bin/env python3
"""Run offline UEFI acceptance, optionally checking published delivery afterward."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from workspace import disk_guard, kept_running, newest_accepted_fixture, own, socket_guard

from verification.digest import digest
from verification.evidence import Evidence

from verification import apps, boot, browser, camera, desktop, dux_update, integration, pdf, recovery, suspend
from verification.vm import VM, PASSWORD, graphical_login, installed_login, stage_probes

ROOT = Path(__file__).resolve().parents[1]


def _boot(vm, passed, args):
    boot.verify_boot(vm, passed, args.swap_gib)


def _policy(vm, passed, _args):
    vm.probe('policy', 'check', timeout=180)
    passed('Package presets, systemd policy and PAM-unlocked keyring')


def _network(vm, passed, _args):
    vm.probe('network', 'check', timeout=240)
    passed('Inbound firewall and resolve-only mDNS')


def _online(vm, passed, _args):
    # Fallback leaves the guest stopped; a skipped/failed fallback can leave
    # it running. Start published delivery from a known stopped state.
    try:
        vm.stop()
    except Exception:
        pass
    integration.verify_published(vm, passed)


def _wrap(func):
    def run(vm, passed, _args):
        return func(vm, passed)

    return run


# Canonical full-acceptance order. Tags mark opt-in cost: dux_update's two
# scheduled-check waits dominate ordinary runs, so it is excluded by default
# (see --include-tags). Requires lists stages whose failure would make this
# stage's result meaningless; those stages are skipped instead of failed.
STAGES = [
    {'name': 'boot', 'func': _boot, 'tags': frozenset(), 'requires': []},
    {'name': 'policy', 'func': _policy, 'tags': frozenset(), 'requires': ['boot']},
    {'name': 'network', 'func': _network, 'tags': frozenset(), 'requires': ['boot']},
    {'name': 'desktop', 'func': _wrap(desktop.verify), 'tags': frozenset(), 'requires': ['boot', 'policy']},
    # Apps, PDF, camera and browser share only the desktop session. They must
    # not require each other, so one failure still leaves the rest to run.
    {'name': 'apps', 'func': _wrap(apps.verify), 'tags': frozenset(), 'requires': ['desktop']},
    {'name': 'pdf', 'func': _wrap(pdf.verify), 'tags': frozenset(), 'requires': ['desktop']},
    {'name': 'camera', 'func': _wrap(camera.verify), 'tags': frozenset(), 'requires': ['desktop']},
    {'name': 'browser', 'func': _wrap(browser.verify), 'tags': frozenset(), 'requires': ['desktop']},
    {'name': 'lock', 'func': _wrap(desktop.verify_lock), 'tags': frozenset(), 'requires': ['desktop']},
    {'name': 'suspend', 'func': _wrap(suspend.verify), 'tags': frozenset(), 'requires': ['boot']},
    {'name': 'dux_update', 'func': _wrap(dux_update.verify), 'tags': frozenset({'slow'}), 'requires': ['desktop']},
    {'name': 'recovery', 'func': _wrap(recovery.verify), 'tags': frozenset(), 'requires': ['boot']},
    {'name': 'fallback', 'func': _wrap(boot.verify_fallback), 'tags': frozenset(), 'requires': ['recovery']},
    {'name': 'online', 'func': _online, 'tags': frozenset(), 'requires': ['boot']},
]

STAGE_MAP = {stage['name']: stage for stage in STAGES}


def list_stages():
    for stage in STAGES:
        tags = ','.join(sorted(stage['tags'])) or '-'
        requires = ','.join(stage['requires']) or '-'
        print(f'{stage["name"]:10} tags={tags:5} requires={requires}')


def select_stages(args):
    """Canonical-order stage names for a full run, honouring tags and --stages."""
    if args.stages is not None:
        names = [name.strip() for name in args.stages.split(',') if name.strip()]
        if names == ['all']:
            names = [stage['name'] for stage in STAGES if stage['name'] != 'online']
        unknown = [name for name in names if name not in STAGE_MAP]
        if unknown:
            raise ValueError(f'Unknown stage(s): {", ".join(unknown)} (see --list-stages)')
        if 'online' in names and not args.online_repository:
            raise ValueError("'online' needs --online-repository")
        selected = [name for name in STAGE_MAP if name in names]
        if args.online_repository and 'online' not in selected:
            selected.append('online')
        return selected
    include = {tag.strip() for tag in (args.include_tags or '').split(',') if tag.strip()}
    if args.online_repository and args.include_tags is None:
        # Releases qualify everything, including the slow updater waits.
        include.add('slow')
    return [stage['name'] for stage in STAGES if stage['name'] != 'online' and not (stage['tags'] - include)] + (
        ['online'] if args.online_repository else []
    )


def guest_alive(vm, timeout=30):
    try:
        if vm.process is None or vm.process.poll() is not None:
            return False
        vm.command('true', timeout=timeout)
        return True
    except Exception:
        return False


FAST_STAGES = ('plymouth', 'policy', 'overrides', 'wallpaper')


def run_fixture(args, parser):
    """Fixture mode (folded verify_fast.py): qualify new packages on an accepted overlay."""
    for flag in ('--iso', '--disk', '--install-only', '--online-repository', '--swap-gib', '--include-tags'):
        attr = flag.lstrip('-').replace('-', '_')
        if args.__dict__.get(attr) not in (None, False):
            parser.error(
                f'{flag} is for full runs; fixture mode uses --fixture [--stages] [--kernel-package] [--keep].'
            )
    raw = args.fixture
    fixture = newest_accepted_fixture() if raw is None or str(raw) == 'auto' else Path(raw).resolve()
    if fixture is None:
        parser.error('No accepted fixture found; run make verify first or pass --fixture PATH.')
    accepted = fixture.parent
    if not fixture.is_file() or not (accepted / 'ACCEPTANCE-PASSED').is_file():
        parser.error('Fixture must be a disk from a completed full acceptance run.')
    if args.stages is not None:
        selected = [name.strip() for name in args.stages.split(',') if name.strip()]
        unknown = [name for name in selected if name not in FAST_STAGES]
        if unknown:
            parser.error(f'Unknown fast stage(s): {", ".join(unknown)} (choose from {", ".join(FAST_STAGES)})')
    else:
        selected = list(FAST_STAGES)
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
            'verification_scripts': probe_hashes | {'verify.py': digest(Path(__file__))},
            'fast_stages': selected,
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
            'runuser -u tester -- python /run/verify/integration_build.py '
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
        if 'plymouth' in selected:
            vm.probe('boot', 'plymouth-config')
        if 'policy' in selected:
            vm.probe('policy', 'check', timeout=180)
        if 'overrides' in selected:
            vm.probe('desktop', 'niri-overrides', timeout=180)
        if 'wallpaper' in selected:
            vm.probe('desktop', 'wallpaper-preserved', timeout=180)
        if not args.keep:
            vm.poweroff()
        (directory / 'FAST-PASSED').write_text(
            json.dumps(
                {
                    'fixture': str(fixture),
                    'source_sha256': digest(source / 'integration.tar'),
                    'kernel_in_transaction': bool(args.kernel_package),
                    'fast_stages': selected,
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
        if args.keep and started and vm.process is not None and vm.process.poll() is not None:
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


def run_stages(vm, passed, args, names):
    """Run stages in order, continuing past failures while the guest answers.

    Returns (failures, skipped): failures maps stage names to errors, skipped
    lists stages omitted because a stage they require failed.
    """
    failures = {}
    skipped = []
    completed = set()
    for name in names:
        stage = STAGE_MAP[name]
        missing = [dep for dep in stage['requires'] if dep not in completed]
        if missing:
            skipped.append(name)
            print(f'SKIP: {name} (requires {", ".join(missing)})', flush=True)
            continue
        start = time.monotonic()
        try:
            stage['func'](vm, passed, args)
        except Exception as error:
            elapsed = time.monotonic() - start
            failures[name] = error
            print(f'FAIL: {name} after {elapsed:.0f}s: {error}', flush=True)
            if not guest_alive(vm):
                print(f'Stopping: guest no longer answers after {name} failed.', flush=True)
                break
            continue
        elapsed = time.monotonic() - start
        print(f'DONE: {name} in {elapsed:.0f}s', flush=True)
        completed.add(name)
    return failures, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--directory', type=Path, help='Fresh verification workspace; default under out/verify')
    parser.add_argument('--disk', type=Path, help='New disposable qcow2 path; default inside the workspace')
    parser.add_argument('--install-only', action='store_true', help='Debug only; does not produce acceptance success')
    parser.add_argument(
        '--online-repository',
        action='store_true',
        help='After offline acceptance, boot with networking and check published package delivery',
    )
    parser.add_argument('--swap-gib', type=int, help='Override Auto swap sizing; 0 disables swap and hibernation')
    parser.add_argument('--keymap', default='us', help='Console keymap for the installed system (default: us)')
    parser.add_argument(
        '--keep',
        action='store_true',
        help='On failure keep the guest running for debugging; the disk is still unlinked',
    )
    parser.add_argument(
        '--stages',
        help='Comma-separated stage names in canonical order, or "all"; default all except tagged slow (see --list-stages)',
    )
    parser.add_argument(
        '--include-tags',
        default=None,
        help='Comma-separated stage tags to also run; dux_update is tagged slow '
        '(releases via --online-repository include it by default)',
    )
    parser.add_argument('--list-stages', action='store_true', help='List stages, tags and dependencies and exit')
    parser.add_argument(
        '--fixture',
        nargs='?',
        const='auto',
        help='Fixture mode: reuse an accepted disk.qcow2 instead of installing '
        '(PATH, or auto-discovers the newest accepted fixture)',
    )
    parser.add_argument(
        '--kernel-package',
        type=Path,
        help='Fixture mode only: also reinstall this kernel archive in the same transaction',
    )
    args = parser.parse_args()
    if args.list_stages:
        list_stages()
        return 0
    if args.fixture is not None:
        return run_fixture(args, parser)
    if args.kernel_package is not None:
        parser.error('--kernel-package needs --fixture.')
    try:
        names = select_stages(args)
    except ValueError as error:
        parser.error(str(error))
    if args.swap_gib is not None and args.swap_gib < 0:
        parser.error('--swap-gib must be zero or a positive number of GiB.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.keymap):
        parser.error('--keymap must be a plain console keymap name.')
    images = sorted((ROOT / 'out').glob('*.iso'))
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
    probe_hashes['verify.py'] = digest(Path(__file__))
    evidence.inputs(
        {
            'iso': str(iso.resolve()),
            'iso_sha256': iso_digest,
            'package_manifest_sha256': package_digest,
            'verification_scripts': probe_hashes,
            'stages': names,
        }
    )
    evidence.key(PASSWORD)

    source = directory / 'integration-input'
    source.mkdir()
    manifest = json.loads((ROOT / 'out/packages.manifest.json').read_text())['integration']
    (directory / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    import integration_release as release

    release.validate_manifest(ROOT, ROOT / 'out/integration')
    for filename, target, field in [
        ('integration.tar', 'integration.tar', 'source_sha256'),
        ('integration.PKGBUILD', 'PKGBUILD', 'recipe_sha256'),
    ]:
        original = ROOT / 'out/integration' / filename
        if digest(original) != manifest[field]:
            raise ValueError(f'Integration build input changed: {filename}')
        shutil.copyfile(original, source / target)
    vm = VM(directory, iso.resolve(), disk)
    succeeded = False
    if args.keep:
        vm.socket_directory = directory / '.sockets'
        vm.socket_directory.mkdir(exist_ok=True)

    passed = evidence.passed

    try:
        print(f'VM evidence: {directory}', flush=True)
        vm.start()
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        # USB boot with enough RAM reproduces archiso's automatic-copy path.
        # The payload must stay on mounted media instead of disappearing.
        vm.command(
            'grep -qw copytoram=n /proc/cmdline; mountpoint -q /run/archiso/bootmnt; test ! -e /run/archiso/copytoram/airootfs.sfs'
        )
        vm.mount_probes()
        vm.command('fastfetch --logo none --structure OS:Kernel')
        vm.command('test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "')
        vm.command(
            f'test "$(sha256sum /run/archiso/bootmnt/packages.manifest.json | cut -d " " -f 1)" = {package_digest}'
        )
        evidence.verify_installer(vm, ROOT / 'installer')
        installation_output = vm.command(
            'arch-niri-install --config /run/verify/install.json --confirm-disk /dev/vda', timeout=2400
        )
        elapsed = re.search(r'INSTALLATION COMPLETE[^\n]*took ([0-9]+) seconds', installation_output)
        if elapsed is None:
            raise RuntimeError('Installer did not report its elapsed completion time.')
        (directory / 'install-time-seconds').write_text(elapsed.group(1) + '\n')
        passed('Offline encrypted installation completed')
        vm.command(
            'cryptsetup open --key-file /run/verify/key /dev/vda2 dux-root; mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; mount /dev/vda1 /mnt/verify/boot'
        )
        vm.probe('boot', 'prepare', '/mnt/verify', timeout=300)
        vm.command('umount -R /mnt/verify; cryptsetup close dux-root; sync')
        vm.stop()
        subprocess.run(['qemu-img', 'snapshot', '-c', 'pristine', str(disk)], check=True)
        if args.install_only:
            print('Install-only debug run complete. Full acceptance has NOT passed.', flush=True)
            succeeded = True
            return 0
        print(f'Stages: {", ".join(names)}', flush=True)
        failures, skipped = run_stages(vm, passed, args, names)
        if failures or skipped:
            lines = [f'{name}: {error}' for name, error in failures.items()]
            lines += [f'{name}: skipped (requires failed dependency)' for name in skipped]
            (directory / 'FAILURE.txt').write_text('\n'.join(lines) + '\n')
            for line in lines:
                print(f'FAILED: {line}', file=sys.stderr)
            print(
                f'Verification failed: {len(failures)} stage(s) failed, {len(skipped)} skipped\nEvidence: {directory}',
                file=sys.stderr,
            )
            return 1
        (directory / 'ACCEPTANCE-PASSED').write_text('All acceptance stages passed.\n')
        print(f'Acceptance passed. Evidence: {directory}', flush=True)
        succeeded = True
        return 0
    except Exception as error:
        try:
            if vm.process and vm.process.poll() is None and vm.screenshot('failure') is None:
                vm.probe('desktop', 'failure-screenshot', timeout=15)
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
            console = f'./scripts/build-in-dev python3 scripts/vm_console.py --directory {directory}'
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


if __name__ == '__main__':
    sys.exit(main())
