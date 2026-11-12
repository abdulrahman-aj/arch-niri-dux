#!/usr/bin/env python3
"""Interactive persistent test-drive VMs for humans.

`make try` manages small persistent VMs under
``~/.local/share/arch-niri-dux/try/<name>/`` so a person can install and
revisit arch-niri-dux in a window without other VM software. The first
`open` boots the installer ISO with the new disk attached; later opens
boot the installed disk directly.

Installed detection: the host probes the disk image's GPT for an EFI
System Partition (type GUID c12a7328-...). The installer writes that
partition before mkfs, so its presence means the disk was partitioned
by a real install. Reading the GPT via `qemu-img dd` needs no root,
no NBD and no mounting; checking for Limine files inside the guest
filesystem would need all three, so partition-type probing is the
robust host-side signal. A metadata flag caches the result but the
probe wins whenever it runs.
"""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]

HUMAN_ONLY = 'make try is for humans; agents use make verify-fast ARGS=--keep'

# Exact variables that reliably identify an agent worker. T3CODE_THREAD_ID,
# CLAUDECODE and AI_AGENT are required by the design; the rest were observed
# in this worker's own environment (names only) and only ever appear there.
AGENT_VARS = [
    'T3CODE_THREAD_ID',
    'CLAUDECODE',
    'AI_AGENT',
    'AGENT',
    'OPENCODE',
    'OPENCODE_SESSION_ID',
    'OPENCODE_TERMINAL',
    'OPENCODE_CONFIG_CONTENT',
    'T3CODE_HOME',
    'T3CODE_LAN',
    'T3_BOOT_SERVICE_UNIT',
    'T3_SERVICE_LAUNCHER_CONTEXT',
]

# Prefixes for agent families (Codex sets CODEX_*; kept defensive since no
# Codex worker was available to observe exact names).
AGENT_PREFIXES = ['CODEX_', 'OPENCODE_', 'T3CODE_', 'T3_', 'CLAUDECODE', 'CLAUDE_CODE_']

ESP_GUID = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b')

NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
DEFAULT_NAME = 'default'
DEFAULT_DISK_GIB = 48


def agent_markers(env=None):
    env = os.environ if env is None else env
    found = [name for name in AGENT_VARS if env.get(name)]
    for key, value in env.items():
        if value and any(key.startswith(prefix) for prefix in AGENT_PREFIXES) and key not in found:
            found.append(key)
    return sorted(found)


def refuse_if_agent(env=None):
    markers = agent_markers(env)
    if markers:
        raise SystemExit(f'{HUMAN_ONLY} (agent environment: {", ".join(markers)})')


def try_base():
    data = os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))
    return Path(data) / 'arch-niri-dux' / 'try'


def out_dir():
    return Path(os.environ.get('TRY_OUT_DIR', str(ROOT / 'out')))


def valid_name(name):
    return bool(NAME_RE.fullmatch(name or ''))


def vm_dir(name, base=None):
    return (try_base() if base is None else Path(base)) / name


def list_vms(base=None):
    root = try_base() if base is None else Path(base)
    if not root.is_dir():
        return []
    return sorted([path for path in root.iterdir() if path.is_dir() and valid_name(path.name)])


def load_meta(directory):
    meta_file = Path(directory) / 'meta.json'
    if not meta_file.is_file():
        return {}
    try:
        return json.loads(meta_file.read_text())
    except (ValueError, OSError):
        return {}


def save_meta(directory, meta):
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / 'meta.json').write_text(json.dumps(meta, indent=2) + '\n')


def newest_iso(directory=None):
    candidates = sorted((out_dir() if directory is None else Path(directory)).glob('*.iso'))
    return candidates[-1] if candidates else None


def iso_version(iso):
    return Path(iso).name.removesuffix('.iso')


def human_size_gib(path):
    try:
        result = subprocess.run(
            ['qemu-img', 'info', '--output=json', str(path)], check=True, capture_output=True, text=True
        )
        return round(int(json.loads(result.stdout)['virtual-size']) / 1024**3)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        return None


