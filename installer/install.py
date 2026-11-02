#!/usr/bin/env python3
"""Guided whole-disk installation. All package inputs come from the booted ISO."""

import argparse
from dataclasses import dataclass, field
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

SHARE = Path('/usr/local/share/arch-niri-dux')
REPO = Path('/run/archiso/bootmnt/repo')
TARGET = Path('/mnt/arch-niri-dux')
MAPPER = 'dux-root'
MIN_DISK_BYTES = 32 * 1024**3
GIB = 1024**3
SUBVOLUMES = {'@': '/', '@home': '/home', '@log': '/var/log',
              '@cache': '/var/cache/pacman/pkg', '@snapshots': '/.snapshots'}


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
    confirmed_device: tuple | None = field(default=None, init=False)
    confirmed_at: float | None = field(default=None, init=False)
    installation_started: bool = field(default=False, init=False)

    def validate(self):
        if not re.fullmatch(r'/dev/(?:sd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+)', self.disk):
            raise ValueError('Select a whole physical disk, not a partition or loop device.')
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


def memory_gib():
    total = next(line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))
    return (int(total.split()[1]) * 1024 + GIB - 1) // GIB


def swap_size(settings, disk_bytes):
    size = memory_gib() + 2 if settings.swap_gib is None else settings.swap_gib
    # Leave room for the ESP, LUKS metadata and a usable desktop installation.
    maximum = disk_bytes // GIB - 4 - 24 - 1
    if size and size > maximum:
        raise ValueError(f'{size} GiB swap will not fit while leaving 24 GiB for the system. '
                         f'Choose at most {max(0, maximum)} GiB, no swap, or a larger disk.')
    return size


