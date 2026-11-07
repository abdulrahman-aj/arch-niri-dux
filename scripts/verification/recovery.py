"""Recovery acceptance behavior."""

from pathlib import Path
import re
import subprocess
from . import dispatch
from .guest import run, run_interactive
from .boot import storage
from . import integration
from .vm import installed_login


def boot_entry_path(text, snapshot=None):
    names = []
    for line in text.splitlines():
        entry = re.match(r'^\s*(/+)(?:\+)?(.+)$', line)
        if entry:
            depth = len(entry.group(1))
            names = names[: depth - 1] + [entry.group(2).strip()]
        if re.match(r'\s*(?:kernel_cmdline|cmdline):', line):
            expected = f'/@snapshots/{snapshot}/snapshot' if snapshot else None
            matches = expected in line if snapshot else re.search(r'rootflags=subvol=/?@(?:\s|$)', line)
            if matches:
                return '/'.join(name.replace('\\', '\\\\').replace('/', '\\/').replace('#', '\\#') for name in names)
    raise RuntimeError(f'No matching Limine boot entry for snapshot {snapshot}')


def select_boot_entry(path):
    config = Path('/boot/limine.conf')
    text = config.read_text()
    # Only global options precede the first menu header.
    lines = text.splitlines()
    first_entry = next(index for index, line in enumerate(lines) if line.lstrip().startswith('/'))
    global_options = [
        line
        for line in lines[:first_entry]
        if not re.match(r'\s*(default_entry|remember_last_entry|timeout|serial):', line)
    ]
    global_options += [f'default_entry: {path}', 'remember_last_entry: no', 'timeout: 1', 'serial: yes']
    config.write_text('\n'.join(global_options + lines[first_entry:]) + '\n')


def snapshots():
    from . import migrations

    run('systemctl', 'stop', 'snapper-cleanup.timer')
    home = Path('/home/tester/keep-this-file')
    home.write_text('home-must-survive-rollback\n')
    before = run('snapper', '-c', 'root', '--csvout', 'list', stdout=subprocess.PIPE).stdout
    stage = Path('/tmp/verify-package')
    (stage / 'etc').mkdir(parents=True, exist_ok=True)
    (stage / 'etc/dux-verification-version').write_text('known-good\n')
    (stage / '.PKGINFO').write_text(
        'pkgname = dux-verification\npkgver = 1-1\npkgdesc = Offline verification fixture\narch = any\nsize = 11\nlicense = MIT\n'
    )
    package = '/tmp/dux-verification-1-1-any.pkg.tar.gz'
    run('tar', '-C', str(stage), '-czf', package, '.PKGINFO', 'etc')
    run('pacman', '-U', '--noconfirm', package)
    after = run('snapper', '-c', 'root', '--csvout', 'list', stdout=subprocess.PIPE).stdout
    if len(after.splitlines()) <= len(before.splitlines()):
        raise RuntimeError('The package transaction did not create automatic snapshots.')
    migrations.prepare_snapshot()
    number = run(
        'snapper',
        '-c',
        'root',
        'create',
        '--print-number',
        '--cleanup-algorithm',
        'number',
        '--description',
        'VM known-good',
        stdout=subprocess.PIPE,
    ).stdout.strip()
    Path('/run/verify/recovery-snapshot').write_text(number + '\n')
    migrations.after_snapshot()
    integration.verify_upgrade()
    home.write_text('home-created-after-snapshot\n')
    Path('/etc/dux-verification-version').write_text('changed-after-snapshot\n')
    run('limine-snapper-sync')
    text = Path('/boot/limine.conf').read_text()
    Path('/run/verify/limine-before-recovery.conf').write_text(text)
    select_boot_entry(boot_entry_path(text, snapshot=number))
    # A controlled broken desktop on the normal root makes recovery observable.
    unit = Path('/etc/systemd/system/greetd.service')
    unit.unlink(missing_ok=True)
    unit.symlink_to('/dev/null')
    run('sync')


def recovery():
    number = Path('/run/verify/recovery-snapshot').read_text().strip()
    cmdline = Path('/proc/cmdline').read_text()
    assert f'/@snapshots/{number}/snapshot' in cmdline
    assert 'noresume' in cmdline.split(), 'Recovery could resume a stale normal-root memory image.'
    condition = subprocess.run(
        ['systemd-analyze', 'condition', 'ConditionKernelCommandLine=!noresume'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert condition.returncode == 1, condition.stdout
    assert Path('/etc/dux-verification-version').read_text().strip() == 'known-good'
    assert Path('/home/tester/keep-this-file').read_text().strip() == 'home-created-after-snapshot'
    assert not Path('/etc/systemd/system/greetd.service').is_symlink()
    # Restore through the same upstream terminal interface the laptop will use.
    run_interactive(
        ['limine-snapper-restore'],
        [
            (b'Choice [r/l/c]: ', b'r\n'),
            (b'Description for backup subvolume @: ', b'VM pre-restore backup\n'),
            (b'Restore complete. Reboot now? [Y/n]:', b'n\n'),
        ],
    )
    select_boot_entry(boot_entry_path(Path('/boot/limine.conf').read_text()))
    run('limine-snapper-info')
    run('sync')


def restored():
    from . import migrations

    migrations.restored()
    assert '/@snapshots/' not in Path('/proc/cmdline').read_text()
    assert Path('/etc/dux-verification-version').read_text().strip() == 'known-good'
    assert Path('/home/tester/keep-this-file').read_text().strip() == 'home-created-after-snapshot'
    integration.assert_restored()
    run('systemctl', 'is-active', 'greetd')
    storage()


def verify(vm, passed):
    vm.probe('recovery', 'snapshots', timeout=600)
    passed('Integration upgrade, baseline changes and personal preservation passed; recovery fixture prepared')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    vm.mount_probes()
    vm.probe('recovery', 'recovery', timeout=300)
    vm.screenshot('04-recovery')
    passed('Limine snapshot boot and restoration')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    vm.mount_probes()
    vm.probe('recovery', 'restored')
    vm.screenshot('05-restored')
    passed('Restored system rebooted normally and home was preserved')


if __name__ == '__main__':
    dispatch(
        'recovery',
        {
            'snapshots': snapshots,
            'recovery': recovery,
            'restored': restored,
        },
    )
