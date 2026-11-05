"""Source-bound standard class decorators; no decorator execution or guessing."""

import ast

DATACLASS_HOOK = "_behave_dataclass_definition"


def observe_dataclasses(tree):
    """Retain bindings produced by the actual standard decorator, before use."""
    tables = {}

    def observe(decorator):
        def apply(klass):
            result = decorator(klass)
            tables[result] = dict(vars(result))
            return result
        return apply

    # Bind all definitions before mutating the decorator syntax.
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and imported_class_decorator(tree, node)]
    for node in classes:
        decorator = node.decorator_list[0]
        node.decorator_list = [ast.copy_location(ast.Call(
            func=ast.Name(id=DATACLASS_HOOK, ctx=ast.Load()),
            args=[decorator], keywords=[]), decorator)]
    ast.fix_missing_locations(tree)
    return observe, tables


def dataclass_declaration(node, resolve):
    """Recognize a standard constructor without replacing its Python semantics."""
    if len(node.decorator_list) != 1:
        return False
    decorator = node.decorator_list[0]
    if isinstance(decorator, ast.Call):
        if decorator.args or any(keyword.arg is None or not isinstance(keyword.value, ast.Constant)
                                 for keyword in decorator.keywords):
            return False
        decorator = decorator.func
    return resolve(decorator) == ("dataclasses", "dataclass")


def imported_bindings(tree, node):
    """Resolve the definition-time module namespace, including import aliases."""
    bindings = {}

    def resolve(expression):
        if isinstance(expression, ast.Name):
            return bindings.get(expression.id)
        if isinstance(expression, ast.Attribute):
            owner = resolve(expression.value)
            if owner is not None and owner[1] is None:
                return owner[0], expression.attr
        return None

    for statement in tree.body:
        if statement is node:
            break
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                bindings[alias.asname or alias.name.split(".")[0]] = (alias.name, None)
        elif isinstance(statement, ast.ImportFrom) and not statement.level:
            from .source_intrinsics import concrete_import_aliases

            for alias in concrete_import_aliases(statement):
                bindings[alias.asname or alias.name] = (statement.module, alias.name)
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1 \
                and isinstance(statement.targets[0], ast.Name):
            bindings[statement.targets[0].id] = resolve(statement.value)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.pop(statement.name, None)
        else:
            # A conditional or effectful binding is not a known library import.
            from .extract import module_bound_names

            for name in module_bound_names(ast.Module(body=[statement], type_ignores=[])):
                bindings.pop(name, None)
            for part in ast.walk(statement):
                if isinstance(part, (ast.Attribute, ast.Subscript)) and isinstance(part.ctx, (ast.Store, ast.Del)):
                    root = part
                    while isinstance(root, (ast.Attribute, ast.Subscript)):
                        root = root.value
                    if isinstance(root, ast.Name):
                        bindings.pop(root.id, None)
    return resolve


def imported_class_decorator(tree, node):
    return dataclass_declaration(node, imported_bindings(tree, node))


def dataclass_factories(tree, node):
    """Source expressions invoked by a standard generated constructor."""
    resolve = imported_bindings(tree, node)
    if not dataclass_declaration(node, resolve):
        return ()
    return tuple(keyword.value for member in node.body
                 if isinstance(member, (ast.Assign, ast.AnnAssign))
                 and isinstance(member.value, ast.Call)
                 and resolve(member.value.func) == ('dataclasses', 'field')
                 for keyword in member.value.keywords if keyword.arg == 'default_factory')
