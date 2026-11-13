#!/usr/bin/env python3
"""Guided erase or existing-free-space installation. All package inputs come from the booted ISO."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time

if __package__:
    from . import storage
else:
    import storage

run = storage.run


SHARE = Path('/usr/local/share/arch-niri-dux')
REPO = Path('/run/archiso/bootmnt/repo')
TARGET = Path('/mnt/arch-niri-dux')
MAPPER = 'dux-root'
GIB = 1024**3
SUBVOLUMES = {
    '@': '/',
    '@home': '/home',
    '@log': '/var/log',
    '@cache': '/var/cache/pacman/pkg',
    '@snapshots': '/.snapshots',
}


@dataclass(repr=False)
class Settings:
    disk: str
    username: str
    hostname: str
    timezone: str
    keymap: str
    passphrase: str
    password: str
    swap_gib: int | None = None
    mode: str = 'erase'
    free_region: list[int] | None = None
    existing_boot: str | None = None

    def validate(self):
        if not re.fullmatch(r'/dev/(?:sd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+)', self.disk):
            raise ValueError('Select a whole physical disk, not a partition or loop device.')
        if self.mode not in ('erase', 'alongside'):
            raise ValueError('Unknown installation mode.')
        if self.mode == 'alongside':
            if (
                not isinstance(self.free_region, list)
                or len(self.free_region) != 2
                or any(type(value) is not int or value < 1 for value in self.free_region)
            ):
                raise ValueError('Select an exact free region [first sector, last sector].')
            if not isinstance(self.existing_boot, str) or not re.fullmatch(r'[0-9A-Fa-f]{4}', self.existing_boot):
                raise ValueError('Select an existing firmware boot entry.')
        elif self.free_region is not None or self.existing_boot is not None:
            raise ValueError('Alongside settings cannot be used for disk erasure.')
        validate_username(self.username)
        validate_hostname(self.hostname)
        zone = (Path('/usr/share/zoneinfo') / self.timezone).resolve()
        if not zone.is_relative_to('/usr/share/zoneinfo') or not zone.is_file():
            raise ValueError('Unknown timezone.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.keymap):
            raise ValueError('Invalid console keymap name.')
        if self.keymap != 'us' and not any(Path('/usr/share/kbd/keymaps').rglob(self.keymap + '.map.gz')):
            raise ValueError('Unknown console keymap.')
        for name, value in [('Disk passphrase', self.passphrase), ('Login password', self.password)]:
            validate_secret(value, name)
        if self.swap_gib is not None and (type(self.swap_gib) is not int or self.swap_gib < 0):
            raise ValueError('Swap size must be a nonnegative whole number of GiB, or null for Auto.')


@dataclass
class InstallationState:
    confirmed_layout: dict | None = None
    confirmed_device: tuple | None = None
    installation_started: bool = False

    def begin(self):
        self.installation_started = True


class Cancelled(Exception):
    pass


def validate_username(value):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', value) or value in {'root', 'nobody', 'alpm'}:
        raise ValueError('Use a non-system lowercase username (letters, digits, underscore, dash).')


def validate_hostname(value):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
        raise ValueError('Use a hostname of 1–63 lowercase letters, digits or dashes.')


def validate_secret(value, name):
    if len(value) < 8 or any(char in value for char in '\n\r\0'):
        raise ValueError(f'{name} must have at least 8 characters and no line breaks.')


def memory_gib():
    total = next(line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))
    return (int(total.split()[1]) * 1024 + GIB - 1) // GIB


def swap_size(swap_gib, disk_bytes):
    size = memory_gib() + storage.AUTO_SWAP_PAD_GIB if swap_gib is None else swap_gib
    # Leave room for the ESP, LUKS metadata and a usable desktop installation.
    maximum = storage.max_swap_gib(disk_bytes // GIB)
    if size and size > maximum:
        raise ValueError(
            f'{size} GiB swap will not fit while leaving 24 GiB for the system. '
            f'Choose at most {max(0, maximum)} GiB, no swap, or a larger disk.'
        )
    return size


# Interactive presentation. Only the wizard and the live progress below touch the
# console; automated --config runs keep plain, parseable output.
ACCENT = '6'
WARNING = '1'
DIM = '8'
# Every element shares one left margin, so text and widgets line up.
PAD = '0 0 0 2'
LOG = Path('/var/log/arch-niri-install.log')


def gum(*args, stdin=None, show=False):
    """Run a gum widget and return what it printed, or let it print to the console when show is set.
    Esc or Ctrl-C cancels the installation."""
    stdout = None if show else subprocess.PIPE
    result = subprocess.run(['gum', *args], input=stdin, text=True, stdout=stdout, check=False)
    if result.returncode != 0:
        raise Cancelled()
    return '' if show else result.stdout.removesuffix('\n')


def say(text, color=None, bold=False):
    args = ['style', '--padding', PAD]
    if color:
        args += ['--foreground', color]
    if bold:
        args.append('--bold')
    gum(*args, text, show=True)


def screen(title, intro=''):
    """Clear the console and start a wizard screen under the installer banner."""
    print('\x1b[H\x1b[2J\n', end='', flush=True)
    say('Arch + Niri + Dux', ACCENT, bold=True)
    say(title, bold=True)
    if intro:
        say(intro, DIM)
    print()


def choose(label, choices, default=None, search=False):
    if not choices:
        raise ValueError(f'No choices available for {label.lower()}.')
    if default in choices:
        choices = [default] + [choice for choice in choices if choice != default]
    style = ['--header', label, '--header.foreground', ACCENT, '--padding', PAD]
    if search:
        # Word-start matching: "amm" finds Asia/Amman, "ber" Europe/Berlin; fuzzy ranks those poorly.
        widget = ['filter', *style, '--placeholder', 'Type to search', '--height', '12', '--no-fuzzy']
        widget += ['--indicator.foreground', ACCENT, '--match.foreground', ACCENT]
    else:
        widget = ['choose', *style, '--cursor.foreground', ACCENT, '--selected.foreground', ACCENT]
    selected = gum(*widget, stdin='\n'.join(choices) + '\n')
    if selected not in choices:
        raise ValueError('The picker returned an invalid selection.')
    return selected


def show_error(error):
    say(str(error), WARNING)


def prompt(label, default='', validate=None, placeholder=''):
    while True:
        answer = gum(
            'input',
            '--header',
            label,
            '--header.foreground',
            ACCENT,
            '--padding',
            PAD,
            '--value',
            default,
            '--placeholder',
            placeholder,
        ).strip()
        try:
            if validate:
                validate(answer)
            return answer
        except ValueError as error:
            show_error(error)


def secret(label):
    while True:
        first = gum('input', '--password', '--header', label, '--header.foreground', ACCENT, '--padding', PAD)
        try:
            validate_secret(first, label)
            repeat = gum(
                'input',
                '--password',
                '--header',
                'Repeat ' + label.lower(),
                '--header.foreground',
                ACCENT,
                '--padding',
                PAD,
            )
            if first != repeat:
                raise ValueError('Passwords did not match. Try again.')
            return first
        except ValueError as error:
            show_error(error)


def done(label, value):
    say(f'{label}: {value}', DIM)


def table(rows):
    width = max(len(key) for key, _ in rows)
    return '\n'.join(f'{key.ljust(width)}  {value}' for key, value in rows)


def boot_name(snapshot, number):
    return storage.entry_label(snapshot['entries'][number])


def disk_label(disk):
    return storage.disk_identity(disk)


def wizard(state):
    if not shutil.which('gum'):
        raise ValueError('The live installer is missing gum, required for its menus.')
    if not sys.stdin.isatty():
        raise ValueError('The guided installer needs the console; automated runs use --config.')
    rejections = storage.disk_rejections()
    disks = [disk for disk, reason in rejections if reason is None]
    if not disks:
        detail = storage.describe_rejections(rejections)
        raise ValueError(
            f'No unused writable disk of at least {storage.MIN_DISK_BYTES // storage.GIB} GiB found. '
            'TTY2 is available for recovery.' + (f'\n{detail}' if detail else '')
        )
    labels = {disk_label(disk): disk for disk in disks}
    boot_map = storage.boot_entry_nodes(disks)
    screen(
        'Welcome',
        'Installs the full desktop from this USB; no network needed. Secure Boot must be disabled.\n'
        'Esc cancels at any point before the final confirmation. TTY2 has a root shell.',
    )
    for disk in disks:
        say(disk_label(disk), bold=True)
        say(textwrap.indent(storage.describe_contents(disk, boot_map), '  '))
    print()
    disk = labels[choose('Install on which disk?', list(labels))]
    erase = f'Erase disk: replace everything on {disk["name"]}'
    alongside = 'Alongside: use existing free space and keep every partition'
    screen('Storage', disk_label(disk))
    snapshot = None
    region = None
    boot_number = None
    while choose('How should this disk be used?', [erase, alongside]) == alongside:
        try:
            snapshot = storage.alongside_snapshot(disk['name'])
            break
        except ValueError as error:
            # The refusal names what this disk lacks; Erase disk is still on offer.
            show_error(error)
    if snapshot:
        sector = snapshot['table']['sectorsize']
        regions = {
            f'{(last - first + 1) * sector / GIB:.1f} GiB free (sectors {first}–{last})': [first, last]
            for first, last in snapshot['regions']
        }
        region = regions[choose('Install into which unallocated region?', list(regions))]
        boots = {f'{boot_name(snapshot, number)} (Boot{number})': number for number in snapshot['boots']}
        boot_number = boots[choose('Which existing OS should stay in the Limine boot menu?', list(boots))]
        storage.validate_existing_loader(snapshot['boots'][boot_number])
    screen('Keyboard and time zone')
    keymaps = run('localectl', 'list-keymaps', capture=True).splitlines()
    keymap = choose('Keyboard layout', keymaps, default='us', search=True)
    run('loadkeys', keymap)
    done('Keyboard layout', keymap)
    timezones = run('timedatectl', 'list-timezones', capture=True).splitlines()
    timezone = choose('Time zone', timezones, default='Asia/Amman', search=True)
    done('Time zone', timezone)
    screen('Your account')
    username = prompt('Username', validate=validate_username, placeholder='lowercase letters, digits, - or _')
    done('Username', username)
    hostname = prompt('Computer name (hostname)', 'dux', validate=validate_hostname)
    done('Computer name', hostname)
    state.confirmed_layout = snapshot
    available_bytes = (region[1] - region[0] + 1) * snapshot['table']['sectorsize'] if snapshot else int(disk['size'])
    screen('Swap and hibernation', 'Swap lives in an encrypted file; hibernation needs at least as much swap as RAM.')
    while True:
        automatic = f'Auto: {memory_gib() + 2} GiB encrypted swap with hibernation'
        choice = choose('Swap size', [automatic, 'Custom size', 'No swap and no hibernation'])
        if choice == automatic:
            swap_gib = None
        elif choice == 'Custom size':

            def validate_size(value):
                if not value.isdecimal() or int(value) < 1:
                    raise ValueError('Enter a whole number of GiB greater than zero.')

            swap_gib = int(prompt('Swap size in GiB', str(memory_gib() + 2), validate_size))
        else:
            swap_gib = 0
        try:
            size = swap_size(swap_gib, available_bytes)
            break
        except ValueError as error:
            show_error(error)
    state.confirmed_device = storage.require_disk(disk['name'])
    screen(
        'Passwords',
        'The disk passphrase unlocks the encrypted disk at every boot.\n'
        'The login password is for your account and for sudo. Each needs at least 8 characters.',
    )
    passphrase = secret('Disk passphrase')
    done('Disk passphrase', 'set')
    password = secret('Login password')
    settings = Settings(
        disk['name'],
        username,
        hostname,
        timezone,
        keymap,
        passphrase,
        password,
        swap_gib=swap_gib,
        mode='alongside' if snapshot else 'erase',
        free_region=region,
        existing_boot=boot_number,
    )
    settings.validate()
    if storage.require_disk(disk['name']) != state.confirmed_device:
        raise ValueError('Disk identity changed while entering installation settings.')
    swap = f'{size} GiB encrypted, hibernation on' if size else 'none, hibernation off'
    rows = [
        ('Disk', disk_label(disk)),
        ('Account', f'{settings.username} on {settings.hostname}'),
        ('Keyboard', settings.keymap),
        ('Time zone', settings.timezone),
        ('Swap', swap),
    ]
    if snapshot:
        plan = storage.alongside_plan(settings, snapshot)
        title = 'Alongside: every existing partition is kept'
        rows.append(('Keep booting', boot_name(snapshot, boot_number)))
        changes = 'New partitions (Limine becomes the firmware default):\n' + '\n'.join(
            f'  {storage.partition_name(settings.disk, n)}  sectors {a}–{b}, '
            f'{(b - a + 1) * snapshot["table"]["sectorsize"] / GIB:.2f} GiB, {label}'
            for n, a, b, _kind, label in plan
        )
        border = ACCENT
        action = 'Create partitions and install'
    else:
        title = f'ERASE ALL DATA on {settings.disk}'
        rows.append(('Layout', f'{storage.ESP_GIB} GiB EFI + encrypted Btrfs with a separate /home'))
        changes = 'Destroyed: everything currently on this disk\n' + textwrap.indent(
            storage.describe_contents(disk, boot_map), '  '
        )
        border = WARNING
        action = 'Erase and install'
    if size and size < memory_gib():
        changes += '\nWarning: swap is smaller than RAM; hibernation may fail under heavy memory use.'
    screen('Review')
    say(title, border, bold=True)
    gum(
        'style',
        '--border',
        'normal',
        '--border-foreground',
        border,
        '--padding',
        '0 1',
        '--margin',
        PAD,
        table(rows) + '\n\n' + changes,
        show=True,
    )
    print()
    confirmed = subprocess.run(
        ['gum', 'confirm', '--padding', PAD, '--default=false', '--affirmative', action, '--negative', 'Cancel']
        + ['--selected.background', border, '--prompt.foreground', border, 'Start the installation?'],
        check=False,
    )
    if confirmed.returncode != 0:
        raise Cancelled()
    settings.swap_gib = size
    return settings


STEPS = 6


class PlainProgress:
    """Numbered step lines on stdout. Automated runs and their log readers rely on this format."""

    def step(self, number, label, packages=False):
        print(f'\n[{number}/{STEPS}] {label}…', flush=True)


# Share of the package step's time spent before post-transaction hooks start
# (about 70% in a VM: hooks such as the initramfs build take the rest).
HOOK_START = 0.7


class PacmanProgress:
    """Follows pacman's output without a terminal: plain 'installing NAME...' and '(i/n) hook' lines."""

    def __init__(self):
        self.total = None
        self.installed = 0
        self.hook = None
        self.activity = ''

    def feed(self, line):
        line = line.strip()
        if match := re.fullmatch(r'Packages \((\d+)\).*', line):
            self.total = int(match[1])
        elif re.fullmatch(r'installing \S+\.\.\.', line):
            self.installed += 1
            self.activity = line
        elif match := re.fullmatch(r'\(\s*(\d+)/(\d+)\) (.+)', line):
            self.hook = (int(match[1]), int(match[2]))
            self.activity = match[3]
        elif line.startswith((':: ', 'checking ', 'loading ')):
            self.activity = line.removeprefix(':: ')

    def fraction(self):
        """Share done for one bar that never moves back: packages fill most of it, hooks the rest.
        None before pacman reports a total."""
        if self.hook:
            return HOOK_START + (1 - HOOK_START) * (self.hook[0] - 1) / self.hook[1]
        if self.total:
            return HOOK_START * min(self.installed / self.total, 1)
        return None

    def counts(self):
        if self.hook:
            return f'finishing up {self.hook[0]} of {self.hook[1]}'
        if self.total:
            return f'{self.installed} of {self.total} packages'
        return 'preparing'


