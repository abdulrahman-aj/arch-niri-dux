"""Dux announces pending updates on its own schedule; its updater installs them.

The packaged defaults put our release button on the bar, hidden while no
newer arch-niri-dux release is available, and make Dux's server check every 6
hours, with no check at login. Dux schedules from its last check, persisted in
~/.cache/dms, so logins and reboots don't reset the interval. This stage
shortens the interval to Dux's 5-minute minimum the way a user would, lets the
scheduled check find a fixture repository with nothing newer, re-logs in
before the interval to show no extra check runs, then offers our packages one
pkgrel newer and lets the next scheduled check find them. The pill appears when
Dux's own check finds the newer arch-niri-dux, with no rescan or visibility
workaround. The host clicks the button, sends Update All's request, types the
password into Dux's real authentication dialog, and checks the installed
release end to end. Dux runs `pkexec pacman -Syu --needed --noconfirm`
through its pacman backend because no AUR helper is installed.
"""

import json
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import time

from . import dispatch
from .apps import daemon_request
from .guest import png_dimensions, png_top_rows, run, screenshot, user_command, wait_until
from .vm import PASSWORD

EVIDENCE = Path('/run/verify')
# Probe steps run as separate processes around host keyboard input.
WORK = Path('/var/tmp/dux-update-fixture')
PACMAN_CONF = Path('/etc/pacman.conf')
SAVED_CONF = WORK / 'pacman.conf.original'
REPOSITORY = 'dux-update-fixture'
PACKAGES = ('arch-niri-dux', 'arch-niri-dux-baseline')
PROMPT = 'dux-update-prompt.png'
NONE = 'dux-update-none.png'
AVAILABLE = 'dux-update-available.png'
PANEL = 'dux-update-panel.png'
# A row through the middle of Dux's default top bar, in screenshot pixels.
BAR_MIDDLE = 22
BAR_ABOVE_PILLS = 6
# The packaged seed interval; the stage shortens it to SHORT and restores it.
SIX_HOURS = 6 * 60 * 60
# Dux's minimum interval.
SHORT = 5 * 60
SCHEDULE = Path('/home/tester/.cache/dms/sysupdate.json')
UPDATE_SEED = Path('/home/tester/.config/dms/config.d/00-arch-niri-dux-updates.json')


def installed():
    return subprocess.check_output(['pacman', '-Q', *PACKAGES], text=True).strip()


def build_candidate():
    """Build our packages one pkgrel step newer for Dux's updater to find."""
    user = pwd.getpwnam('tester')
    source = WORK / 'source'
    source.mkdir()
    run('tar', '-xf', str(EVIDENCE / 'integration-input/integration.tar'), '-C', str(source))
    recipe = (EVIDENCE / 'integration-input/PKGBUILD').read_text()
    # N.1 sorts between this release and the integration upgrade's N+1 and N+2
    # candidates, so that later stage still upgrades from what we install.
    recipe, changes = re.subn(r'^pkgrel=(\d+)$', r'pkgrel=\1.1', recipe, flags=re.MULTILINE)
    assert changes == 1
    recipe_path = source / 'packages/integration/PKGBUILD'
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text(recipe)
    run('chown', '-R', f'{user.pw_uid}:{user.pw_gid}', str(WORK))
    packages = WORK / 'packages'
    run(
        'runuser',
        '-u',
        user.pw_name,
        '--',
        'env',
        '-u',
        'XDG_CONFIG_HOME',
        '-u',
        'XDG_STATE_HOME',
        '-u',
        'XDG_DATA_HOME',
        f'HOME={user.pw_dir}',
        'python',
        str(EVIDENCE / 'integration_build.py'),
        str(source),
        str(WORK / 'build'),
        str(packages),
        '--epoch',
        '1234',
    )
    run('repo-add', str(packages / f'{REPOSITORY}.db.tar.gz'), *map(str, packages.glob('*.pkg.tar.zst')))
    return packages


