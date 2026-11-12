#!/usr/bin/env python3
"""Guided persistent test-drive VMs for humans (`make try`).

Disks persist under ~/.local/share/arch-niri-dux/try/<name>/; every open
attaches the VM's stored install source (release or checkout ISO) with the
disk first in boot order."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HUMAN_ONLY = 'make try is for humans; agents use make verify-fast ARGS=--keep'

AGENT_VARS = ['CLAUDECODE', 'AI_AGENT', 'AGENT']
AGENT_PREFIXES = ['CODEX_', 'OPENCODE', 'T3CODE_']

NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
# No longer typed commands, but still rejected as names (validation parity).
RESERVED_NAMES = ('new', 'delete')
DEFAULT_NAME = 'default'
DEFAULT_RAM_GIB = 8
DEFAULT_DISK_GIB = 48
RELEASE = 'release'
DEVELOPMENT = 'development'
# Release ISOs come from `make iso`; checkout ISOs from `make iso-development`
# and carry a -development suffix (see scripts/build-guest) so both coexist.
BUILD_TARGET = {RELEASE: 'iso', DEVELOPMENT: 'iso-development'}

NEW_LABEL = 'New VM'
DELETE_LABEL = 'Delete a VM'
SOURCE_LABELS = {DEVELOPMENT: 'This checkout (your unreleased changes)', RELEASE: 'Latest release (what users get)'}

GUM_MISSING = 'gum is missing; run inside the dev shell (make try enters it automatically).'


class GumMissingError(SystemExit):
    """gum cannot be spawned; unlike operational errors, retrying cannot help."""


def require_gum():
    """Exit unless gum can run interactively; without a TTY it would hang."""
    if shutil.which('gum') is None:
        raise SystemExit(GUM_MISSING)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('make try needs a terminal; it cannot read from a pipe.')


def _run_gum(args, **kwargs):
    try:
        return subprocess.run(['gum', *args], check=False, **kwargs)
    except OSError:
        raise GumMissingError(GUM_MISSING)


def gum_choose(header, options):
    """Return the picked option, or None when the user backs out (Esc/Ctrl-C)."""
    completed = _run_gum(['choose', '--header', header, *options], stdout=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def gum_input(header, placeholder, default):
    """Return the typed value, or None when the user backs out (Esc/Ctrl-C)."""
    completed = _run_gum(
        ['input', '--header', header, '--placeholder', placeholder, '--value', str(default)],
        stdout=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def gum_confirm(prompt, default_yes):
    args = ['confirm', prompt]
    if not default_yes:
        args.append('--default=false')
    return _run_gum(args).returncode == 0


def gum_style(text):
    _run_gum(['style', '--border', 'rounded', '--padding', '0 1', text])


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
    return bool(name and NAME_RE.fullmatch(name) and any(character.isalpha() for character in name))


def resolve_base(base):
    return try_base() if base is None else Path(base)


def vm_dir(name, base=None):
    return resolve_base(base) / name


def list_vms(base=None):
    root = resolve_base(base)
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and valid_name(path.name) and ((path / 'disk.qcow2').exists() or (path / 'config').exists())
    )


def require_vm_dir(name, base=None):
    directory = vm_dir(name, base)
    if not directory.is_dir():
        raise SystemExit(f"VM '{name}' does not exist.")
    return directory


def read_choices(directory):
    """Per-VM choices stored at creation; a missing file or value means the default."""
    choices = {'ram_gib': DEFAULT_RAM_GIB, 'disk_gib': DEFAULT_DISK_GIB, 'source': DEVELOPMENT}
    try:
        text = (directory / 'config').read_text()
    except OSError:
        return choices
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or key not in choices:
            continue
        if key == 'source':
            if value in (RELEASE, DEVELOPMENT):
                choices[key] = value
        elif value.isdigit() and int(value) > 0:
            choices[key] = int(value)
    return choices


def write_choices(directory, ram_gib, disk_gib, source):
    (directory / 'config').write_text(f'ram_gib={ram_gib}\ndisk_gib={disk_gib}\nsource={source}\n')


def ask_name():
    while True:
        name = gum_input('VM name', 'Letters, digits, dot, dash or underscore', DEFAULT_NAME)
        if name is None:
            return None
        name = name.strip() or DEFAULT_NAME
        if valid_name(name) and name.lower() not in RESERVED_NAMES:
            return name
        print(
            f'Use letters, digits, dot, dash or underscore; {", ".join(repr(word) for word in RESERVED_NAMES)} and digits-only names are reserved.'
        )


def ask_positive_int(label, default):
    while True:
        answer = gum_input(f'{label} in GiB', 'Positive whole number', default)
        if answer is None:
            return None
        answer = answer.strip()
        if not answer:
            return default
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        print('Enter a positive whole number.')


def ask_source():
    choice = gum_choose('Install from:', [SOURCE_LABELS[DEVELOPMENT], SOURCE_LABELS[RELEASE]])
    for kind, label in SOURCE_LABELS.items():
        if choice == label:
            return kind
    return None


def ask_yes_no(prompt, default_yes):
    return gum_confirm(prompt, default_yes)


def iso_for_kind(kind):
    out = Path(os.environ.get('TRY_OUT_DIR', str(ROOT / 'out')))
    if kind == DEVELOPMENT:
        candidates = sorted(out.glob('*-development.iso'))
    else:
        candidates = sorted(path for path in out.glob('*.iso') if not path.name.endswith('-development.iso'))
    return candidates[-1] if candidates else None


def checkout_newer_than(iso):
    try:
        commit_time = int(
            subprocess.check_output(['git', '-C', str(ROOT), 'log', '-1', '--format=%ct'], text=True).strip()
        )
    except (subprocess.CalledProcessError, ValueError, OSError):
        return False
    return iso.stat().st_mtime < commit_time


def run_build(kind):
    """Run the existing make target with streamed output. The triggered build
    keeps its heavy-guard; agents never reach it (refuse_if_agent exits first)."""
    target = BUILD_TARGET[kind]
    print(f'Building the {kind} ISO with make {target}; this takes several minutes.')
    try:
        subprocess.run(['make', '-C', str(ROOT), target], check=True)
    except KeyboardInterrupt:
        print('\nBuild aborted.')
        return False
    except subprocess.CalledProcessError as error:
        print(f'make {target} failed with status {error.returncode}.')
        return False
    return True


def ensure_iso(kind):
    iso = iso_for_kind(kind)
    if iso is None:
        reason, default_yes = f'No {kind} installer ISO in out/ yet.', True
    elif kind == DEVELOPMENT and checkout_newer_than(iso):
        reason, default_yes = 'This checkout changed since the checkout ISO was built.', False
    else:
        return iso.resolve()
    if ask_yes_no(f'{reason} Build it now with make {BUILD_TARGET[kind]}?', default_yes) and run_build(kind):
        iso = iso_for_kind(kind) or iso
    if iso is None:
        raise SystemExit(f'No {kind} installer ISO in out/; build one with: make {BUILD_TARGET[kind]}')
    return iso.resolve()


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


def build_qemu_command(*, code, variables, disk, iso, ram_gib, gl):
    command = ['qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host', '-m', str(ram_gib * 1024)]
    command += ['-smp', '4']
    command += [
        '-display',
        f'gtk{",gl=on" if gl else ""},show-cursor=on',
        '-device',
        'virtio-vga-gl' if gl else 'virtio-vga',
    ]
    command += ['-drive', f'if=pflash,format=raw,readonly=on,file={code}']
    command += ['-drive', f'if=pflash,format=raw,file={variables}']
    command += ['-drive', f'if=none,id=target-disk,format=qcow2,file={disk}']
    command += ['-device', 'virtio-blk-pci,drive=target-disk,bootindex=1']
    command += ['-nic', 'user,model=virtio-net-pci']
    command += ['-drive', f'if=none,id=install-media,format=raw,readonly=on,file={iso}']
    command += ['-device', 'qemu-xhci', '-device', 'usb-storage,drive=install-media,bootindex=2']
    return command


def create_vm(name, base=None, ram_gib=DEFAULT_RAM_GIB, disk_gib=DEFAULT_DISK_GIB, source=DEVELOPMENT):
    if not valid_name(name) or name.lower() in RESERVED_NAMES:
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
        write_choices(directory, ram_gib, disk_gib, source)
        subprocess.run(
            ['qemu-img', 'create', '-f', 'qcow2', str(directory / 'disk.qcow2'), f'{disk_gib}G'],
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
    directory = require_vm_dir(name, base)
    disk = directory / 'disk.qcow2'
    if not disk.exists():
        raise SystemExit(f"Disk missing for VM '{name}': {disk}")
    code, template = firmware()
    variables = directory / 'OVMF_VARS.fd'
    if not variables.exists():
        shutil.copyfile(template, variables)
    choices = read_choices(directory)
    iso = ensure_iso(choices['source'])
    render = Path(os.environ.get('QEMU_RENDER_NODE', '/dev/dri/renderD128'))
    gl = os.access(render, os.R_OK | os.W_OK)
    command = build_qemu_command(code=code, variables=variables, disk=disk, iso=iso, ram_gib=choices['ram_gib'], gl=gl)
    print(f"Booting '{name}' with {iso.name} (disk first, installer ISO second).")
    print('Install, then quit this window; reopen to boot the installed system.')
    try:
        subprocess.run(command, check=True)
    except KeyboardInterrupt:
        print('\nVM closed.')
    except subprocess.CalledProcessError as error:
        raise SystemExit(f'QEMU exited with status {error.returncode}.')


def delete_vm(name, base=None):
    directory = require_vm_dir(name, base)
    if not ask_yes_no(f"Delete VM '{name}' and its disk?", False):
        print('Kept.')
        return
    shutil.rmtree(directory)
    print(f"Deleted VM '{name}'.")


def create_flow(base=None):
    """Run the New VM prompts and boot the VM; return its name, or None when cancelled."""
    name = ask_name()
    if name is None:
        return None
    source = ask_source()
    if source is None:
        return None
    ram_gib = ask_positive_int('RAM', DEFAULT_RAM_GIB)
    if ram_gib is None:
        return None
    disk_gib = ask_positive_int('Disk', DEFAULT_DISK_GIB)
    if disk_gib is None:
        return None
    create_vm(name, base, ram_gib=ram_gib, disk_gib=disk_gib, source=source)
    disk = vm_dir(name, base) / 'disk.qcow2'
    gum_style(f"VM '{name}' is ready.\nReopen it any time with make try.\nDisk: {disk}")
    open_vm(name, base)
    return name


def interactive(base=None):
    require_gum()
    try:
        gum_style('Test-drive VMs')
    except KeyboardInterrupt:
        print()
        return
    while True:
        names = [path.name for path in list_vms(base)]
        options = [*names, NEW_LABEL]
        if names:
            options.append(DELETE_LABEL)
        header = 'Your test-drive VMs (Esc quits):' if names else 'No try VMs yet (Esc quits):'
        try:
            choice = gum_choose(header, options)
            if choice is None:
                return
            if choice == NEW_LABEL:
                create_flow(base)
            elif choice == DELETE_LABEL and names:
                target = gum_choose('Delete which VM? (Esc keeps all)', names)
                if target is not None:
                    delete_vm(target, base)
            elif choice in names:
                open_vm(choice, base)
        except (EOFError, KeyboardInterrupt):
            print()
            return
        except GumMissingError as error:
            print(error)
            return
        except SystemExit as error:
            print(error)


if __name__ == '__main__':
    refuse_if_agent()
    interactive()
