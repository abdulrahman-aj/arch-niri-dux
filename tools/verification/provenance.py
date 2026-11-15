"""Hash host verifier sources, including dependencies imported inside functions."""

import ast
from importlib.util import resolve_name
import subprocess

from tools.digest import digest


def host_sources(root):
    # Seed the whole verifier and transport so coverage does not depend on which
    # stages have executed yet. Guest probes already have their own staged hashes.
    pending = {
        path
        for directory in ('tools/verification', 'tools/vm')
        for path in (root / directory).rglob('*.py')
        if 'probes' not in path.relative_to(root).parts
    }
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        relative = path.relative_to(root)
        package = '.'.join(relative.parent.parts)
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module
                if base:
                    names.add(base)
                    names.update(base + '.' + alias.name for alias in node.names)
        for name in names:
            parts = name.split('.')
            # Include package initializers, which Python executes on import.
            for index in range(1, len(parts) + 1):
                module = root.joinpath(*parts[:index])
                for candidate in (module.with_suffix('.py'), module / '__init__.py'):
                    if candidate.is_file() and candidate not in visited:
                        pending.add(candidate)
    # This data file determines the host error-gate verdict too.
    visited.add(root / 'tools/verification/gate-allowlist.txt')
    return {str(path.relative_to(root)): digest(path) for path in sorted(visited)}


def checkout_state(root):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

    return {
        'commit': git('rev-parse', 'HEAD'),
        'dirty': bool(git('status', '--porcelain', '--untracked-files=all')),
    }
