#!/usr/bin/env python3
"""Focused release-button checks on a disposable overlay of an accepted install.

Builds the current integration packages (reflecting uncommitted edits) from
the checkout, installs them once into a pristine export of a completed full
acceptance fixture, then runs each iteration on a fresh overlay: the packaged
checker hides the button with nothing newer, a newer fixture release shows it
after a supported plugin rescan, a supported manual update check re-primes
the pending list, and a physical click opens Dux's updater panel. No ISO
rebuild, no fresh installation and no scheduler wait per iteration.

This is a focused debug tier. It never writes acceptance markers; scheduled
checks, authentication, upgrades, migrations and fresh installs stay with
`make verify`.
"""

import argparse
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

from workspace import disk_guard, own

from verification.digest import digest
from verification.evidence import Evidence
from verification.dux_update import BAR_MIDDLE, added_pill, bar_pills, screen_shows
from verification.guest import png_dimensions
from verification.release_focus import CANDIDATE
from verification.vm import VM, graphical_login, installed_login, stage_probes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SNAPSHOT = 'pristine'
BASE = 'base.qcow2'
DISK = 'disk.qcow2'
HIDDEN = 'release-hidden.png'
AVAILABLE = 'release-available.png'
PANEL = 'release-panel.png'
EVIDENCE_FILES = (
    HIDDEN,
    AVAILABLE,
    PANEL,
    'release-focus-plugin-status.txt',
    'release-focus-plugin-list.txt',
    'release-focus-dms-journal.txt',
    'release-focus-state.json',
    'focus-results.json',
    'FOCUS-PASSED',
    'FAILURE.txt',
)


