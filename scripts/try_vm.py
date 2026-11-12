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
import time
from pathlib import Path

import workspace

ROOT = Path(__file__).resolve().parents[1]

HUMAN_ONLY = 'make try is for humans; agents use make verify-fast ARGS=--keep'

NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
# No longer typed commands, but still rejected as names (validation parity).
RESERVED_NAMES = ('new', 'delete')
DEFAULT_NAME = 'default'
DEFAULT_RAM_GIB = 8
DEFAULT_DISK_GIB = 48
# An installed VM's qcow2 measured 5.6-6.0 GiB; the rest is room to use it.
VM_SPACE = 8 * 1024**3
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


def gum_style(text, warning=False):
    args = ['style', '--border', 'rounded', '--padding', '0 1']
    if warning:
        args += ['--border-foreground', '214']
    _run_gum([*args, text])


def refuse_if_agent(env=None):
    markers = workspace.agent_markers(env)
    if markers:
        raise SystemExit(f'{HUMAN_ONLY} (agent environment: {", ".join(markers)})')


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


def gib(size):
    return f'{size / 1024**3:.1f} GiB'


def space_report(needs):
    """Plain-words explanation when a filesystem lacks room for needs, else None."""
    shortfalls = workspace.space_shortfalls(needs)
    if not shortfalls:
        return None
    lines = ['Not enough free disk space.']
    for path, need, free, purposes in shortfalls:
        lines += ['', f'{path} has {gib(free)} free but needs {gib(need)} for:']
        lines += [f'  • {purpose}' for purpose in purposes]
    lines += ['', 'Free some space and try again.', 'make gc removes builds left by deleted checkouts.']
    return '\n'.join(lines)


def check_new_vm_space(base=None):
    """Warn before the New VM prompts; return False when the human backs out."""
    needs = [(resolve_base(base), VM_SPACE, "the VM's disk once installed")]
    if iso_for_kind(DEVELOPMENT) is None:
        # The default source has no ISO yet, so the first boot likely builds one.
        needs += workspace.build_needs(workspace.build_state(ROOT))
    report = space_report(needs)
    if report is None:
        return True
    gum_style(report, warning=True)
    return ask_yes_no('Continue anyway?', False)


# A plain build log goes to out/try-build.log; the screen shows these stages,
# detected from the commands the builder VM traces (set -x) into its log. The
# seconds are typical durations measured on msi-dux (16 cores), used only to
# weight the progress bar; elapsed times on screen are real.
BUILD_STAGES = {
    DEVELOPMENT: [
        ('Start the builder VM', None, 30),
        ('Download Arch packages', 'scripts/package_lock.py fetch', 70),
        ('Build Dux packages from this checkout', 'scripts/package_build.py', 195),
        ('Stage the offline package repository', 'scripts/iso_payload.py', 15),
        ('Assemble the ISO', 'mkarchiso', 80),
    ],
    RELEASE: [
        ('Start the builder VM', None, 30),
        ('Download Arch packages', 'scripts/package_lock.py fetch', 70),
        ('Download the released Dux packages', 'scripts/integration_release.py fetch', 20),
        ('Stage the offline package repository', 'scripts/iso_payload.py', 15),
        ('Assemble the ISO', 'mkarchiso', 80),
    ],
}

SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
GREEN, CYAN, RED, DIM, BOLD, RESET = (f'\x1b[{code}m' for code in (32, 36, 31, 2, 1, 0))


def build_stage(stages, log_text, current=0):
    """Index of the latest stage whose marker appears in a traced command of the builder log."""
    for line in log_text.splitlines():
        if not line.startswith('+'):
            continue
        for index, (_, marker, _) in enumerate(stages):
            if marker and index > current and marker in line:
                current = index
    return current


def build_fraction(stages, current, stage_elapsed):
    """Share of typical build time done; the current stage counts up to 95% of its typical length."""
    total = sum(seconds for _, _, seconds in stages)
    done = sum(seconds for _, _, seconds in stages[:current])
    typical = stages[current][2]
    return (done + min(stage_elapsed / typical, 0.95) * typical) / total


