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


def validate_checkout(root, tag, sha256, commit, source_commit=None):
    checksums(tag, sha256)
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Expected a full workflow commit.')

    def git(*args):
        try:
            return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
        except subprocess.CalledProcessError as error:
            raise ValueError('Validation requires the fetched release tag and origin/main refs.') from error

    if git('rev-parse', 'HEAD') != commit:
        raise ValueError('Checkout must match the dispatched main commit.')
    source = git('rev-parse', f'refs/tags/{tag}^{{commit}}')
    if source_commit is not None and source != source_commit:
        raise ValueError('Release tag changed after validation.')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, 'origin/main'], check=True)
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', source, commit], check=True)
    return source


def validate_release(tag):
    iso_name(tag)
    release = json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPOSITORY}/releases/tags/{tag}']))
    if release['tag_name'] != tag or release['draft'] or release['prerelease']:
        raise ValueError('Expected an existing published, non-prerelease package release.')


def summary(tag, sha256, source, commit):
    return (
        f'ISO checksum authorization for `{tag}`\n\n'
        f'ISO source commit: `{source}`\n\nWorkflow commit: `{commit}`\n\n'
        f'```text\n{checksums(tag, sha256).decode()}```\n'
    )


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
                    '--armor',
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


def sign_checksums(root, tag, sha256, output, secret):
    content = checksums(tag, sha256)
    output.mkdir(parents=True)
    (output / 'SHA256SUMS').write_bytes(content)
    sign(root, output, secret)


def exit_on_signal(number, _frame):
    raise SystemExit(128 + number)


def main():
    secret = os.environ.pop('PACKAGE_SIGNING_KEY', '')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'validate', 'sign'):
        command = commands.add_parser(name)
        command.add_argument('--tag', required=True)
        if name in ('prepare', 'sign'):
            command.add_argument('--output', type=Path, default=Path('out/iso-checksums'))
        if name == 'prepare':
            command.add_argument('--iso', type=Path, required=True)
        else:
            command.add_argument('--root', type=Path, default=Path.cwd())
            command.add_argument('--sha256', required=True)
            command.add_argument('--commit', required=True)
        if name == 'sign':
            command.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(prepare(args.tag, args.iso, args.output), end='')
        return
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Authorization requires a dispatch at this repository main branch.')
    source = validate_checkout(args.root, args.tag, args.sha256, args.commit, getattr(args, 'source_commit', None))
    validate_release(args.tag)
    text = summary(args.tag, args.sha256, source, args.commit)
    if args.command == 'sign':
        sign_checksums(args.root, args.tag, args.sha256, args.output, secret)
        text += f'\nSHA256SUMS.sig\n\n```text\n{(args.output / "SHA256SUMS.sig").read_text()}```\n'
        text += '\nDownload the run artifact and share both checksum files alongside the ISO.\n'
    elif output_path := os.environ.get('GITHUB_OUTPUT'):
        with open(output_path, 'a', encoding='utf-8') as stream:
            stream.write(f'source_commit={source}\n')
    print(text)
    if summary_path := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary_path, 'a', encoding='utf-8') as stream:
            stream.write(text)


if __name__ == '__main__':
    # Let finally/context-manager cleanup run when Actions cancels a signing job.
    for termination_signal in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(termination_signal, exit_on_signal)
    main()
