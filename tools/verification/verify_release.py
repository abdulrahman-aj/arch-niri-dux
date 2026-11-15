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
import urllib.parse
from tools.verification.session import ROOT, prepare_integration_inputs, run_install
from tools.workspace.state import own
from tools.workspace.resources import disk_guard
from tools.digest import digest
from tools.verification.evidence import Evidence
from tools.vm.control import VM
from tools.verification.constants import PASSWORD
from tools.verification.payload import stage_probes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True, help='Package release tag, e.g. v0.3.2-1')
    parser.add_argument('--repository-url', help='Disposable rehearsal repository; skips ISO authorization staging')
    parser.add_argument('--iso', type=Path, help='Release ISO (default: build it)')
    parser.add_argument('--directory', type=Path, help='Fresh workspace')
    args = parser.parse_args()
    from tools.release import iso_checksums
    from tools.verification import upgrade

    if args.repository_url:
        url = urllib.parse.urlsplit(args.repository_url)
        if url.scheme not in ('http', 'https') or not url.netloc or any(c.isspace() for c in args.repository_url):
            parser.error('--repository-url must be an HTTP(S) URL without whitespace.')
    canonical_name = iso_checksums.iso_name(args.tag)
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        parser.error('The VM verification requires access to /dev/kvm.')
    if args.repository_url and not args.iso:
        parser.error('--repository-url requires an explicit --iso for rehearsal.')
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
    if directory.exists():
        parser.error('Verification requires a fresh workspace; choose another --directory.')
    disk_guard(directory)
    disk = directory / 'disk.qcow2'
    evidence = Evidence(directory, parents=True)
    own(directory)
    iso_digest = digest(iso)
    canonical = ROOT / 'out' / canonical_name
    # SHA256SUMS hashes the canonical file while the dispatch command carries
    # iso_digest; refuse a stale staged ISO before the long VM run.
    if not args.repository_url and canonical.exists() and digest(canonical) != iso_digest:
        parser.error(f'A different ISO is already staged as {canonical}; remove it first.')
    evidence.inputs(
        {
            'tag': args.tag,
            'repository_url': args.repository_url,
            'iso_sha256': iso_digest,
            'package_manifest_sha256': package_digest,
            'verification_scripts': stage_probes(directory),
        }
    )
    evidence.key(PASSWORD)
    if args.repository_url:
        (directory / 'repository-url').write_text(args.repository_url + '\n')
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
        run_install(
            vm,
            evidence.passed,
            evidence,
            directory,
            package_digest,
            serial_preboot=True,
            rehearsal=bool(args.repository_url),
        )
        upgrade.verify_published(vm, evidence.passed)
        (directory / 'RELEASE-CHECK-PASSED').write_text('Release install and online delivery passed.\n')
        print('RELEASE-CHECK-PASSED', flush=True)
        if args.repository_url:
            print('Disposable repository rehearsal; no ISO authorization staged.')
            return 0
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
