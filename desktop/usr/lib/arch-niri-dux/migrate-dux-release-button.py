"""Rewrite our untouched updates seed to the release button.

Only values that still equal the seed we shipped before the release button are
changed: updaterIntervalSeconds 604800 becomes 21600, and a main bar identical
to our old seed gets our new bar. Anything the user changed stays untouched.
Missing files are left to the seeder, which covers fresh accounts.

Exit 0 after applying changes, exit 77 with a stdout reason when there is
nothing to do, so the migration queue records a skip instead of failing.
"""

import copy
import json
import os
import stat
import sys
from pathlib import Path

SEED_NAME = '00-arch-niri-dux-updates.json'
OLD_INTERVAL_SECONDS = 7 * 24 * 60 * 60
NEW_INTERVAL_SECONDS = 6 * 60 * 60
OLD_BUTTON = {'id': 'systemUpdate', 'enabled': True, 'hideWhenIdle': True}
NEW_BUTTON = {'id': 'archNiriDuxRelease', 'enabled': True}

# Frozen historical record of the main bar our old seed installed. A bar is
# only replaced when it still equals this exactly; any user edit keeps it.
OLD_MAIN_BAR = {
    'id': 'default',
    'name': 'Main Bar',
    'enabled': True,
    'position': 0,
    'screenPreferences': ['all'],
    'showOnLastDisplay': True,
    'leftWidgets': ['launcherButton', 'workspaceSwitcher', 'focusedWindow'],
    'centerWidgets': ['music', 'clock', OLD_BUTTON, 'weather'],
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

# Frozen record of the bar this migration installs: the same bar with our
# release button in place of the built-in systemUpdate entry.
NEW_MAIN_BAR = {
    'id': 'default',
    'name': 'Main Bar',
    'enabled': True,
    'position': 0,
    'screenPreferences': ['all'],
    'showOnLastDisplay': True,
    'leftWidgets': ['launcherButton', 'workspaceSwitcher', 'focusedWindow'],
    'centerWidgets': ['music', 'clock', NEW_BUTTON, 'weather'],
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


def seed_path():
    config = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(config) / 'dms/config.d' / SEED_NAME


def migrate(target):
    """Rewrite untouched old seed values. Return a summary, or None if untouched."""
    if target.is_symlink():
        return None, 'seed is a symlink; left untouched'
    if not target.exists():
        return None, 'no seed file; fresh accounts are covered by the seeder'
    try:
        value = json.loads(target.read_text())
    except ValueError as error:
        return None, f'seed is not valid JSON; left untouched ({error})'
    if not isinstance(value, dict):
        return None, 'seed is not a JSON object; left untouched'
    changes = []
    if value.get('updaterIntervalSeconds') == OLD_INTERVAL_SECONDS:
        value['updaterIntervalSeconds'] = NEW_INTERVAL_SECONDS
        changes.append('updaterIntervalSeconds 604800 -> 21600')
    bars = value.get('barConfigs')
    if isinstance(bars, list):
        for index, bar in enumerate(bars):
            if bar == OLD_MAIN_BAR:
                bars[index] = copy.deepcopy(NEW_MAIN_BAR)
                changes.append('main bar systemUpdate -> archNiriDuxRelease')
    if not changes:
        return None, 'seed holds no untouched old values; left untouched'
    mode = stat.S_IMODE(target.stat().st_mode)
    temporary = target.parent / f'.{target.name}.tmp'
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.chmod(temporary, mode)
    temporary.replace(target)
    return ', '.join(changes), None


def main():
    changed, reason = migrate(seed_path())
    if reason is not None:
        print(reason)
        return 77
    print(f'Migrated {SEED_NAME}: {changed}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
