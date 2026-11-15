"""Stage the guest payload and invoke its probes through a VM transport."""

from pathlib import Path
import shlex
import shutil
from tools.digest import digest


def mount_probes(vm):
    vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')


def probe(vm, module, *arguments, timeout=120):
    command = ['python', '-m', 'tools.verification.probes.' + module, *map(str, arguments)]
    return vm.command('cd /run/verify && ' + shlex.join(command), timeout=timeout)


def stage_probes(directory):
    """Stage every guest probe and the small package-builder dependency set."""
    source = Path(__file__).parents[1]
    files = [
        '__init__.py',
        'digest.py',
        'verification/__init__.py',
        'verification/constants.py',
        'verification/images.py',
        'packages/__init__.py',
        'packages/integration.py',
        'packages/inputs.py',
    ]
    paths = [source / name for name in files]
    for path in paths:
        target = directory / 'tools' / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    probes = source / 'verification/probes'
    shutil.copytree(
        probes,
        directory / 'tools/verification/probes',
        ignore=shutil.ignore_patterns('__pycache__'),
        dirs_exist_ok=True,
    )
    paths += sorted(path for path in probes.rglob('*') if path.is_file() and '__pycache__' not in path.parts)
    return {str(path.relative_to(source.parent)): digest(path) for path in paths}
