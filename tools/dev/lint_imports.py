"""Check the architectural import boundaries established by the tooling move."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def violations(path, source):
    module = '.'.join(path.with_suffix('').parts)
    for node in ast.walk(ast.parse(source)):
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
        forbidden = []
        if module.startswith('tools.packages.'):
            forbidden = ['tools.build', 'tools.release', 'tools.verification']
        elif module.startswith('tools.build.'):
            forbidden = ['tools.release']
        elif module in ('tools.workspace.state', 'tools.workspace.resources', 'tools.workspace.fixtures'):
            forbidden = ['tools.workspace.gc', 'tools.workspace.__main__']
        elif module.startswith('installer.'):
            forbidden = ['tools']
        for name in names:
            if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden):
                yield f'{path}:{node.lineno}: forbidden import {name}'


# TODO phase 5: guest probes must not import host VM controls after their split.
def main():
    errors = []
    for directory in ('tools', 'installer'):
        for path in sorted((ROOT / directory).rglob('*.py')):
            errors.extend(violations(path.relative_to(ROOT), path.read_text()))
    if errors:
        raise SystemExit('\n'.join(errors))


if __name__ == '__main__':
    main()