def clock(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f'{minutes}:{seconds:02d}'


def visible(line):
    return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]', '', line.split('\r')[-1]).rstrip()


class LiveProgress:
    """Shows install steps, elapsed time and a package bar on the console while every
    command's output goes to the log.

    Redirecting file descriptors 1 and 2 captures the installer's own prints and every
    child process without changing how commands run. gum has no progress bar, so this
    redraws a small block in place, like `make try`. It sticks to glyphs in the Linux
    console's default font.
    """

    SPINNER = '|/-\\'

    def __init__(self, log_path=LOG):
        self.log_path = log_path
        self.steps = []
        self.pacman = None
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.started = time.monotonic()
        self.height = 0
        self.last_line = ''
        self.partial = ''

    def __enter__(self):
        # Open the console first, so a missing one leaves the previous log intact.
        self.tty = open('/dev/tty', 'w')
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = self.log_path.open('w')
        self.reader = self.log_path.open('r', errors='replace')
        sys.stdout.flush()
        sys.stderr.flush()
        self.saved = [os.dup(1), os.dup(2)]
        os.dup2(self.log.fileno(), 1)
        os.dup2(self.log.fileno(), 2)
        self.tty.write('\x1b[H\x1b[2J\x1b[?25l')
        self.thread = threading.Thread(target=self.animate, daemon=True)
        self.thread.start()
        return self

    def step(self, number, label, packages=False):
        print(f'\n[{number}/{STEPS}] {label}…', flush=True)
        with self.lock:
            self.steps.append((number, label, time.monotonic()))
            self.pacman = PacmanProgress() if packages else None

    def follow_log(self):
        # Hold back an unfinished last line so the parser never sees half of one.
        *lines, self.partial = (self.partial + self.reader.read()).split('\n')
        for line in lines:
            line = visible(line)
            if self.pacman:
                self.pacman.feed(line)
            if re.search('[A-Za-z]{3}', line):
                self.last_line = line.strip()

    def frame(self, tick, failed=False):
        now = time.monotonic()
        width = max(40, os.get_terminal_size(self.tty.fileno()).columns - 1)
        lines = ['', f'  \x1b[1;36mInstalling Arch + Niri + Dux\x1b[0m  {clock(now - self.started)}', '']
        for index, (number, label, began) in enumerate(self.steps):
            current = index == len(self.steps) - 1
            ended = now if current else self.steps[index + 1][2]
            if not current:
                mark = '\x1b[32m√\x1b[0m'
            elif failed:
                mark = '\x1b[31mx\x1b[0m'
            else:
                mark = '\x1b[36m' + self.SPINNER[tick % len(self.SPINNER)] + '\x1b[0m'
            lines.append(f'  {mark} {number}/{STEPS}  {label.ljust(46)} {clock(ended - began)}')
            if current and self.pacman:
                fraction = self.pacman.fraction()
                bar = 36
                filled = int((fraction or 0) * bar)
                lines.append(
                    f'          \x1b[36m{"█" * filled}\x1b[2m{"░" * (bar - filled)}\x1b[0m  {self.pacman.counts()}'
                )
        if not failed and self.last_line:
            lines += ['', '\x1b[2m  ' + self.last_line[: width - 4] + '\x1b[0m']
        lines += ['', f'\x1b[2m  Full log: {self.log_path}\x1b[0m']
        return lines

    def draw(self, lines):
        up = f'\x1b[{self.height}F' if self.height else ''
        self.tty.write(up + '\x1b[J' + ''.join(line + '\n' for line in lines))
        self.tty.flush()
        self.height = len(lines)

    def animate(self):
        tick = 0
        while not self.stopped.wait(0.2):
            try:
                with self.lock:
                    self.follow_log()
                    self.draw(self.frame(tick))
            except OSError as error:
                # The display is a convenience; the installation must go on without it.
                print(f'Progress display stopped: {error}', flush=True)
                return
            tick += 1

    def __exit__(self, kind, error, traceback):
        self.stopped.set()
        self.thread.join()
        sys.stdout.flush()
        sys.stderr.flush()
        # Restore the console before anything else can fail, so the installer's
        # own error message is never lost in the log.
        os.dup2(self.saved[0], 1)
        os.dup2(self.saved[1], 2)
        for descriptor in self.saved:
            os.close(descriptor)
        try:
            with self.lock:
                self.follow_log()
                self.draw(self.frame(0, failed=kind is not None))
            self.tty.write('\x1b[?25h')
            self.tty.close()
        except OSError:
            pass
        self.log.close()
        self.reader.close()
        if kind is not None and kind is not Cancelled:
            tail = [visible(line) for line in self.log_path.read_text(errors='replace').splitlines()]
            tail = [line for line in tail if line.strip()][-15:]
            print('\nLast lines of the log:')
            print('\n'.join('  ' + line for line in tail))
            print(f'Full log: {self.log_path}')
        return False