def run(*args, data=None, capture=False):
    # Secrets travel through stdin; never put them in argv or log them.
    result = subprocess.run(args, input=data, text=True, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def disk_inventory():
    return json.loads(run('lsblk', '--json', '--bytes', '--paths', '--output',
                          'NAME,TYPE,SIZE,MODEL,RO,MOUNTPOINTS', capture=True))['blockdevices']


def descendants(disk):
    yield disk
    for child in disk.get('children', []):
        yield from descendants(child)


def disk_rejection(disk, swaps):
    if disk['type'] != 'disk':
        return 'not a whole disk'
    if disk.get('ro'):
        return 'read-only'
    if int(disk['size']) < MIN_DISK_BYTES:
        return 'smaller than 32 GiB'
    for child in descendants(disk):
        if any(child.get('mountpoints') or []):
            return 'contains a mounted filesystem (possibly the live USB)'
        if child['name'] in swaps:
            return 'contains active swap'
        holders = Path('/sys/class/block') / Path(child['name']).name / 'holders'
        if holders.is_dir() and any(holders.iterdir()):
            return 'has an active device mapping'
    return None


def eligible_disks():
    swaps = {line.split()[0] for line in Path('/proc/swaps').read_text().splitlines()[1:]}
    return [disk for disk in disk_inventory() if disk_rejection(disk, swaps) is None]


def require_disk(settings):
    matches = [disk for disk in eligible_disks() if disk['name'] == settings.disk]
    if len(matches) != 1:
        raise ValueError('Target disk is unavailable, mounted, in use, read-only or too small.')
    device_stat = os.stat(settings.disk)
    return device_stat.st_rdev, device_stat.st_ino, matches[0]['size']


def prompt(label, default='', validate=None):
    while True:
        answer = input(f'{label}' + (f' [{default}]' if default else '') + ': ').strip() or default
        try:
            if validate:
                validate(answer)
            return answer
        except ValueError as error:
            print(error)


def choose(label, choices, default=None, header=''):
    if not choices:
        raise ValueError(f'No choices available for {label.lower()}.')
    if default in choices:
        choices = [default] + [choice for choice in choices if choice != default]
    result = subprocess.run(['fzf', '--layout=reverse', '--border', '--no-sort', '--no-multi',
                             '--prompt', label + '> ',
                             '--header', header + ('\n\n' if header else '') +
                             'Type to search · ↑/↓ to choose · Enter to select · Esc to cancel'],
                            input='\n'.join(choices) + '\n', text=True, stdout=subprocess.PIPE)
    if result.returncode in (1, 130):
        raise Cancelled()
    result.check_returncode()
    selected = result.stdout.strip()
    if selected not in choices:
        raise ValueError('The picker returned an invalid selection.')
    return selected


def secret(label):
    while True:
        first = getpass.getpass(label + ': ')
        try:
            validate_secret(first, label)
            if first != getpass.getpass('Repeat ' + label.lower() + ': '):
                raise ValueError('Passwords did not match. Try again.')
            return first
        except ValueError as error:
            print(error)


def wizard():
    print('\nArch + Niri + Dux — offline installation\n')
    print('This erases ONE selected disk. Secure Boot must be disabled.\n')
    disks = eligible_disks()
    if not disks:
        raise ValueError('No unused writable disk of at least 32 GiB found. TTY2 is available for recovery.')
    if not shutil.which('fzf'):
        raise ValueError('The live installer is missing fzf, required for its selection menus.')
    labels = {f"{disk['name']}  {int(disk['size']) / GIB:.1f} GiB  {(disk.get('model') or '').strip()}".rstrip(): disk
              for disk in disks}
    disk = labels[choose('Disk to erase', list(labels))]
    keymaps = run('localectl', 'list-keymaps', capture=True).splitlines()
    keymap = choose('Keyboard layout', keymaps, default='us')
    run('loadkeys', keymap)
    timezones = run('timedatectl', 'list-timezones', capture=True).splitlines()
    timezone = choose('Timezone', timezones, default='Asia/Amman')
    settings = Settings(disk['name'], prompt('Username', validate=validate_username),
                        prompt('Hostname', 'dux', validate=validate_hostname), timezone,
                        keymap, 'validate-inputs', 'validate-inputs')
    swap_error = ''
    while True:
        automatic = f'Auto — {memory_gib() + 2} GiB encrypted swap and hibernation'
        choice = choose('Swap / hibernation', [automatic, 'Custom size', 'No swap / no hibernation'],
                        header=swap_error)
        if choice == automatic:
            settings.swap_gib = None
        elif choice == 'Custom size':
            def validate_size(value):
                if not value.isdecimal() or int(value) < 1:
                    raise ValueError('Enter a whole number of GiB greater than zero.')
            settings.swap_gib = int(prompt('Swap size in GiB', str(memory_gib() + 2), validate_size))
        else:
            settings.swap_gib = 0
        try:
            size = swap_size(settings, int(disk['size']))
            break
        except ValueError as error:
            swap_error = str(error)
    settings.confirmed_device = require_disk(settings)
    settings.passphrase = secret('Disk passphrase')
    settings.password = secret('Login password')
    settings.validate()
    if require_disk(settings) != settings.confirmed_device:
        raise ValueError('Disk identity changed while entering installation settings.')
    summary = (f'ERASE ALL DATA on {settings.disk} ({int(disk["size"]) / GIB:.1f} GiB, '
               f'{(disk.get("model") or "unknown model").strip()})\n'
               f'User: {settings.username}    Host: {settings.hostname}\n'
               f'Timezone: {settings.timezone}    Keyboard: {settings.keymap}\n'
               'Storage: 4 GiB EFI + encrypted Btrfs, with separate /home\n'
               f'Swap: {str(size) + " GiB encrypted; hibernation enabled" if size else "disabled; hibernation disabled"}')
    if size and size < memory_gib():
        summary += '\nWarning: swap is smaller than RAM; hibernation may fail under heavy memory use.'
    print('\n' + summary + '\n')
    if choose('Confirm installation', ['Cancel', 'Erase and install'], header=summary) != 'Erase and install':
        raise Cancelled()
    settings.swap_gib = size
    settings.confirmed_at = time.monotonic()
    return settings


def write(relative, contents, mode=0o644):
    path = TARGET / relative.lstrip('/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(mode)


def partition_name(disk, number):
    return disk + ('p' if disk[-1].isdigit() else '') + str(number)


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


def install(settings):
    started_at = settings.confirmed_at if settings.confirmed_at is not None else time.monotonic()
    settings.validate()
    original_device = require_disk(settings)
    if settings.confirmed_device is not None and original_device != settings.confirmed_device:
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
    if not list(REPO.glob('*.db')):
        raise ValueError('The ISO offline package repository is missing.')
    print('\n[1/6] Checking the offline package payload…', flush=True)
    verify_repository(REPO)
    for name in ('sgdisk', 'wipefs', 'cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'pacstrap', 'arch-chroot'):
        if not shutil.which(name):
            raise ValueError(f'Required installer tool missing: {name}')
    # Recheck immediately before the first destructive operation.
    if require_disk(settings) != original_device:
        raise ValueError('Disk identity changed; refusing to erase it.')
    size = swap_size(settings, int(original_device[2]))
    subvolumes = dict(SUBVOLUMES)
    if size:
        subvolumes['@swap'] = '/swap'

    esp = partition_name(settings.disk, 1)
    encrypted = partition_name(settings.disk, 2)
    mapped = '/dev/mapper/' + MAPPER
    mounted = False
    opened = False
    TARGET.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dux-install-', dir='/run') as staging:
        stage = Path(staging)
        offline_conf = stage / 'pacman.conf'
        # The build verifies upstream signatures and pins hashes of the ISO payload.
        # No remote repositories are enabled during installation.
        database = next(REPO.glob('*.db')).stem
        offline_conf.write_text(f'[options]\nArchitecture = auto\nSigLevel = Never\n'
                                f'LocalFileSigLevel = Never\n[{database}]\nServer = file://{REPO}\n')
        package_db = stage / 'db'
        package_db.mkdir()
        resolved = run('pacman', '--config', str(offline_conf), '--dbpath', str(package_db),
                       '--sync', '--refresh', '--print', '--noconfirm', '--print-format', '%f', *packages, capture=True)
        for filename in resolved.splitlines():
            if '.pkg.tar.' in filename and not (REPO / filename).is_file():
                raise ValueError(f'Offline dependency is missing: {filename}')
        if require_disk(settings) != original_device:
            raise ValueError('Disk identity changed during package preflight; refusing to erase it.')
        try:
            print('\n[2/6] Partitioning and encrypting the selected disk…', flush=True)
            settings.installation_started = True
            run('wipefs', '--all', settings.disk)
            run('sgdisk', '--zap-all', '--new=1:0:+4G', '--typecode=1:ef00',
                '--change-name=1:EFI', '--new=2:0:0', '--typecode=2:8309',
                '--change-name=2:Linux encrypted', settings.disk)
            run('udevadm', 'settle')
            run('mkfs.fat', '-F', '32', '-n', 'DUX_EFI', esp)
            run('cryptsetup', 'luksFormat', '--batch-mode', '--type', 'luks2',
                '--pbkdf-memory', '262144', '--key-file', '-', encrypted, data=settings.passphrase)
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
                resume_offset = run('btrfs', 'inspect-internal', 'map-swapfile', '-r',
                                    str(TARGET / 'swap/swapfile'), capture=True)
                if not re.fullmatch(r'[0-9]+', resume_offset):
                    raise ValueError('Btrfs returned an invalid swap resume offset.')
            print('\n[3/6] Installing packages from the USB…', flush=True)
            # Install transaction hooks only after Snapper and boot are configured.
            base_packages = [package for package in packages if package != 'snap-pac']
            run('pacstrap', '-C', str(offline_conf), '-K', str(TARGET), *base_packages)
            fstab = run('genfstab', '-U', str(TARGET), capture=True)
            if size:
                fstab += '\n/swap/swapfile none swap defaults 0 0\n'
            write('/etc/fstab', recovery_fstab(fstab))
            print('\n[4/6] Configuring the desktop, user and bootloader…', flush=True)
            write('/etc/hostname', settings.hostname + '\n')
            write('/etc/hosts', '127.0.0.1 localhost\n::1 localhost\n'
                  f'127.0.1.1 {settings.hostname}.localdomain {settings.hostname}\n')
            write('/etc/locale.gen', 'en_US.UTF-8 UTF-8\n')
            write('/etc/locale.conf', 'LANG=en_US.UTF-8\n')
            write('/etc/vconsole.conf', 'KEYMAP=' + settings.keymap + '\n')
            write('/etc/profile.d/editor.sh', 'export EDITOR=nvim\nexport VISUAL=nvim\n')
            write('/etc/xdg/mimeapps.list', '[Default Applications]\ntext/html=chromium.desktop\n'
                  'application/xhtml+xml=chromium.desktop\nx-scheme-handler/http=chromium.desktop\n'
                  'x-scheme-handler/https=chromium.desktop\n')
            shutil.copytree(SHARE / 'desktop', TARGET, dirs_exist_ok=True)
            # arch-chroot mounts a fresh /run, so stage outside that mountpoint.
            target_script = TARGET / 'root/configure-target.sh'
            shutil.copy2(Path(__file__).with_name('configure-target.sh'), target_script)
            luks_uuid = run('blkid', '-s', 'UUID', '-o', 'value', encrypted, capture=True)
            filesystem_uuid = run('blkid', '-s', 'UUID', '-o', 'value', mapped, capture=True)
            run('arch-chroot', str(TARGET), 'bash', '/root/configure-target.sh',
                settings.username, settings.timezone, luks_uuid, filesystem_uuid, resume_offset)
            target_script.unlink()
            run('arch-chroot', str(TARGET), 'chpasswd',
                data=f'{settings.username}:{settings.password}\n')
            print('\n[5/6] Preparing automatic snapshots and recovery…', flush=True)
            if 'snap-pac' in packages:
                run('pacstrap', '-C', str(offline_conf), '-G', '-M', str(TARGET), 'snap-pac')
            run('arch-chroot', str(TARGET), 'snapper', '--no-dbus', '-c', 'root',
                'create', '--cleanup-algorithm', 'number', '--description', 'Fresh installation')
            run('arch-chroot', str(TARGET), 'limine-snapper-sync')
            print('\n[6/6] Saving changes and unmounting the installation…', flush=True)
            run('sync')
        finally:
            if mounted:
                subprocess.run(['umount', '--recursive', str(TARGET)], check=False)
            if opened:
                subprocess.run(['cryptsetup', 'close', MAPPER], check=False)
    elapsed = round(time.monotonic() - started_at)
    print(f'\nINSTALLATION COMPLETE — took {elapsed} seconds. Remove the USB and reboot.\n'
          'Use the disk passphrase at boot, then your username and login password.\n'
          'Snapshot recovery is available in the Limine boot menu.')


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
    settings = None
    try:
        if args.config:
            if args.config.stat().st_mode & 0o077:
                parser.error('The input JSON must be private (chmod 600).')
            settings = Settings(**json.loads(args.config.read_text()))
            if args.confirm_disk != settings.disk:
                parser.error('--config requires --confirm-disk with the exact target disk path.')
            settings.confirmed_at = time.monotonic()
        else:
            settings = wizard()
        install(settings)
    except (Cancelled, EOFError):
        print('\nInstallation cancelled. No disk was changed.')
        return 130
    except (ValueError, TypeError, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        consequence = ('The selected disk may be incomplete.' if settings and settings.installation_started
                       else 'No disk was changed.')
        print(f'\nInstallation stopped: {error}\n{consequence}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
