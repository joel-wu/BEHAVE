"""Finite string-state domains proved by the source's assignment closure.

This is a representation proof, not a reachability analysis. Every assignment
contributes, regardless of its guard; unknown writes poison dependent domains.
Only literals, conditionals and binding copies enter this finite lattice. The
ordinary lowerer still owns execution, effects, errors and all source goals.
"""

from __future__ import annotations

import ast
from collections import defaultdict, deque

from .extract import _assignment_targets, _state_targets, method_parameters


def finite_string_state_domains(model, resolver, initial, *, exclude=(), budget):
    candidates = {name for name, value in initial.items()
                  if type(value) is str and value and name not in exclude}
    if not candidates:
        return {}
    values = defaultdict(set)
    dependencies = defaultdict(set)
    bad = set()
    seen_targets = set()

    def key(node):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name) and node.value.id == "self"):
            return ("state", node.attr)
        if isinstance(node, ast.Name):
            owner = resolver.local_owner(resolver.scope_of(node), node.id)
            return None if owner is None else (id(owner), node.id)
        return None

    def expression(node, dest):
        budget.spend(node)
        if isinstance(node, ast.Constant) and type(node.value) is str:
            values[dest].add(node.value)
        elif isinstance(node, ast.IfExp):
            expression(node.body, dest)
            expression(node.orelse, dest)
        elif (origin := key(node)) is not None:
            dependencies[dest].add(origin)
        else:
            bad.add(dest)

    def assign(target, source):
        seen_targets.add(id(target))
        if (isinstance(target, (ast.Tuple, ast.List))
                and isinstance(source, (ast.Tuple, ast.List))
                and len(target.elts) == len(source.elts)
                and not any(isinstance(item, ast.Starred)
                            for item in (*target.elts, *source.elts))):
            for child, value in zip(target.elts, source.elts):
                assign(child, value)
            return
        dest = key(target)
        if dest is not None:
            values[dest]  # Retain even an unseeded copy cycle.
            expression(source, dest)
            return
        for name in _state_targets(target):
            bad.add(("state", name))
        for child in ast.walk(target):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                dest = key(child)
                if dest is not None:
                    bad.add(dest)

    # Construction is the initial-value authority. Source declarations alone
    # cannot prove that a conditional constructor actually created the field.
    for name, value in initial.items():
        dest = ("state", name)
        if name not in exclude and type(value) is str and value:
            values[dest].add(value)
        else:
            bad.add(dest)

    # Reuse the resolver's closed class namespace, which excludes instance
    # writes and definition-time effects. Do not infer constants by spelling.
    for name, value in resolver.class_tables.items():
        if type(value) is str:
            values[("state", name)].add(value)

    nodes = tuple(ast.walk(model.cls))
    for node in nodes:
        budget.spend(node)
        if isinstance(node, ast.FunctionDef):
            # A local later overwritten with a label is still unknown at entry.
            # Source lowering may support it, but this flow-insensitive proof
            # must not silently constrain a caller's parameter to that label.
            for name in method_parameters(node):
                bad.add((id(node), name))
        for target in _assignment_targets(node):
            rhs = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            assign(target, rhs)
        if isinstance(node, ast.NamedExpr):
            assign(node.target, node.value)
        if isinstance(node, ast.Delete):
            for target in node.targets:
                assign(target, None)
    for node in nodes:
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Store):
            if id(node) not in seen_targets and (dest := key(node)) is not None:
                bad.add(dest)

    users = defaultdict(set)
    for dest, origins in dependencies.items():
        for origin in origins:
            users[origin].add(dest)
            if origin not in values:
                bad.add(origin)
    queue = deque(bad)
    while queue:
        for dest in users[queue.popleft()]:
            if dest not in bad:
                bad.add(dest)
                queue.append(dest)

    queue = deque(dest for dest in values if dest not in bad)
    queued = set(queue)
    while queue:
        origin = queue.popleft()
        queued.remove(origin)
        for dest in users[origin]:
            budget.spend(model.cls)
            if dest in bad:
                continue
            added = values[origin] - values[dest]
            if added:
                values[dest].update(added)
                if dest not in queued:
                    queued.add(dest)
                    queue.append(dest)
    return {name: tuple(sorted(values[("state", name)]))
            for name in sorted(candidates)
            if ("state", name) not in bad and "" not in values[("state", name)]}