def finish(settings, packages, seconds):
    """Say what was installed, then reboot when the user is ready."""
    screen('Installation complete', f'Installed {packages} packages in {clock(seconds)}.')
    swap = f'{settings.swap_gib} GiB encrypted, hibernation on' if settings.swap_gib else 'none'
    rows = [
        ('Desktop', 'Niri with Dux, from this USB'),
        ('Disk', f'{settings.disk}, encrypted Btrfs'),
        ('Account', f'{settings.username} on {settings.hostname}'),
        ('Swap', swap),
        ('Recovery', 'snapshot "Fresh installation" in the Limine boot menu'),
    ]
    gum(
        'style',
        '--border',
        'normal',
        '--border-foreground',
        ACCENT,
        '--padding',
        '0 1',
        '--margin',
        PAD,
        table(rows),
        show=True,
    )
    say(
        'At boot, type the disk passphrase, then log in as '
        f'{settings.username}.\nWi-Fi and updates are set up from the desktop.'
    )
    print()
    reboot = subprocess.run(
        ['gum', 'confirm', '--padding', PAD, '--affirmative', 'Reboot', '--negative', 'Stay in the live system']
        + [
            '--selected.background',
            ACCENT,
            '--prompt.foreground',
            ACCENT,
            'Remove the USB, then press Enter to reboot.',
        ],
        check=False,
    )
    if reboot.returncode == 0:
        run('systemctl', 'reboot')
    else:
        print('Type reboot when you are ready.')


