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
PROBES = Path('tools/verification/probes')


def swallowed_broad_except(_path, tree):
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


def pass_through_wrapper(_path, tree):
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


def probe_local_helpers(path, tree):
    # Guest probes share one evidence writer and one command runner in guest.py; local copies drifted
    # (dropped newlines, diverging failure output), so flag them where new ones would be added.
    if Path(path).parent != PROBES or Path(path).name == 'guest.py':
        return
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ('record', 'output'):
            yield node.lineno, "define the shared probe helper in guest.py instead of a local 'record'/'output'"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == 'EVIDENCE' for target in targets):
                yield node.lineno, 'import EVIDENCE from guest.py instead of redefining it'
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'write_text'):
            continue
        dumps = any(
            isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == 'dumps'
            for inner in ast.walk(node)
        )
        # record() only covers EVIDENCE/<name>.json; scratch JSON in WORK or LOCAL_PROBES stays inline,
        # so the rule mirrors that boundary instead of flagging every json.dumps write.
        evidence_target = any(
            isinstance(inner, ast.Name) and inner.id in ('EVIDENCE', 'evidence') for inner in ast.walk(node.func.value)
        ) or any(
            isinstance(inner, ast.Constant) and isinstance(inner.value, str) and '/run/verify' in inner.value
            for inner in ast.walk(node.func.value)
        )
        if dumps and evidence_target:
            yield node.lineno, 'write probe evidence with guest.record instead of write_text(json.dumps(...))'


RULES = (swallowed_broad_except, pass_through_wrapper, probe_local_helpers)


def violations(path, source):
    tree = ast.parse(source, filename=str(path))
    for rule in RULES:
        for line, message in rule(path, tree):
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