def has_esp_gpt(data):
    """True when raw head bytes contain a GPT with an ESP partition entry."""
    if len(data) < 34 * 512 or data[510:512] != b'\x55\xaa':
        return False
    if data[512:520] != b'EFI PART':
        return False
    array_lba, entry_count, entry_size = struct.unpack_from('<QII', data, 512 + 72)
    esp = ESP_GUID.bytes_le
    for index in range(entry_count):
        start = array_lba * 512 + index * entry_size
        entry = data[start : start + entry_size]
        if len(entry) < 16 or not any(entry[:16]):
            continue
        if bytes(entry[:16]) == esp:
            return True
    return False


def read_head(disk, sectors=34):
    """Read the first sectors of a qcow2 (or raw) image without root."""
    disk = Path(disk)
    with disk.open('rb') as stream:
        if stream.read(4) != b'QFI\xfb':
            stream.seek(0)
            return stream.read(sectors * 512)
    tmp = disk.parent / f'.gpt-head-{os.getpid()}.bin'
    try:
        subprocess.run(
            ['qemu-img', 'dd', f'if={disk}', f'of={tmp}', 'bs=512', f'count={sectors}'],
            check=True,
            capture_output=True,
        )
        return tmp.read_bytes()
    except (OSError, subprocess.CalledProcessError):
        return b''
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def disk_is_installed(disk, meta=None):
    """Probe the GPT; fall back to the cached metadata flag when unreadable."""
    try:
        head = read_head(disk)
    except OSError:
        head = b''
    if head:
        return has_esp_gpt(head)
    return bool((meta or {}).get('installed', False))


def firmware():
    base = Path(os.environ.get('OVMF_DIR', '/usr/share/edk2/x64'))
    for code_name, vars_name in (('OVMF_CODE.4m.fd', 'OVMF_VARS.4m.fd'), ('OVMF_CODE.fd', 'OVMF_VARS.fd')):
        code, template = base / code_name, base / vars_name
        if code.is_file() and template.is_file():
            return code, template
    raise SystemExit(f'OVMF firmware missing under {base}; install edk2-ovmf or run through make try.')


def mem_available_gib(path='/proc/meminfo'):
    for line in Path(path).read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) / 1024**2
    raise SystemExit(f'Could not read MemAvailable from {path}')


def choose_memory(requested=None, meminfo='/proc/meminfo', interactive=False):
    """Pick the guest size: 8 GiB when the host can spare it, else 4 GiB."""
    if requested is not None:
        if requested <= 0:
            raise SystemExit('--memory needs a positive number of GiB.')
        return requested
    available = mem_available_gib(meminfo)
    if available >= 10:
        return 8
    if available < 6:
        raise SystemExit(f'Only {available:.1f} GiB available; need at least 6 GiB to start a 4 GiB guest.')
    message = f'Only {available:.1f} GiB available; using a 4 GiB guest instead of 8 GiB.'
    if interactive:
        try:
            answer = input(f'{message} Continue? [Y/n] ').strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit('Aborted.')
        if answer.lower().startswith('n'):
            raise SystemExit('Aborted.')
        return 4
    print(message)
    return 4


def build_qemu_command(*, code, variables, disk, iso=None, offline=False, memory_gib=8, display='gtk', gl=True):
    """Pure QEMU argv builder so tests can assert ISO/disk selection headlessly."""
    command = ['qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host', '-m', str(memory_gib * 1024)]
    command += ['-smp', '4']
    if display == 'none':
        command += ['-display', 'none']
    elif gl:
        command += ['-display', 'gtk,gl=on,show-cursor=on']
    else:
        command += ['-display', 'gtk,show-cursor=on']
    if display == 'none':
        command += ['-vga', 'none', '-device', 'VGA']
    elif gl:
        command += ['-device', 'virtio-vga-gl']
    else:
        command += ['-device', 'virtio-vga']
    command += ['-drive', f'if=pflash,format=raw,readonly=on,file={code}']
    command += ['-drive', f'if=pflash,format=raw,file={variables}']
    command += ['-drive', f'if=none,id=target-disk,format=qcow2,file={disk}']
    # Disk first: a blank disk falls through to the ISO; after the install a
    # reboot lands on the installed system instead of the installer again.
    command += ['-device', 'virtio-blk-pci,drive=target-disk,bootindex=1']
    command += ['-nic', 'none' if offline else 'user,model=virtio-net-pci']
    if iso is not None:
        command += ['-drive', f'if=none,id=install-media,format=raw,readonly=on,file={iso}']
        command += ['-device', 'qemu-xhci', '-device', 'usb-storage,drive=install-media,bootindex=2']
    return command


