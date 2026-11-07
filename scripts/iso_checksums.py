#!/usr/bin/env python3
"""Authorize one locally built ISO without giving the builder a release secret."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

from integration_release import signing_subkey, verify
from verification.digest import digest


REPOSITORY = 'abdulrahman-aj/arch-niri-dux'
ASSETS = ('SHA256SUMS', 'SHA256SUMS.sig', 'integration-signing.gpg')


def iso_name(tag):
    match = re.fullmatch(r'v([0-9]+(?:\.[0-9]+)*)-[0-9]+(?:\.[0-9]+)*', tag)
    if not match:
        raise ValueError('Expected a package release tag: v<version>-<pkgrel>.')
    return f'arch-niri-dux-{match[1]}-x86_64.iso'


def checksums(tag, sha256):
    name = iso_name(tag)
    if not re.fullmatch(r'[a-f0-9]{64}', sha256):
        raise ValueError('Expected one lowercase SHA-256 digest.')
    return f'{sha256}  {name}\n'.encode('ascii')


def prepare(tag, iso, output):
    if iso.name != iso_name(tag) or iso.is_symlink() or not iso.is_file():
        raise ValueError('Select one regular ISO with the canonical release filename.')
    content = checksums(tag, digest(iso))
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'SHA256SUMS'
    # Do not silently replace an earlier release candidate's authorization.
    with path.open('xb') as stream:
        stream.write(content)
    return content.decode('ascii')


def validate_checkout(root, tag, sha256, commit):
    content = checksums(tag, sha256)
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Expected a full source commit.')

    def git(*args):
        try:
            return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
        except subprocess.CalledProcessError as error:
            raise ValueError('Validation requires the checked-out tag and fetched origin/main refs.') from error

    if git('rev-parse', 'HEAD') != commit or git('rev-parse', f'refs/tags/{tag}^{{commit}}') != commit:
        raise ValueError('Checkout and release tag must match the dispatched commit.')
    git('rev-parse', '--verify', 'origin/main^{commit}')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, 'origin/main'], check=True)
    return f'ISO checksum authorization for `{tag}`\n\nSource commit: `{commit}`\n\n```text\n{content.decode()}```\n'


def sign(root, directory, secret):
    if not secret:
        raise ValueError('Missing CI signing secret.')
    # Neither keys nor agents survive success, import failure, or signing failure.
    with tempfile.TemporaryDirectory(prefix='dux-iso-signing-') as temporary:
        home = Path(temporary)
        environment = dict(os.environ, GNUPGHOME=str(home))
        environment.pop('PACKAGE_SIGNING_KEY', None)
        try:
            subprocess.run(['gpg', '--batch', '--import'], input=secret.encode(), env=environment, check=True)
            subprocess.run(
                [
                    'gpg',
                    '--batch',
                    '--local-user',
                    signing_subkey(root) + '!',
                    '--digest-algo',
                    'SHA256',
                    '--output',
                    str(directory / 'SHA256SUMS.sig'),
                    '--detach-sign',
                    str(directory / 'SHA256SUMS'),
                ],
                env=environment,
                check=True,
            )
            verify(root, directory / 'SHA256SUMS', current_signer=True)
        finally:
            subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'all'], check=False)


class GitHubRelease:
    """Only add assets to an existing published release; never create or replace it."""

    def __init__(self, tag):
        iso_name(tag)
        self.tag = tag
        release = json.loads(self.api(f'repos/{REPOSITORY}/releases/tags/{tag}'))
        if release['tag_name'] != tag or release['draft'] or release['prerelease']:
            raise ValueError('Expected an existing published, non-prerelease package release.')
        pages = json.loads(self.api(f'repos/{REPOSITORY}/releases/{release["id"]}/assets', '--paginate', '--slurp'))
        self.entries = {}
        for entry in (entry for page in pages for entry in page):
            name = entry['name']
            if name not in ASSETS:
                continue
            if name in self.entries or not 0 < entry['size'] <= 1024 * 1024:
                raise ValueError('Duplicate or oversized ISO authorization asset.')
            self.entries[name] = entry['id']

    @staticmethod
    def api(endpoint, *args):
        return subprocess.check_output(['gh', 'api', endpoint, *args])

    def names(self):
        return set(self.entries)

    def download(self, name, destination):
        with destination.open('wb') as stream:
            subprocess.run(
                [
                    'gh',
                    'api',
                    f'repos/{REPOSITORY}/releases/assets/{self.entries[name]}',
                    '-H',
                    'Accept: application/octet-stream',
                ],
                stdout=stream,
                check=True,
            )

    def upload(self, path):
        # gh refuses duplicate names without --clobber, including concurrent uploads.
        subprocess.run(['gh', 'release', 'upload', self.tag, '--repo', REPOSITORY, str(path)], check=True)


def publish(root, tag, sha256, release, secret=''):
    content = checksums(tag, sha256)
    with tempfile.TemporaryDirectory(prefix='dux-iso-assets-') as temporary:
        directory = Path(temporary)
        expected = {
            'SHA256SUMS': content,
            'integration-signing.gpg': (root / 'keys/integration-signing.gpg').read_bytes(),
        }
        present = release.names()
        for name, data in expected.items():
            path = directory / name
            if name in present:
                release.download(name, path)
                if path.read_bytes() != data:
                    raise ValueError(f'Refusing to replace conflicting release asset: {name}')
            else:
                path.write_bytes(data)
        if 'SHA256SUMS.sig' in present:
            release.download('SHA256SUMS.sig', directory / 'SHA256SUMS.sig')
            verify(root, directory / 'SHA256SUMS', current_signer=True)
        else:
            sign(root, directory, secret)
        # Signatures include timestamps. Preserve an existing valid signature rather
        # than generating different bytes on a retry. Validate everything before uploading.
        for name in ASSETS:
            if name not in present:
                release.upload(directory / name)


def exit_on_signal(number, _frame):
    raise SystemExit(128 + number)


def main():
    secret = os.environ.pop('PACKAGE_SIGNING_KEY', '')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'validate', 'publish'):
        command = commands.add_parser(name)
        command.add_argument('--tag', required=True)
        if name == 'prepare':
            command.add_argument('--iso', type=Path, required=True)
            command.add_argument('--output', type=Path, default=Path('out/iso-checksums'))
        else:
            command.add_argument('--root', type=Path, default=Path.cwd())
            command.add_argument('--sha256', required=True)
            command.add_argument('--commit', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(prepare(args.tag, args.iso, args.output), end='')
        return
    summary = validate_checkout(args.root, args.tag, args.sha256, args.commit)
    if args.command == 'validate':
        print(summary)
        if summary_path := os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(summary_path, 'a', encoding='utf-8') as stream:
                stream.write(summary)
    else:
        if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF_TYPE') != 'tag':
            raise ValueError('Publication requires this repository and a tag dispatch.')
        publish(args.root, args.tag, args.sha256, GitHubRelease(args.tag), secret)


if __name__ == '__main__':
    # Let finally/context-manager cleanup run when Actions cancels a signing job.
    for termination_signal in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(termination_signal, exit_on_signal)
    main()
