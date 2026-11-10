#!/usr/bin/env python3
"""Drive a kept verify-fast VM: attach to its sockets and run one verb.

The guest mounts this evidence directory at /run/verify, so files reach it
without a push verb: copy on the host, then sh 'cp /run/verify/<name> <dest>'.
One driver at a time; the serial marker protocol does not multiplex.
"""

import argparse
from pathlib import Path
import sys

from verification.vm import VM


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path, help='Evidence directory of a kept (--keep) VM')
    parser.add_argument('verb', choices=('sh', 'probe', 'shot', 'click', 'key', 'poweroff'))
    parser.add_argument('args', nargs='*', help='Verb arguments')
    args = parser.parse_args()
    directory = args.directory.resolve()
    vm = VM(directory, None, directory / 'disk.qcow2')
    vm.socket_directory = directory / '.sockets'
    try:
        vm.attach()
    except RuntimeError as error:
        print(f'vm_console: {error}', file=sys.stderr)
        raise SystemExit(1)
    try:
        if args.verb == 'sh':
            if not args.args:
                parser.error('sh needs a guest command.')
            print(vm.command(' '.join(args.args)), end='')
        elif args.verb == 'probe':
            if len(args.args) < 2:
                parser.error('probe needs a module and a function.')
            print(vm.probe(args.args[0], *args.args[1:]), end='')
        elif args.verb == 'shot':
            if len(args.args) != 1:
                parser.error('shot needs a screenshot name.')
            path = vm.screenshot(args.args[0])
            print(path if path is not None else directory / (args.args[0] + '.unavailable.txt'))
        elif args.verb == 'click':
            if len(args.args) != 2:
                parser.error('click needs fractional x y coordinates.')
            try:
                x, y = (float(value) for value in args.args)
            except ValueError:
                parser.error('click coordinates must be numbers between 0 and 1.')
            if not 0 <= x <= 1 or not 0 <= y <= 1:
                parser.error('click coordinates must be numbers between 0 and 1.')
            vm.click(x, y)
        elif args.verb == 'key':
            if len(args.args) != 1:
                parser.error('key needs a QMP key name.')
            vm.key(args.args[0])
        elif args.verb == 'poweroff':
            if args.args:
                parser.error('poweroff takes no arguments.')
            vm.poweroff()
    finally:
        vm.stop()


if __name__ == '__main__':
    main()