def ensure_iso(iso_arg=None):
    if iso_arg:
        iso = Path(iso_arg)
        if not iso.is_file():
            raise SystemExit(f'Installer ISO not found: {iso}')
        return iso.resolve()
    iso = newest_iso()
    if iso is None:
        print('No ISO in out/; building a development ISO first.')
        subprocess.run(['./scripts/build-iso', 'development'], check=True, cwd=ROOT)
        iso = newest_iso()
    if iso is None:
        raise SystemExit('ISO build produced no out/*.iso.')
    return iso.resolve()


def create_vm(name, disk_size_gib=DEFAULT_DISK_GIB, iso=None, base=None):
    if not valid_name(name):
        raise SystemExit(f'Invalid VM name {name!r}; use letters, digits, dot, dash or underscore.')
    if disk_size_gib <= 0:
        raise SystemExit('--disk-size needs a positive number of GiB.')
    directory = vm_dir(name, base)
    if directory.exists():
        raise SystemExit(f"VM '{name}' already exists; open or delete it.")
    for tool in ('qemu-img', 'qemu-system-x86_64'):
        if shutil.which(tool) is None:
            raise SystemExit(f'Missing required tool: {tool}')
    code, template = firmware()
    if not (os.access('/dev/kvm', os.R_OK | os.W_OK)):
        raise SystemExit('KVM is required')
    directory.mkdir(parents=True)
    disk = directory / 'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), f'{disk_size_gib}G'], check=True)
    shutil.copyfile(template, directory / 'OVMF_VARS.fd')
    meta = {
        'name': name,
        'created': dt.datetime.now(dt.UTC).isoformat(timespec='seconds'),
        'last_used': None,
        'disk_size_gib': disk_size_gib,
        'iso_path': str(iso) if iso else None,
        'iso_version': iso_version(iso) if iso else None,
        'installed': False,
        'firmware_code': code.name,
    }
    save_meta(directory, meta)
    return directory


def describe_vm(directory):
    meta = load_meta(directory)
    disk = Path(directory) / 'disk.qcow2'
    size = meta.get('disk_size_gib') or human_size_gib(disk)
    return {
        'name': Path(directory).name,
        'iso_version': meta.get('iso_version') or '(not installed yet)',
        'disk_gib': size if size is not None else '?',
        'last_used': meta.get('last_used') or 'never',
        'installed': meta.get('installed', False),
    }


def print_table(vms):
    rows = [describe_vm(path) for path in vms]
    widths = [
        max(len(str(row[key])) for row in rows + [{key: label}])
        for key, label in [
            ('name', 'NAME'),
            ('iso_version', 'ISO VERSION'),
            ('disk_gib', 'DISK GiB'),
            ('last_used', 'LAST USED'),
        ]
    ]
    print(f'{"NAME":<{widths[0]}}  {"ISO VERSION":<{widths[1]}}  {"DISK GiB":<{widths[2]}}  LAST USED')
    for row in rows:
        print(
            f'{row["name"]:<{widths[0]}}  {row["iso_version"]:<{widths[1]}}  '
            f'{row["disk_gib"]:<{widths[2]}}  {row["last_used"]}'
        )