def interactive(state):
    settings = wizard(state)
    started = time.monotonic()
    with LiveProgress() as progress:
        packages = install(settings, state, progress)
    finish(settings, packages, time.monotonic() - started)


def write(relative, contents, mode=0o644):
    path = TARGET / relative.lstrip('/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(mode)


def write_keyboard_layout(keymap):
    """Write the console keymap and its matching X11 layout for the login stack.

    Niri leaves its xkb section empty so it inherits systemd's X11 keyboard
    setting. The console KEYMAP alone leaves that setting at US, so a non-US
    password typed at the login screen would use the wrong layout.
    """
    mapping = storage.x11_keyboard_layout(keymap)
    if mapping is None:
        print(f"Warning: no X11 layout mapping for console keymap '{keymap}'; the login screen will use US.")
        layout, model, variant, options = 'us', 'pc105', None, None
    else:
        layout, model, variant, options = mapping
    write('/etc/X11/xorg.conf.d/00-keyboard.conf', storage.xorg_keyboard_conf(keymap, layout, model, variant, options))


def register_alongside_boot(settings, snapshot, plan):
    try:
        storage.finish_alongside_boot(settings, snapshot, plan[0][0], TARGET)
    except (ValueError, subprocess.CalledProcessError) as error:
        # Firmware refusals surface as CalledProcessError (efibootmgr exits
        # nonzero when the variable write is rejected), not ValueError.
        raise ValueError(f'{error}\n\n{storage.alongside_recovery_text(settings, snapshot, plan)}') from error


def write_boot_config(luks_uuid, filesystem_uuid, resume_offset, *, defer_efi=False):
    cmdline = f'rd.luks.name={luks_uuid}=dux-root root=/dev/mapper/dux-root rootflags=subvol=@ rw quiet splash'
    if resume_offset:
        cmdline += f' resume=/dev/mapper/dux-root resume_offset={resume_offset}'
    else:
        write('/etc/systemd/sleep.conf.d/60-hibernation.conf', '[Sleep]\nAllowHibernation=no\n')
    defaults = (
        'ESP_PATH="/boot"\n'
        f'KERNEL_CMDLINE[default]="{cmdline}"\n'
        f'FS_UUID="{filesystem_uuid}"\n'
        '# Snapshot recovery must not resume a normal-root memory image.\n'
        'SNAPSHOT_KERNEL_PARAMETERS+=noresume\n'
    )
    if defer_efi:
        defaults += 'SKIP_UEFI=yes\n'
    write('/etc/default/limine', defaults)


def recovery_fstab(text):
    lines = []
    for line in text.splitlines():
        fields = line.split()
        if not line.lstrip().startswith('#') and len(fields) >= 4:
            options = fields[3].split(',')
            if any(option.startswith('subvol=') for option in options):
                # Replacing the root from a snapshot changes its numeric subvolume ID.
                fields[3] = ','.join(option for option in options if not option.startswith('subvolid='))
                line = '\t'.join(fields)
        lines.append(line)
    return '\n'.join(lines) + '\n'


def check_installation_clock():
    """Refuse when the system clock predates the ISO build; stale clocks mimic expired keys."""
    try:
        build_date = int((SHARE / 'iso-build-date').read_text().strip())
    except (OSError, ValueError):
        raise ValueError('The ISO build date is missing or invalid; refusing to install from this medium.')
    now = time.time()
    if now < build_date:
        iso = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(build_date))
        clock = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(now))
        raise ValueError(
            f'System clock ({clock}) is earlier than the ISO build date ({iso}). Fix the system clock and retry.'
        )


