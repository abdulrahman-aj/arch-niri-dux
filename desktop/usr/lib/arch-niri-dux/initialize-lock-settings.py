"""Seed missing Dux lock choices once, before DMS reads its settings."""

import json
import os
from pathlib import Path


def initialize(config, defaults):
    directory = config / 'dms/config.d'
    target = directory / '00-arch-niri-dux-lock.json'
    # Dux may remove default-valued keys after a user disables locking. Keep
    # even an empty seed file as the record that initialization already ran.
    if target.exists() or target.is_symlink():
        return
    choices = {}
    for path in [config / 'dms/settings.json', *sorted(directory.glob('*.json'))]:
        if path.exists():
            choices.update(json.loads(path.read_text()))
    missing = {key: value for key, value in defaults.items() if key not in choices}
    directory.mkdir(parents=True, exist_ok=True)
    with target.open('x') as stream:
        stream.write(json.dumps(missing, indent=2) + '\n')


if __name__ == '__main__':
    initialize(
        Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))),
        json.loads(Path('/usr/share/arch-niri-dux/lock-defaults.json').read_text()),
    )
