"""Keep out code patterns that agents copy from neighbouring code.

Each rule names the pattern it guards against; the definitions and the counts that justified them are in the
pull request that added the rule.
"""

import ast
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
# Python sources without a .py suffix that ruff also checks (see the lint-in-dev recipe in the Makefile).
EXTRA_SOURCES = ('desktop/usr/bin/arch-niri-dux-keyring-recover',)
BROAD_EXCEPTIONS = ('Exception', 'BaseException')


def swallowed_broad_except(tree):
    # A broad handler that neither re-raises nor uses the error turns crashes, including programming
    # errors, into a quiet skip or pass. Catch the specific exceptions, or record the error.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in BROAD_EXCEPTIONS)
        if not broad:
            continue
        body = [inner for statement in node.body for inner in ast.walk(statement)]
        reraises = any(isinstance(inner, ast.Raise) for inner in body)
        uses_error = node.name is not None and any(
            isinstance(inner, ast.Name) and inner.id == node.name for inner in body
        )
        if not reraises and not uses_error:
            yield node.lineno, 'broad except swallows the error; catch specific exceptions or record it'


RULES = (swallowed_broad_except,)


def violations(path, source):
    tree = ast.parse(source, filename=str(path))
    for rule in RULES:
        for line, message in rule(tree):
            yield f'{path}:{line}: {message}'


def sources():
    names = subprocess.check_output(['git', 'ls-files', '*.py', *EXTRA_SOURCES], cwd=ROOT, text=True).split()
    return [Path(name) for name in names if not name.startswith('packages/dux-apps/')]


def main():
    errors = []
    for path in sources():
        errors.extend(violations(path, (ROOT / path).read_text()))
    if errors:
        raise SystemExit('\n'.join(errors))


if __name__ == '__main__':
    main()