def verify_payload_signatures(config, database, repository, filenames):
    """Verify every resolved package file against the install keyring before the disk is touched.

    pacman --print resolves names only, so without this the first pacstrap is
    the first real signature check, after partitioning. Checking the files in
    place exercises the same keyring and expiry enforcement the pacstrap will
    use, which plain gpg verification would still accept. Nothing is copied,
    and the medium is never written.
    """
    try:
        run(
            'pacman',
            '--config',
            str(config),
            '--dbpath',
            str(database),
            '--upgrade',
            '--print',
            '--print-format',
            '%f',
            *(str(repository / name) for name in filenames),
        )
    except subprocess.CalledProcessError as error:
        raise ValueError(
            'Package signature preflight failed; the install keyring cannot verify the offline payload. '
            'A signing key may have expired or is missing; check the system clock and use a newer ISO.'
        ) from error


def verify_repository(repository):
    manifest = repository / 'SHA256SUMS'
    if not manifest.is_file():
        raise ValueError('Offline payload checksum manifest is missing.')
    listed = set()
    for line in manifest.read_text().splitlines():
        if not line:
            continue
        checksum, filename = line.split(maxsplit=1)
        filename = filename.lstrip('*')
        path = repository / filename
        if not re.fullmatch(r'[a-f0-9]{64}', checksum) or not path.resolve().is_relative_to(repository.resolve()):
            raise ValueError('Invalid offline payload manifest.')
        if not path.is_file():
            raise ValueError(f'Offline payload file is missing: {filename}')
        with path.open('rb') as stream:
            actual_checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual_checksum != checksum:
            raise ValueError(f'Offline payload checksum failed: {filename}')
        listed.add(filename)
    packages = {file.name for file in repository.glob('*.pkg.tar.*') if not file.name.endswith('.sig')}
    databases = {file.name for file in repository.glob('*.db')}
    if not packages or not databases or not (packages | databases).issubset(listed):
        raise ValueError('Offline manifest does not cover every package and repository database.')


