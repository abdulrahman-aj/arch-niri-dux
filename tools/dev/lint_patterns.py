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


def pass_through_wrapper(tree):
    # A body that only forwards its own parameters to another callable adds a name to learn
    # without adding behavior; call the target directly instead.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.decorator_list:
            continue
        arguments = node.args
        # Signatures with *args, **kwargs or keyword-only parameters usually adapt an interface; stay narrow.
        if arguments.vararg or arguments.kwarg or arguments.kwonlyargs:
            continue
        names = [argument.arg for argument in (*arguments.posonlyargs, *arguments.args)]
        if names[:1] in (['self'], ['cls']):
            names = names[1:]
        body = node.body[1:] if ast.get_docstring(node) is not None else node.body
        if not names or len(body) != 1 or not isinstance(body[0], (ast.Return, ast.Expr)):
            continue
        call = body[0].value
        if not isinstance(call, ast.Call) or call.keywords:
            continue
        if [argument.id if isinstance(argument, ast.Name) else None for argument in call.args] == names:
            yield node.lineno, 'pass-through wrapper forwards its parameters unchanged; call the target directly'


RULES = (swallowed_broad_except, pass_through_wrapper)


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
