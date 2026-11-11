#!/usr/bin/env python3
"""Guided erase or existing-free-space installation. All package inputs come from the booted ISO."""

import argparse
from dataclasses import dataclass
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
    size = memory_gib() + 2 if swap_gib is None else swap_gib
    # Leave room for the ESP, LUKS metadata and a usable desktop installation.
    maximum = disk_bytes // GIB - 4 - 24 - 1
    if size and size > maximum:
        raise ValueError(
            f'{size} GiB swap will not fit while leaving 24 GiB for the system. '
            f'Choose at most {max(0, maximum)} GiB, no swap, or a larger disk.'
        )
    return size


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
    result = subprocess.run(
        [
            'fzf',
            '--layout=reverse',
            '--border',
            '--no-sort',
            '--no-multi',
            '--prompt',
            label + '> ',
            '--header',
            header + ('\n\n' if header else '') + 'Type to search · ↑/↓ to choose · Enter to select · Esc to cancel',
        ],
        input='\n'.join(choices) + '\n',
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
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


def disk_label(disk):
    identity = (disk.get('serial') or disk.get('wwn') or '').strip()
    label = f'{disk["name"]}  {int(disk["size"]) / GIB:.1f} GiB  {(disk.get("model") or "").strip()}'.rstrip()
    return f'{label}  ID: {identity}' if identity else label


def wizard(state):
    print('\nArch + Niri + Dux — offline installation\n')
    print('Choose erasure or existing unallocated space. Secure Boot must be disabled.\n')
    disks = storage.eligible_disks()
    if not disks:
        raise ValueError('No unused writable disk of at least 32 GiB found. TTY2 is available for recovery.')
    if not shutil.which('fzf'):
        raise ValueError('The live installer is missing fzf, required for its selection menus.')
    labels = {disk_label(disk): disk for disk in disks}
    disk = labels[choose('Installation disk', list(labels))]
    mode = choose('Storage mode', ['Erase disk', 'Alongside in existing free space'])
    snapshot = None
    region = None
    boot_number = None
    if mode.startswith('Alongside'):
        snapshot = storage.alongside_snapshot(disk['name'])
        regions = {
            f'Sectors {first}–{last} ({(last - first + 1) * snapshot["table"]["sectorsize"] / GIB:.2f} GiB)': [
                first,
                last,
            ]
            for first, last in snapshot['regions']
        }
        region = regions[choose('Unallocated region', list(regions))]
        boots = {snapshot['entries'][number]: number for number in snapshot['boots']}
        boot_number = boots[choose('Existing OS to keep in Limine', list(boots))]
        storage.validate_existing_loader(snapshot['boots'][boot_number])
    keymaps = run('localectl', 'list-keymaps', capture=True).splitlines()
    keymap = choose('Keyboard layout', keymaps, default='us')
    run('loadkeys', keymap)
    timezones = run('timedatectl', 'list-timezones', capture=True).splitlines()
    timezone = choose('Timezone', timezones, default='Asia/Amman')
    username = prompt('Username', validate=validate_username)
    hostname = prompt('Hostname', 'dux', validate=validate_hostname)
    state.confirmed_layout = snapshot
    available_bytes = (region[1] - region[0] + 1) * snapshot['table']['sectorsize'] if snapshot else int(disk['size'])
    swap_error = ''
    while True:
        automatic = f'Auto — {memory_gib() + 2} GiB encrypted swap and hibernation'
        choice = choose('Swap / hibernation', [automatic, 'Custom size', 'No swap / no hibernation'], header=swap_error)
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
            swap_error = str(error)
    state.confirmed_device = storage.require_disk(disk['name'])
    settings = Settings(
        disk['name'],
        username,
        hostname,
        timezone,
        keymap,
        secret('Disk passphrase'),
        secret('Login password'),
        swap_gib=swap_gib,
        mode='alongside' if snapshot else 'erase',
        free_region=region,
        existing_boot=boot_number,
    )
    settings.validate()
    if storage.require_disk(disk['name']) != state.confirmed_device:
        raise ValueError('Disk identity changed while entering installation settings.')
    summary = (
        f'ERASE ALL DATA on {disk_label(disk)}\n'
        f'User: {settings.username}    Host: {settings.hostname}\n'
        f'Timezone: {settings.timezone}    Keyboard: {settings.keymap}\n'
        'Storage: 4 GiB EFI + encrypted Btrfs, with separate /home\n'
        f'Swap: {str(size) + " GiB encrypted; hibernation enabled" if size else "disabled; hibernation disabled"}'
    )
    if snapshot:
        plan = storage.alongside_plan(settings, snapshot)
        summary = (
            f'ALONGSIDE on {disk_label(disk)}; preserve every existing partition.\n'
            + '\n'.join(
                f'Create {storage.partition_name(settings.disk, n)}: sectors {a}–{b}, '
                f'{(b - a + 1) * snapshot["table"]["sectorsize"]} bytes, {label} ({kind})'
                for n, a, b, kind, label in plan
            )
            + f'\nExisting OS: {snapshot["entries"][boot_number]}\n'
            + f'Swap: {size} GiB; Limine becomes firmware default.'
        )
    if size and size < memory_gib():
        summary += '\nWarning: swap is smaller than RAM; hibernation may fail under heavy memory use.'
    print('\n' + summary + '\n')
    action = 'Create partitions and install' if snapshot else 'Erase and install'
    if choose('Confirm installation', ['Cancel', action], header=summary) != action:
        raise Cancelled()
    settings.swap_gib = size
    return settings


def write(relative, contents, mode=0o644):
    path = TARGET / relative.lstrip('/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(mode)


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


def verify_payload_signatures(config, database, cache, names):
    """Verify every payload package against the install keyring before the disk is touched.

    pacman --print resolves names only, so without this the first pacstrap is
    the first real signature check, after partitioning. --downloadonly
    exercises the same SigLevel and keyring the pacstrap will use, including
    key expiry, which plain gpg verification would still accept. The cache is
    disposable: corrupt entries are deleted from it, never from the medium.
    """
    cache.mkdir(parents=True, exist_ok=True)
    try:
        run(
            'pacman',
            '--config',
            str(config),
            '--dbpath',
            str(database),
            '--cachedir',
            str(cache),
            '--sync',
            '--downloadonly',
            '--noconfirm',
            *names,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError(
            'Package signature preflight failed; the install keyring cannot verify the offline payload. '
            'A signing key may have expired or is missing; check the system clock and use a newer ISO.'
        ) from error
    finally:
        shutil.rmtree(cache, ignore_errors=True)


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


def install(settings, state=None):
    state = state if state is not None else InstallationState()
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
    print('\n[1/6] Checking the offline package payload…', flush=True)
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
            offline_conf, package_db, stage / 'payload-cache', [*packages, 'arch-niri-dux', 'arch-niri-dux-baseline']
        )
        if storage.require_disk(settings.disk) != original_device:
            raise ValueError('Disk identity changed during package preflight; refusing to erase it.')
        try:
            print('\n[2/6] Partitioning and encrypting the selected disk…', flush=True)
            esp, encrypted = storage.create_partitions(settings, snapshot, original_device, state.begin)
            run('mkfs.fat', '-F', '32', '-n', 'DUX_EFI', esp)
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
            print('\n[3/6] Installing packages from the USB…', flush=True)
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
            print('\n[4/6] Configuring the desktop, user and bootloader…', flush=True)
            write('/etc/hostname', settings.hostname + '\n')
            write(
                '/etc/hosts',
                f'127.0.0.1 localhost\n::1 localhost\n127.0.1.1 {settings.hostname}.localdomain {settings.hostname}\n',
            )
            write('/etc/locale.gen', 'en_US.UTF-8 UTF-8\n')
            write('/etc/locale.conf', 'LANG=en_US.UTF-8\n')
            write('/etc/vconsole.conf', 'KEYMAP=' + settings.keymap + '\n')
            # arch-chroot mounts a fresh /run, so stage outside that mountpoint.
            target_script = TARGET / 'root/configure-target.sh'
            shutil.copy2(Path(__file__).with_name('configure-target.sh'), target_script)
            run('arch-chroot', str(TARGET), 'bash', '/root/configure-target.sh', settings.username, settings.timezone)
            target_script.unlink()
            run('arch-chroot', str(TARGET), 'chpasswd', data=f'{settings.username}:{settings.password}\n')
            print('\n[5/6] Preparing automatic snapshots and recovery…', flush=True)
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
            print('\n[6/6] Saving changes and unmounting the installation…', flush=True)
            if snapshot:
                storage.finish_alongside_boot(settings, snapshot, plan[0][0], TARGET)
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
        else:
            settings = wizard(state)
        install(settings, state)
    except (Cancelled, EOFError):
        print('\nInstallation cancelled. No disk was changed.')
        return 130
    except (ValueError, TypeError, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        consequence = 'The selected disk may be incomplete.' if state.installation_started else 'No disk was changed.'
        print(f'\nInstallation stopped: {error}\n{consequence}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
