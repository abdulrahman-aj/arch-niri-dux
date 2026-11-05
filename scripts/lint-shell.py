#!/usr/bin/env python3
"""Discover first-party Bash/POSIX/Korn shell sources, including untracked files."""

import os
from pathlib import Path
import re
import subprocess


def main():
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
    if scripts:
        subprocess.run(['shellcheck', '--', *scripts], check=True)
    if recipes:
        # makepkg consumes metadata, supplies paths and makes pkgname scalar in
        # split-package functions. Those conventions aren't ordinary Bash dataflow.
        subprocess.run(['shellcheck', '--shell=bash', '--exclude=SC2034,SC2154,SC2128',
                        '--', *recipes], check=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