def use_fixture_repository(packages):
    """Point the system pacman configuration at the fixture repository only.

    Dux checks and upgrades through /etc/pacman.conf, and the guest is offline,
    so the online repositories it includes are swapped out until cleanup.
    Signature enforcement is qualified by the integration upgrade stage.
    """
    original = PACMAN_CONF.read_text()
    include = 'Include = /etc/pacman.d/arch-niri-dux.conf'
    assert original.count(include) == 1, original
    SAVED_CONF.write_text(original)
    PACMAN_CONF.write_text(
        original.replace(include, f'[{REPOSITORY}]\nSigLevel = Optional TrustAll\nServer = file://{packages}')
    )


def restore_pacman_conf():
    if SAVED_CONF.exists():
        PACMAN_CONF.write_text(SAVED_CONF.read_text())


def database():
    return WORK / 'packages' / f'{REPOSITORY}.db.tar.gz'


def write_before(before):
    (WORK / 'before.json').write_text(json.dumps(before, indent=2) + '\n')


def state():
    return daemon_request('sysupdate.getState')


def set_interval(seconds):
    """Change the interval as Dux Settings does: saved, then applied on connect."""
    reply = user_command(
        'dms', 'ipc', 'call', 'settings', 'set', 'updaterIntervalSeconds', str(seconds), capture=True
    ).stdout
    assert 'SUCCESS' in reply, reply

    def saved():
        # Dux saves on a debounce; a restart before then would drop the change.
        return json.loads(UPDATE_SEED.read_text()).get('updaterIntervalSeconds') == seconds

    wait_until('Dux saving the interval to its seed file', saved, timeout=30)
    relogin(seconds)


def relogin(interval):
    """Restart the shell and its server as a logout and login do."""
    user_command('systemctl', '--user', 'restart', 'dms.service')

    def connected():
        # The restarted shell re-sends its saved interval once it connects.
        current = state()
        return current['intervalSeconds'] == interval and current

    try:
        return wait_until('Dux server after login', connected)
    except TimeoutError:
        (EVIDENCE / 'dux-update-state.json').write_text(json.dumps(state(), indent=2))
        raise


def scheduled_check(previous):
    """Wait for Dux's scheduler to complete a successful check after `previous`."""

    def checked():
        current = state()
        # Failed background checks also advance lastCheckUnix; only a
        # successful check counts.
        return current['phase'] == 'idle' and current.get('lastSuccessUnix', 0) > previous and current

    try:
        # The next check is at most one interval plus Dux's startup grace away.
        return wait_until("Dux's scheduled update check", checked, timeout=SHORT + 180)
    except TimeoutError:
        (EVIDENCE / 'dux-update-state.json').write_text(json.dumps(state(), indent=2))
        raise


def prepare():
    """Dux checks on its schedule, finds nothing newer, and skips login checks."""
    run('systemd-detect-virt', '--vm')
    assert (EVIDENCE / 'install.json').is_file(), 'Requires the disposable installation fixture.'
    # A fresh account's first check runs shortly after its first login, long
    # before this stage, without anything opening Settings.
    initial = state()
    assert initial['lastCheckUnix'] > 0 and initial['intervalSeconds'] == SIX_HOURS, initial
    assert not initial.get('error'), initial
    # The schedule must outlive reboots, so its state can't be on tmpfs.
    filesystem = subprocess.check_output(['findmnt', '-n', '-o', 'FSTYPE', '-T', str(SCHEDULE)], text=True).strip()
    assert SCHEDULE.is_file() and filesystem != 'tmpfs', filesystem
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(mode=0o755)
    try:
        candidate_version = None
        packages = build_candidate()
        for package in packages.glob('arch-niri-dux-[0-9]*.pkg.tar.zst'):
            candidate_version = subprocess.check_output(['pacman', '-Qp', str(package)], text=True).split()[1]
        assert candidate_version, 'Candidate arch-niri-dux package was not built.'
        run('repo-remove', str(database()), *PACKAGES)
        use_fixture_repository(packages)
        set_interval(SHORT)
        none = scheduled_check(initial.get('lastSuccessUnix', 0))
        assert none['count'] == 0 and not none.get('error'), none
        # Logging in again before the interval elapses keeps the schedule. This
        # restart and wait take well under the 5-minute interval.
        after_login = relogin(SHORT)
        time.sleep(30)
        later = state()
        assert later['lastCheckUnix'] == none['lastCheckUnix'], (none, later)
        assert later['nextCheckUnix'] >= none['lastCheckUnix'] + SHORT, later
        write_before(
            {
                'packages': installed().splitlines(),
                'candidate': candidate_version,
                'checkupdates': shutil.which('checkupdates'),
                'fakeroot': shutil.which('fakeroot'),
                'first_check': {key: initial[key] for key in ('lastCheckUnix', 'intervalSeconds')},
                'none_check': none['lastSuccessUnix'],
                'relogin_kept_schedule': {
                    key: after_login[key] for key in ('lastCheckUnix', 'nextCheckUnix', 'intervalSeconds')
                },
            }
        )
    except BaseException:
        restore_pacman_conf()
        raise


