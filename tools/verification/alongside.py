"""Guest-only planning for existing Linux and Windows fixtures."""

import json
from pathlib import Path
import sys

from . import dispatch


def plan():
    # Import the installer shipped by the ISO, rather than a staged host copy.
    sys.path.insert(0, '/usr/local/lib/arch-niri-dux/installer')
    import install

    device, os_name = sys.argv[2:]
    settings = install.Settings(
        device,
        'tester',
        'alongside',
        'UTC',
        'us',
        'vm-verification-only',
        'vm-verification-only',
        swap_gib=0,
        mode='alongside',
    )
    snapshot = install.storage.alongside_snapshot(settings.disk)
    assert len(snapshot['regions']) == 1
    boot_path = {'linux': 'debian', 'windows': 'microsoft'}[os_name]
    boots = [number for number, boot in snapshot['boots'].items() if boot_path in boot['path'].lower()]
    assert boots and len({(snapshot['boots'][n]['guid'], snapshot['boots'][n]['path'].lower()) for n in boots}) == 1
    settings.free_region = snapshot['regions'][0]
    settings.existing_boot = sorted(boots)[0]
    settings.validate()
    configuration = {
        key: getattr(settings, key)
        for key in (
            'disk',
            'username',
            'hostname',
            'timezone',
            'keymap',
            'passphrase',
            'password',
            'swap_gib',
            'mode',
            'free_region',
            'existing_boot',
        )
    }
    evidence = Path('/run/verify')
    (evidence / 'install.json').write_text(json.dumps(configuration))
    (evidence / 'old-partitions.json').write_text(json.dumps(snapshot['table']['partitions']))
    (evidence / 'new-partitions.json').write_text(json.dumps(install.storage.alongside_plan(settings, snapshot)))


if __name__ == '__main__':
    dispatch('alongside', {'plan': plan})
