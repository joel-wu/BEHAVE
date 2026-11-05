"""Source-owned proof of a missing concrete Behavior call interface.

An absent AST method alone is not proof: construction, inheritance or a
decorator can supply it. Reuse the closed module linker, then establish that
construction/reset only assign literal data to ordinary non-entry fields.
Other construction forms retain their original capability refusal.
"""

from __future__ import annotations

import ast

from ..core.failure import CandidateFault
from ..core.refusal import Refused


def missing_process_is_definite(tree: ast.Module, model: str) -> bool:
    from .module_linker import Linker, Rewrite
    from .source_bundle import BehaviorSourceBundle

    # This proof runs only on the missing-entry path, never on a valid model's
    # hot path. The same namespace rules cover raw and already linked sources.
    # Already linked sources use reserved names; do not guess around that
    # boundary here. Their original module can be checked before linking.
    try:
        bundle = BehaviorSourceBundle({"entry.py": ast.unparse(tree).encode()}, "entry.py", model)
    except ValueError:
        return False  # The proof's normalized source must fit the same source budget.
    linker = Linker(bundle)
    try:
        module = linker.read(bundle.entry)
        selected = module.bindings.get(model)
        if selected is None or selected.kind != "class" or module.initialization_effects:
            return False
        linker.selected = selected
        normalized = Rewrite(linker, module).visit(module.tree)
    except (Refused, CandidateFault):
        return False
    return plain_namespace_missing_process(normalized.body, model)


def plain_namespace_missing_process(body: list[ast.stmt], model: str) -> bool:
    """An already linked namespace with no definition-time code execution."""
    from .module_linker import definition_expressions

    klass = next(node for node in body if isinstance(node, ast.ClassDef) and node.name == model)
    if not plain_class_missing_process(klass):
        return False
    # An eager annotation C[T] can invoke C.__class_getitem__ and install
    # methods even without an ast.Call. Postponed annotations were normalized
    # to strings by the linker; they do not have this effect.
    return not any(isinstance(part, (ast.Call, ast.Subscript))
                   for node in body for part in definition_expressions(node))


def plain_class_missing_process(klass: ast.ClassDef) -> bool:
    """Called only after the linker has validated the complete namespace."""
    from .module_linker import static_value

    if klass.decorator_list:
        return False  # A standard constructor can call __post_init__ or factories.
    members = {node.name: node for node in klass.body if isinstance(node, ast.FunctionDef)}
    bindings = set(members)
    for node in klass.body:
        if isinstance(node, ast.Assign):
            bindings.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            bindings.add(node.target.id)
    if any(name == "process" or name.startswith("process_") for name in bindings):
        return False
    if any(name.startswith("__") and name.endswith("__") and name != "__init__"
           for name in bindings):
        return False
    for name in ("__init__", "reset"):
        function = members.get(name)
        if function is None:
            if name in bindings:
                return False
            continue
        if function.decorator_list or not function.args.args:
            return False
        receiver = function.args.args[0].arg
        for statement in function.body:
            if isinstance(statement, ast.Pass):
                continue
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
                continue
            if isinstance(statement, ast.Return) and (statement.value is None or
                    isinstance(statement.value, ast.Constant) and statement.value.value is None):
                continue
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)) or statement.value is None:
                return False
            targets = statement.targets if isinstance(statement, ast.Assign) else (statement.target,)
            if any(not isinstance(target, ast.Attribute)
                   or not isinstance(target.value, ast.Name) or target.value.id != receiver
                   or target.attr in bindings or target.attr.startswith("__")
                   or target.attr == "process" or target.attr.startswith("process_")
                   for target in targets):
                return False
            # Without names or calls, all operands are literal built-in data;
            # no overloaded operator or escaped instance can install a method.
            if not static_value(statement.value) or any(
                    isinstance(part, ast.Name) for part in ast.walk(statement.value)):
                return False
    return True