def offer():
    """Dux's next scheduled check finds our newer packages."""
    try:
        before = json.loads((WORK / 'before.json').read_text())
        # Taken just before the offer so the clock rarely changes width
        # between the two screenshots the host compares.
        screenshot(NONE)
        run('repo-add', str(database()), *map(str, (WORK / 'packages').glob('*.pkg.tar.zst')))
        found = scheduled_check(before['none_check'])
        update = next(
            (
                package
                for package in found['packages']
                if package['name'] == 'arch-niri-dux' and package['toVersion'] == before['candidate']
            ),
            None,
        )
        assert update, found
        assert found['nextCheckUnix'] - found['lastCheckUnix'] == SHORT, found
        # A selected AUR helper would run in a terminal instead of through pkexec.
        system = [backend['id'] for backend in found['backends'] if backend['repo'] == 'system']
        assert system == ['pacman'], found['backends']
        before |= {'found': update, 'backends': found['backends'], 'available_check': found['lastSuccessUnix']}
        write_before(before)
        screenshot(AVAILABLE)
        # Our plugin holds Dux's updater alive, so the status query that used
        # to report "no package manager available" without the native widget
        # must now answer from fetched state. It triggers a background check,
        # so assert only after the evidence above is captured.
        status = user_command('dms', 'ipc', 'call', 'systemupdater', 'updatestatus', capture=True).stdout
        assert 'ERROR' not in status, status
    except BaseException:
        restore_pacman_conf()
        raise


def upgrade():
    """Send the request Update All sends: SystemUpdateService.runUpdates' defaults."""
    try:
        daemon_request('sysupdate.upgrade', {'includeFlatpak': True, 'includeAUR': True, 'terminal': '', 'ignored': []})
        wait_until(
            'Dux starting pkexec',
            lambda: subprocess.run(['pgrep', '-x', 'pkexec'], stdout=subprocess.DEVNULL, check=False).returncode == 0,
        )
    except BaseException:
        restore_pacman_conf()
        raise