def clock(seconds):
    seconds = int(seconds)
    return f'{seconds // 60}:{seconds % 60:02d}'


class LogTail:
    """Follows a log file written by another process; truncation (a fresh run) restarts it."""

    def __init__(self, path, since=0.0):
        self.path = path
        self.since = since
        self.offset = 0
        self.lines = []

    def read(self):
        """Return the text appended since the last read; ignores a file older than since."""
        try:
            stat = self.path.stat()
            if stat.st_mtime < self.since:
                return ''
            if stat.st_size < self.offset:
                self.offset, self.lines = 0, []
            with self.path.open('rb') as handle:
                handle.seek(self.offset)
                data = handle.read()
        except OSError:
            return ''
        # Keep a partial last line for the next read.
        cut = data.rfind(b'\n') + 1
        self.offset += cut
        text = data[:cut].decode(errors='replace')
        self.lines = (self.lines + [line for line in text.splitlines() if line.strip()])[-200:]
        return text

    @property
    def active(self):
        return self.offset > 0


class LiveBlock:
    """Redraws a block of lines in place below the cursor."""

    def __init__(self):
        self.height = 0

    def draw(self, lines):
        width = max(20, shutil.get_terminal_size().columns)
        parts = [f'\x1b[{self.height}F' if self.height else '', '\x1b[J']
        parts += [fit(line, width) + '\n' for line in lines]
        sys.stdout.write(''.join(parts))
        sys.stdout.flush()
        self.height = len(lines)


ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def fit(line, width):
    """Clip a line to the terminal width so redraws never wrap; drops color when clipping."""
    plain = ANSI_RE.sub('', line)
    if len(plain) < width:
        return line
    return plain[: width - 2] + '…'


def watch(process, frame, interval=0.2):
    """Redraw frame(tick) until process exits, then clear it; Ctrl-C stops the process too.
    Returns the process status."""
    block = LiveBlock()
    sys.stdout.write('\x1b[?25l')
    tick = 0
    try:
        while process.poll() is None:
            block.draw(frame(tick))
            tick += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        # The terminal's SIGINT reached the child too; give it a moment to clean up.
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
        raise
    finally:
        block.draw([])
        sys.stdout.write('\x1b[?25h')
        sys.stdout.flush()
    return process.returncode


