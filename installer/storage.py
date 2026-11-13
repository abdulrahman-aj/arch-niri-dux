"""Disk selection, GPT preservation and firmware registration for the installer."""

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import uuid
import zlib

GIB = 1024**3
MIN_DISK_BYTES = 32 * GIB
# A default install writes a 4 GiB EFI partition, keeps 24 GiB for the encrypted
# system plus 1 GiB slack, and sizes swap from RAM (RAM + 2 GiB when automatic).
# The disk must cover all three at once, so the true minimum grows with RAM.
ESP_GIB = 4
SYSTEM_RESERVE_GIB = 24
DISK_SLACK_GIB = 1
AUTO_SWAP_PAD_GIB = 2
ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
LUKS_TYPE = 'ca7d7ccb-63ed-4c53-861c-1742536059cc'


def run(*args, data=None, capture=False):
    # Secrets travel through stdin; never put them in argv or log them.
    result = subprocess.run(args, input=data, text=True, check=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def disk_inventory():
    return json.loads(
        run(
            'lsblk',
            '--json',
            '--bytes',
            '--paths',
            '--output',
            'NAME,TYPE,SIZE,MODEL,SERIAL,WWN,FSTYPE,RO,MOUNTPOINTS,TRAN,LABEL,PARTUUID',
            capture=True,
        )
    )['blockdevices']


def disk_identity(disk):
    identity = (disk.get('serial') or disk.get('wwn') or '').strip()
    label = f'{disk["name"]}  {int(disk["size"]) / GIB:.1f} GiB  {(disk.get("model") or "").strip()}'.rstrip()
    tran = (disk.get('tran') or '').strip()
    if tran:
        label += f'  [{tran}]'
    return f'{label}  ID: {identity}' if identity else label


def describe_partition(part):
    size = f'{int(part["size"]) / GIB:.1f} GiB'
    fstype = (part.get('fstype') or '').strip() or 'no filesystem detected'
    text = f'{part["name"]}  {size}  {fstype}'
    label = (part.get('label') or '').strip()
    if label:
        text += f'  "{label}"'
    return text


def entry_label(line):
    return line[9:].split('\t')[0].strip() or 'unnamed entry'


def boot_entry_nodes(disks):
    """Map partition nodes to the firmware boot entries that load from them.

    One efibootmgr pass covers every disk so the erase picker does not pay
    per-disk firmware round trips. A partition matches when its PARTUUID
    equals the GUID in a decoded EFI device path.
    """
    try:
        firmware = run('efibootmgr', '--verbose', capture=True)
    except (OSError, subprocess.CalledProcessError):
        return {}
    entries = firmware_entries(firmware)
    nodes = {}
    for disk in disks:
        for child in descendants(disk):
            uuid = (child.get('partuuid') or '').lower()
            if uuid and child['name'] != disk['name']:
                nodes[uuid] = child['name']
    if not nodes:
        return {}
    found = {}
    for number, line in entries.items():
        path = firmware_boot_path(number)
        if path is None:
            continue
        _part_number, guid, _loader = path
        node = nodes.get(guid.lower())
        if node is None:
            continue
        found.setdefault(node, []).append((number, entry_label(line)))
    return found


def describe_contents(disk, boots=None):
    """Partition and firmware lines for the erase picker and its summary."""
    if boots is None:
        boots = boot_entry_nodes([disk])
    parts = [child for child in descendants(disk) if child['name'] != disk['name']]
    lines = [describe_partition(part) for part in parts] or ['no partitions']
    for part in parts:
        for number, label in boots.get(part['name'], []):
            lines.append(f'{label} (Boot{number}) boots from this disk via {Path(part["name"]).name}')
    return '\n'.join(lines)


def describe_disk(disk, boots=None):
    return disk_identity(disk) + '\n' + describe_contents(disk, boots)


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


def alongside_recovery_text(settings, snapshot, plan):
    """Exact state and commands after an alongside firmware-registration refusal."""
    esp_number, root_number = plan[0][0], plan[1][0]
    esp_node = partition_name(settings.disk, esp_number)
    root_node = partition_name(settings.disk, root_number)
    return (
        f'The two new partitions already exist: {esp_node} and {root_node}.\n'
        'Register the finished system by hand:\n'
        f'  efibootmgr --create-only --disk {settings.disk} --part {esp_number}'
        " --label 'Arch Niri Dux' --loader '\\EFI\\limine\\limine_x64.efi'\n"
        f'  efibootmgr --bootorder <new-entry-number>,{snapshot["order"]}\n'
        'Remove the unfinished alongside partitions to undo or retry:\n'
        f'  sgdisk --delete={esp_number} {settings.disk}\n'
        f'  sgdisk --delete={root_number} {settings.disk}'
    )


def descendants(disk):
    yield disk
    for child in disk.get('children', []):
        yield from descendants(child)


def max_swap_gib(disk_gib):
    """Largest swap that still leaves the ESP, system reserve and slack on this disk."""
    return disk_gib - ESP_GIB - SYSTEM_RESERVE_GIB - DISK_SLACK_GIB


def min_disk_gib(swap_gib):
    """Smallest whole-GiB disk that fits the ESP, system reserve, slack and this swap.

    The disk-size floor stands on its own: even with swap disabled the installer
    still needs the EFI partition and room for the system.
    """
    return max(MIN_DISK_BYTES // GIB, ESP_GIB + SYSTEM_RESERVE_GIB + DISK_SLACK_GIB + swap_gib)


def disk_rejection(disk, swaps):
    if disk['type'] != 'disk':
        return 'not a whole disk'
    if disk.get('ro'):
        return 'read-only'
    if int(disk['size']) < MIN_DISK_BYTES:
        return f'smaller than {MIN_DISK_BYTES // GIB} GiB'
    for child in descendants(disk):
        if any(child.get('mountpoints') or []):
            return 'contains a mounted filesystem (possibly the live USB)'
        if child['name'] in swaps:
            return 'contains active swap'
        holders = Path('/sys/class/block') / Path(child['name']).name / 'holders'
        if holders.is_dir() and any(holders.iterdir()):
            return 'has an active device mapping'
    return None


def disk_rejections():
    """Every disk lsblk reports, paired with its rejection reason or None when usable."""
    swaps = {line.split()[0] for line in Path('/proc/swaps').read_text().splitlines()[1:]}
    return [(disk, disk_rejection(disk, swaps)) for disk in disk_inventory()]


def eligible_disks():
    return [disk for disk, reason in disk_rejections() if reason is None]


def describe_rejections(rejections):
    """One plain-words line per rejected disk, for errors that must name the cause."""
    return '\n'.join(f'{disk_identity(disk)}: {reason}' for disk, reason in rejections if reason is not None)


def require_disk(disk):
    rejections = disk_rejections()
    matches = [entry for entry, reason in rejections if entry['name'] == disk and reason is None]
    if len(matches) != 1:
        detail = describe_rejections(rejections)
        raise ValueError(
            'Target disk is unavailable, mounted, in use, read-only or too small.' + (f'\n{detail}' if detail else '')
        )
    device_stat = os.stat(disk)
    return (
        device_stat.st_rdev,
        device_stat.st_ino,
        matches[0]['size'],
        matches[0].get('serial'),
        matches[0].get('wwn'),
        str(Path('/sys/class/block', Path(disk).name).resolve()),
    )


def free_regions(table, minimum=MIN_DISK_BYTES):
    """Only return complete aligned extents; never infer space inside a partition."""
    if table.get('label') != 'gpt' or table.get('unit') != 'sectors':
        raise ValueError('Alongside requires an existing GPT partition table.')
    sector = table.get('sectorsize')
    if sector not in (512, 4096):
        raise ValueError('Unsupported logical sector size.')
    first, last = table['firstlba'], table['lastlba']
    alignment = 1024 * 1024 // sector
    cursor = first
    regions = []
    for part in sorted(table['partitions'], key=lambda part: part['start']):
        start, end = part['start'], part['start'] + part['size'] - 1
        if start < cursor or end > last or part['size'] <= 0:
            raise ValueError('Overlapping or invalid GPT partitions.')
        aligned = (cursor + alignment - 1) // alignment * alignment
        if (start - aligned) * sector >= minimum:
            regions.append([aligned, start - 1])
        cursor = end + 1
    aligned = (cursor + alignment - 1) // alignment * alignment
    if (last - aligned + 1) * sector >= minimum:
        regions.append([aligned, last])
    return regions


def alongside_refusal(table, regions, boots):
    """Name the first missing Alongside requirement in plain words, or None when all are met."""
    sector = table['sectorsize']
    largest = max(((last - first + 1) * sector for first, last in free_regions(table, minimum=1)), default=0)
    free = f'{largest / GIB:.0f} GiB free' if largest >= GIB else 'less than 1 GiB free'
    if not regions:
        return (
            f'This disk has {free}; Alongside needs at least {MIN_DISK_BYTES // GIB} GiB of unallocated space in one piece. '
            "Shrink a partition with the other OS's own tools first, use Erase disk, or pick another disk."
        )
    if len(table['partitions']) > 126:
        return (
            f'This disk has {free}, but its partition table is full ({len(table["partitions"])} of 128 entries used); '
            'Alongside needs two free entries. Delete an unused partition first, use Erase disk, or pick another disk.'
        )
    if not boots:
        return (
            f'This disk has {free} and room for partitions, but no boot partition with a firmware entry. '
            'Alongside needs the other OS to boot from this same disk; use Erase disk or pick another disk.'
        )
    return None


def firmware_entries(text):
    return {line[4:8].upper(): line for line in text.splitlines() if re.match(r'^Boot[0-9A-Fa-f]{4}[* ]', line)}


def decode_efi_boot_path(data):
    """Read the EFI_LOAD_OPTION device path, independent of efibootmgr formatting."""
    length = struct.unpack_from('<H', data, 4)[0]
    cursor = 6
    while data[cursor : cursor + 2] != b'\0\0':
        cursor += 2
        if cursor + 2 > len(data):
            raise ValueError('Invalid EFI boot description.')
    cursor += 2
    end = cursor + length
    if end > len(data):
        raise ValueError('Truncated EFI device path.')
    partition = None
    paths = []
    while cursor < end:
        kind, subtype, size = struct.unpack_from('<BBH', data, cursor)
        if size < 4 or cursor + size > end:
            raise ValueError('Invalid EFI device path node.')
        node = data[cursor : cursor + size]
        if (kind, subtype) == (4, 1):
            if partition is not None or size != 42 or node[40:42] != b'\2\2':
                raise ValueError('Unsupported EFI hard drive path.')
            partition = (struct.unpack_from('<I', node, 4)[0], str(uuid.UUID(bytes_le=node[24:40])))
        if (kind, subtype) == (4, 4):
            paths.append(node[4:].decode('utf-16-le').rstrip('\0'))
        cursor += size
    if partition is None or not paths:
        return None
    return (*partition, ''.join(paths))


def firmware_boot_path(number):
    variable = Path('/sys/firmware/efi/efivars') / f'Boot{number}-8be4df61-93ca-11d2-aa0d-00e098032b8c'
    try:
        return decode_efi_boot_path(variable.read_bytes()[4:])
    except (OSError, ValueError, struct.error, UnicodeError):
        return None


def verify_gpt(device):
    verification = run('sgdisk', '--verify', device, capture=True)
    if 'No problems found.' not in verification:
        raise ValueError('GPT verification failed; repair the disk separately.')


def read_gpt_metadata(device, sector):
    with open(device, 'rb', buffering=0) as disk:
        mbr = disk.read(512)
        if len(mbr) != 512:
            raise ValueError('Truncated GPT metadata.')
        if mbr[510:512] != b'\x55\xaa' or [mbr[450 + n * 16] for n in range(4)] != [0xEE, 0, 0, 0]:
            raise ValueError('Hybrid or unsupported protective MBR.')
        disk.seek(sector)
        header = disk.read(sector)
        if len(header) != sector:
            raise ValueError('Truncated GPT metadata.')
        # Restrict support to the standard GPT table size. Capture both copies,
        # including unused slots, so a new partition cannot escape revalidation.
        if header[:8] != b'EFI PART' or struct.unpack_from('<II', header, 80) != (128, 128):
            raise ValueError('Unsupported GPT entry array.')
        backup = struct.unpack_from('<Q', header, 32)[0]
        array = struct.unpack_from('<Q', header, 72)[0]
        disk.seek(array * sector)
        primary = disk.read(128 * 128)
        disk.seek(backup * sector)
        backup_header = disk.read(sector)
        if len(backup_header) != sector:
            raise ValueError('Truncated GPT metadata.')
        backup_array = struct.unpack_from('<Q', backup_header, 72)[0]
        disk.seek(backup_array * sector)
        secondary = disk.read(128 * 128)
    if len(primary) != 128 * 128 or len(secondary) != 128 * 128:
        raise ValueError('Truncated GPT partition array.')
    # sgdisk may recover a bad header in memory while printing its report.
    # Verify the bytes on disk too, without accepting an implicit repair.
    for copy, entries in ((header, primary), (backup_header, secondary)):
        if copy[:8] != b'EFI PART' or struct.unpack_from('<II', copy, 80) != (128, 128):
            raise ValueError('Unsupported GPT header.')
        size = struct.unpack_from('<I', copy, 12)[0]
        if not 92 <= size <= sector:
            raise ValueError('Invalid GPT header size.')
        checked = bytearray(copy[:size])
        struct.pack_into('<I', checked, 16, 0)
        if (
            zlib.crc32(checked) != struct.unpack_from('<I', copy, 16)[0]
            or zlib.crc32(entries) != struct.unpack_from('<I', copy, 88)[0]
        ):
            raise ValueError('GPT checksum mismatch; refusing an implicit repair.')
    if primary != secondary:
        raise ValueError('GPT partition arrays disagree.')
    return mbr, header, primary, backup_header, secondary


def firmware_order(text):
    order = re.search(r'^BootOrder: ([0-9A-Fa-f,]+)$', text, re.M)
    if not order:
        raise ValueError('Firmware BootOrder is unavailable.')
    return order[1].upper()


def alongside_snapshot(disk):
    require_disk(disk)
    # Capture stderr too: on a blank disk sfdisk's complaint would land on the wizard screen.
    listing = subprocess.run(['sfdisk', '--json', disk], capture_output=True, text=True, check=False)
    if listing.returncode != 0 and 'does not contain a recognized partition table' in listing.stderr:
        raise ValueError(
            'This disk has no partition table, so there is no other OS to keep; use Erase disk or pick another disk.'
        )
    if listing.returncode != 0:
        raise ValueError(f'Cannot read the partition table: {listing.stderr.strip()}')
    table = json.loads(listing.stdout)['partitiontable']
    regions = free_regions(table)
    verify_gpt(disk)
    mbr, header, primary, backup_header, secondary = read_gpt_metadata(disk, table['sectorsize'])
    firmware = run('efibootmgr', '--verbose', capture=True)
    entries = firmware_entries(firmware)
    boots = {}
    for number in entries:
        boot_path = firmware_boot_path(number)
        if boot_path is None:
            continue
        part_number, guid, path = boot_path
        part = next(
            (
                part
                for part in table['partitions']
                if part.get('uuid', '').lower() == guid.lower() and part.get('type', '').lower() == ESP_TYPE
            ),
            None,
        )
        if part is None or partition_name(disk, int(part_number)) != part['node']:
            continue
        path = path.replace('\\', '/')
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+\.efi', path, re.I) or '..' in path.split('/'):
            continue
        boots[number] = dict(partition=part['node'], guid=str(uuid.UUID(guid)), path=path)
    refusal = alongside_refusal(table, regions, boots)
    if refusal:
        raise ValueError(refusal)
    occupied = {
        index: primary[index * 128 : (index + 1) * 128].hex()
        for index in range(128)
        if primary[index * 128 : index * 128 + 16] != bytes(16)
    }
    return dict(
        table=table,
        regions=regions,
        boots=boots,
        entries=entries,
        order=firmware_order(firmware),
        metadata=hashlib.sha256(mbr + header + primary + backup_header + secondary).hexdigest(),
        bootstrap=hashlib.sha256(mbr[:446]).hexdigest(),
        occupied=occupied,
    )


def alongside_plan(settings, snapshot):
    if settings.free_region not in snapshot['regions'] or settings.existing_boot.upper() not in snapshot['boots']:
        raise ValueError('Selected free region or existing boot entry is no longer available.')
    table = snapshot['table']
    used = {int(part['node'].removeprefix(settings.disk).removeprefix('p')) for part in table['partitions']}
    slots = [number for number in range(1, 129) if number not in used][:2]
    first, last = settings.free_region
    esp_end = first + 4 * GIB // table['sectorsize'] - 1
    return [(slots[0], first, esp_end, 'ef00', 'DUX EFI'), (slots[1], esp_end + 1, last, '8309', 'DUX encrypted')]


def reject_unsupported_existing_storage(settings, snapshot):
    # A different boot chain can trigger TPM-bound recovery. Without recovery
    # credentials, refusing this layout is safer than stranding the old OS.
    disk = next(disk for disk in disk_inventory() if disk['name'] == settings.disk)
    if any((part.get('fstype') or '').lower() == 'bitlocker' for part in descendants(disk)):
        raise ValueError('BitLocker/device encryption is unsupported; decrypt Windows before alongside installation.')
    dynamic_types = {'5808c8aa-7e8f-42e0-85d2-e1e90434cfb3', 'af9b60a0-1431-4f62-bc68-3311714a69ad'}
    if any(part['type'].lower() in dynamic_types for part in snapshot['table']['partitions']):
        raise ValueError('Windows dynamic disks are unsupported.')


def validate_existing_loader(boot):
    # Never mount an old filesystem read-write, even during inspection.
    if run('blkid', '-s', 'TYPE', '-o', 'value', boot['partition'], capture=True) != 'vfat':
        raise ValueError('Existing boot entry must point to a FAT ESP.')
    with tempfile.TemporaryDirectory(prefix='dux-old-esp-', dir='/run') as directory:
        run('mount', '-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', boot['partition'], directory)
        try:
            loader = Path(directory) / boot['path'].lstrip('/')
            with loader.open('rb') as stream:
                header = stream.read(64)
                if len(header) != 64 or header[:2] != b'MZ':
                    raise ValueError('Existing loader is not a PE EFI application.')
                stream.seek(struct.unpack_from('<I', header, 60)[0])
                if stream.read(6) != b'PE\0\0\x64\x86':
                    raise ValueError('Existing loader must be an x86-64 EFI application.')
        finally:
            run('umount', directory)


def create_alongside_partitions(settings, snapshot, identity, on_start):
    plan = alongside_plan(settings, snapshot)
    # Last read before mutation, after payload and dependency preflight.
    if require_disk(settings.disk) != identity or alongside_snapshot(settings.disk) != snapshot:
        raise ValueError('Disk identity, GPT, free extents or firmware entries changed; no partitions created.')
    lines = []
    for number, first, last, kind, label in plan:
        partition_type = ESP_TYPE if kind == 'ef00' else LUKS_TYPE
        lines.append(
            f'{partition_name(settings.disk, number)} : start={first}, size={last - first + 1}, '
            f'type={partition_type}, name="{label}"'
        )
    on_start()
    # Appending with wiping disabled preserves Windows' protective-MBR disk
    # signature. sgdisk clears that field on block devices when adding entries.
    run(
        'sfdisk',
        '--append',
        '--quiet',
        '--lock=nonblock',
        '--wipe',
        'never',
        '--wipe-partitions',
        'never',
        settings.disk,
        data='\n'.join(lines) + '\n',
    )
    verify_gpt(settings.disk)
    mbr, _, primary, _, secondary = read_gpt_metadata(settings.disk, snapshot['table']['sectorsize'])
    if hashlib.sha256(mbr[:446]).hexdigest() != snapshot['bootstrap'] or any(
        primary[index * 128 : (index + 1) * 128].hex() != original
        or secondary[index * 128 : (index + 1) * 128].hex() != original
        for index, original in snapshot['occupied'].items()
    ):
        raise ValueError('Original GPT entries or MBR bootstrap changed; refusing to format.')
    current = json.loads(run('sfdisk', '--json', settings.disk, capture=True))['partitiontable']
    if (
        current['id'] != snapshot['table']['id']
        or len(current['partitions']) != len(snapshot['table']['partitions']) + 2
        or any(part not in current['partitions'] for part in snapshot['table']['partitions'])
    ):
        raise ValueError('Partition creation did not preserve the original GPT entries; refusing to format.')
    for number, first, last, kind, _label in plan:
        part = next((p for p in current['partitions'] if p['node'] == partition_name(settings.disk, number)), None)
        expected_type = ESP_TYPE if kind == 'ef00' else LUKS_TYPE
        if (
            part is None
            or part['start'] != first
            or part['size'] != last - first + 1
            or part['type'].lower() != expected_type
        ):
            raise ValueError('Created partition geometry/type differs from the plan; refusing to format.')
    return tuple(partition_name(settings.disk, part[0]) for part in plan)


def verify_partition_nodes(settings, plan, sector_size):
    for number, first, last, _kind, _label in plan:
        node = partition_name(settings.disk, number)
        sysfs = Path('/sys/class/block') / Path(node).name
        if (
            int((sysfs / 'start').read_text()) * 512 != first * sector_size
            or int((sysfs / 'size').read_text()) * 512 != (last - first + 1) * sector_size
        ):
            raise ValueError('Kernel partition geometry differs from the plan; refusing to format.')


def require_new_boot_entry(entries, number, esp_number, guid):
    if entries.get(number, '')[8:9] != '*':
        raise ValueError('New firmware boot entry is missing or inactive.')
    path = firmware_boot_path(number)
    if (
        path is None
        or path[0] != esp_number
        or path[1].lower() != guid
        or path[2].replace('\\', '/').lower() != '/efi/limine/limine_x64.efi'
    ):
        raise ValueError('New firmware boot entry has the wrong ESP identity or loader path.')


def finish_alongside_boot(settings, snapshot, esp_number, target):
    binary = target / 'boot/EFI/limine/limine_x64.efi'
    if not binary.is_file():
        raise ValueError('The new ESP is missing its Limine EFI binary; refusing firmware registration.')
    guid = str(
        uuid.UUID(
            run('blkid', '-s', 'PARTUUID', '-o', 'value', partition_name(settings.disk, esp_number), capture=True)
        )
    )
    before_text = run('efibootmgr', '--verbose', capture=True)
    before = firmware_entries(before_text)
    if before != snapshot['entries'] or firmware_order(before_text) != snapshot['order']:
        raise ValueError('Firmware entries changed during installation; refusing to change BootOrder.')
    boot = snapshot['boots'][settings.existing_boot.upper()]
    config = target / 'boot/limine.conf'
    text = config.read_text()
    # Resolve the old ESP by its original GUID, retaining adjacent GRUB/BCD files.
    # Keep the entry outside generated markers so package/snapshot hooks retain it.
    text += (
        f'\n/Existing OS (firmware {settings.existing_boot.upper()})\n'
        f'    protocol: efi\n    path: guid({boot["guid"]}):{boot["path"]}\n'
    )
    config.write_text(text)
    run(
        'efibootmgr',
        '--create-only',
        '--disk',
        settings.disk,
        '--part',
        str(esp_number),
        '--label',
        'Arch Niri Dux',
        '--loader',
        '\\EFI\\limine\\limine_x64.efi',
    )
    after = firmware_entries(run('efibootmgr', '--verbose', capture=True))
    added = after.keys() - before.keys()
    if len(added) != 1 or any(after.get(key) != value for key, value in before.items()):
        raise ValueError('Firmware did not preserve existing boot entries.')
    number = next(iter(added))
    require_new_boot_entry(after, number, esp_number, guid)
    expected_order = number + ',' + snapshot['order']
    run('efibootmgr', '--bootorder', expected_order)
    final = run('efibootmgr', '--verbose', capture=True)
    final_entries = firmware_entries(final)
    if firmware_order(final) != expected_order or any(final_entries.get(key) != value for key, value in before.items()):
        raise ValueError('Firmware failed to retain entries or make Limine the default.')
    require_new_boot_entry(final_entries, number, esp_number, guid)


def partition_name(disk, number):
    return disk + ('p' if disk[-1].isdigit() else '') + str(number)


def create_partitions(settings, snapshot, identity, on_start):
    """Create the selected layout, reporting the first write only after revalidation."""
    esp = partition_name(settings.disk, 1)
    encrypted = partition_name(settings.disk, 2)
    if snapshot:
        esp, encrypted = create_alongside_partitions(settings, snapshot, identity, on_start)
    else:
        on_start()
        run('wipefs', '--all', settings.disk)
        run(
            'sgdisk',
            '--zap-all',
            f'--new=1:0:+{ESP_GIB}G',
            '--typecode=1:ef00',
            '--change-name=1:EFI',
            '--new=2:0:0',
            '--typecode=2:8309',
            '--change-name=2:Linux encrypted',
            settings.disk,
        )
    # The table writer updates kernel partition nodes. Let filesystem probes finish
    # before inspecting them: forcing another full reread races those
    # probes and can fail EBUSY even though the new nodes are correct.
    run('udevadm', 'settle')
    if snapshot:
        verify_partition_nodes(settings, alongside_plan(settings, snapshot), snapshot['table']['sectorsize'])
    else:
        table = json.loads(run('sfdisk', '--json', settings.disk, capture=True))['partitiontable']
        plan = [
            (
                int(part['node'].removeprefix(settings.disk).removeprefix('p')),
                part['start'],
                part['start'] + part['size'] - 1,
                '',
                '',
            )
            for part in table['partitions']
        ]
        verify_partition_nodes(settings, plan, table['sectorsize'])
    return esp, encrypted
