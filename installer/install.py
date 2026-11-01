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

SHARE = Path('/usr/local/share/arch-niri-dux')
REPO = Path('/run/archiso/bootmnt/repo')
TARGET = Path('/mnt/arch-niri-dux')
MAPPER = 'dux-root'
MIN_DISK_BYTES = 32 * 1024**3
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
    confirmed_device: tuple | None = field(default=None, init=False)

    def validate(self):
        if not re.fullmatch(r'/dev/(?:sd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+)', self.disk):
            raise ValueError('Select a whole physical disk, not a partition or loop device.')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', self.username) or self.username in {'root', 'nobody', 'alpm'}:
            raise ValueError('Use a non-system lowercase username (letters, digits, underscore, dash).')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', self.hostname):
            raise ValueError('Use a hostname of 1–63 lowercase letters, digits or dashes.')
        zone = (Path('/usr/share/zoneinfo') / self.timezone).resolve()
        if not zone.is_relative_to('/usr/share/zoneinfo') or not zone.is_file():
            raise ValueError('Unknown timezone.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.keymap):
            raise ValueError('Invalid console keymap name.')
        if self.keymap != 'us' and not any(Path('/usr/share/kbd/keymaps').rglob(self.keymap + '.map.gz')):
            raise ValueError('Unknown console keymap.')
        for name, secret in [('Disk passphrase', self.passphrase), ('Login password', self.password)]:
            if len(secret) < 8 or any(char in secret for char in '\n\r\0'):
                raise ValueError(f'{name} must have at least 8 characters and no line breaks.')


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


def prompt(label, default=''):
    answer = input(f'{label}' + (f' [{default}]' if default else '') + ': ').strip()
    return answer or default


def secret(label):
    first = getpass.getpass(label + ': ')
    if first != getpass.getpass('Repeat ' + label.lower() + ': '):
        raise ValueError('Passwords did not match.')
    return first


def wizard():
    print('\nArch + Niri + Dux — offline installation\n')
    print('This erases ONE selected disk. Secure Boot must be disabled.\n')
    disks = eligible_disks()
    if not disks:
        raise ValueError('No unused writable disk of at least 32 GiB found. TTY2 is available for recovery.')
    for disk in disks:
        print(f"  {disk['name']}  {int(disk['size']) / 1024**3:.1f} GiB  {(disk.get('model') or '').strip()}")
    settings = Settings(prompt('Target disk (full path)'), prompt('Username'),
                        prompt('Hostname', 'dux'), prompt('Timezone', 'Asia/Amman'),
                        prompt('Console keyboard layout', 'us'), 'validate-inputs', 'validate-inputs')
    settings.validate()
    settings.confirmed_device = require_disk(settings)
    run('loadkeys', settings.keymap)
    settings.passphrase = secret('Disk passphrase')
    settings.password = secret('Login password')
    settings.validate()
    if require_disk(settings) != settings.confirmed_device:
        raise ValueError('Disk identity changed while entering installation settings.')
    print(f'\nERASE ALL DATA on {settings.disk}; create a 4 GiB EFI partition and encrypted Btrfs.')
    if input(f'Type ERASE {settings.disk} to continue: ') != f'ERASE {settings.disk}':
        raise ValueError('Cancelled. No disk was changed.')
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
    verify_repository(REPO)
    for name in ('sgdisk', 'wipefs', 'cryptsetup', 'mkfs.btrfs', 'mkfs.fat', 'pacstrap', 'arch-chroot'):
        if not shutil.which(name):
            raise ValueError(f'Required installer tool missing: {name}')
    # Recheck immediately before the first destructive operation.
    if require_disk(settings) != original_device:
        raise ValueError('Disk identity changed; refusing to erase it.')

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
            for volume in SUBVOLUMES:
                run('btrfs', 'subvolume', 'create', str(TARGET / volume))
            run('umount', str(TARGET))
            mounted = False
            run('mount', '-o', 'subvol=@,compress=zstd:1,noatime', mapped, str(TARGET))
            mounted = True
            for volume, location in SUBVOLUMES.items():
                if location == '/':
                    continue
                mountpoint = TARGET / location.lstrip('/')
                mountpoint.mkdir(parents=True, exist_ok=True)
                run('mount', '-o', f'subvol={volume},compress=zstd:1,noatime', mapped, str(mountpoint))
            (TARGET / 'boot').mkdir(exist_ok=True)
            run('mount', '-o', 'umask=0077', esp, str(TARGET / 'boot'))
            # Install transaction hooks only after Snapper and boot are configured.
            base_packages = [package for package in packages if package != 'snap-pac']
            run('pacstrap', '-C', str(offline_conf), '-K', str(TARGET), *base_packages)
            fstab = run('genfstab', '-U', str(TARGET), capture=True)
            write('/etc/fstab', recovery_fstab(fstab))
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
                settings.username, settings.timezone, luks_uuid, filesystem_uuid)
            target_script.unlink()
            run('arch-chroot', str(TARGET), 'chpasswd',
                data=f'{settings.username}:{settings.password}\n')
            if 'snap-pac' in packages:
                run('pacstrap', '-C', str(offline_conf), '-G', '-M', str(TARGET), 'snap-pac')
            run('arch-chroot', str(TARGET), 'snapper', '--no-dbus', '-c', 'root',
                'create', '--cleanup-algorithm', 'number', '--description', 'Fresh installation')
            run('arch-chroot', str(TARGET), 'limine-snapper-sync')
            run('sync')
            print('\nINSTALLATION COMPLETE. Remove the USB and reboot.\n'
                  'Use the disk passphrase at boot, then your username and login password.\n'
                  'Snapshot recovery is available in the Limine boot menu.')
        finally:
            if mounted:
                subprocess.run(['umount', '--recursive', str(TARGET)], check=False)
            if opened:
                subprocess.run(['cryptsetup', 'close', MAPPER], check=False)


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
    try:
        if args.config:
            if args.config.stat().st_mode & 0o077:
                parser.error('The input JSON must be private (chmod 600).')
            settings = Settings(**json.loads(args.config.read_text()))
            if args.confirm_disk != settings.disk:
                parser.error('--config requires --confirm-disk with the exact target disk path.')
        else:
            settings = wizard()
        install(settings)
    except (ValueError, TypeError, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        print(f'\nInstallation stopped: {error}\nThe disk may be incomplete if installation had begun.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
