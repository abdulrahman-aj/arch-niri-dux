#!/usr/bin/env python3
"""Verify genuine memory resume on an already accepted, disposable VM disk."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from verify import VM, PASSWORD, installed_login


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='Completed make verify workspace')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not (directory / 'ACCEPTANCE-PASSED').is_file():
        parser.error('Run full make verify first; this requires its disposable VM and evidence.')
    inputs = json.loads((directory / 'inputs.json').read_text())
    iso = Path(inputs['iso'])
    with iso.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != inputs['iso_sha256']:
            parser.error('The ISO changed after acceptance; verify the new artifact first.')
    disk = directory / 'disk.qcow2'
    info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(disk)], text=True))
    if info['format'] != 'qcow2' or not any(snapshot['name'] == 'pristine' for snapshot in info.get('snapshots', [])):
        parser.error('The workspace must contain the accepted qcow2 with its pristine snapshot.')
    for name in ('HIBERNATE-RESUME-PASSED', 'HIBERNATE-FAILURE.txt'):
        (directory / name).unlink(missing_ok=True)
    shutil.copyfile(Path(__file__).with_name('verify-guest.py'), directory / 'verify-guest.py')
    vm = VM(directory, iso, disk)
    try:
        vm.start(cdrom=False)
        installed_login(vm)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('python /run/verify/verify-guest.py prepare-hibernate')
        expected = json.loads((directory / 'hibernate-expected.json').read_text())
        # The installation has no 9p share. Detach this test instrument before
        # sleep so a fresh QEMU server need not recover the old client's FIDs.
        vm.command('umount /run/verify')
        vm.send('systemctl hibernate\n')
        vm.wait_exit()
        vm.stop()
        vm.start(cdrom=False)
        vm.expect(r'(?i)(passphrase|password).*?:', timeout=180)
        vm.send(PASSWORD + '\n')
        vm.expect('DUX_HIBERNATION_RESUMED', timeout=180)
        output = vm.command('printf "\\nHIBERNATE_BOOT_ID:"; cat /proc/sys/kernel/random/boot_id; '
                            'printf "\\nHIBERNATE_MEMORY_TOKEN:"; cat /run/dux-hibernate-memory-marker; '
                            'test -e /run/dux-hibernate-resumed')
        boot_id = re.search(r'HIBERNATE_BOOT_ID:([a-f0-9-]+)', output)
        memory = re.search(r'HIBERNATE_MEMORY_TOKEN:([a-f0-9]+)', output)
        if not boot_id or not memory or boot_id.group(1) != expected['boot_id'] or memory.group(1) != expected['memory_token']:
            raise RuntimeError('Kernel boot ID or RAM-backed token changed; this was not a genuine memory resume.')
        (directory / 'hibernate-resume.json').write_text(json.dumps({
            'boot_id': boot_id.group(1), 'boot_id_unchanged': True,
            'ram_token_survived': True, 'hibernation_service_returned': True,
        }, indent=2) + '\n')
        (directory / 'HIBERNATE-RESUME-PASSED').write_text('Same kernel boot ID and RAM-backed state survived hibernation.\n')
        vm.screenshot('06-hibernate-resumed')
        vm.poweroff()
        print(f'Hibernation memory resume passed. Evidence: {directory}', flush=True)
        return 0
    except Exception as error:
        (directory / 'HIBERNATE-FAILURE.txt').write_text(str(error) + '\n')
        print(f'Hibernation verification failed: {error}\nEvidence: {directory}', file=sys.stderr)
        return 1
    finally:
        vm.stop()


if __name__ == '__main__':
    sys.exit(main())