def check():
    try:
        before = json.loads((WORK / 'before.json').read_text())

        def finished():
            state = daemon_request('sysupdate.getState')
            assert state['phase'] != 'error', state
            return state['phase'] == 'idle' and installed().splitlines() != before['packages'] and state

        try:
            state = wait_until('Dux upgrade transaction', finished, timeout=300)
        except (TimeoutError, AssertionError):
            (EVIDENCE / 'dux-update-state.json').write_text(json.dumps(daemon_request('sysupdate.getState'), indent=2))
            raise
        after = installed().splitlines()
        assert f'arch-niri-dux {before["candidate"]}' in after, after
        # pkexec logs COMMAND= just before exec'ing pacman, so journald may
        # attribute that line to _COMM=pacman; pkexec's syslog ident is stable.
        journal = subprocess.check_output(
            ['journalctl', '-b', '--no-pager', '-o', 'cat', 'SYSLOG_IDENTIFIER=pkexec'], text=True
        ).strip()
        # The transaction came from Dux's backend argv, not another upgrade.
        assert 'COMMAND=/usr/bin/pacman -Syu --needed --noconfirm' in journal, journal
        (EVIDENCE / 'dux-update.json').write_text(
            json.dumps(
                {
                    'trigger': "Dux's scheduled checks, the bar button, then Update All's server request",
                    'first_check': before['first_check'],
                    'relogin_kept_schedule': before['relogin_kept_schedule'],
                    'none_check': before['none_check'],
                    'available_check': before['available_check'],
                    'check_mechanism_present': 'checkupdates'
                    if before['checkupdates']
                    else 'fakeroot private database',
                    'backends': before['backends'],
                    'found': before['found'],
                    'before': before['packages'],
                    'after': after,
                    'upgrade_log': state.get('recentLog', []),
                    'pkexec_journal': journal.splitlines(),
                    'authentication_dialog_answered': True,
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        restore_pacman_conf()
    # Back to the shipped 6-hour interval for later stages.
    set_interval(SIX_HOURS)
    shutil.rmtree(WORK)


def screen_shows(path, *phrases):
    text = subprocess.check_output(
        ['tesseract', str(path), 'stdout', '--psm', '11'], text=True, stderr=subprocess.DEVNULL
    ).lower()
    return all(phrase in text for phrase in phrases)


def bar_pills(path):
    """Return (start, end) columns of the widget pills on the top bar."""
    rows = png_top_rows(path, BAR_MIDDLE + 1)
    row = rows[BAR_MIDDLE]
    # Pills are inset from the bar's top edge, so that strip is bar background.
    background = max(set(rows[BAR_ABOVE_PILLS]), key=rows[BAR_ABOVE_PILLS].count)
    pills, start = [], None
    for x, pixel in enumerate(row + [background]):
        if pixel != background and start is None:
            start = x
        elif pixel == background and start is not None:
            pills.append((start, x))
            start = None
    return pills


def added_pill(before, after):
    """Return the (start, end) span of the one pill the after screenshot gained.

    Dux's bar lays out pills with a fixed gap, so inserting a widget makes the
    new pill start at the same column as the pill it displaces, and every
    later pill shifts right with its width intact. The clock can still resize
    on a minute or date rollover, so widths are not the invariant; displaced
    left edges are. Ambiguity raises instead of guessing a click.
    """
    assert len(after) == len(before) + 1, ('Expected exactly one new bar pill', before, after)
    lefts = [start for start, _ in before]
    added = [
        (start, end)
        for start, end in after
        if (start, end) not in before and any(abs(start - left) <= 2 for left in lefts)
    ]
    assert len(added) == 1, ('Release pill is not uniquely identified', before, after, added)
    return added[0]


def update_button(vm):
    """Locate the release pill that appeared once a newer release was offered."""
    start, end = added_pill(bar_pills(vm.directory / NONE), bar_pills(vm.directory / AVAILABLE))
    width, height = png_dimensions(vm.directory / AVAILABLE)
    return (start + end) / 2 / width, BAR_MIDDLE / height


def verify(vm, passed):
    vm.probe('dux_update', 'prepare', timeout=600)
    vm.probe('dux_update', 'offer', timeout=300)
    passed(
        "Dux's scheduled checks found no release with the button hidden, skipped a re-login, then showed it for ours"
    )
    vm.click(*update_button(vm))
    # The button opens Dux's updater panel. OCR cannot read its buttons, so the
    # upgrade is the same server request its Update All sends.
    deadline = time.monotonic() + 30
    while True:
        vm.probe('dux_update', 'panel-screenshot')
        if screen_shows(vm.directory / PANEL, 'updates', 'arch-niri-dux'):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"Dux's updater panel did not list our package; see {PANEL}.")
        time.sleep(1)
    vm.key('esc')
    vm.probe('dux_update', 'upgrade', timeout=120)
    # Dux's polkit agent opens its authentication dialog for pkexec.
    deadline = time.monotonic() + 60
    while True:
        vm.probe('dux_update', 'prompt-screenshot')
        if screen_shows(vm.directory / PROMPT, 'authentication'):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f'Dux authentication dialog did not appear; see {PROMPT}.')
        time.sleep(2)
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.probe('dux_update', 'check', timeout=420)
    passed("The bar button opened Dux's updater, which installed the offered release")


if __name__ == '__main__':
    dispatch(
        'dux_update',
        {
            'prepare': prepare,
            'offer': offer,
            'upgrade': upgrade,
            'panel-screenshot': lambda: screenshot(PANEL),
            'prompt-screenshot': lambda: screenshot(PROMPT),
            'check': check,
        },
    )
