#!/usr/bin/env python3
"""Guided persistent test-drive VMs for humans (`make try`).

Disks persist under ~/.local/share/arch-niri-dux/try/<name>/; every open
attaches the newest installer ISO with the disk first in boot order."""

import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HUMAN_ONLY = 'make try is for humans; agents use make verify-fast ARGS=--keep'

AGENT_VARS = ['CLAUDECODE', 'AI_AGENT', 'AGENT']
AGENT_PREFIXES = ['CODEX_', 'OPENCODE', 'T3CODE_']

NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
DEFAULT_NAME = 'default'
DEFAULT_DISK_GIB = 48
MEMORY_GIB = 8


def refuse_if_agent(env=None):
    env = os.environ if env is None else env
    found = [name for name in AGENT_VARS if env.get(name)]
    for key, value in env.items():
        if value and key not in found and key.startswith(tuple(AGENT_PREFIXES)):
            found.append(key)
    if found:
        raise SystemExit(f'{HUMAN_ONLY} (agent environment: {", ".join(sorted(found))})')


def try_base():
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'arch-niri-dux' / 'try'


def valid_name(name):
    return bool(NAME_RE.fullmatch(name or ''))


def vm_dir(name, base=None):
    return (try_base() if base is None else Path(base)) / name


def list_vms(base=None):
    root = try_base() if base is None else Path(base)
    if not root.is_dir():
        return []
    return sorted([path for path in root.iterdir() if path.is_dir() and valid_name(path.name)])


def newest_iso():
    candidates = sorted(Path(os.environ.get('TRY_OUT_DIR', str(ROOT / 'out'))).glob('*.iso'))
    return candidates[-1] if candidates else None


def firmware():
    base = Path(os.environ.get('OVMF_DIR', '/usr/share/edk2/x64'))
    for code_name, vars_name in (
        ('OVMF_CODE.4m.fd', 'OVMF_VARS.4m.fd'),
        ('OVMF_CODE.fd', 'OVMF_VARS.fd'),
    ):
        code, template = base / code_name, base / vars_name
        if code.is_file() and template.is_file():
            return code, template
    raise SystemExit(f'OVMF firmware missing under {base}; install edk2-ovmf.')


def build_qemu_command(*, code, variables, disk, iso, gl=True):
    command = ['qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host', '-m', str(MEMORY_GIB * 1024)]
    command += ['-smp', '4']
    if gl:
        command += ['-display', 'gtk,gl=on,show-cursor=on', '-device', 'virtio-vga-gl']
    else:
        command += ['-display', 'gtk,show-cursor=on', '-device', 'virtio-vga']
    command += ['-drive', f'if=pflash,format=raw,readonly=on,file={code}']
    command += ['-drive', f'if=pflash,format=raw,file={variables}']
    command += ['-drive', f'if=none,id=target-disk,format=qcow2,file={disk}']
    command += ['-device', 'virtio-blk-pci,drive=target-disk,bootindex=1']
    command += ['-nic', 'user,model=virtio-net-pci']
    command += ['-drive', f'if=none,id=install-media,format=raw,readonly=on,file={iso}']
    command += ['-device', 'qemu-xhci', '-device', 'usb-storage,drive=install-media,bootindex=2']
    return command


def create_vm(name, base=None):
    if not valid_name(name):
        raise SystemExit(f'Invalid VM name {name!r}; use letters, digits, dot, dash or underscore.')
    directory = vm_dir(name, base)
    if directory.exists():
        raise SystemExit(f"VM '{name}' already exists; open or delete it.")
    for tool in ('qemu-img', 'qemu-system-x86_64'):
        if shutil.which(tool) is None:
            raise SystemExit(f'Missing required tool: {tool}')
    _, template = firmware()
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise SystemExit('KVM is required')
    directory.mkdir(parents=True)
    try:
        subprocess.run(
            ['qemu-img', 'create', '-f', 'qcow2', str(directory / 'disk.qcow2'), f'{DEFAULT_DISK_GIB}G'],
            check=True,
        )
        shutil.copyfile(template, directory / 'OVMF_VARS.fd')
    except KeyboardInterrupt:
        shutil.rmtree(directory, ignore_errors=True)
        print()
        raise SystemExit('Aborted.')
    except (subprocess.CalledProcessError, OSError) as error:
        shutil.rmtree(directory, ignore_errors=True)
        raise SystemExit(f'Could not create VM {name!r}: {error}')


def open_vm(name, base=None):
    directory = vm_dir(name, base)
    if not directory.is_dir():
        raise SystemExit(f"VM '{name}' does not exist.")
    disk = directory / 'disk.qcow2'
    if not disk.exists():
        raise SystemExit(f"Disk missing for VM '{name}': {disk}")
    code, template = firmware()
    variables = directory / 'OVMF_VARS.fd'
    if not variables.exists():
        shutil.copyfile(template, variables)
    iso = newest_iso()
    if iso is None:
        raise SystemExit('No installer ISO in out/; build one with: make iso-development')
    iso = iso.resolve()
    render = Path(os.environ.get('QEMU_RENDER_NODE', '/dev/dri/renderD128'))
    gl = os.access(render, os.R_OK | os.W_OK)
    command = build_qemu_command(code=code, variables=variables, disk=disk, iso=iso, gl=gl)
    print(f"Booting '{name}' (disk first, installer ISO second).")
    print('Install, then quit this window; reopen to boot the installed system.')
    try:
        subprocess.run(command, check=True)
    except KeyboardInterrupt:
        print('\nVM closed.')
    except subprocess.CalledProcessError as error:
        raise SystemExit(f'QEMU exited with status {error.returncode}.')


def delete_vm(name, base=None):
    directory = vm_dir(name, base)
    if not directory.is_dir():
        raise SystemExit(f"VM '{name}' does not exist.")
    try:
        answer = input(f"Delete VM '{name}' and its disk? [y/N] ").strip()
    except (EOFError, KeyboardInterrupt):
        print('\nKept.')
        return
    if not answer.lower().startswith('y'):
        print('Kept.')
        return
    shutil.rmtree(directory)
    print(f"Deleted VM '{name}'.")


def interactive(base=None):
    while True:
        vms = list_vms(base)
        print('\nYour test-drive VMs:' if vms else '\nNo try VMs yet.')
        for index, path in enumerate(vms, 1):
            print(f'  {index}. {path.name}')
        try:
            answer = input("Open which VM? (number or name, 'new', 'delete', Enter quits): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not answer or answer.lower() in ('q', 'quit', 'exit'):
            return
        if answer.lower() in ('n', 'new'):
            try:
                name = input(f'VM name [{DEFAULT_NAME}]: ').strip() or DEFAULT_NAME
            except (EOFError, KeyboardInterrupt):
                print()
                return
            try:
                create_vm(name, base)
                open_vm(name, base)
            except SystemExit as error:
                print(error)
            continue
        if answer.lower().startswith('delete'):
            target = answer.partition(' ')[2].strip()
            if not target:
                try:
                    target = input('Delete which VM? (Enter cancels): ').strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    continue
                if not target:
                    continue
            try:
                delete_vm(target, base)
            except SystemExit as error:
                print(error)
            continue
        if re.fullmatch(r'[0-9]+', answer) and 1 <= int(answer) <= len(vms):
            target = vms[int(answer) - 1].name
        elif valid_name(answer) and vm_dir(answer, base).is_dir():
            target = answer
        else:
            print(f"Unknown VM '{answer}'.")
            continue
        try:
            open_vm(target, base)
        except SystemExit as error:
            print(error)


if __name__ == '__main__':
    refuse_if_agent()
    interactive()
