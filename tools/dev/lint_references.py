"""Check project module attributes without importing or executing project code.

Only module names are followed, not attributes of classes or returned objects.
Like the import-boundary lint, this checks both sides of conditional imports.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module_name(path):
    parts = list(path.with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def import_names(node, module):
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name
            # The installed launcher and guest probes import installer siblings.
            if name in ('install', 'payload', 'storage', 'wizard'):
                name = 'installer.' + name
            yield (
                alias.asname or alias.name.split('.')[0],
                (name if alias.asname or name != alias.name else name.split('.')[0]),
            )
    elif isinstance(node, ast.ImportFrom):
        base = node.module or ''
        if node.level:
            base = '.'.join(module.split('.')[: -node.level] + ([base] if base else []))
        for alias in node.names:
            yield alias.asname or alias.name, base + '.' + alias.name


class Bindings(ast.NodeVisitor):
    """Collect one lexical scope; nested functions have their own bindings."""

    def __init__(self, module):
        self.module = module
        self.names = {}

    def visit_Import(self, node):
        self.names.update(import_names(node, self.module))

    visit_ImportFrom = visit_Import

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.names[node.id] = None

    def visit_FunctionDef(self, node):
        self.names[node.name] = None

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass


def bindings(node, module):
    collector = Bindings(module)
    for statement in node.body:
        collector.visit(statement)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for argument in ast.walk(node.args):
            if isinstance(argument, ast.arg):
                collector.names[argument.arg] = None
    return collector.names


class References:
    def __init__(self, sources):
        self.trees = {name: ast.parse(source) for name, source in sources.items()}
        self.exports = {name: bindings(tree, name) for name, tree in self.trees.items()}
        # Include namespace packages, whose children can be imported explicitly.
        self.modules = {name.rsplit('.', count)[0] for name in sources for count in range(name.count('.') + 1)}

    def resolve(self, name, seen=()):
        if name in seen:
            return None
        if name in self.modules:
            return name
        parent, _, attribute = name.rpartition('.')
        if parent not in self.exports:
            return None
        if attribute not in self.exports[parent]:
            raise ValueError(f'{name} does not exist')
        target = self.exports[parent][attribute]
        return self.resolve(target, (*seen, name)) if target else None

    def check(self, module):
        errors = set()

        def check_chain(node, target, attributes):
            try:
                target = self.resolve(target)
                for attribute in attributes:
                    if target is None:
                        break
                    target = self.resolve(target + '.' + attribute)
            except ValueError as error:
                errors.add((node.lineno, str(error)))

        def walk(node, aliases):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                aliases = aliases | bindings(node, module)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                attributes = []
                base = node
                while isinstance(base, ast.Attribute):
                    attributes.insert(0, base.attr)
                    base = base.value
                if isinstance(base, ast.Name) and aliases.get(base.id):
                    check_chain(node, aliases[base.id], attributes)
            # patch('tools.module.name') is a runtime attribute reference too.
            if isinstance(node, ast.Call) and node.args:
                function = node.func
                patch = (isinstance(function, ast.Name) and function.id == 'patch') or (
                    isinstance(function, ast.Attribute) and function.attr == 'patch'
                )
                argument = node.args[0]
                if patch and isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    parts = argument.value.split('.')
                    if parts[0] in ('installer', 'tools', 'tests'):
                        check_chain(node, parts[0], parts[1:])
            for child in ast.iter_child_nodes(node):
                walk(child, aliases)

        tree = self.trees[module]
        guarded = False
        for statement in tree.body:
            if isinstance(statement, ast.If) and ast.dump(statement.test) == ast.dump(
                ast.parse("__name__ == '__main__'", mode='eval').body
            ):
                guarded = True
            elif guarded and isinstance(statement, (ast.Import, ast.ImportFrom)):
                errors.add((statement.lineno, 'import follows __main__ guard; move it before CLI execution'))
        walk(tree, {})
        return sorted(errors)


def main():
    paths = sorted(path for directory in ('installer', 'tools', 'tests') for path in (ROOT / directory).rglob('*.py'))
    sources = {module_name(path.relative_to(ROOT)): path.read_text() for path in paths}
    references = References(sources)
    errors = [
        f'{path.relative_to(ROOT)}:{line}: {message}'
        for path in paths
        for line, message in references.check(module_name(path.relative_to(ROOT)))
    ]
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Project module references resolve in {len(paths)} files')


if __name__ == '__main__':
    main()
