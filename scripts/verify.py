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

from verification.digest import digest

from verification import apps, boot, browser, camera, desktop, dux_update, integration, pdf, recovery
from verification.vm import VM, PASSWORD, stage_probes

ROOT = Path(__file__).resolve().parents[1]


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
    args = parser.parse_args()
    if args.swap_gib is not None and args.swap_gib < 0:
        parser.error('--swap-gib must be zero or a positive number of GiB.')
    images = sorted((ROOT / 'out').glob('*.iso'))
    iso = args.iso or (images[-1] if images else None)
    if iso is None or not iso.is_file():
        parser.error('Build an ISO first with make iso, or pass --iso PATH.')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('The VM verification requires access to /dev/kvm.')
    directory = args.directory
    if directory is None:
        # Runs hold multi-gigabyte disks, and /tmp is often RAM-backed tmpfs.
        (ROOT / 'out/verify').mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='dux-verify-', dir=ROOT / 'out/verify'))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    disk = args.disk or directory / 'disk.qcow2'
    if disk.exists():
        parser.error('Use a fresh verification directory; refusing to overwrite an existing VM disk.')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '48G'], check=True)
    configuration = dict(
        disk='/dev/vda',
        username='tester',
        hostname='dux-verify',
        timezone='UTC',
        keymap='us',
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
    (directory / 'inputs.json').write_text(
        json.dumps(
            {
                'iso': str(iso.resolve()),
                'iso_sha256': iso_digest,
                'package_manifest_sha256': package_digest,
                'verification_scripts': probe_hashes,
            },
            indent=2,
        )
        + '\n'
    )
    (directory / 'key').write_text(PASSWORD)
    (directory / 'key').chmod(0o600)

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
    milestones = []
    succeeded = False

    def passed(name):
        milestones.append(name)
        (directory / 'milestones.json').write_text(json.dumps(milestones, indent=2))
        print('PASS: ' + name, flush=True)

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
        for filename in ('install.py', 'configure-target.sh'):
            expected = digest(ROOT / 'installer' / filename)
            vm.command(
                f'test "$(sha256sum /usr/local/lib/arch-niri-dux/installer/{filename} | cut -d " " -f 1)" = {expected}'
            )
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
        boot.verify_boot(vm, passed, args.swap_gib)
        vm.probe('policy', 'check', timeout=180)
        passed('Package presets, systemd policy and PAM-unlocked keyring')
        vm.probe('network', 'check', timeout=240)
        passed('Inbound firewall and resolve-only mDNS')
        desktop.verify(vm, passed)
        apps.verify(vm, passed)
        pdf.verify(vm, passed)
        camera.verify(vm, passed)
        browser.verify(vm, passed)
        desktop.verify_lock(vm, passed)
        vm.probe('migrations', 'check', timeout=180)
        passed('Migration ordering, retries, session triggers and new-account baseline')
        dux_update.verify(vm, passed)
        recovery.verify(vm, passed)
        boot.verify_fallback(vm, passed)
        if args.online_repository:
            integration.verify_published(vm, passed)
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
        vm.stop()
        # A failed or interrupted run's disk can never become an accepted
        # fixture; drop it and keep the logs and screenshots.
        if not succeeded:
            disk.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
