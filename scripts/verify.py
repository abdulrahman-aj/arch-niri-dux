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


def _policy(vm, passed):
    vm.probe('policy', 'check', timeout=180)
    passed('Package presets, systemd policy and PAM-unlocked keyring')


def _network(vm, passed):
    vm.probe('network', 'check', timeout=240)
    passed('Inbound firewall and resolve-only mDNS')


def _online(vm, passed):
    # Fallback leaves the guest stopped; a failed fallback can leave it
    # running. Start published delivery from a known stopped state.
    try:
        vm.stop()
    except Exception:
        pass
    integration.verify_published(vm, passed)


def guest_alive(vm, timeout=30):
    try:
        if vm.process is None or vm.process.poll() is not None:
            return False
        vm.command('true', timeout=timeout)
        return True
    except Exception:
        return False


def run_stages(vm, passed, stages):
    """Run (name, func) stages in order, continuing past failures.

    Continues while the guest answers. Boot or desktop failing stops the run,
    since later stages cannot mean anything without them.
    Returns (failures, skipped).
    """
    failures = {}
    skipped = []
    for index, (name, func) in enumerate(stages):
        start = time.monotonic()
        try:
            func(vm, passed)
        except Exception as error:
            failures[name] = error
            print(f'FAIL: {name} after {time.monotonic() - start:.0f}s: {error}', flush=True)
            if name in ('boot', 'desktop') or not guest_alive(vm):
                skipped.extend(n for n, _ in stages[index + 1 :])
                break
            continue
        print(f'DONE: {name} in {time.monotonic() - start:.0f}s', flush=True)
    return failures, skipped


def run_fixture(args, parser):
    """Fixture mode (folded verify_fast.py): qualify new packages on an accepted overlay."""
    for flag in ('--iso', '--disk', '--install-only', '--online-repository', '--swap-gib', '--slow'):
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
        vm.probe('boot', 'plymouth-config')
        vm.probe('policy', 'check', timeout=180)
        vm.probe('desktop', 'niri-overrides', timeout=180)
        vm.probe('desktop', 'wallpaper-preserved', timeout=180)
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


def run_install(vm, passed, evidence, directory, package_digest, serial_preboot=False):
    """Install the ISO offline and prepare the target. Reused by full and release runs."""
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
    vm.command(f'test "$(sha256sum /run/archiso/bootmnt/packages.manifest.json | cut -d " " -f 1)" = {package_digest}')
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
    if serial_preboot:
        # The release check boots once, over serial, with no graphical unlock
        # to qualify. Enable the serial kernel console on the target now;
        # full acceptance does this after its first graphical boot instead.
        vm.command(
            'cd /run/verify && python3 -m verification.boot serial-console-enable /mnt/verify; '
            'arch-chroot /mnt/verify limine-mkinitcpio',
            timeout=300,
        )
    vm.command('umount -R /mnt/verify; cryptsetup close dux-root; sync')
    vm.stop()


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
    parser.add_argument(
        '--slow',
        action='store_true',
        help='Include the slow Dux updater stage (releases via --online-repository always include it)',
    )
    parser.add_argument('--swap-gib', type=int, help='Override Auto swap sizing; 0 disables swap and hibernation')
    parser.add_argument('--keymap', default='us', help='Console keymap for the installed system (default: us)')
    parser.add_argument(
        '--keep',
        action='store_true',
        help='On failure keep the guest running for debugging; the disk is still unlinked',
    )
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
    if args.fixture is not None:
        return run_fixture(args, parser)
    if args.kernel_package is not None:
        parser.error('--kernel-package needs --fixture.')
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
    slow = args.slow or args.online_repository
    stages = [
        ('boot', lambda vm, passed: boot.verify_boot(vm, passed, args.swap_gib)),
        ('policy', _policy),
        ('network', _network),
        ('desktop', desktop.verify),
        ('apps', apps.verify),
        ('pdf', pdf.verify),
        ('camera', camera.verify),
        ('browser', browser.verify),
        ('lock', desktop.verify_lock),
        ('suspend', suspend.verify),
        ('dux_update', dux_update.verify),
        ('recovery', recovery.verify),
        ('fallback', boot.verify_fallback),
    ]
    if not slow:
        stages = [(name, func) for name, func in stages if name != 'dux_update']
    if args.online_repository:
        stages.append(('online', _online))
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
