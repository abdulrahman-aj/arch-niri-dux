#!/usr/bin/env python3
"""Discover first-party Bash/POSIX/Korn shell sources, including untracked files."""

import argparse
import os
from pathlib import Path
import re
import subprocess


# Four-space indent with indented case branches matches the checked-in scripts.
SHFMT = ['shfmt', '-i', '4', '-ci']


def discover():
    root = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).rstrip('\n')
    os.chdir(root)
    names = subprocess.check_output([
        'git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--',
        ':(exclude)out/**', ':(exclude)packages/aur/**',
    ])
    scripts, recipes = [], []
    for name in sorted(set(names.split(b'\0')) - {b''}):
        path = Path(os.fsdecode(name))
        if path.is_symlink() or not path.is_file():
            continue
        if path.name == 'PKGBUILD':
            recipes.append(str(path))
            continue
        with path.open('rb') as stream:
            first_line = stream.readline(256)
        if path.suffix in ('.sh', '.bash', '.ksh') or re.match(
                rb'^#!\s*(?:/usr/bin/env\s+(?:-S\s+)?)?(?:/\S*/)?(?:bash|sh|dash|ksh)(?:\s|$)',
                first_line):
            scripts.append(str(path))
    return scripts, recipes


def lint(scripts, recipes):
    if scripts:
        subprocess.run(['shellcheck', '--', *scripts], check=True)
    if recipes:
        # makepkg consumes metadata, supplies paths and makes pkgname scalar in
        # split-package functions. Those conventions aren't ordinary Bash dataflow.
        subprocess.run(['shellcheck', '--shell=bash', '--exclude=SC2034,SC2154,SC2128',
                        '--', *recipes], check=True)


def format_shell(scripts, write):
    # PKGBUILDs keep makepkg's aligned-continuation conventions; only the
    # plain shell sources above share shfmt's style.
    if not scripts:
        return
    if write:
        subprocess.run([*SHFMT, '-w', '--', *scripts], check=True)
    else:
        subprocess.run([*SHFMT, '-d', '--', *scripts], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--format', dest='format', action='store_true',
                       help='write shfmt formatting to the discovered shell sources')
    group.add_argument('--check-format', action='store_true',
                       help='fail if shfmt would reformat any discovered shell source')
    args = parser.parse_args()
    scripts, recipes = discover()
    if args.format:
        format_shell(scripts, write=True)
    elif args.check_format:
        format_shell(scripts, write=False)
    else:
        lint(scripts, recipes)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
