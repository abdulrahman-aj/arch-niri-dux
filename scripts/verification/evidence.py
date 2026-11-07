"""Workspace files shared by VM acceptance scenarios."""

import json
from pathlib import Path
import shlex

from .digest import digest


class Evidence:
    def __init__(self, directory, *, parents=False, exist_ok=False):
        self.directory = directory
        directory.mkdir(mode=0o700, parents=parents, exist_ok=exist_ok)
        self.milestones = []

    def passed(self, message):
        self.milestones.append(message)
        (self.directory / 'milestones.json').write_text(json.dumps(self.milestones, indent=2))
        print('PASS: ' + message, flush=True)

    def inputs(self, values=None):
        if values is None:
            return json.loads((self.directory / 'inputs.json').read_text())
        (self.directory / 'inputs.json').write_text(json.dumps(values, indent=2) + '\n')
        return values

    def key(self, password):
        path = self.directory / 'key'
        path.write_text(password)
        path.chmod(0o600)

    def verify_installer(self, vm, installer):
        # The ISO stages the whole directory. Check every Python module so a
        # newly extracted dependency cannot silently come from an older ISO.
        files = sorted(installer.glob('*.py')) + [installer / 'configure-target.sh']
        for source in files:
            guest = Path('/usr/local/lib/arch-niri-dux/installer') / source.name
            vm.command(f'test "$(sha256sum {shlex.quote(str(guest))} | cut -d " " -f 1)" = {digest(source)}')
