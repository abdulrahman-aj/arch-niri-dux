#!/usr/bin/env python3
"""Guided erase or existing-free-space installation. All package inputs come from the booted ISO."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

if __package__:
    from . import payload
    from . import storage
    from . import wizard
else:
    import payload
    import storage
    import wizard

run = storage.run


TARGET = Path('/mnt/arch-niri-dux')
MAPPER = 'dux-root'
SUBVOLUMES = {
    '@': '/',
    '@home': '/home',
    '@log': '/var/log',
    '@cache': '/var/cache/pacman/pkg',
    '@snapshots': '/.snapshots',
}


@dataclass
class InstallationState:
    confirmed_layout: dict | None = None
    confirmed_device: tuple | None = None
    installation_started: bool = False

    def begin(self):
        self.installation_started = True


# Interactive runs send every command's output here while LiveProgress owns the console.
LOG = Path('/var/log/arch-niri-install.log')
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
        lines = ['', f'  \x1b[1;36mInstalling Arch + Niri + Dux\x1b[0m  {wizard.clock(now - self.started)}', '']
        for index, (number, label, began) in enumerate(self.steps):
            current = index == len(self.steps) - 1
            ended = now if current else self.steps[index + 1][2]
            if not current:
                mark = '\x1b[32m√\x1b[0m'
            elif failed:
                mark = '\x1b[31mx\x1b[0m'
            else:
                mark = '\x1b[36m' + self.SPINNER[tick % len(self.SPINNER)] + '\x1b[0m'
            lines.append(f'  {mark} {number}/{STEPS}  {label.ljust(46)} {wizard.clock(ended - began)}')
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
        if kind is not None and kind is not wizard.Cancelled:
            tail = [visible(line) for line in self.log_path.read_text(errors='replace').splitlines()]
            tail = [line for line in tail if line.strip()][-15:]
            print('\nLast lines of the log:')
            print('\n'.join('  ' + line for line in tail))
            print(f'Full log: {self.log_path}')
        return False


def interactive(state):
    settings = wizard.ask(state)
    started = time.monotonic()
    with LiveProgress() as progress:
        packages = install(settings, state, progress)
    wizard.finish(settings, packages, time.monotonic() - started)


def x11_keyboard_layout(keymap, map_path=Path('/usr/share/systemd/kbd-model-map')):
    """Map a console keymap to (layout, model, variant, options) for the login stack.

    Niri leaves its xkb section empty so it inherits systemd's X11 keyboard
    setting; without this mapping a non-US console keymap would still type US
    at the login screen. The conversion table is the same kbd-model-map that
    localectl set-keymap consults.
    """
    try:
        text = Path(map_path).read_text()
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        fields = stripped.split()
        if len(fields) < 5 or fields[0] != keymap:
            continue
        layout, model, variant, options = fields[1], fields[2], fields[3], fields[4]
        return (layout, model, None if variant == '-' else variant, None if options == '-' else options)
    return None


def xorg_keyboard_conf(keymap, layout, model, variant=None, options=None):
    """Render the localectl-equivalent X11 keyboard file for the target."""
    lines = [
        f'# Generated by arch-niri-install from console keymap {keymap}.',
        'Section "InputClass"',
        '        Identifier "system-keyboard"',
        '        MatchIsKeyboard "on"',
        f'        Option "XkbLayout" "{layout}"',
        f'        Option "XkbModel" "{model}"',
    ]
    if variant:
        lines.append(f'        Option "XkbVariant" "{variant}"')
    if options:
        lines.append(f'        Option "XkbOptions" "{options}"')
    lines.append('EndSection')
    return '\n'.join(lines) + '\n'


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
    mapping = x11_keyboard_layout(keymap)
    if mapping is None:
        print(f"Warning: no X11 layout mapping for console keymap '{keymap}'; the login screen will use US.")
        layout, model, variant, options = 'us', 'pc105', None, None
    else:
        layout, model, variant, options = mapping
    write('/etc/X11/xorg.conf.d/00-keyboard.conf', xorg_keyboard_conf(keymap, layout, model, variant, options))


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
    packages_file = payload.SHARE / 'packages.target'
    packages = [line.split('#', 1)[0].strip() for line in packages_file.read_text().splitlines()]
    packages = [package for package in packages if package]
    if not packages or any(not re.fullmatch(r'[a-zA-Z0-9@+_.-]+', package) for package in packages):
        raise ValueError('Invalid or missing target package list.')
    progress.step(1, 'Checking the offline package payload')
    payload.check_installation_clock()
    payload.verify_repository(payload.REPO)
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
    size = storage.swap_size(settings.swap_gib, available_bytes)
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
        database = next(payload.REPO.glob('*.db')).stem
        offline_conf.write_text(payload.offline_pacman_config(payload.REPO, database))
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
            if '.pkg.tar.' in filename and not (payload.REPO / filename).is_file():
                raise ValueError(f'Offline dependency is missing: {filename}')
        payload.verify_payload_signatures(
            offline_conf, package_db, payload.REPO, [line for line in resolved.splitlines() if '.pkg.tar.' in line]
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
            settings = wizard.Settings(**json.loads(args.config.read_text()))
            if args.confirm_disk != settings.disk:
                parser.error('--config requires --confirm-disk with the exact target disk path.')
            install(settings, state)
        else:
            interactive(state)
    except (wizard.Cancelled, EOFError):
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
