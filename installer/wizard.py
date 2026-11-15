"""Guided questions, settings validation and confirmation, plus the closing reboot screen.

Automated --config runs skip the questions and construct Settings directly."""

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

if __package__:
    from . import storage
else:
    import storage

run = storage.run


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


# Interactive presentation. Only the wizard and the engine's live progress touch the
# console; automated --config runs keep plain, parseable output.
ACCENT = '6'
WARNING = '1'
DIM = '8'
# Every element shares one left margin, so text and widgets line up.
PAD = '0 0 0 2'


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


def ask(state):
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
    labels = {storage.disk_identity(disk): disk for disk in disks}
    boot_map = storage.boot_entry_nodes(disks)
    screen(
        'Welcome',
        'Installs the full desktop from this USB; no network needed. Secure Boot must be disabled.\n'
        'Esc cancels at any point before the final confirmation. TTY2 has a root shell.',
    )
    for disk in disks:
        say(storage.disk_identity(disk), bold=True)
        say(textwrap.indent(storage.describe_contents(disk, boot_map), '  '))
    print()
    disk = labels[choose('Install on which disk?', list(labels))]
    erase = f'Erase disk: replace everything on {disk["name"]}'
    alongside = 'Alongside: use existing free space and keep every partition'
    screen('Storage', storage.disk_identity(disk))
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
            f'{(last - first + 1) * sector / storage.GIB:.1f} GiB free (sectors {first}–{last})': [first, last]
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
        automatic = f'Auto: {storage.memory_gib() + 2} GiB encrypted swap with hibernation'
        choice = choose('Swap size', [automatic, 'Custom size', 'No swap and no hibernation'])
        if choice == automatic:
            swap_gib = None
        elif choice == 'Custom size':

            def validate_size(value):
                if not value.isdecimal() or int(value) < 1:
                    raise ValueError('Enter a whole number of GiB greater than zero.')

            swap_gib = int(prompt('Swap size in GiB', str(storage.memory_gib() + 2), validate_size))
        else:
            swap_gib = 0
        try:
            size = storage.swap_size(swap_gib, available_bytes)
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
        ('Disk', storage.disk_identity(disk)),
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
            f'{(b - a + 1) * snapshot["table"]["sectorsize"] / storage.GIB:.2f} GiB, {label}'
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
    if size and size < storage.memory_gib():
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


def clock(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f'{minutes}:{seconds:02d}'


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
