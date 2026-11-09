"""Focused release-button interaction probe for the disposable focused tier.

Runs guest-side around host input, mirroring dux_update's probe style. This
tier exercises the packaged widget's visibility and click behavior only:
the checker hides with nothing newer, a newer fixture release shows the pill
after a supported plugin rescan, and a supported manual sysupdate refresh
re-primes the pending list without waiting for the scheduler. It never
qualifies the scheduled checks, authentication, upgrades, migrations or a
fresh installation; full acceptance owns those.
"""

import json
from pathlib import Path
import shutil

from . import dispatch
from .apps import daemon_request
from .dux_update import PLUGIN_ID, added_pill, bar_pills, plugin_listed, release_check_result
from .guest import screenshot, user_command, wait_until

EVIDENCE = Path('/run/verify')
WORK = Path('/var/tmp/release-focus')
PACMAN_CONF = Path('/etc/pacman.conf')
SAVED_CONF = WORK / 'pacman.conf.original'
REPOSITORY = 'release-focus-fixture'
SERVER = WORK / 'repo'
# Far newer than any real release, so the offered upgrade is unmistakable.
CANDIDATE = '99.0-1'
HIDDEN = 'release-hidden.png'
AVAILABLE = 'release-available.png'
PANEL = 'release-panel.png'
SEED = Path('/home/tester/.config/dms/config.d/00-arch-niri-dux-updates.json')
PLUGIN_SETTINGS = Path('/home/tester/.config/dms/plugin_settings.json')


def widget_on_bar():
    """Whether the tester's effective bar config carries the release widget."""
    merged = {}
    directory = SEED.parent
    for path in [directory.parent / 'dms/settings.json', *sorted(directory.glob('*.json'))]:
        if path.is_file():
            merged |= json.loads(path.read_text())
    return any(
        widget == PLUGIN_ID or (isinstance(widget, dict) and widget.get('id') == PLUGIN_ID)
        for bar in merged.get('barConfigs', [])
        if isinstance(bar, dict)
        for widgets in ('leftWidgets', 'centerWidgets', 'rightWidgets')
        for widget in bar.get(widgets, [])
    )


def prepare_disposable_user():
    """Put the release widget on this disposable tester's bar explicitly.

    A fresh installation seeds the widget at first login, and this overlay
    base normally qualifies as fresh. When it does not, prepare the
    disposable account's own seed instead of claiming fresh-install state.
    A real user's files are never touched: this account exists only inside
    the disposable overlay.
    """
    seed = json.loads(SEED.read_text())
    for bar in seed.get('barConfigs', []):
        widgets = bar.setdefault('centerWidgets', [])
        if not any(
            widget == PLUGIN_ID or (isinstance(widget, dict) and widget.get('id') == PLUGIN_ID) for widget in widgets
        ):
            widgets.append({'id': PLUGIN_ID, 'enabled': True})
    SEED.write_text(json.dumps(seed, indent=2) + '\n')
    settings = json.loads(PLUGIN_SETTINGS.read_text())
    settings.setdefault(PLUGIN_ID, {}).setdefault('enabled', True)
    PLUGIN_SETTINGS.write_text(json.dumps(settings, indent=2) + '\n')
    # A shell restart applies the prepared config the way a login does; the
    # visibility check then runs immediately instead of on the 6-hour poll.
    user_command('systemctl', '--user', 'restart', 'dms.service')
    wait_until('Dux shell after the prepared config', plugin_listed, timeout=120)


def use_fixture_repository():
    """Point pacman at the fixture repository only, keeping a restorable backup."""
    SERVER.mkdir(parents=True, exist_ok=True)
    for name in (f'{REPOSITORY}.db', 'arch-niri-dux.db'):
        shutil.copyfile(EVIDENCE / 'release-focus.db', SERVER / name)
    original = PACMAN_CONF.read_text()
    include = 'Include = /etc/pacman.d/arch-niri-dux.conf'
    assert original.count(include) == 1, original
    SAVED_CONF.write_text(original)
    PACMAN_CONF.write_text(
        original.replace(include, f'[{REPOSITORY}]\nSigLevel = Optional TrustAll\nServer = file://{SERVER}')
    )


def restore_pacman_conf():
    if SAVED_CONF.exists():
        PACMAN_CONF.write_text(SAVED_CONF.read_text())


def pending_release(candidate):
    """Dux's pending list once the manual refresh has found the fixture release."""

    def found():
        current = daemon_request('sysupdate.getState')
        assert current.get('phase') != 'error', current
        return (
            current['phase'] == 'idle'
            and any(
                package['name'] == 'arch-niri-dux' and package['toVersion'] == candidate
                for package in current.get('packages', [])
            )
            and current
        )

    return wait_until("Dux's pending list after the manual refresh", found, timeout=120)