def open_vm(
    name,
    *,
    iso_arg=None,
    offline=False,
    memory=None,
    display='gtk',
    print_command=False,
    interactive=False,
    base=None,
    confirm_downgrade=True,
):
    directory = vm_dir(name, base)
    if not directory.is_dir():
        raise SystemExit(f"VM '{name}' does not exist; create it with: make try ARGS='new {name}'")
    meta = load_meta(directory)
    disk = directory / 'disk.qcow2'
    if not disk.exists():
        raise SystemExit(f"Disk missing for VM '{name}': {disk}")
    variables = directory / 'OVMF_VARS.fd'
    if not variables.exists():
        _, template = firmware()
        shutil.copyfile(template, variables)
    installed = disk_is_installed(disk, meta)
    iso = None
    if not installed:
        iso = ensure_iso(iso_arg)
        meta['iso_path'] = str(iso)
        meta['iso_version'] = iso_version(iso)
    elif iso_arg:
        print(f"VM '{name}' is already installed; ignoring --iso and booting the disk.")
    code, _ = firmware()
    render = Path(os.environ.get('QEMU_RENDER_NODE', '/dev/dri/renderD128'))
    gl = os.access(render, os.R_OK | os.W_OK)
    if display != 'none' and not gl:
        print(f'No accessible GPU render node ({render}); using software display.', file=sys.stderr)
    memory_gib = choose_memory(memory, interactive=interactive and confirm_downgrade and memory is None)
    command = build_qemu_command(
        code=code,
        variables=variables,
        disk=disk,
        iso=iso,
        offline=offline,
        memory_gib=memory_gib,
        display=display,
        gl=gl,
    )
    if print_command:
        print(' '.join(str(part) for part in command))
        return command
    if installed:
        print(f"Booting installed disk for '{name}' ({disk}).")
    else:
        print(f'Booting installer {iso} (disk: {disk}).')
        print('Install, then quit or power off this window; reopen to boot the installed disk.')
    subprocess.run([str(part) for part in command], check=True)
    meta['last_used'] = dt.datetime.now(dt.UTC).isoformat(timespec='seconds')
    now_installed = disk_is_installed(disk, meta)
    meta['installed'] = now_installed
    save_meta(directory, meta)
    if not installed and now_installed:
        print(f"Installed disk detected for '{name}'; future opens boot it directly.")
    elif not now_installed:
        print(f"Disk for '{name}' still looks blank; open it again to retry the install.")
    return command


def delete_vm(name, *, assume_yes=False, base=None):
    directory = vm_dir(name, base)
    if not directory.is_dir():
        raise SystemExit(f"VM '{name}' does not exist.")
    if not assume_yes:
        if not sys.stdin.isatty():
            raise SystemExit(f"Refusing to delete '{name}' without --yes when stdin is not a TTY.")
        try:
            answer = input(f"Delete VM '{name}' and its disk? [y/N] ").strip()
        except (EOFError, KeyboardInterrupt):
            print('\nKept.')
            return False
        if not answer.lower().startswith('y'):
            print('Kept.')
            return False
    shutil.rmtree(directory)
    print(f"Deleted VM '{name}'.")
    return True


def prompt_text(prompt, default=''):
    marker = f' [{default}]' if default else ''
    try:
        answer = input(f'{prompt}{marker}: ').strip()
    except EOFError:
        print()
        return default
    except KeyboardInterrupt:
        print()
        raise SystemExit('Aborted.')
    return answer or default


def prompt_size(default=DEFAULT_DISK_GIB):
    while True:
        answer = prompt_text('Disk size in GiB', str(default)).strip()
        if re.fullmatch(r'[0-9]+', answer) and int(answer) > 0:
            return int(answer)
        print('Enter a positive number of GiB.')


