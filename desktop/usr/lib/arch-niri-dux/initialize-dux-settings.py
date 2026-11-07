"""Seed missing Dux settings once per feature, before DMS reads its settings.

Dux merges `~/.config/dms/settings.json` with `config.d/*.json` and saves a
changed key back to the file that holds it, so a seed file stays editable from
Dux Settings. Each feature has its own seed file: adding a feature later still
reaches accounts that already have older seeds.
"""

import json
import os
import sys
from pathlib import Path

# Dux 2.1's default main bar, plus the updater button right of the clock. The
# button hides while Dux knows of no updates; clicking it opens Dux's updater.
# Unlisted bar options keep Dux's own defaults.
MAIN_BAR = {
    'id': 'default',
    'name': 'Main Bar',
    'enabled': True,
    'position': 0,
    'screenPreferences': ['all'],
    'showOnLastDisplay': True,
    'leftWidgets': ['launcherButton', 'workspaceSwitcher', 'focusedWindow'],
    'centerWidgets': ['music', 'clock', {'id': 'systemUpdate', 'enabled': True, 'hideWhenIdle': True}, 'weather'],
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
    # The bar button keeps Dux's server checking in the background. Weekly,
    # with no login check, keeps the button quiet on live Arch. Dux schedules
    # from its last check persisted in ~/.cache/dms, so reboots don't reset the
    # week; a failed (offline) check retries every 5 minutes, silently.
    '00-arch-niri-dux-updates.json': {
        'updaterIntervalSeconds': 7 * 24 * 60 * 60,
        'barConfigs': [MAIN_BAR],
    },
}


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


def initialize(config, seeds=SEEDS):
    for name, defaults in seeds.items():
        try:
            _seed(config, name, defaults)
        except (OSError, ValueError, TypeError) as error:
            # An unreadable user file must not prevent the desktop from starting.
            print(f'Dux defaults {name} were not seeded: {error}', file=sys.stderr)


if __name__ == '__main__':
    initialize(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))))
