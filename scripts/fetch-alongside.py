#!/usr/bin/env python3
"""Fetch official disposable OS media with a reserved host-space budget."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

SOURCES = {
    'linux': ('https://cloud.debian.org/images/cloud/trixie/20261026-2643/debian-13-genericcloud-amd64-20261026-2643.qcow2', 'debian.qcow2'),
    'windows': ('https://go.microsoft.com/fwlink/?clcid=0x409&country=us&culture=en-us&linkid=2289029', 'windows11-ltsc-eval.iso'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os', choices=SOURCES, required=True)
    parser.add_argument('--verify-existing', action='store_true', help='Record/verify already downloaded official media without downloading it again')
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    url, filename = SOURCES[args.os]
    destination = args.directory / filename
    if destination.exists() and not args.verify_existing:
        parser.error('Media already exists; preserve it and use its recorded hashes rather than downloading again.')
    request = urllib.request.Request(url, method='HEAD' if args.verify_existing else 'GET')
    with urllib.request.urlopen(request, timeout=60) as response:
        size = int(response.headers['Content-Length'])
        if not args.verify_existing and shutil.disk_usage(args.directory).free < size + 8 * 1024**3:
            parser.error('Download would leave less than 8 GiB host headroom.')
        resolved = response.url
        if args.verify_existing:
            chunks = ()
        else:
            chunks = iter(lambda: response.read(4 * 1024**2), b'')
        if not args.verify_existing:
            with destination.open('xb') as stream:
                for chunk in chunks:
                    if shutil.disk_usage(args.directory).free < 8 * 1024**3 + len(chunk):
                        raise RuntimeError('Concurrent disk use exhausted the reserved download budget.')
                    stream.write(chunk)
    if destination.stat().st_size != size:
        raise RuntimeError('Incomplete official media download.')
    with destination.open('rb') as stream:
        sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    metadata = dict(source=url, resolved=resolved, size=size, sha256=sha256)
    if args.os == 'linux':
        manifest_url = url.rsplit('/', 1)[0] + '/SHA512SUMS'
        manifest = urllib.request.urlopen(manifest_url, timeout=60).read().decode()
        expected = next(line.split()[0] for line in manifest.splitlines() if line.endswith(url.rsplit('/', 1)[1]))
        with destination.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha512').hexdigest()
        if actual != expected:
            raise RuntimeError('Official Debian SHA512SUMS mismatch.')
        metadata['verified_sha512'] = actual
        (args.directory / 'SHA512SUMS').write_text(manifest)
    destination.with_suffix(destination.suffix + '.json').write_text(json.dumps(metadata, indent=2))
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