def interactive(base=None, iso_arg=None, offline=False):
    vms = list_vms(base)
    if not vms:
        print('No try VMs yet; creating one.')
        name = prompt_text('VM name', DEFAULT_NAME)
        size = prompt_size()
        create_vm(name, size, base=base)
        open_vm(name, iso_arg=iso_arg, offline=offline, interactive=True, base=base)
        return
    while True:
        print()
        print_table(list_vms(base))
        print()
        try:
            answer = input("Open which VM? (number or name, 'new', 'delete NAME', 'quit') ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not answer or answer.lower() in ('q', 'quit', 'exit'):
            return
        if answer.lower() in ('n', 'new'):
            name = prompt_text('VM name', DEFAULT_NAME)
            size = prompt_size()
            try:
                create_vm(name, size, base=base)
            except SystemExit as error:
                print(error)
                continue
            open_vm(name, iso_arg=iso_arg, offline=offline, interactive=True, base=base)
            return
        if answer.lower().startswith(('delete ', 'del ', 'd ')):
            _, _, target = answer.partition(' ')
            target = target.strip()
            if not target:
                print('Usage: delete NAME')
                continue
            try:
                delete_vm(target, base=base)
            except SystemExit as error:
                print(error)
            continue
        candidates = list_vms(base)
        target = None
        if re.fullmatch(r'[0-9]+', answer):
            index = int(answer) - 1
            if 0 <= index < len(candidates):
                target = candidates[index].name
        elif valid_name(answer) and vm_dir(answer, base).is_dir():
            target = answer
        if target is None:
            print(f"Unknown VM '{answer}'.")
            continue
        open_vm(target, iso_arg=iso_arg, offline=offline, interactive=True, base=base)
        return


def build_parser():
    parser = argparse.ArgumentParser(description='Persistent interactive test-drive VMs (humans only).')
    parser.add_argument('--iso', default=None, help='installer ISO (default: newest out/*.iso)')
    parser.add_argument('--offline', action='store_true', help='no virtual network (loopback only)')
    parser.add_argument(
        '--display', default=None, choices=['gtk', 'none'], help='window display (none is for headless checks)'
    )
    parser.add_argument('--print-command', action='store_true', help='print the QEMU command and exit')
    parser.add_argument('--base-dir', default=None, help='override the try state directory (tests)')
    parser.add_argument('--out-dir', default=None, help='override out/ for ISO lookup (tests)')
    sub = parser.add_subparsers(dest='command')
    p_new = sub.add_parser('new', help='create a VM')
    p_new.add_argument('name', nargs='?', default=None)
    p_new.add_argument('--disk-size', type=int, default=DEFAULT_DISK_GIB)
    p_open = sub.add_parser('open', help='open a VM (ISO on first boot, disk afterwards)')
    p_open.add_argument('name', nargs='?', default=None)
    p_open.add_argument('--memory', type=int, default=None, help='guest memory in GiB (default: 8, or 4 when low)')
    # SUPPRESS keeps flags passed before the subcommand: argparse would
    # otherwise overwrite them with these same-dest defaults.
    p_open.add_argument('--iso', default=argparse.SUPPRESS, help='installer ISO (default: newest out/*.iso)')
    p_open.add_argument('--offline', action='store_true', default=argparse.SUPPRESS, help='no virtual network')
    p_open.add_argument(
        '--display', default=argparse.SUPPRESS, choices=['gtk', 'none'], help='window display (none is headless)'
    )
    p_open.add_argument(
        '--print-command', action='store_true', default=argparse.SUPPRESS, help='print the QEMU command and exit'
    )
    p_delete = sub.add_parser('delete', help='delete a VM')
    p_delete.add_argument('name', nargs='?', default=None)
    p_delete.add_argument('--yes', action='store_true')
    sub.add_parser('list', help='list VMs')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    refuse_if_agent()
    if args.out_dir:
        os.environ['TRY_OUT_DIR'] = args.out_dir
    base = Path(args.base_dir) if args.base_dir else None

    if args.command is None:
        if not sys.stdin.isatty():
            parser.error('no TTY; pass list, open NAME, new NAME or delete NAME')
        interactive(base=base, iso_arg=args.iso, offline=args.offline)
        return
    if args.command == 'list':
        vms = list_vms(base)
        if not vms:
            print('No try VMs yet.')
        else:
            print_table(vms)
        return
    if args.command == 'new':
        name = args.name or (prompt_text('VM name', DEFAULT_NAME) if sys.stdin.isatty() else None)
        if not name:
            parser.error('new needs a NAME when stdin is not a TTY')
        create_vm(name, args.disk_size, base=base)
        print(f"Created VM '{name}'. Open it with: make try ARGS='open {name}'")
        return
    if args.command == 'open':
        name = args.name or (prompt_text('VM name', DEFAULT_NAME) if sys.stdin.isatty() else None)
        if not name:
            parser.error('open needs a NAME when stdin is not a TTY')
        open_vm(
            name,
            iso_arg=args.iso,
            offline=args.offline,
            memory=args.memory,
            display=args.display or 'gtk',
            print_command=args.print_command,
            interactive=sys.stdin.isatty(),
            base=base,
        )
        return
    if args.command == 'delete':
        name = args.name or (prompt_text('VM name', DEFAULT_NAME) if sys.stdin.isatty() else None)
        if not name:
            parser.error('delete needs a NAME when stdin is not a TTY')
        delete_vm(name, assume_yes=args.yes, base=base)
        return
    parser.error(f'unknown command {args.command}')


if __name__ == '__main__':
    main()
