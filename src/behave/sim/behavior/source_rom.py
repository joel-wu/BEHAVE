"""Finite, immutable module values used as Behavior ROMs.

These values are compile-time source objects, not SolverIR arrays.  Keeping a
typed recursive image lets the frontend preserve Python collection structure
while a lowering-only reference follows a finite path to the scalar payload it
eventually emits.  Only a closed set is admitted; an unsupported expression
loses the fact rather than being partially evaluated.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .budgets import ROM_EXPANSION_NODES
from ..symbolic.capabilities import SCALAR_BITS
from .source_values import SourceValueBudget

# Compatibility name retained for the extraction API.  The authority is the
# reported capability in ``budgets`` rather than an unreported local number.
DEFAULT_ROM_NODES = ROM_EXPANSION_NODES


@dataclass(frozen=True)
class FrozenValue:
    """A finite typed scalar, sequence, or mapping."""

    kind: str
    payload: object

    @property
    def is_collection(self):
        return self.kind in {"list", "tuple", "dict"}

    def mutable_identities(self, *, budget=None, node=None):
        """Mutable objects shared by frozen declaration images, without copying."""
        pending, seen, mutable = [self], set(), set()
        while pending:
            value = pending.pop()
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            if budget is not None:
                budget.spend(node)
            if value.kind in {'list', 'dict'}:
                mutable.add(identity)
            pending.extend(_children(value))
        return frozenset(mutable)

    def fits_expansion(self, max_nodes, *, budget=None, node=None):
        """Whether materialising this DAG as a value tree stays bounded.

        Source bindings can share a previously frozen collection many times.
        ``FrozenValue`` preserves that sharing, while SolverIR's finite ROM
        value is a tree and therefore duplicates it.  Count the expanded tree
        with saturated arithmetic before constructing it; otherwise a short
        chain such as ``v = [v, v]`` can allocate exponentially in the
        verifier while declaration analysis is still deciding admission.
        """
        if type(max_nodes) is not int or max_nodes < 1:
            raise ValueError("a frozen-value expansion bound must be positive")
        memo, active = {}, set()
        stack = [(self, False)]
        while stack:
            value, ready = stack.pop()
            if not isinstance(value, FrozenValue):
                return False
            identity = id(value)
            cached = memo.get(identity)
            if cached is not None and cached[0] is value:
                continue
            if ready:
                active.discard(identity)
                if budget is not None:
                    budget.spend(node)
                total = 1
                for child in _children(value):
                    cached = memo.get(id(child))
                    if cached is None or cached[0] is not child:
                        return False
                    total += cached[1]
                    if total > max_nodes:
                        total = max_nodes + 1
                        break
                memo[identity] = (value, total)
                continue
            if identity in active:
                return False
            active.add(identity)
            stack.append((value, True))
            for child in reversed(_children(value)):
                cached = memo.get(id(child))
                if cached is None or cached[0] is not child:
                    stack.append((child, False))

        return memo[id(self)][1] <= max_nodes

    def to_plain(self, *, budget=None, node=None):
        """Plain immutable tree consumed by the existing ROM selector."""
        return _project(
            self, preserve_carriers=False, budget=budget, node=node)

    def to_source_value(self, *, budget=None, node=None):
        """A fresh host value retaining the source collection carriers.

        The ROM selector intentionally normalizes both sequence kinds to an
        immutable tuple.  Source contracts cannot use that view: Python can
        observe ``list`` versus ``tuple`` through equality, concatenation and
        other operations before the selected payload reaches SolverIR.  Keep
        this second projection as contract evidence only; no returned mutable
        object is shared with execution.
        """
        return _project(
            self, preserve_carriers=True, budget=budget, node=node)


def _children(value):
    """Immediate frozen children, under the closed carrier grammar."""
    if value.kind in {"bool", "int", "str", "none"}:
        return ()
    if value.kind in {"list", "tuple"}:
        return tuple(value.payload)
    if value.kind == "dict":
        return tuple(child for pair in value.payload for child in pair)
    raise ValueError(f"unknown frozen-value kind {value.kind!r}")


def _project(root, *, preserve_carriers, budget=None, node=None):
    """Project one frozen DAG without recursing on its source depth.

    A module may build a deep value with a sequence of shallow assignments, so
    Python's parser nesting limit does not bound this graph.  Memoising also
    preserves source sharing; no returned mutable object is shared with a
    different call.
    """
    memo, active = {}, set()
    stack = [(root, False)]
    while stack:
        value, ready = stack.pop()
        if not isinstance(value, FrozenValue):
            raise TypeError("a frozen value contains a non-frozen child")
        identity = id(value)
        if identity in memo:
            continue
        if ready:
            active.discard(identity)
            if budget is not None:
                budget.spend(node)
            if value.kind in {"bool", "int", "str", "none"}:
                out = value.payload
            elif value.kind in {"list", "tuple"}:
                items = [memo[id(item)] for item in value.payload]
                out = (items if preserve_carriers and value.kind == "list"
                       else tuple(items))
            elif value.kind == "dict":
                out = {memo[id(key)]: memo[id(item)]
                       for key, item in value.payload}
            else:
                raise ValueError(f"unknown frozen-value kind {value.kind!r}")
            memo[identity] = out
            continue
        if identity in active:
            raise ValueError("a frozen value contains itself")
        active.add(identity)
        stack.append((value, True))
        for child in reversed(_children(value)):
            if id(child) not in memo:
                stack.append((child, False))
    return memo[id(root)]


class _Budget:
    __slots__ = ("left", "shared")

    def __init__(self, limit, shared=None):
        self.left = int(limit)
        if shared is not None and not isinstance(shared, SourceValueBudget):
            raise TypeError("a shared ROM budget must be a SourceValueBudget")
        self.shared = shared

    def take(self, node=None, amount=1):
        if type(amount) is not int or amount < 0:
            raise ValueError("finite ROM work must be nonnegative")
        self.left -= amount
        if self.left < 0:
            raise ValueError("finite ROM exceeds its node budget")
        if self.shared is not None:
            self.shared.spend(node, amount)


def _scalar(value):
    if type(value) is bool:
        return FrozenValue("bool", value)
    if type(value) is int:
        return FrozenValue("int", value)
    if type(value) is str:
        return FrozenValue("str", value)
    return None


def is_frozen_key(value):
    """Whether a frozen value is a hashable key in the closed grammar."""
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if item.kind == "tuple":
            if id(item) not in seen:
                seen.add(id(item))
                pending.extend(item.payload)
        elif item.kind not in {"bool", "int", "str"}:
            return False
    return True


def is_source_key(value):
    """The same key grammar for already projected closed source values."""
    pending, seen = [value], set()
    while pending:
        item = pending.pop()
        if type(item) is tuple:
            if id(item) not in seen:
                seen.add(id(item))
                pending.extend(item)
        elif type(item) not in (bool, int, str):
            return False
    return True


def freeze_value(value, *, max_nodes=DEFAULT_ROM_NODES):
    """Freeze one already evaluated value, or return ``None`` outside the set."""
    budget = _Budget(max_nodes)

    def visit(current, ancestors):
        budget.take()
        scalar = FrozenValue("none", None) if current is None else _scalar(current)
        if scalar is not None:
            return scalar
        kind = type(current)
        if kind not in (list, tuple, dict):
            return None
        identity = id(current)
        if identity in ancestors:
            return None
        below = ancestors | {identity}
        if kind in (list, tuple):
            items = tuple(visit(item, below) for item in current)
            if any(item is None for item in items):
                return None
            return FrozenValue(kind.__name__, items)
        pairs = []
        host_keys = []
        for key, item_value in current.items():
            frozen_key = visit(key, below)
            frozen_value = visit(item_value, below)
            if (frozen_key is None or frozen_value is None
                    or not is_frozen_key(frozen_key)):
                return None
            # Python may identify differently typed keys (True and 1).  The
            # typed frontend must not silently pick one interpretation.
            if any(type(old) is not type(key) and old == key
                   for old in host_keys):
                return None
            host_keys.append(key)
            pairs.append((frozen_key, frozen_value))
        return FrozenValue("dict", tuple(pairs))

    try:
        return visit(value, set())
    except ValueError:
        return None


def evaluate_frozen(node, value_of, *, function_of=None, builtin_of=None,
                    max_nodes=DEFAULT_ROM_NODES,
                    max_int_bits=SCALAR_BITS, max_shift=None, budget=None):
    """Evaluate finite source values under lexical value/call certificates.

    No submitted Python executes here. ``function_of`` resolves source
    helpers; ``builtin_of`` certifies unshadowed builtin call occurrences.
    Only locally assigned, fully interpreted helper paths establish values.
    Unsupported expressions lose their fact rather than guessing a value.
    """
    return _evaluate_frozen(
        node, value_of, function_of=function_of, builtin_of=builtin_of,
        max_nodes=max_nodes, max_int_bits=max_int_bits,
        max_shift=max_shift, budget=budget)


def update_frozen_table(node, name, value, value_of, *, function_of=None,
                        builtin_of=None, budget=None):
    """Prove finite scalar-cell initialization of one unaliased module list.

    The caller separately proves that no unmodelled use exposes or mutates the
    table. Reuse expression interpretation and work accounting; never execute
    submitted initialization in the host. Helper reads of loop-carried module
    bindings are declined until their changing namespace can be represented.
    """
    protected = {name} | {part.id for loop in ast.walk(node)
                          if isinstance(loop, ast.For)
                          for part in ast.walk(loop.target)
                          if isinstance(part, ast.Name)}

    def closed_function(call):
        fn = function_of(call) if function_of is not None else None
        if fn is not None and not any(isinstance(part, ast.Name) and part.id in protected
                                      for part in ast.walk(fn)):
            return fn
        return None

    return _evaluate_frozen(node, value_of, function_of=closed_function,
                            builtin_of=builtin_of, budget=budget, table=(name, value))


def _evaluate_frozen(node, value_of, *, function_of=None, builtin_of=None,
                     max_nodes=DEFAULT_ROM_NODES, max_int_bits=SCALAR_BITS,
                     max_shift=None, budget=None, table=None):
    """Evaluate the closed finite-ROM expression subset.

    ``value_of`` is the lexical declaration authority.  It may return a
    :class:`FrozenValue` already established by an earlier binding, or one of
    the admitted host scalars. ``function_of(call)`` may additionally return a
    lexically resolved source ``FunctionDef``. Its executed path must stay
    inside this interpreter's local-only grammar. Such a function is read on
    concrete frozen arguments; submitted Python is never executed here.
    ``builtin_of(call)`` certifies an unshadowed builtin at that occurrence.
    Without it, even familiar constructor spellings remain unknown.
    """
    budget = _Budget(max_nodes, budget)
    # One scalar-width authority, not a smaller historical shift ceiling.
    # An explicit caller limit is still honored; result magnitude is checked too.
    if max_shift is None:
        max_shift = max_int_bits
    if max_int_bits < 1 or max_shift < 0:
        raise ValueError("finite-ROM integer budgets must be nonnegative")

    def bounded_int(value):
        if type(value) is not int or value.bit_length() > max_int_bits:
            return None
        return FrozenValue("int", value)

    def integer_binop(op, left, right):
        if (left is None or right is None
                or left.kind != "int" or right.kind != "int"):
            return None
        a, b = left.payload, right.payload
        if isinstance(op, ast.Mult):
            # Check before multiplying: constructing the result is precisely
            # the operation whose size this source evaluator must bound.
            if a and b and a.bit_length() + b.bit_length() > max_int_bits + 1:
                return None
            value = a * b
        elif isinstance(op, ast.Add):
            value = a + b
        elif isinstance(op, ast.Sub):
            value = a - b
        elif isinstance(op, ast.FloorDiv):
            if b == 0:
                return None
            value = a // b
        elif isinstance(op, ast.Mod):
            if b == 0:
                return None
            value = a % b
        elif isinstance(op, ast.LShift):
            if b < 0 or b > max_shift or a.bit_length() + b > max_int_bits:
                return None
            value = a << b
        elif isinstance(op, ast.RShift):
            if b < 0 or b > max_shift:
                return None
            value = a >> b
        elif isinstance(op, ast.BitAnd):
            value = a & b
        elif isinstance(op, ast.BitOr):
            value = a | b
        elif isinstance(op, ast.BitXor):
            value = a ^ b
        else:
            return None
        return bounded_int(value)

    def truth(value):
        if value is None:
            return None
        if value.kind == "none":
            return False
        if value.kind == "bool":
            return value.payload
        if value.kind == "int":
            return value.payload != 0
        if value.kind == "str":
            return bool(value.payload)
        if value.kind in {"list", "tuple", "dict"}:
            return bool(value.payload)
        return None

    def compare(op, left, right):
        if left is None or right is None or left.kind != right.kind:
            return None
        a, b = left.payload, right.payload
        if left.kind not in {"bool", "int", "str"}:
            return None
        if isinstance(op, ast.Eq):
            answer = a == b
        elif isinstance(op, ast.NotEq):
            answer = a != b
        elif isinstance(op, ast.Lt):
            answer = a < b
        elif isinstance(op, ast.LtE):
            answer = a <= b
        elif isinstance(op, ast.Gt):
            answer = a > b
        elif isinstance(op, ast.GtE):
            answer = a >= b
        else:
            return None
        return FrozenValue("bool", answer)

    def bind_arguments(fn, call, local, active):
        args = fn.args
        if (args.posonlyargs or args.vararg is not None
                or args.kwarg is not None or args.kwonlyargs):
            return None
        positional = []
        for argument in call.args:
            if isinstance(argument, ast.Starred):
                return None
            value = visit(argument, local, active)
            if value is None:
                return None
            positional.append(value)
        names = [argument.arg for argument in args.args]
        if len(positional) > len(names):
            return None
        bound = dict(zip(names, positional))
        for keyword in call.keywords:
            if keyword.arg is None or keyword.arg not in names \
                    or keyword.arg in bound:
                return None
            value = visit(keyword.value, local, active)
            if value is None:
                return None
            bound[keyword.arg] = value
        first_default = len(names) - len(args.defaults)
        for index, name in enumerate(names):
            if name in bound:
                continue
            if index < first_default:
                return None
            default = args.defaults[index - first_default]
            # Python evaluates defaults when the def statement runs.  A
            # literal has the same value here; re-evaluating a name at the
            # later ROM binding could observe a different module binding.
            if not isinstance(default, ast.Constant):
                return None
            value = visit(default, {}, active)
            if value is None:
                return None
            bound[name] = value
        return bound

    unsupported = object()

    def builtin(call, local):
        return (builtin_of(call) if builtin_of is not None
                and isinstance(call.func, ast.Name)
                and call.func.id not in local else None)

    def iterable(current, local, active):
        # A range is an iterator source, not a tuple-valued ROM. Converting
        # it here preserves the distinction in expressions such as equality.
        if isinstance(current, ast.Call) and builtin(current, local) == 'range':
            if current.keywords or not 1 <= len(current.args) <= 3:
                return None
            args = [visit(arg, local, active) for arg in current.args]
            if any(arg is None or arg.kind not in {'int', 'bool'} for arg in args):
                return None
            try:
                values = range(*(arg.payload for arg in args))
                size = len(values)
            except (ValueError, OverflowError):
                return None
            budget.take(current, size)
            return tuple(FrozenValue('int', value) for value in values)
        value = visit(current, local, active)
        return (value.payload if value is not None
                and value.kind in {'list', 'tuple'} else None)

    def bind_target(target, value, local):
        budget.take(target)
        if isinstance(target, ast.Name):
            local[target.id] = value
            return True
        if (isinstance(target, (ast.Tuple, ast.List))
                and value.kind in {'list', 'tuple'}
                and len(target.elts) == len(value.payload)):
            return all(bind_target(child, item, local)
                       for child, item in zip(target.elts, value.payload))
        return False

    def comprehension(current, local, active):
        result = []

        def expand(index, scope, values=None):
            if index == len(current.generators):
                value = visit(current.elt, scope, active)
                if value is None:
                    return False
                result.append(value)
                return True
            gen = current.generators[index]
            if gen.is_async:
                return False
            if values is None:
                values = iterable(gen.iter, scope, active)
            if values is None:
                return False
            for value in values:
                budget.take(gen)
                inner = dict(scope)
                if not bind_target(gen.target, value, inner):
                    return False
                for guard in gen.ifs:
                    condition = truth(visit(guard, inner, active))
                    if condition is None:
                        return False
                    if not condition:
                        break
                else:
                    if not expand(index + 1, inner):
                        return False
            return True

        # Comprehension bindings never escape into the surrounding module or
        # helper. Reject reads of a not-yet-bound comprehension local instead
        # of accidentally resolving it to a same-spelled module constant.
        scope = dict(local)
        for gen in current.generators:
            for target in ast.walk(gen.target):
                if isinstance(target, ast.Name):
                    scope[target.id] = None
        first = current.generators[0] if current.generators else None
        if first is None or first.is_async:
            return None
        # Python evaluates the outer iterable outside the comprehension scope.
        values = iterable(first.iter, local, active)
        if values is None:
            return None
        return (FrozenValue('list', tuple(result))
                if expand(0, scope, values) else None)

    def execute(statements, local, active, *, initializing=False):
        """Interpret one concrete, side-effect-free function path."""
        for statement in statements:
            budget.take(statement)
            if isinstance(statement, ast.Pass):
                continue
            if isinstance(statement, ast.Assert):
                if truth(visit(statement.test, local, active)) is not True:
                    return unsupported
                continue
            if isinstance(statement, ast.Return):
                if initializing:
                    return unsupported
                value = (None if statement.value is None else
                         visit(statement.value, local, active))
                return unsupported if value is None else ("return", value)
            if isinstance(statement, ast.Assign):
                value = visit(statement.value, local, active)
                if initializing:
                    if value is None or len(statement.targets) != 1:
                        return unsupported
                    target = statement.targets[0]
                    if (not isinstance(target, ast.Subscript)
                            or not isinstance(target.value, ast.Name)
                            or target.value.id != table[0]):
                        return unsupported
                    cells = local[table[0]]
                    index = visit(target.slice, local, active)
                    if (cells.kind != 'list' or index is None
                            or index.kind not in {'int', 'bool'}
                            or not -len(cells.payload) <= index.payload < len(cells.payload)
                            or value.is_collection):
                        return unsupported
                    budget.take(statement, len(cells.payload))
                    updated = list(cells.payload)
                    updated[index.payload] = value
                    local[table[0]] = FrozenValue('list', tuple(updated))
                    continue
                if value is None or not statement.targets \
                        or any(not isinstance(target, ast.Name)
                               for target in statement.targets):
                    return unsupported
                for target in statement.targets:
                    local[target.id] = value
                continue
            if isinstance(statement, ast.AnnAssign):
                if (initializing or not isinstance(statement.target, ast.Name)
                        or statement.value is None):
                    return unsupported
                value = visit(statement.value, local, active)
                if value is None:
                    return unsupported
                local[statement.target.id] = value
                continue
            if isinstance(statement, ast.AugAssign):
                if initializing or not isinstance(statement.target, ast.Name) \
                        or statement.target.id not in local:
                    return unsupported
                right = visit(statement.value, local, active)
                value = integer_binop(
                    statement.op, local[statement.target.id], right)
                if value is None:
                    return unsupported
                local[statement.target.id] = value
                continue
            if isinstance(statement, ast.If):
                condition = truth(visit(statement.test, local, active))
                if condition is None:
                    return unsupported
                result = execute(
                    statement.body if condition else statement.orelse,
                    local, active, initializing=initializing)
                if result is unsupported or result is not None:
                    return result
                continue
            if isinstance(statement, ast.Expr):
                if visit(statement.value, local, active) is None:
                    return unsupported
                continue
            if initializing and isinstance(statement, ast.For):
                if (statement.orelse or not isinstance(statement.target, ast.Name)
                        or statement.target.id == table[0]):
                    return unsupported
                values = iterable(statement.iter, local, active)
                if values is None:
                    return unsupported
                for value in values:
                    budget.take(statement)
                    local[statement.target.id] = value
                    if execute(statement.body, local, active, initializing=True) is not None:
                        return unsupported
                continue
            return unsupported
        return None

    def visit(current, local=None, active=frozenset()):
        local = {} if local is None else local
        budget.take(current)
        if isinstance(current, ast.Constant):
            if current.value is None:
                return FrozenValue("none", None)
            scalar = _scalar(current.value)
            return (bounded_int(scalar.payload)
                    if scalar is not None and scalar.kind == "int"
                    else scalar)
        if isinstance(current, ast.Name) and current.id in local:
            return local[current.id]
        # A compound expression may contain a comprehension/helper local.
        # Do not let an outer declaration fact fold across that binding.
        local_read = False
        if local:
            for child in ast.walk(current):
                budget.take(child)
                if isinstance(child, ast.Name) and child.id in local:
                    local_read = True
                    break
        known = None if local_read else value_of(current)
        if isinstance(known, FrozenValue):
            return (bounded_int(known.payload) if known.kind == "int"
                    else known)
        if type(known) in (bool, int, str):
            return bounded_int(known) if type(known) is int else _scalar(known)
        if isinstance(current, (ast.List, ast.Tuple)):
            items = tuple(visit(item, local, active) for item in current.elts)
            if any(item is None for item in items):
                return None
            return FrozenValue(
                "list" if isinstance(current, ast.List) else "tuple", items)
        if isinstance(current, ast.ListComp):
            return comprehension(current, local, active)
        if isinstance(current, ast.Dict):
            pairs = []
            host_keys = []
            for key_node, value_node in zip(current.keys, current.values):
                if key_node is None:
                    return None
                key = visit(key_node, local, active)
                value = visit(value_node, local, active)
                if (key is None or value is None
                        or not is_frozen_key(key)):
                    return None
                plain = key.to_plain()
                if any(type(old) is not type(plain) and old == plain
                       for old in host_keys):
                    return None
                host_keys.append(plain)
                # Match dict-display execution: a later equal key replaces
                # the value.  Typed collisions were refused above.
                replaced = False
                for index, (old, _old_value) in enumerate(pairs):
                    if old.to_plain() == plain:
                        pairs[index] = (old, value)
                        replaced = True
                        break
                if not replaced:
                    pairs.append((key, value))
            return FrozenValue("dict", tuple(pairs))
        if isinstance(current, ast.UnaryOp):
            value = visit(current.operand, local, active)
            if isinstance(current.op, ast.Not):
                answer = truth(value)
                return (None if answer is None else
                        FrozenValue("bool", not answer))
            if value is None or value.kind != "int":
                return None
            if isinstance(current.op, ast.USub):
                return bounded_int(-value.payload)
            if isinstance(current.op, ast.UAdd):
                return bounded_int(value.payload)
            if isinstance(current.op, ast.Invert):
                return bounded_int(~value.payload)
            return None
        if isinstance(current, ast.BinOp):
            left = visit(current.left, local, active)
            right = visit(current.right, local, active)
            if isinstance(current.op, ast.Mult):
                sequence, count = ((left, right) if left is not None
                                   and left.kind in {"list", "tuple"}
                                   else (right, left))
                if (sequence is not None and count is not None
                        and sequence.kind in {"list", "tuple"}
                        and count.kind == "int"):
                    times = max(0, count.payload)
                    expanded = len(sequence.payload) * times
                    if expanded > budget.left:
                        return None
                    budget.take(current, expanded)
                    return FrozenValue(
                        sequence.kind, sequence.payload * times)
            if (isinstance(current.op, ast.Add)
                    and left is not None and right is not None
                    and left.kind == right.kind
                    and left.kind in {"list", "tuple"}):
                expanded = len(left.payload) + len(right.payload)
                if expanded > budget.left:
                    return None
                budget.take(current, expanded)
                return FrozenValue(
                    left.kind, left.payload + right.payload)
            return integer_binop(current.op, left, right)
        if isinstance(current, ast.BoolOp):
            if not current.values:
                return None
            value = visit(current.values[0], local, active)
            for operand in current.values[1:]:
                condition = truth(value)
                if condition is None:
                    return None
                if ((isinstance(current.op, ast.And) and not condition)
                        or (isinstance(current.op, ast.Or) and condition)):
                    return value
                value = visit(operand, local, active)
            return value
        if isinstance(current, ast.IfExp):
            condition = truth(visit(current.test, local, active))
            if condition is None:
                return None
            return visit(
                current.body if condition else current.orelse, local, active)
        if isinstance(current, ast.Compare):
            left = visit(current.left, local, active)
            for operator, comparator in zip(current.ops, current.comparators):
                right = visit(comparator, local, active)
                result = compare(operator, left, right)
                if result is None:
                    return None
                if not result.payload:
                    return result
                left = right
            return FrozenValue("bool", True)
        if isinstance(current, ast.Subscript):
            base = visit(current.value, local, active)
            key = visit(current.slice, local, active)
            if base is None or key is None:
                return None
            if base.kind in {"list", "tuple"} and key.kind == "int":
                index = key.payload
                if index < 0:
                    index += len(base.payload)
                return (base.payload[index]
                        if 0 <= index < len(base.payload) else None)
            if base.kind == "dict" and is_frozen_key(key):
                for candidate, value in base.payload:
                    if candidate.to_source_value() == key.to_source_value():
                        return value
            return None
        if (isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute)
                and current.func.attr == 'bit_length' and not current.args
                and not current.keywords):
            receiver = visit(current.func.value, local, active)
            if receiver is not None and receiver.kind in {'int', 'bool'}:
                return bounded_int(receiver.payload.bit_length())
            return None
        if isinstance(current, ast.Call) and builtin(current, local) in {'list', 'tuple'}:
            if current.keywords or len(current.args) > 1:
                return None
            values = (() if not current.args else
                      iterable(current.args[0], local, active))
            if values is None:
                return None
            budget.take(current, len(values))
            return FrozenValue(builtin(current, local), tuple(values))
        if isinstance(current, ast.Call) and function_of is not None:
            if isinstance(current.func, ast.Name) and current.func.id in local:
                return None
            fn = function_of(current)
            if not isinstance(fn, ast.FunctionDef) or id(fn) in active:
                return None
            arguments = bind_arguments(fn, current, local, active)
            if arguments is None:
                return None
            result = execute(fn.body, arguments, active | {id(fn)})
            if not isinstance(result, tuple) or result[0] != "return":
                return None
            return result[1]
        return None

    try:
        if table is not None:
            local = {table[0]: table[1]}
            result = execute([node], local, frozenset(), initializing=True)
            return local[table[0]] if result is None else None
        return visit(node)
    except (RecursionError, ValueError):
        return None


__all__ = ["DEFAULT_ROM_NODES", "FrozenValue", "evaluate_frozen",
           "freeze_value", "update_frozen_table"]
