"""Seed missing Dux settings once per feature, before DMS reads its settings.

Dux merges `~/.config/dms/settings.json` with `config.d/*.json` and saves a
changed key back to the file that holds it, so a seed file stays editable from
Dux Settings. Each feature has its own seed file: adding a feature later still
reaches accounts that already have older seeds.
"""

import json
import os
import stat
import sys
from pathlib import Path

# Dux 2.1's default main bar, plus our release button right of the clock. The
# button appears only when a newer arch-niri-dux release is available; clicking
# it opens Dux's updater. Unlisted bar options keep Dux's own defaults, but the
# widget lists are a copy: re-sync them with Dux's default bar when updating Dux.
MAIN_BAR = {
    'id': 'default',
    'name': 'Main Bar',
    'enabled': True,
    'position': 0,
    'screenPreferences': ['all'],
    'showOnLastDisplay': True,
    'leftWidgets': ['launcherButton', 'workspaceSwitcher', 'focusedWindow'],
    'centerWidgets': ['music', 'clock', {'id': 'archNiriDuxRelease', 'enabled': True}, 'weather'],
    'rightWidgets': [
        'systemTray',
        'clipboard',
        'cpuUsage',
        'memUsage',
        'notificationButton',
        'battery',
        'controlCenterButton',
    ],
}

SEEDS = {
    # Dux 2.1 defaults disable locking before suspend and idle locking.
    '00-arch-niri-dux-lock.json': {'lockBeforeSuspend': True, 'acLockTimeout': 600, 'batteryLockTimeout': 600},
    # The updater panel still checks in the background every 6 hours, which also
    # drives the release button. Dux schedules from its last check persisted in
    # ~/.cache/dms, so reboots don't reset the interval; a failed (offline)
    # check retries every 5 minutes, silently.
    '00-arch-niri-dux-updates.json': {
        'updaterIntervalSeconds': 6 * 60 * 60,
        'barConfigs': [MAIN_BAR],
    },
}

# System plugin id for the release button. Dux renders a widget plugin only
# when enabled in ~/.config/dms/plugin_settings.json; system plugins are
# unaffected by AdminPolicy.disableUserPlugins, and the directory watcher picks
# up the packaged plugin on a fresh session without a manual rescan.
PLUGIN_ID = 'archNiriDuxRelease'


def _seed(config, name, defaults):
    directory = config / 'dms/config.d'
    target = directory / name
    # Dux may remove default-valued keys after a user changes them. Keep even
    # an empty seed file as the record that this feature was already seeded.
    if target.exists() or target.is_symlink():
        return
    choices = {}
    for path in [config / 'dms/settings.json', *sorted(directory.glob('*.json'))]:
        if path.exists():
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ValueError(f'Expected a JSON object in {path}')
            choices.update(value)
    missing = {key: value for key, value in defaults.items() if key not in choices}
    directory.mkdir(parents=True, exist_ok=True)
    # The seed file is also the done-marker, so it must appear complete or not
    # at all. Dux reads only *.json, so it ignores the temporary name.
    temporary = directory / f'.{name}.tmp'
    temporary.write_text(json.dumps(missing, indent=2) + '\n')
    temporary.replace(target)


def _seed_plugin(config):
    target = config / 'dms/plugin_settings.json'
    # Never replace a user-owned file or link that already records a choice.
    if target.is_symlink():
        return
    if target.exists():
        value = json.loads(target.read_text())
        if not isinstance(value, dict):
            raise ValueError(f'Expected a JSON object in {target}')
        entry = value.get(PLUGIN_ID)
        if isinstance(entry, dict) and 'enabled' in entry:
            return
        updated = dict(value)
        merged = dict(entry) if isinstance(entry, dict) else {}
        merged['enabled'] = True
        updated[PLUGIN_ID] = merged
    else:
        updated = {PLUGIN_ID: {'enabled': True}}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / '.plugin_settings.json.tmp'
    temporary.write_text(json.dumps(updated, indent=2) + '\n')
    if target.exists():
        # Keep the user's file mode; the temporary file has the umask default.
        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
    temporary.replace(target)


def initialize(config, seeds=SEEDS):
    for name, defaults in seeds.items():
        try:
            _seed(config, name, defaults)
        except (OSError, ValueError, TypeError) as error:
            # An unreadable user file must not prevent the desktop from starting.
            print(f'Dux defaults {name} were not seeded: {error}', file=sys.stderr)
    try:
        _seed_plugin(config)
    except (OSError, ValueError, TypeError) as error:
        print(f'Dux plugin {PLUGIN_ID} was not seeded: {error}', file=sys.stderr)


if __name__ == '__main__':
    initialize(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))))
