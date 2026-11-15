"""Keep out code patterns that agents copy from neighbouring code.

Each rule names the pattern it guards against; the definitions and the counts that justified them are in the
pull request that added the rule.

A single line can opt out with a trailing `# lint-patterns:` comment of the
form `allow <rule> -- <reason>`, for example allowing
`mock_interaction_assertion` because the outcome is asserted below.
The comment must sit on the reported line, name a known rule, and give
a reason after `--`; anything else is itself a violation.
"""

import ast
import re
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


def unquoted_shell_interpolation(path, tree):
    # A value spliced unquoted into a shell string breaks on spaces or becomes
    # injection the first time an agent copies the line for a real path; quoting
    # is a no-op on today's safe values, so every interpolation goes through shlex.
    # Scope: the string passed straight to vm.command or after 'sh'/'bash' '-c';
    # a command assembled in a variable first is not followed.
    # Tests build shell code on purpose (sourcing fixtures), so this watches tools/.
    if not str(path).startswith('tools/'):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            elements = node.args
            is_command = isinstance(node.func, ast.Attribute) and node.func.attr == 'command' and node.args
            commands = [node.args[0]] if is_command else []
        elif isinstance(node, (ast.List, ast.Tuple)):
            elements, commands = node.elts, []
        else:
            continue
        commands += _dash_c_command(elements)
        if any(_unquoted_interpolation(command) for command in commands):
            yield node.lineno, 'shell command interpolates a value without shlex.quote/shlex.join'


def _dash_c_command(elements):
    words = [element.value if isinstance(element, ast.Constant) else None for element in elements]
    if '-c' not in words:
        return []
    index = words.index('-c')
    if index + 1 < len(elements) and {'sh', 'bash'} & set(words[:index]):
        return [elements[index + 1]]
    return []


def _shlex_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ('quote', 'join')
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'shlex'
    )


def _unquoted_interpolation(command):
    if isinstance(command, ast.JoinedStr):
        return any(isinstance(value, ast.FormattedValue) and not _shlex_call(value.value) for value in command.values)
    if isinstance(command, ast.BinOp) and isinstance(command.op, ast.Add):
        return any(
            not isinstance(side, ast.Constant)
            and not _shlex_call(side)
            and (not isinstance(side, (ast.JoinedStr, ast.BinOp)) or _unquoted_interpolation(side))
            for side in (command.left, command.right)
        )
    # %-formatting and str.format can't route each value through shlex readably; use an f-string.
    is_format = (
        isinstance(command, ast.Call) and isinstance(command.func, ast.Attribute) and command.func.attr == 'format'
    )
    return is_format or (isinstance(command, ast.BinOp) and isinstance(command.op, ast.Mod))


def mock_interaction_assertion(path, tree):
    # Tests that pin how code was called break on every refactor and pass while
    # the real command is wrong; assert outcomes (return values, files, errors).
    if not str(path).startswith('tests/'):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in (
            'assert_called',
            'assert_called_once',
            'assert_called_with',
            'assert_called_once_with',
            'assert_any_call',
            'assert_has_calls',
            'assert_not_called',
            'call_args',
            'call_args_list',
            'mock_calls',
            'call_count',
            'method_calls',
        ):
            yield node.lineno, ('mock interaction assertion pins how code was called; assert outcomes instead')


def _import_map(tree):
    mapping = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mapping[alias.asname or alias.name.split('.')[0]] = (
                    alias.name if alias.asname else alias.name.split('.')[0]
                )
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                mapping[alias.asname or alias.name] = f'{node.module}.{alias.name}'
    return mapping


def _resolve(dotted, mapping):
    first, separator, rest = dotted.partition('.')
    return mapping.get(first, first) + separator + rest


def _repo_callable(dotted):
    parts = dotted.split('.')
    for index in range(len(parts) - 1, 0, -1):
        source = ROOT.joinpath(*parts[:index]).with_suffix('.py')
        if not source.is_file():
            source = ROOT.joinpath(*parts[:index], '__init__.py')
        if not source.is_file():
            continue
        definitions = ast.parse(source.read_text()).body
        target = parts[index:]
        for name in target:
            definition = next(
                (
                    node
                    for node in definitions
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name
                ),
                None,
            )
            if definition is None:
                return False
            definitions = definition.body if isinstance(definition, ast.ClassDef) else []
        return True
    return False


def patched_repo_function(path, tree):
    # Replacing a repo callable bypasses the behavior under test; keep fakes at external boundaries
    # (stdlib, executables on PATH, constants). Attribute assignment and setattr with a literal name
    # replace callables just like patch(); a setattr with a computed name can't be resolved statically.
    # Code inside a string that a test runs as a subprocess program is not parsed either; the probe
    # entrypoint test uses that only to swap the QEMU-backed VM class, the hardware boundary.
    if not str(path).startswith('tests/'):
        return
    mapping = _import_map(tree)
    for node in ast.walk(tree):
        for target in _replaced_targets(node):
            if _repo_callable(target) or _repo_callable(_resolve(target, mapping)):
                yield node.lineno, f"replaces repo callable '{target}'; use a real seam instead"


def _replaced_targets(node):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            for element in target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]:
                if isinstance(element, ast.Attribute):
                    yield ast.unparse(element)
        return
    if not isinstance(node, ast.Call):
        return
    function = ast.unparse(node.func).split('.')
    names = [argument.value if isinstance(argument, ast.Constant) else None for argument in node.args]
    if function[-1] == 'patch' and names and isinstance(names[0], str):
        yield names[0]
    elif (
        (function[-2:] == ['patch', 'object'] or function == ['setattr'])
        and len(names) >= 2
        and isinstance(names[1], str)
    ):
        yield f'{ast.unparse(node.args[0])}.{names[1]}'


RULES = (
    swallowed_broad_except,
    pass_through_wrapper,
    probe_local_helpers,
    unquoted_shell_interpolation,
    mock_interaction_assertion,
    patched_repo_function,
)

RULE_NAMES = {rule.__name__ for rule in RULES}
ALLOW = re.compile(r'#\s*lint-patterns:\s*allow\b(?P<tail>.*)$')


def parse_opt_outs(source):
    """Split lint-patterns allow comments into suppressions and errors.

    Returns ({line: {rule names}}, [(line, message)]). An unknown rule or
    a missing reason is an error, and the invalid comment suppresses
    nothing, so the original violation still reports.
    """
    allowed = {}
    errors = []
    for number, line in enumerate(source.splitlines(), 1):
        match = ALLOW.search(line)
        if match is None:
            continue
        head, separator, rest = match.group('tail').partition('--')
        rule = head.strip()
        reason = rest.strip() if separator else ''
        if not rule:
            errors.append((number, 'lint-patterns allow needs a rule name'))
        elif rule not in RULE_NAMES:
            errors.append((number, f"unknown lint-patterns rule '{rule}'"))
        elif not reason:
            errors.append((number, f"lint-patterns allow '{rule}' needs a reason after --"))
        else:
            allowed.setdefault(number, set()).add(rule)
    return allowed, errors


def violations(path, source):
    tree = ast.parse(source, filename=str(path))
    allowed, opt_out_errors = parse_opt_outs(source)
    for rule in RULES:
        for line, message in rule(path, tree):
            if rule.__name__ not in allowed.get(line, ()):
                yield f'{path}:{line}: {message}'
    for line, message in opt_out_errors:
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
