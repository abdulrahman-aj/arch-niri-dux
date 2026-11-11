#!/usr/bin/env python3
"""Update the vendored Dux app recipes to their latest upstream GitHub releases.

Prints a one-line summary of the updated apps, or nothing when all are current.
The recipes stay ours: versions, checksums and vendored trust files change, and every
release-asset checksum must equal the digest GitHub publishes for that asset.
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

from verification.digest import digest

ARCHES = ('', '_x86_64', '_aarch64')
import dux_apps as apps

# Stable local filenames keep upstream policy and service changes visible in bump PRs.
VENDORED_SOURCES = {
    'dux-calendar-bin': {
        'dev.duxlabs.calendar.desktop': 'assets/dev.duxlabs.calendar.desktop',
        'dcal.service': 'assets/systemd/dcal.service',
    },
    'dux-files-bin': {
        'dev.duxlabs.files.desktop': 'assets/dev.duxlabs.files.desktop',
        'dev.duxlabs.files.metainfo.xml': 'assets/dev.duxlabs.files.metainfo.xml',
        'dev.duxlabs.files.policy': 'assets/dev.duxlabs.files.policy',
        'dev.duxlabs.files.FileManager1.service': 'assets/dbus/dev.duxlabs.files.FileManager1.service',
        'org.freedesktop.impl.portal.desktop.dfiles.service': 'assets/dbus/org.freedesktop.impl.portal.desktop.dfiles.service',
        'dfiles.portal': 'assets/portal/dfiles.portal',
        'dfiles.service': 'assets/systemd/dfiles.service',
    },
    'dux-greeter-bin': {
        'tmpfiles-dms-greeter.conf': 'assets/systemd/tmpfiles-dms-greeter.conf',
        'dms-niri.kdl': 'assets/examples/dms-niri.kdl',
        'dms-hypr.lua': 'assets/examples/dms-hypr.lua',
    },
}


def srcinfo(text):
    """Map each .SRCINFO key to its values in order (sources pair with checksums by index)."""
    fields = {}
    for key, value in re.findall(r'^\s*(\S+) = (.*)$', text, re.MULTILINE):
        fields.setdefault(key, []).append(value)
    return fields


def set_version(pkgbuild, version):
    for key, value in (('pkgver', version), ('pkgrel', '1')):
        pkgbuild, count = re.subn(rf'^{key}=[0-9]+(?:\.[0-9]+)*$', f'{key}={value}', pkgbuild, flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f'Expected exactly one numeric literal {key} assignment.')
    return pkgbuild


def set_checksums(pkgbuild, arrays):
    """Replace the sha256sums arrays with makepkg -g output, keeping their position."""
    pattern = re.compile(r'^sha256sums(?:_[a-z0-9_]+)?=\([^)]*\)\n', re.MULTILINE)
    first = pattern.search(pkgbuild)
    if first is None:
        raise ValueError('Expected sha256sums arrays in the recipe.')
    rest = pattern.sub('', pkgbuild[first.start() :])
    return pkgbuild[: first.start()] + arrays.strip() + '\n' + rest


def check_sources(fields, repository, release):
    """Reject stale sources and any release-asset checksum that differs from GitHub's digest."""
    tag = release['tag_name']
    digests = {asset['name']: asset.get('digest') for asset in release['assets']}
    assets = f'{repository}/releases/download/{tag}/'
    raw = repository.replace('https://github.com/', 'https://raw.githubusercontent.com/') + f'/{tag}/'
    for arch in ARCHES:
        sources, sums = fields.get('source' + arch, []), fields.get('sha256sums' + arch, [])
        if len(sources) != len(sums):
            raise ValueError('Every source needs a SHA-256 checksum.')
        for source, checksum in zip(sources, sums, strict=False):
            url = source.split('::', 1)[-1]
            if url.startswith(assets):
                if digests.get(url.removeprefix(assets)) != f'sha256:{checksum}':
                    raise ValueError(f'Checksum differs from the GitHub release digest: {url}')
            elif url.startswith(raw):
                pass  # Repository files at the release tag; GitHub publishes no digest for them.
            elif '://' in url:
                raise ValueError(f'Source is not from the upstream release {tag}: {url}')


def refresh_vendored(work, name, repository, tag):
    raw = repository.replace('https://github.com/', 'https://raw.githubusercontent.com/')
    for filename, upstream in VENDORED_SOURCES.get(name, {}).items():
        with urllib.request.urlopen(f'{raw}/{tag}/{upstream}', timeout=60) as response:
            (work / filename).write_bytes(response.read())


def latest_release(repository):
    request = urllib.request.Request(
        repository.replace('https://github.com/', 'https://api.github.com/repos/') + '/releases/latest',
        headers={'Accept': 'application/vnd.github+json'},
    )
    if token := os.environ.get('GH_TOKEN'):
        request.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def unprivileged(work, *arguments):
    # makepkg refuses root; the CI container runs the bot as root.
    prefix = ['runuser', '-u', 'nobody', '--'] if os.geteuid() == 0 else []
    return subprocess.check_output([*prefix, *arguments], cwd=work, text=True)


def bump(root, recipe):
    source = root / 'packages/aur' / recipe['name']
    fields = srcinfo((source / '.SRCINFO').read_text())
    repository = fields['url'][0]
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+', repository):
        raise ValueError(f'Upstream is not a GitHub repository: {repository}')
    release = latest_release(repository)
    match = re.fullmatch(r'v([0-9]+(?:\.[0-9]+)*)', release['tag_name'])
    if not match:
        raise ValueError(f'Unexpected release tag: {release["tag_name"]}')
    version = match[1]
    if int(subprocess.check_output(['vercmp', version, fields['pkgver'][0]], text=True)) <= 0:
        return None
    with tempfile.TemporaryDirectory(prefix='dux-bump-') as directory:
        work = Path(directory)
        for name in recipe['files']:
            shutil.copy2(source / name, work / name)
        pkgbuild = set_version((work / 'PKGBUILD').read_text(), version)
        (work / 'PKGBUILD').write_text(pkgbuild)
        if os.geteuid() == 0:
            for path in (work, *work.iterdir()):
                shutil.chown(path, 'nobody')
        refresh_vendored(work, recipe['name'], repository, release['tag_name'])
        # makepkg -g downloads every architecture's sources and prints their checksums.
        (work / 'PKGBUILD').write_text(set_checksums(pkgbuild, unprivileged(work, 'makepkg', '-g')))
        text = unprivileged(work, 'makepkg', '--printsrcinfo')
        check_sources(srcinfo(text), repository, release)
        (work / '.SRCINFO').write_text(text)
        for name in recipe['files']:
            shutil.copyfile(work / name, source / name)
    recipe['files'] = {name: digest(source / name) for name in recipe['files']}
    published = datetime.fromisoformat(release['published_at'].replace('Z', '+00:00'))
    recipe['source_date_epoch'] = int(published.timestamp())
    return f'{recipe["name"]} {version}'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path)
    root = parser.parse_args().root.resolve()
    manifest = root / 'packages/aur-recipes.json'
    recipes = json.loads(manifest.read_text())
    for recipe in recipes:
        apps.validate_recipe(root, recipe)
    updated = []
    for recipe in recipes:
        if summary := bump(root, recipe):
            # Record each finished recipe, so a later failure leaves no unrecorded edit.
            manifest.write_text(json.dumps(recipes, indent=2) + '\n')
            updated.append(summary)
    if updated:
        print(', '.join(updated))


if __name__ == '__main__':
    main()
