"""Shared offline installation journeys and package-input preflight."""

import json
import re
import shutil
import shlex
from tools.digest import digest
from tools.verification.payload import probe, mount_probes
from tools.workspace.state import ROOT


def run_install(vm, passed, evidence, directory, package_digest, serial_preboot=False, rehearsal=False):
    """Install the ISO offline and prepare the target. Reused by full and release runs."""
    print(f'VM evidence: {directory}', flush=True)
    vm.start()
    vm.expect(r'root@archiso[^\n]*#', timeout=180)
    # USB boot with enough RAM reproduces archiso's automatic-copy path.
    # The payload must stay on mounted media instead of disappearing.
    vm.command(
        'grep -qw copytoram=n /proc/cmdline; mountpoint -q /run/archiso/bootmnt; test ! -e /run/archiso/copytoram/airootfs.sfs'
    )
    mount_probes(vm)
    vm.command('fastfetch --logo none --structure OS:Kernel')
    vm.command('test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "')
    vm.command(f'test "$(sha256sum /run/archiso/bootmnt/packages.manifest.json | cut -d " " -f 1)" = {package_digest}')
    evidence.verify_installer(vm, ROOT / 'installer')
    installation_output = vm.command(
        'arch-niri-install --config /run/verify/install.json --confirm-disk /dev/vda', timeout=2400
    )
    elapsed = re.search(r'INSTALLATION COMPLETE[^\n]*took ([0-9]+) seconds', installation_output)
    if elapsed is None:
        raise RuntimeError('Installer did not report its elapsed completion time.')
    (directory / 'install-time-seconds').write_text(elapsed.group(1) + '\n')
    passed('Offline encrypted installation completed')
    vm.command(
        'cryptsetup open --key-file /run/verify/key /dev/vda2 dux-root; mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; mount /dev/vda1 /mnt/verify/boot'
    )
    if rehearsal:
        # Development package signers live only in the ISO's offline keyring.
        # Provision that same trust before boot, without trusting the HTTP server.
        keyring = '/usr/local/share/arch-niri-dux/install-keyring'
        target = '/mnt/verify/etc/pacman.d/gnupg'
        vm.command(
            f'gpg --homedir {keyring} --export | gpg --homedir {target} --batch --import; '
            f'gpg --homedir {keyring} --export-ownertrust | gpg --homedir {target} --import-ownertrust'
        )
    probe(vm, 'boot', 'prepare', '/mnt/verify', timeout=300)
    if serial_preboot:
        # The release check boots once, over serial, with no graphical unlock
        # to qualify. Enable the serial kernel console on the target now;
        # full acceptance does this after its first graphical boot instead.
        vm.command(
            'cd /run/verify && python3 -m tools.verification.probes.boot serial-console-enable /mnt/verify; '
            'arch-chroot /mnt/verify limine-mkinitcpio',
            timeout=300,
        )
    vm.command('umount -R /mnt/verify; cryptsetup close dux-root; sync')
    vm.stop()


def prepare_integration_inputs(directory):
    """Stage package build inputs for provenance checks. Shared by full and release runs."""
    source = directory / 'integration-input'
    source.mkdir()
    manifest = json.loads((ROOT / 'out/packages.manifest.json').read_text())['integration']
    (directory / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    from tools.packages import artifacts as release

    release.validate_manifest(ROOT, ROOT / 'out/integration')
    for filename, target, field in [
        ('integration.tar', 'integration.tar', 'source_sha256'),
        ('integration.PKGBUILD', 'PKGBUILD', 'recipe_sha256'),
    ]:
        original = ROOT / 'out/integration' / filename
        if digest(original) != manifest[field]:
            raise ValueError(f'Package build input changed: {filename}')
        shutil.copyfile(original, source / target)


def run_alongside_install(vm, passed, evidence, directory, device, os_name):
    vm.start()
    vm.expect(r'root@archiso[^\n]*#', timeout=180)
    mount_probes(vm)
    evidence.verify_installer(vm, ROOT / 'installer')
    probe(vm, 'alongside', 'plan', device, os_name)
    vm.command('chmod 600 /run/verify/install.json')
    parts = json.loads((directory / 'old-partitions.json').read_text())
    devices = ' '.join(shlex.quote(part['node']) for part in parts)
    vm.command(f'sha256sum {devices} > /run/verify/old-partitions.sha256', timeout=600)
    vm.command('efibootmgr -v > /run/verify/firmware-before.txt')
    vm.command(f'arch-niri-install --config /run/verify/install.json --confirm-disk {device}', timeout=2400)
    vm.command('sha256sum -c /run/verify/old-partitions.sha256', timeout=600)
    vm.command('efibootmgr -v > /run/verify/firmware-after.txt')
    passed('Every byte of every existing partition survives installation')
    plan = json.loads((directory / 'new-partitions.json').read_text())
    separator = 'p' if device[-1].isdigit() else ''
    esp, encrypted = [device + separator + str(part[0]) for part in plan]
    vm.command(
        f'cryptsetup open --key-file /run/verify/key {encrypted} dux-root; '
        'mkdir -p /mnt/verify; mount -o subvol=@ /dev/mapper/dux-root /mnt/verify; '
        f'mount {esp} /mnt/verify/boot'
    )
    vm.command(
        'cd /run/verify && python -m tools.verification.probes.boot prepare /mnt/verify; '
        'cd /run/verify && python -m tools.verification.probes.boot serial-console-enable /mnt/verify; '
        'arch-chroot /mnt/verify limine-mkinitcpio',
        timeout=300,
    )
    vm.command(
        'umount -R /mnt/verify; cryptsetup close dux-root; '
        'cp /run/archiso/bootmnt/repo/linux-[0-9]*.pkg.tar.zst /run/verify/; sync'
    )
