#!/usr/bin/env python3
"""Release acceptance: install the release ISO, boot once, check online delivery, stage checksums.

Implements packages/RELEASING.md step 5 onward in one command. The release
ISO differs from the verified candidate only in CI-built copies of our
packages, whose source hash integration_release already checks, so this runs
no offline stages: it installs, boots once with networking, and runs only
the online delivery check against offline trust and exact release hashes.
It then stages the canonical ISO and its SHA256SUMS and prints the
ISO-checksum dispatch command. Approval, artifact download, and sharing stay
in the runbook.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from workspace import disk_guard, own

from verification.digest import digest
from verification.evidence import Evidence

from verification.vm import VM, PASSWORD, stage_probes

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True, help='Package release tag, e.g. v0.3.2-1')
    parser.add_argument('--iso', type=Path, help='Release ISO (default: build it with make iso)')
    parser.add_argument('--directory', type=Path, help='Fresh workspace (default: out/verify-release-<tag>)')
    args = parser.parse_args()
    import iso_checksums

    try:
        canonical_name = iso_checksums.iso_name(args.tag)
    except ValueError as error:
        parser.error(str(error))
    iso = args.iso.resolve() if args.iso else None
    if iso is None:
        # A release ISO embeds the exact authenticated packages, rather than
        # independently rebuilding packages under the same version. Verify the
        # artifact the build just produced, not a retained older one.
        before = set((ROOT / 'out').glob('*.iso'))
        subprocess.run(['make', 'iso'], cwd=ROOT, check=True)
        new = sorted(set((ROOT / 'out').glob('*.iso')) - before)
        if len(new) != 1:
            parser.error('The build did not produce exactly one new ISO; pass --iso to select one.')
        iso = new[0]
    if not iso.is_file():
        parser.error(f'ISO not found: {iso}')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('The VM verification requires access to /dev/kvm.')
    manifest_path = ROOT / 'out/packages.manifest.json'
    if not manifest_path.is_file():
        parser.error('No out/packages.manifest.json; build the release ISO first.')
    package_digest = digest(manifest_path)
    manifest = json.loads(manifest_path.read_text())['integration']
    if manifest.get('version') != args.tag[1:]:
        parser.error(f'Release manifest version {manifest.get("version")} does not match --tag {args.tag}.')
    embedded = subprocess.run(['bsdtar', '-xOf', str(iso), 'packages.manifest.json'], capture_output=True, check=False)
    if embedded.returncode != 0 or hashlib.sha256(embedded.stdout).hexdigest() != package_digest:
        parser.error("The ISO does not embed this checkout's package manifest; check --tag, --iso, and the build.")
    directory = args.directory.resolve() if args.directory else ROOT / 'out' / f'verify-release-{args.tag[1:]}'
    disk_guard(directory)
    disk = directory / 'disk.qcow2'
    if disk.exists():
        parser.error('Use a fresh verification directory; refusing to overwrite an existing VM disk.')
    evidence = Evidence(directory, parents=True, exist_ok=True)
    own(directory)
    iso_digest = digest(iso)
    canonical = ROOT / 'out' / canonical_name
    if canonical.exists() and digest(canonical) != iso_digest:
        parser.error(f'{canonical} already exists with different bytes; choose exactly one release ISO.')
    if (ROOT / 'out' / 'iso-checksums' / 'SHA256SUMS').exists():
        parser.error(
            'out/iso-checksums/SHA256SUMS already exists; retain or explicitly remove it before preparing another.'
        )
    probe_hashes = stage_probes(directory)
    probe_hashes['verify_release.py'] = digest(Path(__file__))
    evidence.inputs(
        {
            'tag': args.tag,
            'iso': str(iso),
            'iso_sha256': iso_digest,
            'package_manifest_sha256': package_digest,
            'verification_scripts': probe_hashes,
        }
    )
    evidence.key(PASSWORD)

    source = directory / 'integration-input'
    source.mkdir()
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
    config_file = directory / 'install.json'
    config_file.write_text(json.dumps(configuration))
    config_file.chmod(0o600)
    vm = VM(directory, iso, disk)
    succeeded = False
    try:
        print(f'VM evidence: {directory}', flush=True)
        vm.start()
        vm.expect(r'root@archiso[^\n]*#', timeout=180)
        vm.command(
            'grep -qw copytoram=n /proc/cmdline; mountpoint -q /run/archiso/bootmnt; test ! -e /run/archiso/copytoram/airootfs.sfs'
        )
        vm.mount_probes()
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
        evidence.passed('Offline encrypted installation completed')
        vm.command(
            'cryptsetup open --key-file /run/verify/key /dev/vda2 dux-root; mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; mount /dev/vda1 /mnt/verify/boot'
        )
        vm.probe('boot', 'prepare', '/mnt/verify', timeout=300)
        # The release check boots once, over serial, with no graphical unlock
        # to qualify. Enable the serial kernel console on the target now;
        # full acceptance does this after its first graphical boot instead.
        # A serial console forces Plymouth details mode; that UI is already
        # qualified, so the text prompt is what this path wants.
        vm.command(
            'cd /run/verify && python3 -m verification.boot serial-console-enable /mnt/verify; '
            'arch-chroot /mnt/verify limine-mkinitcpio',
            timeout=300,
        )
        vm.command('umount -R /mnt/verify; cryptsetup close dux-root; sync')
        vm.stop()
        # Boot once, with networking, and run only the online delivery check.
        from verification import integration

        integration.verify_published(vm, evidence.passed)
        (directory / 'RELEASE-CHECK-PASSED').write_text('Release install and online delivery passed.\n')
        print(f'Release check passed. Evidence: {directory}', flush=True)
        canonical = ROOT / 'out' / canonical_name
        if canonical.exists():
            if digest(canonical) != iso_digest:
                raise ValueError(f'{canonical} already exists with different bytes; choose exactly one release ISO.')
        else:
            shutil.copyfile(iso, canonical)
        checksums = ROOT / 'out' / 'iso-checksums'
        try:
            iso_checksums.prepare(args.tag, canonical, checksums)
        except FileExistsError:
            raise ValueError(
                f'{checksums / "SHA256SUMS"} already exists; retain or explicitly remove it before preparing another.'
            )
        print(f'gh workflow run iso-checksums.yml --ref main -f tag={args.tag} -f sha256={iso_digest}')
        succeeded = True
        return 0
    except Exception as error:
        (directory / 'FAILURE.txt').write_text(str(error) + '\n')
        print(f'Release check failed: {error}\nEvidence: {directory}', file=sys.stderr)
        return 1
    finally:
        vm.stop()
        # A release check never mints fixtures.
        if not succeeded:
            disk.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
