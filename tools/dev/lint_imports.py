"""Check the architectural import boundaries established by the tooling move, and that only the
installer's storage module runs disk-writing commands."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER_MODULES = ('install', 'nvidia', 'payload', 'storage', 'wizard')
# install.py is the engine and launcher target; it alone composes the others.
INSTALLER_LAYERS = {
    'installer.nvidia': ['installer.install', 'installer.payload', 'installer.storage', 'installer.wizard'],
    'installer.storage': ['installer.install', 'installer.payload', 'installer.wizard'],
    'installer.payload': ['installer.install', 'installer.wizard'],
    'installer.wizard': ['installer.install', 'installer.payload'],
}
# Disk writes and target mounts belong in installer/storage.py, behind its revalidate-then-write checks.
DISK_COMMANDS = ('btrfs', 'cryptsetup', 'mount', 'umount', 'sfdisk', 'sgdisk', 'wipefs')
RUNNERS = ('run', 'Popen', 'call', 'check_call', 'check_output', 'system')


def disk_command_violations(path, tree):
    if path.parts[0] != 'installer' or path.name == 'storage.py':
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        runner = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
        if runner not in RUNNERS:
            continue
        # The command is the first argument: run('mount', ...), subprocess.run(['umount', ...]) or a shell string.
        command = node.args[0]
        if isinstance(command, (ast.List, ast.Tuple)) and command.elts:
            command = command.elts[0]
        if not isinstance(command, ast.Constant) or not isinstance(command.value, str):
            continue
        name = (command.value.split() or [''])[0]
        if name in DISK_COMMANDS or name.startswith('mkfs'):
            yield f'{path}:{node.lineno}: {name} writes to disk; run it from installer/storage.py'


def violations(path, source):
    module = '.'.join(path.with_suffix('').parts)
    tree = ast.parse(source)
    yield from disk_command_violations(path, tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            if module.startswith(('tools.packages.', 'tools.build.')) and 'tools' in names:
                yield f'{path}:{node.lineno}: import a specific tools module to expose its boundary'
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                base = '.'.join(module.split('.')[: -node.level] + ([base] if base else []))
            names = [base, *(base + '.' + alias.name for alias in node.names)]
        else:
            continue
        guest = module == 'tools.verification.probes' or module.startswith('tools.verification.probes.')
        shared = module in ('tools.verification.constants', 'tools.verification.images')
        dependencies = (
            [(base + '.' + alias.name) for alias in node.names] if isinstance(node, ast.ImportFrom) else names
        )
        allowed = (
            (
                'tools.verification.probes',
                'tools.verification.constants',
                'tools.verification.images',
                'tools.packages',
                'tools.digest',
            )
            if guest
            else ('tools.digest',)
        )
        if guest or shared:
            for name in dependencies:
                if (name == 'tools' or name.startswith('tools.')) and not any(
                    name == prefix or name.startswith(prefix + '.') for prefix in allowed
                ):
                    yield f'{path}:{node.lineno}: forbidden import {name}'
        elif any(
            name == 'tools.verification.probes' or name.startswith('tools.verification.probes.') for name in names
        ):
            yield f'{path}:{node.lineno}: host code imports a guest-only probe'
        forbidden = []
        if module.startswith('tools.packages.'):
            forbidden = ['tools.build', 'tools.release', 'tools.verification']
        elif module.startswith('tools.build.'):
            forbidden = ['tools.release']
        elif module in ('tools.workspace.state', 'tools.workspace.resources', 'tools.workspace.fixtures'):
            forbidden = ['tools.workspace.gc', 'tools.workspace.__main__']
        elif module.startswith('tools.verification.scenarios.') or module == 'tools.verification.stages':
            forbidden = [
                'tools.verification.session',
                'tools.verification.verify_alongside',
                'tools.verification.verify_release',
                'tools.verification.verify_hibernate',
            ]
        elif module == 'tools.vm.control':
            forbidden = ['tools.verification']
        elif module.startswith('installer.'):
            forbidden = ['tools', *INSTALLER_LAYERS.get(module, [])]
            # The launcher runs install.py as a script, so sibling modules also arrive as bare top-level names.
            names = [f'installer.{name}' if name in INSTALLER_MODULES else name for name in names]
        for name in names:
            if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden):
                yield f'{path}:{node.lineno}: forbidden import {name}'


def main():
    errors = []
    for directory in ('tools', 'installer'):
        for path in sorted((ROOT / directory).rglob('*.py')):
            errors.extend(violations(path.relative_to(ROOT), path.read_text()))
    if errors:
        raise SystemExit('\n'.join(errors))


if __name__ == '__main__':
    main()
