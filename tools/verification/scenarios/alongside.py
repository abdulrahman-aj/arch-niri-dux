"""Alongside verification lifecycle."""

import json
import shlex
import shutil
import subprocess
from tools.workspace.state import own
from tools.workspace.resources import disk_guard
from tools.digest import digest
from tools.verification.evidence import Evidence
from tools.vm.control import VM
from tools.verification.constants import PASSWORD
from tools.verification.login import installed_login
from tools.verification.payload import stage_probes, probe, mount_probes
from tools.fixtures.windows import WindowsVM, BootReports
from .install import run_alongside_install


def old_linux_login(vm):
    vm.expect(r'login:', timeout=240)
    vm.send('tester\n')
    vm.expect(r'Password:')
    vm.send(PASSWORD + '\n')
    vm.expect(r'\x1b\[\?2004h', timeout=60)
    vm.command('test -f /etc/debian_version; test "$(cat /var/lib/alongside-sentinel)" = preserve-existing-linux')
    vm.send('sudo -i\n')
    vm.expect(r'\x1b\[\?2004h', timeout=60)
    vm.command('test "$(id -u)" = 0')


def run_alongside(args, parser):
    disk_guard(args.directory)
    evidence = Evidence(args.directory)  # Never reuse evidence or fixture overlays.
    own(args.directory)
    directory = args.directory.resolve()
    disk = directory / 'disk.qcow2'
    subprocess.run(
        ['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(args.fixture.resolve()), str(disk)], check=True
    )
    shutil.copyfile(args.firmware, directory / 'OVMF_VARS.fd')
    inputs = {}
    for name, path in [('iso', args.iso), ('fixture', args.fixture), ('firmware', args.firmware)]:
        inputs[name] = dict(path=str(path.resolve()), sha256=digest(path))
    evidence.inputs(inputs)
    reports = None
    if args.os == 'windows':
        if args.windows_token is None:
            parser.error('Windows requires the provisioned fixture report-token file.')
        vm = WindowsVM(directory, args.iso.resolve(), disk, tpm_source=args.fixture.parent / 'tpm')
        reports = BootReports(directory, args.windows_token.read_text().strip())
    else:
        vm = VM(directory, args.iso.resolve(), disk)
    vm.min_free_bytes = 9 * 1024**3
    device = '/dev/sda' if reports else '/dev/vda'
    evidence.key(PASSWORD)
    stage_probes(directory)
    passed = evidence.passed
    try:
        vm.start(cdrom=False, network=bool(reports))
        if reports:
            reports.wait(vm, 'windows-before')
        else:
            old_linux_login(vm)
            vm.poweroff()
        passed(f'Real existing {args.os} boots before installation and sentinel is intact')
        run_alongside_install(vm, passed, evidence, directory, device, args.os)
        vm.poweroff()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('test -f /etc/arch-release; test "$(findmnt -no FSTYPE /)" = btrfs')
        passed('Default firmware path boots the new encrypted installation')
        # Reinstall the pinned kernel: exercise actual package hooks and
        # snapshot generation rather than only editing the menu in place.
        mount_probes(vm)
        probe(vm, 'boot', 'luks-pbkdf')
        passed('LUKS2 argon2id uses cryptsetup defaults, not the old 256 MiB pin')
        vm.command('pacman -U --noconfirm /run/verify/linux-[0-9]*.pkg.tar.zst', timeout=600)
        vm.command(
            'grep -A2 "^/Existing OS" /boot/limine.conf > /run/verify/chainload-after-update.txt; '
            'grep -q "path: guid(" /run/verify/chainload-after-update.txt; '
            'cp /boot/limine.conf /run/verify/limine-after-update.conf'
        )
        passed('Pinned kernel reinstall and snapshot hooks preserve the existing OS entry')
        vm.poweroff()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('test -f /etc/arch-release; test -e /dev/mapper/dux-root')
        passed('New encrypted installation boots after kernel package reinstall')
        # Explicit selection by entry path exercises the same chainloader as
        # manual menu selection without timing-dependent keyboard simulation.
        boot = json.loads((directory / 'install.json').read_text())['existing_boot'].upper()
        script = f'1i default_entry: Existing OS (firmware {boot})\\ntimeout: 1\\nremember_last_entry: no'
        vm.command(f'sed -i {shlex.quote(script)} /boot/limine.conf')
        vm.poweroff()
        vm.start(cdrom=False, network=bool(reports))
        if reports:
            reports.wait(vm, 'windows-after-update-chainload')
        else:
            old_linux_login(vm)
            vm.poweroff()
        passed(f'Existing {args.os} boots through Limine after package update and retains its sentinel')
        (directory / 'SUCCESS').write_text(f'{args.os} alongside acceptance passed\n')
    finally:
        vm.stop()
        if reports:
            reports.close()
