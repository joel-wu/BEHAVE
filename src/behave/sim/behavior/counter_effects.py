"""Project helper effects onto one literal-indexed integer state cell.

This proof interpreter retains affine values relative to the incoming counter.
It does not rewrite executable ASTs. All normal exits must agree on the change;
unknown selectors, escaping containers and conditional changes refuse the proof.
Loops inside helpers must preserve this counter; their own termination remains
the ordinary source planner's responsibility.
"""

import ast
from dataclasses import dataclass, field

from .source_calls import CallBindingError, bind_helper_arguments, resolve_helper
from .while_plan import WhilePlanError


@dataclass
class _Facts:
    counter: tuple[int, int] = (1, 0)
    values: dict = field(default_factory=dict)

    def copy(self):
        return _Facts(self.counter, dict(self.values))


def _merge(states):
    first, *rest = states
    result = first.copy()
    for state in rest:
        if state.counter != result.counter:
            raise WhilePlanError("helper has a conditional counter effect")
        result.values = {name: value for name, value in result.values.items()
                         if state.values.get(name) == value}
    return result


def state_cell(node):
    """A selected state location, independent of a changing source index."""
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name) and node.value.value.id == 'self'
            and isinstance(node.slice, ast.Constant) and type(node.slice.value) is int
            and node.slice.value >= 0):
        return node.value.attr, node.slice.value
    return None


