#!/usr/bin/env python3
"""Release acceptance: install the release ISO, boot once, check online delivery."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify import ROOT, prepare_integration_inputs, run_install

from workspace import disk_guard, own
from verification.digest import digest
from verification.evidence import Evidence
from verification.vm import VM, PASSWORD, stage_probes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True, help='Package release tag, e.g. v0.3.2-1')
    parser.add_argument('--iso', type=Path, help='Release ISO (default: build it)')
    parser.add_argument('--directory', type=Path, help='Fresh workspace')
    args = parser.parse_args()
    import iso_checksums
    from verification import upgrade

    canonical_name = iso_checksums.iso_name(args.tag)
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('The VM verification requires access to /dev/kvm.')
    iso = args.iso.resolve() if args.iso else None
    if iso is None:
        subprocess.run(['make', 'iso'], cwd=ROOT, check=True)
        isos = sorted((ROOT / 'out').glob('*.iso'), key=lambda p: p.stat().st_mtime)
        if not isos:
            parser.error('The build produced no ISO; pass --iso to select one.')
        iso = isos[-1]
    package_digest = digest(ROOT / 'out/packages.manifest.json')
    manifest = json.loads((ROOT / 'out/packages.manifest.json').read_text())['integration']
    if manifest.get('version') != args.tag[1:]:
        parser.error('Manifest version does not match --tag.')
    embedded = subprocess.run(['bsdtar', '-xOf', str(iso), 'packages.manifest.json'], capture_output=True, check=False)
    if hashlib.sha256(embedded.stdout).hexdigest() != package_digest:
        parser.error("ISO does not embed this checkout's package manifest.")
    directory = args.directory.resolve() if args.directory else ROOT / 'out' / f'verify-release-{args.tag[1:]}'
    disk_guard(directory)
    disk = directory / 'disk.qcow2'
    if disk.exists():
        parser.error('Refusing to overwrite an existing VM disk.')
    evidence = Evidence(directory, parents=True, exist_ok=True)
    own(directory)
    iso_digest = digest(iso)
    canonical = ROOT / 'out' / canonical_name
    # SHA256SUMS hashes the canonical file while the dispatch command carries
    # iso_digest; refuse a stale staged ISO before the long VM run.
    if canonical.exists() and digest(canonical) != iso_digest:
        parser.error(f'A different ISO is already staged as {canonical}; remove it first.')
    evidence.inputs(
        {
            'tag': args.tag,
            'iso_sha256': iso_digest,
            'package_manifest_sha256': package_digest,
            'verification_scripts': stage_probes(directory),
        }
    )
    evidence.key(PASSWORD)
    prepare_integration_inputs(directory)
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '48G'], check=True)
    (directory / 'install.json').write_text(
        json.dumps(
            {
                'disk': '/dev/vda',
                'username': 'tester',
                'hostname': 'dux-verify',
                'timezone': 'UTC',
                'keymap': 'us',
                'passphrase': PASSWORD,
                'password': PASSWORD,
            }
        )
    )
    (directory / 'install.json').chmod(0o600)
    vm = VM(directory, iso, disk)
    try:
        run_install(vm, evidence.passed, evidence, directory, package_digest, serial_preboot=True)
        upgrade.verify_published(vm, evidence.passed)
        (directory / 'RELEASE-CHECK-PASSED').write_text('Release install and online delivery passed.\n')
        if not canonical.exists():
            shutil.copyfile(iso, canonical)
        iso_checksums.prepare(args.tag, canonical, ROOT / 'out' / 'iso-checksums')
        print(f'gh workflow run iso-checksums.yml --ref main -f tag={args.tag} -f sha256={iso_digest}')
        return 0
    except Exception as error:
        (directory / 'FAILURE.txt').write_text(str(error) + '\n')
        print(f'Release check failed: {error}', file=sys.stderr)
        return 1
    finally:
        vm.stop()
        if not (directory / 'RELEASE-CHECK-PASSED').exists():
            disk.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
