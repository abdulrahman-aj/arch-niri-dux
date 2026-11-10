#!/usr/bin/env python3
"""Drive a kept VM (verify --keep or verify-fast --keep): attach to its sockets and run one verb.

The guest mounts this evidence directory at /run/verify, so files reach it
without a push verb: copy on the host, then sh 'cp /run/verify/<name> <dest>'.
One driver at a time; the serial marker protocol does not multiplex.
"""

import argparse
import importlib
from pathlib import Path
import re
import sys

from verification.vm import VM

ANSI_RE = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]')
MARKER_RE = re.compile(r'__VERIFY_[0-9A-Fa-f]+__:[0-9]+')
ECHO_RE = re.compile(r'__VERIFY_[0-9A-Fa-f]+__:%s')
# Stages whose verify(vm, passed) never restarts QEMU. Suspend and recovery
# power the guest off and start a new QEMU on the evidence disk, which a kept
# failed-verify run has already unlinked; running them would destroy the
# debug session instead of qualifying anything.
STAGES = ('apps', 'browser', 'camera', 'desktop', 'dux_update', 'pdf')


def clean_output(output):
    """Drop the serial command's completion marker line and terminal escapes."""
    kept = []
    for line in output.split('\n'):
        text = ANSI_RE.sub('', line).replace('\r', '')
        stripped = text.strip()
        # The guest echoes the sent payload, whose printf carries a literal %s; drop that line too.
        if MARKER_RE.fullmatch(stripped) or ECHO_RE.search(stripped):
            continue
        kept.append(text)
    return '\n'.join(kept).rstrip('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path, help='Evidence directory of a kept (--keep) VM')
    parser.add_argument('verb', choices=('sh', 'probe', 'shot', 'click', 'key', 'poweroff', 'stage'))
    parser.add_argument('args', nargs='*', help='Verb arguments')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not (directory / '.sockets').is_dir():
        parser.error(f'No kept VM sockets in {directory / ".sockets"}; start one with verify-fast --keep first.')
    vm = VM(directory, None, directory / 'disk.qcow2')
    vm.socket_directory = directory / '.sockets'
    try:
        try:
            vm.attach()
        except RuntimeError as error:
            print(f'vm_console: {error}', file=sys.stderr)
            raise SystemExit(1)
        if args.verb == 'sh':
            if args.args:
                script = ' '.join(args.args)
            else:
                if sys.stdin.isatty():
                    parser.error('sh needs a guest command or a script on stdin.')
                script = sys.stdin.read()
                if not script.strip():
                    parser.error('sh needs a guest command or a script on stdin.')
            print(clean_output(vm.command(script)), end='')
        elif args.verb == 'probe':
            if len(args.args) < 2:
                parser.error('probe needs a module and a function.')
            print(clean_output(vm.probe(args.args[0], *args.args[1:])), end='')
        elif args.verb == 'shot':
            if len(args.args) != 1:
                parser.error('shot needs a screenshot name.')
            name = args.args[0]
            vm.probe('guest', 'screenshot', name)
            print(directory / name)
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
        elif args.verb == 'stage':
            if len(args.args) != 1:
                parser.error('stage needs a verification module name.')
            name = args.args[0]
            if name not in STAGES:
                parser.error(f'unknown stage: {name!r} (choose from {", ".join(STAGES)}).')
            module = importlib.import_module('verification.' + name)

            def passed(message):
                print('PASS: ' + message, flush=True)

            module.verify(vm, passed)
    finally:
        vm.stop()


if __name__ == '__main__':
    main()