class CounterEffects:
    def __init__(self, location, resolver):
        self.location = location
        self.resolver = resolver
        self.active = set()

    def delta(self, node, owner):
        if isinstance(node, ast.expr):
            facts = _Facts()
            self._expr(node, facts, owner)
            result = facts.counter
        else:
            exits = self._block([node], _Facts(), owner)
            result = _merge([state for _kind, state in exits]).counter
        if result[0] != 1:
            raise WhilePlanError("helper does not preserve an additive counter relation")
        return result[1]

    def _selected(self, node, facts, owner):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name) and node.value.value.id == 'self'
                and node.value.attr == self.location[0]):
            index = self._expr(node.slice, facts, owner)
            if index is None or index[0] != 0 or index[1] < 0:
                raise WhilePlanError("helper state-cell selector is not a fixed nonnegative index")
            return index[1] == self.location[1]
        return False

    def _expr(self, node, facts, owner):
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return (0, int(node.value)) if type(node.value) in (int, bool) else None
        if isinstance(node, ast.Name):
            return facts.values.get(node.id)
        if isinstance(node, (ast.NamedExpr, ast.Lambda, ast.ListComp,
                             ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            raise WhilePlanError("counter effect has an unrepresented expression scope")
        if self._selected(node, facts, owner):
            return facts.counter
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and node.value.attr == self.location[0]
                and isinstance(node.value.value, ast.Name) and node.value.value.id == 'self'):
            return None  # a proved distinct cell, not an escaping container
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == 'self' and node.attr == self.location[0]):
            raise WhilePlanError("the counter's container escapes its indexed access")
        if isinstance(node, ast.Call):
            args = [self._expr(argument, facts, owner) for argument in node.args]
            keywords = [(item.arg, self._expr(item.value, facts, owner)) for item in node.keywords]
            if any(isinstance(argument, ast.Starred) for argument in node.args):
                raise WhilePlanError("counter effect has unpacked helper arguments")
            target = resolve_helper(self.resolver, owner, node) if self.resolver else None
            if target is None:
                if self.resolver and self.resolver.call_has_no_external_effect(owner, node):
                    return None
                raise WhilePlanError("counter effect has an unresolved call")
            if id(target) not in self.resolver._method_ids or target in self.active:
                raise WhilePlanError("counter effect needs a nonrecursive model method")

            def default(name, expression):
                frozen = self.resolver.helper_default(target, name, expression)
                return ((0, int(frozen.value)) if frozen is not None
                        and type(frozen.value) in (int, bool) else None)

            try:
                params, values = bind_helper_arguments(
                    target, args, keywords, receiver=True, default=default)
            except CallBindingError as exc:
                raise WhilePlanError(str(exc)) from None
            self.active.add(target)
            try:
                exits = self._block(target.body, _Facts(facts.counter, dict(zip(params, values))), target)
            finally:
                self.active.remove(target)
            facts.counter = _merge([state for _kind, state in exits]).counter
            return None
        if isinstance(node, ast.BinOp):
            left = self._expr(node.left, facts, owner)
            right = self._expr(node.right, facts, owner)
            if left is not None and right is not None:
                if isinstance(node.op, ast.Add):
                    return left[0] + right[0], left[1] + right[1]
                if isinstance(node.op, ast.Sub):
                    return left[0] - right[0], left[1] - right[1]
        else:
            before = facts.counter
            for child in ast.iter_child_nodes(node):
                self._expr(child, facts, owner)
                if isinstance(node, (ast.BoolOp, ast.IfExp, ast.Compare)) and facts.counter != before:
                    raise WhilePlanError("conditional expression changes the counter")
        return None

    def _store(self, target, value, facts, owner):
        if self._selected(target, facts, owner):
            if value is None:
                raise WhilePlanError("counter store has no affine value")
            facts.counter = value
        elif isinstance(target, ast.Name):
            facts.values[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                self._store(child, None, facts, owner)
        else:
            self._expr(target, facts, owner)

    def _block(self, statements, entry, owner):
        live = entry
        stopped = []
        for stmt in statements:
            if live is None:
                break
            facts = live
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                value = self._expr(stmt.value, facts, owner)
                for target in stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]:
                    self._store(target, value, facts, owner)
            elif isinstance(stmt, ast.AugAssign):
                left = self._expr(stmt.target, facts, owner)
                right = self._expr(stmt.value, facts, owner)
                value = None
                if left is not None and right is not None and isinstance(stmt.op, (ast.Add, ast.Sub)):
                    sign = 1 if isinstance(stmt.op, ast.Add) else -1
                    value = left[0] + sign * right[0], left[1] + sign * right[1]
                self._store(stmt.target, value, facts, owner)
            elif isinstance(stmt, ast.If):
                self._expr(stmt.test, facts, owner)
                paths = self._block(stmt.body, facts.copy(), owner) + self._block(stmt.orelse, facts.copy(), owner)
                normal = [state for kind, state in paths if kind == 'normal']
                stopped.extend((kind, state) for kind, state in paths if kind != 'normal')
                live = _merge(normal) if normal else None
            elif isinstance(stmt, (ast.For, ast.While)):
                initial = facts.counter
                loop = facts.copy()
                # No first-iteration constant may certify later selectors.
                for part in ast.walk(stmt):
                    if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store):
                        loop.values.pop(part.id, None)
                self._expr(stmt.iter if isinstance(stmt, ast.For) else stmt.test, loop, owner)
                paths = self._block(stmt.body, loop, owner)
                if any(state.counter != initial for _kind, state in paths):
                    raise WhilePlanError("nested helper loop changes the counter")
                stopped.extend((kind, state) for kind, state in paths if kind == 'return')
                live = _merge([facts, *[state for kind, state in paths if kind != 'return']])
                paths = self._block(stmt.orelse, live.copy(), owner)
                if any(state.counter != initial for _kind, state in paths):
                    raise WhilePlanError("nested helper loop else changes the counter")
                live = _merge([live, *[state for kind, state in paths if kind == 'normal']])
                stopped.extend((kind, state) for kind, state in paths if kind != 'normal')
            elif isinstance(stmt, ast.Return):
                self._expr(stmt.value, facts, owner)
                stopped.append(('return', facts))
                live = None
            elif isinstance(stmt, (ast.Break, ast.Continue)):
                stopped.append(('break' if isinstance(stmt, ast.Break) else 'continue', facts))
                live = None
            elif isinstance(stmt, ast.Expr):
                self._expr(stmt.value, facts, owner)
            elif not isinstance(stmt, ast.Pass):
                raise WhilePlanError("counter effect has an unrepresented control statement")
        if live is not None:
            stopped.append(('normal', live))
        return stopped