def offline_pacman_config(repository, database):
    # The database is assembled on the builder, without a release signing key.
    # Require signatures on every package even when its metadata is rewritten.
    return (
        '[options]\nArchitecture = auto\nSigLevel = Required DatabaseNever TrustedOnly\n'
        'LocalFileSigLevel = Required TrustedOnly\n'
        f'GPGDir = {SHARE / "install-keyring"}\n'
        f'[{database}]\nServer = file://{repository}\n'
    )


def install(settings, state=None, progress=None):
    """Install offline onto the confirmed disk and return how many packages were installed."""
    state = state if state is not None else InstallationState()
    progress = progress or PlainProgress()
    started_at = time.monotonic()
    settings.validate()
    original_device = storage.require_disk(settings.disk)
    if state.confirmed_device is not None and original_device != state.confirmed_device:
        raise ValueError('The confirmed disk was replaced; refusing to erase it.')
    if Path('/dev/mapper', MAPPER).exists():
        raise ValueError(f'/dev/mapper/{MAPPER} is already in use.')
    if os.path.ismount(TARGET):
        raise ValueError('Installation mountpoint is already mounted.')
    packages_file = SHARE / 'packages.target'
    packages = [line.split('#', 1)[0].strip() for line in packages_file.read_text().splitlines()]
    packages = [package for package in packages if package]
    if not packages or any(not re.fullmatch(r'[a-zA-Z0-9@+_.-]+', package) for package in packages):
        raise ValueError('Invalid or missing target package list.')
    progress.step(1, 'Checking the offline package payload')
    check_installation_clock()
    verify_repository(REPO)
    for name in (('sfdisk', 'efibootmgr') if settings.mode == 'alongside' else ()) + (
        'sgdisk',
        'wipefs',
        'cryptsetup',
        'mkfs.btrfs',
        'mkfs.fat',
        'pacstrap',
        'arch-chroot',
    ):
        if not shutil.which(name):
            raise ValueError(f'Required installer tool missing: {name}')
    # Recheck immediately before the first destructive operation.
    if storage.require_disk(settings.disk) != original_device:
        raise ValueError('Disk identity changed; refusing to erase it.')
    snapshot = None
    available_bytes = int(original_device[2])
    if settings.mode == 'alongside':
        snapshot = storage.alongside_snapshot(settings.disk)
        if state.confirmed_layout is not None and state.confirmed_layout != snapshot:
            raise ValueError('Confirmed partition table or firmware entries changed.')
        plan = storage.alongside_plan(settings, snapshot)
        storage.reject_unsupported_existing_storage(settings, snapshot)
        storage.validate_existing_loader(snapshot['boots'][settings.existing_boot.upper()])
        available_bytes = (settings.free_region[1] - settings.free_region[0] + 1) * snapshot['table']['sectorsize']
        print('Create partitions (inclusive logical sectors):', plan, flush=True)
    size = swap_size(settings.swap_gib, available_bytes)
    subvolumes = dict(SUBVOLUMES)
    if size:
        subvolumes['@swap'] = '/swap'

    mapped = '/dev/mapper/' + MAPPER
    mounted = False
    opened = False
    TARGET.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dux-install-', dir='/run') as staging:
        stage = Path(staging)
        offline_conf = stage / 'pacman.conf'
        database = next(REPO.glob('*.db')).stem
        offline_conf.write_text(offline_pacman_config(REPO, database))
        package_db = stage / 'db'
        package_db.mkdir()
        resolved = run(
            'pacman',
            '--config',
            str(offline_conf),
            '--dbpath',
            str(package_db),
            '--sync',
            '--refresh',
            '--print',
            '--noconfirm',
            '--print-format',
            '%f',
            *packages,
            'arch-niri-dux',
            'arch-niri-dux-baseline',
            capture=True,
        )
        for filename in resolved.splitlines():
            if '.pkg.tar.' in filename and not (REPO / filename).is_file():
                raise ValueError(f'Offline dependency is missing: {filename}')
        verify_payload_signatures(
            offline_conf, package_db, REPO, [line for line in resolved.splitlines() if '.pkg.tar.' in line]
        )
        if storage.require_disk(settings.disk) != original_device:
            raise ValueError('Disk identity changed during package preflight; refusing to erase it.')
        try:
            progress.step(2, 'Partitioning and encrypting the selected disk')
            esp, encrypted = storage.create_partitions(settings, snapshot, original_device, state.begin)
            run('mkfs.fat', '-F', '32', '-n', 'DUX_EFI', esp)
            # LUKS2 uses cryptsetup's default argon2id (benchmarked memory, ~1 GiB here);
            # no pinned limit is set.
            run(
                'cryptsetup',
                'luksFormat',
                '--batch-mode',
                '--type',
                'luks2',
                '--key-file',
                '-',
                encrypted,
                data=settings.passphrase,
            )
            run('cryptsetup', 'open', '--key-file', '-', encrypted, MAPPER, data=settings.passphrase)
            opened = True
            run('mkfs.btrfs', '--force', '--label', 'DUX_ROOT', mapped)
            run('mount', mapped, str(TARGET))
            mounted = True
            for volume in subvolumes:
                run('btrfs', 'subvolume', 'create', str(TARGET / volume))
            run('umount', str(TARGET))
            mounted = False
            run('mount', '-o', 'subvol=@,compress=zstd:1,noatime', mapped, str(TARGET))
            mounted = True
            for volume, location in subvolumes.items():
                if location == '/':
                    continue
                mountpoint = TARGET / location.lstrip('/')
                mountpoint.mkdir(parents=True, exist_ok=True)
                run('mount', '-o', f'subvol={volume},compress=zstd:1,noatime', mapped, str(mountpoint))
            (TARGET / 'boot').mkdir(exist_ok=True)
            run('mount', '-o', 'umask=0077', esp, str(TARGET / 'boot'))
            resume_offset = ''
            if size:
                # mkswapfile sets NODATACOW and disables compression for this file.
                # A separate subvolume keeps it outside every root snapshot.
                (TARGET / 'swap').chmod(0o700)
                run('btrfs', 'filesystem', 'mkswapfile', '--size', f'{size}G', str(TARGET / 'swap/swapfile'))
                resume_offset = run(
                    'btrfs', 'inspect-internal', 'map-swapfile', '-r', str(TARGET / 'swap/swapfile'), capture=True
                )
                if not re.fullmatch(r'[0-9]+', resume_offset):
                    raise ValueError('Btrfs returned an invalid swap resume offset.')
            progress.step(3, 'Installing packages from the USB', packages=True)
            # Delay snap-pac until Snapper and a bootable baseline exist.
            base_packages = [package for package in packages if package != 'snap-pac'] + ['arch-niri-dux']
            luks_uuid = run('blkid', '-s', 'UUID', '-o', 'value', encrypted, capture=True)
            filesystem_uuid = run('blkid', '-s', 'UUID', '-o', 'value', mapped, capture=True)
            # Both EFI deployment and kernel hooks may register firmware entries.
            # Deferral must cover every transaction until preservation is verified.
            write_boot_config(luks_uuid, filesystem_uuid, resume_offset, defer_efi=bool(snapshot))
            run('pacstrap', '-C', str(offline_conf), '-K', str(TARGET), *base_packages)
            fstab = run('genfstab', '-U', str(TARGET), capture=True)
            if size:
                fstab += '\n/swap/swapfile none swap defaults 0 0\n'
            write('/etc/fstab', recovery_fstab(fstab))
            progress.step(4, 'Configuring the desktop, user and bootloader')
            write('/etc/hostname', settings.hostname + '\n')
            write(
                '/etc/hosts',
                f'127.0.0.1 localhost\n::1 localhost\n127.0.1.1 {settings.hostname}.localdomain {settings.hostname}\n',
            )
            write('/etc/locale.gen', 'en_US.UTF-8 UTF-8\n')
            write('/etc/locale.conf', 'LANG=en_US.UTF-8\n')
            write('/etc/vconsole.conf', 'KEYMAP=' + settings.keymap + '\n')
            write_keyboard_layout(settings.keymap)
            # arch-chroot mounts a fresh /run, so stage outside that mountpoint.
            target_script = TARGET / 'root/configure-target.sh'
            shutil.copy2(Path(__file__).with_name('configure-target.sh'), target_script)
            run('arch-chroot', str(TARGET), 'bash', '/root/configure-target.sh', settings.username, settings.timezone)
            target_script.unlink()
            run('arch-chroot', str(TARGET), 'chpasswd', data=f'{settings.username}:{settings.password}\n')
            progress.step(5, 'Preparing automatic snapshots and recovery')
            run('pacstrap', '-C', str(offline_conf), '-G', '-M', str(TARGET), 'arch-niri-dux-baseline')
            run(
                'arch-chroot',
                str(TARGET),
                'snapper',
                '--no-dbus',
                '-c',
                'root',
                'create',
                '--cleanup-algorithm',
                'number',
                '--description',
                'Fresh installation',
            )
            run('arch-chroot', str(TARGET), 'limine-snapper-sync')
            progress.step(6, 'Saving changes and unmounting the installation')
            if snapshot:
                register_alongside_boot(settings, snapshot, plan)
                write_boot_config(luks_uuid, filesystem_uuid, resume_offset)
            run('sync')
        finally:
            if mounted:
                subprocess.run(['umount', '--recursive', str(TARGET)], check=False)
            if opened:
                subprocess.run(['cryptsetup', 'close', MAPPER], check=False)
    elapsed = round(time.monotonic() - started_at)
    print(
        f'\nINSTALLATION COMPLETE — took {elapsed} seconds. Remove the USB and reboot.\n'
        'Use the disk passphrase at boot, then your username and login password.\n'
        'Snapshot recovery is available in the Limine boot menu.'
    )
    return sum('.pkg.tar.' in line for line in resolved.splitlines())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='Private JSON input for automated VM installation')
    parser.add_argument('--confirm-disk', help='Explicit erase confirmation for --config mode')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root from the installation ISO.')
    if not Path('/sys/firmware/efi').exists():
        parser.error('Boot the ISO in UEFI mode.')
    secure_boot_files = list(Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'))
    if secure_boot_files and secure_boot_files[0].read_bytes()[-1] == 1:
        parser.error('Disable Secure Boot before installation.')
    state = InstallationState()
    try:
        if args.config:
            if args.config.stat().st_mode & 0o077:
                parser.error('The input JSON must be private (chmod 600).')
            settings = Settings(**json.loads(args.config.read_text()))
            if args.confirm_disk != settings.disk:
                parser.error('--config requires --confirm-disk with the exact target disk path.')
            install(settings, state)
        else:
            interactive(state)
    except (Cancelled, EOFError):
        print('\nInstallation cancelled. No disk was changed.')
        return 130
    except (ValueError, TypeError, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        consequence = 'The selected disk may be incomplete.' if state.installation_started else 'No disk was changed.'
        detail = ''
        if state.installation_started:
            detail = (
                "\nBefore retrying, release this attempt's mounts and mappings:\n"
                f'  umount -R {TARGET}\n'
                f'  cryptsetup close {MAPPER}'
            )
        print(f'\nInstallation stopped: {error}\n{consequence}{detail}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