def wait_for_pill():
    """Screenshot until the release pill appears next to the hidden baseline.

    The supported rescan reloads the widget asynchronously, so poll the bar
    until exactly one new pill appears by the same displaced-left-edge rule
    the host uses to aim the click.
    """

    def shown():
        screenshot(AVAILABLE)
        try:
            added_pill(bar_pills(EVIDENCE / HIDDEN), bar_pills(EVIDENCE / AVAILABLE))
        except AssertionError:
            return False
        return True

    wait_until('The release pill on the bar', shown, timeout=90)


def hidden():
    """The packaged checker hides the button with nothing newer."""
    # A fresh account's session writes its Dux seed files at first login;
    # they must exist before deciding whether this base is fresh.
    wait_until('The tester account Dux seed at first login', SEED.is_file, timeout=60)
    fresh = widget_on_bar()
    if fresh:
        # The directory watcher lists the packaged plugin asynchronously
        # after the shell connects; wait for it like a user opening the
        # plugin list would.
        wait_until(
            'The release plugin in plugin-scan list',
            plugin_listed,
            timeout=180,
        )
    else:
        prepare_disposable_user()
    assert not release_check_result(), 'Release checker should hide with nothing newer'
    screenshot(HIDDEN)
    print(json.dumps({'fresh_bar_state': fresh, 'checker': 'hidden'}))


def offer():
    """Swap in the newer fixture release, rescan, and manually refresh."""
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(mode=0o755)
    candidate = CANDIDATE
    result = {'candidate': candidate}
    try:
        use_fixture_repository()
        assert release_check_result(), 'Release checker should show the fixture release'
        result['checker'] = 'shown'
        # The supported rescan re-runs the visibility check now instead of on
        # the 6-hour poll; it also briefly drops the widget's updater hold.
        reply = user_command('dms', 'ipc', 'call', 'plugin-scan', 'rescan', PLUGIN_ID, capture=True).stdout
        assert 'RESCAN_TRIGGERED' in reply, reply
        wait_for_pill()
        # Re-prime the pending list with the same request Dux's own
        # checkForUpdates sends; no wait for the scheduled check.
        daemon_request('sysupdate.refresh', {'force': True, 'background': False})
        pending = pending_release(candidate)
        result['pending'] = {key: pending[key] for key in ('phase', 'count', 'lastSuccessUnix')}
        print(json.dumps(result))
    except BaseException:
        restore_pacman_conf()
        raise


def restore():
    restore_pacman_conf()
    print(json.dumps({'pacman_conf': 'restored'}))


def panel_screenshot():
    screenshot(PANEL)


def debug():
    """Capture plugin status and shell logs for a pill or panel that never appeared."""
    try:
        status = user_command('dms', 'ipc', 'call', 'plugin-scan', 'status', PLUGIN_ID, capture=True).stdout
        (EVIDENCE / 'release-focus-plugin-status.txt').write_text(status)
    except BaseException as error:
        (EVIDENCE / 'release-focus-plugin-status.txt').write_text(f'plugin-scan status failed: {error}\n')
    try:
        listed = user_command('dms', 'ipc', 'call', 'plugin-scan', 'list', capture=True).stdout
        (EVIDENCE / 'release-focus-plugin-list.txt').write_text(listed)
    except BaseException as error:
        (EVIDENCE / 'release-focus-plugin-list.txt').write_text(f'plugin-scan list failed: {error}\n')
    try:
        journal = user_command(
            'journalctl', '--user', '-u', 'dms.service', '--no-pager', '-n', '200', capture=True
        ).stdout
        (EVIDENCE / 'release-focus-dms-journal.txt').write_text(journal)
    except BaseException as error:
        (EVIDENCE / 'release-focus-dms-journal.txt').write_text(f'journal capture failed: {error}\n')
    try:
        (EVIDENCE / 'release-focus-state.json').write_text(
            json.dumps(daemon_request('sysupdate.getState'), indent=2) + '\n'
        )
    except BaseException as error:
        (EVIDENCE / 'release-focus-state.json').write_text(f'sysupdate state failed: {error}\n')


if __name__ == '__main__':
    dispatch(
        'release_focus',
        {
            'hidden': hidden,
            'offer': offer,
            'restore': restore,
            'panel-screenshot': panel_screenshot,
            'debug': debug,
        },
    )