def run_build(kind):
    """Run the build's make target with its output in out/try-build.log and live stages on screen.
    The triggered build keeps its heavy-guard; agents never reach it (refuse_if_agent exits first)."""
    report = space_report(workspace.build_needs(workspace.build_state(ROOT)))
    if report is not None:
        gum_style(report, warning=True)
        return False
    _, memory_warning = workspace.builder_memory()
    if memory_warning:
        gum_style(memory_warning, warning=True)
    stages = BUILD_STAGES[kind]
    label_width = max(len(label) for label, _, _ in stages)
    log_path = ROOT / 'out' / 'try-build.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    make_log = LogTail(log_path)
    # The builder rewrites its log on every build; ignore the previous build's.
    guest_log = LogTail(workspace.build_state(ROOT) / 'guest-build.log', since=time.time())
    stage_starts = [time.monotonic()]
    current = 0

    def advance():
        nonlocal current
        make_log.read()
        current = build_stage(stages, guest_log.read(), current)
        while len(stage_starts) <= current:
            stage_starts.append(time.monotonic())

    def frame(tick):
        advance()
        now = time.monotonic()
        spinner = f'{CYAN}{SPINNER[tick % len(SPINNER)]}{RESET}'
        fraction = build_fraction(stages, current, now - stage_starts[current])
        filled = int(fraction * 40)
        lines = [
            f'{BOLD}Building the {kind} ISO{RESET}  {clock(now - stage_starts[0])}',
            f'{CYAN}{"━" * filled}{RESET}{DIM}{"─" * (40 - filled)}{RESET} {int(fraction * 100):3d}%',
            '',
        ]
        for index, (label, _, _) in enumerate(stages):
            if index < current:
                took = clock(stage_starts[index + 1] - stage_starts[index])
                lines.append(f'{GREEN}✓{RESET} {label.ljust(label_width)}  {DIM}{took}{RESET}')
            elif index == current:
                lines.append(f'{spinner} {label.ljust(label_width)}  {clock(now - stage_starts[index])}')
            else:
                lines.append(f'{DIM}· {label}{RESET}')
        tail = (guest_log if guest_log.active else make_log).lines[-3:]
        lines += ['', *[f'{DIM}  {line.strip()}{RESET}' for line in tail]]
        lines.append(f'{DIM}Full log: out/try-build.log · Ctrl-C cancels{RESET}')
        return lines

    with log_path.open('w') as log:
        process = subprocess.Popen(
            ['make', '-C', str(ROOT), BUILD_TARGET[kind]],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            status = watch(process, frame)
        except KeyboardInterrupt:
            print(f'Build cancelled after {clock(time.monotonic() - stage_starts[0])}.')
            return False
    advance()
    elapsed = clock(time.monotonic() - stage_starts[0])
    if status == 0:
        print(f'{GREEN}✓{RESET} Built the {kind} ISO in {elapsed}.')
        return True
    print(f'{RED}✗{RESET} The build failed at "{stages[current][0]}" after {elapsed}. Last lines:')
    source = guest_log if guest_log.active else make_log
    for line in source.lines[-15:]:
        print(f'  {line}')
    print(f'Full logs: {log_path}')
    if guest_log.active:
        print(f'           {guest_log.path}')
    return False


def ensure_iso(kind):
    iso = iso_for_kind(kind)
    if iso is None:
        reason, default_yes = f'No {kind} installer ISO in out/ yet.', True
    elif kind == DEVELOPMENT and checkout_newer_than(iso):
        reason, default_yes = 'This checkout changed since the checkout ISO was built.', False
    else:
        return iso.resolve()
    minutes = round(sum(seconds for _, _, seconds in BUILD_STAGES[kind]) / 60)
    prompt = f'{reason} Build it now? That takes about {minutes} minutes.'
    if ask_yes_no(prompt, default_yes) and run_build(kind):
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
            ['qemu-img', 'create', '-q', '-f', 'qcow2', str(directory / 'disk.qcow2'), f'{disk_gib}G'],
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
    print(f"Booting '{name}' from its disk, with {iso.name} attached as the installer.")
    log_path = directory / 'qemu.log'
    start = time.monotonic()

    def frame(tick):
        return [
            f'{CYAN}{SPINNER[tick % len(SPINNER)]}{RESET} {BOLD}{name}{RESET} is running  {clock(time.monotonic() - start)}'
            f'  {DIM}disk {gib(disk.stat().st_blocks * 512)}{RESET}',
            f'{DIM}Install in the VM window, then quit it to come back here. Reopen boots the installed system.{RESET}',
        ]

    # QEMU's own messages go to a log so they cannot scramble the status lines.
    with log_path.open('w') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        try:
            status = watch(process, frame, interval=0.5)
        except KeyboardInterrupt:
            status = 0
    print(f'{name} ran for {clock(time.monotonic() - start)}; its disk now holds {gib(disk.stat().st_blocks * 512)}.')
    if status != 0:
        tail = log_path.read_text(errors='replace').splitlines()[-10:]
        raise SystemExit('\n'.join([f'QEMU exited with status {status}:', *tail, f'Full log: {log_path}']))


def delete_vm(name, base=None):
    directory = require_vm_dir(name, base)
    if not ask_yes_no(f"Delete VM '{name}' and its disk?", False):
        print('Kept.')
        return
    shutil.rmtree(directory)
    print(f"Deleted VM '{name}'.")


def create_flow(base=None):
    """Run the New VM prompts and boot the VM; return its name, or None when cancelled."""
    if not check_new_vm_space(base):
        return None
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
    disk = str(vm_dir(name, base) / 'disk.qcow2').replace(str(Path.home()), '~', 1)
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