def pristine_present(snapshot_list):
    """Whether `qemu-img snapshot -l` output has the post-install snapshot."""
    for line in snapshot_list.splitlines()[2:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == FIXTURE_SNAPSHOT:
            return True
    return False


def validate_fixture(fixture):
    accepted = fixture.parent
    if not fixture.is_file() or not (accepted / 'ACCEPTANCE-PASSED').is_file():
        raise SystemExit('Fixture must be a disk from a completed full acceptance run.')
    listing = subprocess.check_output(['qemu-img', 'snapshot', '-l', str(fixture)], text=True)
    if not pristine_present(listing):
        raise SystemExit(f'Fixture disk has no {FIXTURE_SNAPSHOT} snapshot to export.')


def write_fixture_db(path, version):
    """A minimal repository database offering our packages at `version`.

    Both package names match the full-acceptance fixture so the panel reads
    the same "N updates" heading its checks OCR.
    """
    with tarfile.open(path, 'w:gz') as archive:
        for name in ('arch-niri-dux', 'arch-niri-dux-baseline'):
            desc = f'%NAME%\n{name}\n\n%VERSION%\n{version}\n'.encode()
            member = tarfile.TarInfo(f'{name}-{version}/desc')
            member.size = len(desc)
            archive.addfile(member, io.BytesIO(desc))


def prepare_needed(prepared, source_sha256, recipe_sha256, epoch):
    """Invalidate installed packages when any build input changes."""
    expected = dict(source_sha256=source_sha256, recipe_sha256=recipe_sha256, epoch=epoch)
    return prepared is None or any(prepared.get(key) != value for key, value in expected.items())


def validate_base(base, expected_sha256):
    """Refuse a modified prepared image before starting a disposable overlay."""
    if digest(base) != expected_sha256:
        raise RuntimeError('The prepared base changed; use a new --directory.')


def clean(directory, checkout=ROOT):
    """Remove a focused workspace this checkout owns."""
    marker = directory / '.arch-niri-dux-owner'
    if not marker.is_file() or marker.read_text().strip() != str(checkout.resolve()):
        raise SystemExit(f'Refusing to clean a directory this checkout does not own: {directory}')
    shutil.rmtree(directory)
    print(f'Removed focused workspace: {directory}')


def next_run(directory):
    """The next free iteration evidence directory."""
    index = 1 + max(
        (int(path.name.split('-')[1]) for path in directory.glob('run-[0-9][0-9]') if path.is_dir()),
        default=0,
    )
    run = directory / f'run-{index:02d}'
    run.mkdir()
    return run


def finish_run(directory, run):
    """Move iteration evidence into its run directory; keep the workspace."""
    for name in EVIDENCE_FILES:
        source = directory / name
        if source.exists():
            source.replace(run / name)


def prepare(directory, fixture, source, epoch):
    """Export the pristine fixture and install the current packages once."""
    started = time.monotonic()
    base = directory / BASE
    base.unlink(missing_ok=True)
    subprocess.run(['qemu-img', 'convert', '-l', FIXTURE_SNAPSHOT, '-O', 'qcow2', str(fixture), str(base)], check=True)
    for name in ('OVMF_VARS.fd', 'install.json'):
        shutil.copyfile(fixture.parent / name, directory / name)
    vm = VM(directory, None, base)
    try:
        # The pristine snapshot has never been booted: its kernel cmdline has
        # no serial console, so unlock it graphically and enable the console
        # the login steps need, exactly as the first acceptance boot does.
        vm.start(cdrom=False, software_display=True)
        installed_login(vm, keyboard_unlock=True, screenshot_tag='focus-prepare')
        vm.mount_probes()
        vm.probe('boot', 'serial-console-enable', timeout=300)
        vm.poweroff()
        vm.stop()
        vm.start(cdrom=False)
        installed_login(vm)
        vm.mount_probes()
        vm.command(
            'mkdir -p /tmp/focus/source/packages/integration /tmp/focus/build /tmp/focus/packages; '
            'tar -xf /run/verify/integration-input/integration.tar -C /tmp/focus/source; '
            'cp /run/verify/integration-input/PKGBUILD /tmp/focus/source/packages/integration/; '
            'chown -R tester:tester /tmp/focus'
        )
        vm.command(
            f'runuser -u tester -- python /run/verify/integration_build.py '
            f'/tmp/focus/source /tmp/focus/build /tmp/focus/packages --epoch {epoch}',
            timeout=180,
        )
        vm.command(
            'pacman -U --noconfirm /tmp/focus/packages/*.pkg.tar.zst '
            '>/run/verify/prepare-transaction.log 2>&1; '
            'cp /tmp/focus/packages/integration.manifest.json /run/verify/',
            timeout=300,
        )
        # The installed widget and checker must be this checkout's, not the
        # fixture ISO's; a hash mismatch would test the wrong code.
        for guest_path, source_path in [
            (
                '/etc/xdg/quickshell/dms-plugins/archNiriDuxRelease/ReleaseWidget.qml',
                'desktop/etc/xdg/quickshell/dms-plugins/archNiriDuxRelease/ReleaseWidget.qml',
            ),
            ('/usr/lib/arch-niri-dux/release-update-check', 'desktop/usr/lib/arch-niri-dux/release-update-check'),
        ]:
            vm.command(f'test "$(sha256sum {guest_path} | cut -d " " -f 1)" = {digest(ROOT / source_path)}')
        vm.poweroff()
    finally:
        vm.stop()
    prepared = dict(
        epoch=epoch,
        source_sha256=digest(source / 'integration.tar'),
        recipe_sha256=digest(source / 'PKGBUILD'),
        packages=json.loads((directory / 'integration.manifest.json').read_text())['packages'],
        fixture=str(fixture),
        fixture_sha256=digest(fixture),
        base_sha256=digest(base),
        preparation_seconds=round(time.monotonic() - started),
    )
    (directory / 'prepared.json').write_text(json.dumps(prepared, indent=2) + '\n')
    return prepared


def iterate(directory, run, prepared):
    """One bounded VM session of focused interaction checks on a fresh overlay."""
    started = time.monotonic()
    marker = started
    phases = {}

    def phase(name):
        nonlocal marker
        now = time.monotonic()
        phases[name] = round(now - marker)
        marker = now

    validate_base(directory / BASE, prepared['base_sha256'])
    disk = directory / DISK
    # A leftover overlay can only be a crashed iteration's; this workspace
    # owns it, so start from a fresh owned state instead of reusing it.
    disk.unlink(missing_ok=True)
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', BASE, str(disk)], check=True)
    vm = VM(directory, None, disk)
    result = {'candidate': CANDIDATE, 'base_sha256': prepared['base_sha256']}
    try:
        vm.start(cdrom=False)
        installed_login(vm)
        vm.mount_probes()
        graphical_login(vm)
        phase('boot_and_login')
        vm.probe('release_focus', 'hidden', timeout=300)
        phase('hidden')
        vm.probe('release_focus', 'offer', timeout=420)
        phase('offer')
        start, end = added_pill(bar_pills(directory / HIDDEN), bar_pills(directory / AVAILABLE))
        width, height = png_dimensions(directory / AVAILABLE)
        coordinates = ((start + end) / 2 / width, BAR_MIDDLE / height)
        result['pill_span'] = [start, end]
        result['click'] = [round(coordinates[0], 4), round(coordinates[1], 4)]
        (directory / 'focus-results.json').write_text(json.dumps(result, indent=2) + '\n')
        vm.click(*coordinates)
        deadline = time.monotonic() + 30
        while True:
            vm.probe('release_focus', 'panel-screenshot')
            if screen_shows(directory / PANEL, 'updates', 'arch-niri-dux'):
                break
            if time.monotonic() > deadline:
                vm.probe('release_focus', 'debug', timeout=60)
                raise TimeoutError(f"Dux's updater panel did not list our package; see {PANEL}.")
            time.sleep(1)
        result['panel_opened'] = True
        phase('click_to_panel')
        vm.probe('release_focus', 'debug', timeout=60)
        vm.key('esc')
        vm.probe('release_focus', 'restore', timeout=60)
        vm.poweroff()
        validate_base(directory / BASE, prepared['base_sha256'])
        result['phases'] = phases
        result['iteration_seconds'] = round(time.monotonic() - started)
        result['preparation_seconds'] = prepared['preparation_seconds']
        result['scope'] = {
            'checks': [
                'packaged checker hides with nothing newer',
                'supported plugin rescan shows the pill for a newer fixture release',
                'supported manual update check re-primes the pending list',
                'physical click on the pill opens the updater panel',
            ],
            'not_qualified': [
                'scheduled update checks and intervals',
                'authentication, upgrade transaction and migrations',
                'fresh installation and published delivery',
                'full acceptance (see make verify)',
            ],
        }
        (directory / 'focus-results.json').write_text(json.dumps(result, indent=2) + '\n')
        (directory / 'FOCUS-PASSED').write_text('Focused interaction checks passed. This is not acceptance.\n')
    except BaseException as error:
        result['error'] = str(error)
        result['phases'] = phases
        result['iteration_seconds'] = round(time.monotonic() - started)
        (directory / 'focus-results.json').write_text(json.dumps(result, indent=2) + '\n')
        try:
            if vm.process and vm.process.poll() is None:
                vm.probe('release_focus', 'debug', timeout=60)
        except BaseException:
            pass
        (run / 'FAILURE.txt').write_text(str(error) + '\n')
        raise
    finally:
        try:
            if vm.process and vm.process.poll() is None:
                vm.probe('release_focus', 'restore', timeout=60)
        except BaseException:
            pass
        vm.stop()
        # The overlay is disposable; the run directory keeps the evidence.
        disk.unlink(missing_ok=True)
        finish_run(directory, run)
    print(f'Focused checks passed. Evidence: {run}', flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, help='disk.qcow2 from a completed full acceptance run')
    parser.add_argument('--directory', required=True, type=Path, help='Focused workspace directory')
    parser.add_argument('--clean', action='store_true', help='Remove the focused workspace directory')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.clean:
        clean(directory)
        return 0
    if args.fixture is None:
        parser.error('--fixture is required unless --clean is given.')
    fixture = args.fixture.resolve()
    validate_fixture(fixture)
    disk_guard(directory)
    evidence = Evidence(directory, parents=True, exist_ok=True)
    own(directory)
    fixture_sha256 = digest(fixture)
    probe_hashes = stage_probes(directory)
    probe_hashes['verify_focus.py'] = digest(Path(__file__))

    import integration_build as builder

    source = directory / 'integration-input'
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir()
    epoch = int(subprocess.check_output(['git', 'log', '-1', '--format=%ct'], cwd=ROOT, text=True))
    builder.prepare_source(ROOT, source, epoch)
    source_sha256 = digest(source / 'integration.tar')
    recipe_sha256 = digest(source / 'PKGBUILD')

    try:
        prepared_path = directory / 'prepared.json'
        prepared = json.loads(prepared_path.read_text()) if prepared_path.is_file() else None
        if prepare_needed(prepared, source_sha256, recipe_sha256, epoch):
            print('Preparing the installed base from the pristine fixture snapshot.', flush=True)
            prepared = prepare(directory, fixture, source, epoch)
            print(f'Preparation took {prepared["preparation_seconds"]} seconds.', flush=True)
        elif prepared['fixture_sha256'] != fixture_sha256:
            raise SystemExit('The fixture changed under the prepared base; use a new --directory.')
        write_fixture_db(directory / 'release-focus.db', CANDIDATE)
        evidence.inputs(
            {
                'fixture': str(fixture),
                'fixture_sha256': fixture_sha256,
                'pristine_snapshot': FIXTURE_SNAPSHOT,
                'base_sha256': prepared['base_sha256'],
                'source_sha256': source_sha256,
                'recipe_sha256': recipe_sha256,
                'epoch': epoch,
                'prepared_packages': prepared['packages'],
                'verification_scripts': probe_hashes,
            }
        )
        run = next_run(directory)
        # Each run retains its inputs even when a later iteration changes them.
        shutil.copyfile(directory / 'inputs.json', run / 'inputs.json')
        print(f'Iteration evidence: {run}', flush=True)
        iterate(directory, run, prepared)
        if digest(fixture) != fixture_sha256:
            raise RuntimeError('The accepted fixture was modified; the focused tier must not do that.')
        return 0
    except BaseException as error:
        if digest(fixture) != fixture_sha256:
            print('The accepted fixture was modified; the focused tier must not do that.', file=sys.stderr)
        print(f'Focused checks failed: {error}\nEvidence: {directory}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
