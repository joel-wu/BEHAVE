"""Exact affine guard facts carried along an acyclic source CFG.

Intervals alone forget that ``a <= b`` implies ``b - a >= 0``. These facts
retain that relation until an operand is written. They only refine a computed
interval; the independent symbolic position-closure check is unchanged.
"""

from collections.abc import Mapping
from types import MappingProxyType

from ..core.frozen import Frozen
from . import ir
from .ranges import (Bounds, affine_form, truth_preserving_operand,
                     value_preserving_operand)


class _Facts(Frozen, Mapping):
    """Owned read-only facts with a reusable operand footprint.

    Guarded facts cross many unrelated assignments. Retaining this value on
    those writes avoids rescanning every affine key and copying its mapping.
    Public mutable input mappings are always copied before being retained.
    """

    __slots__ = ("_values", "operands")

    def __init__(self, values):
        self._values = MappingProxyType(dict(values))
        self.operands = frozenset(name for key in self._values for name, _ in key)
        self._freeze()

    def __reduce__(self):
        return type(self), (dict(self._values),)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, key):
        return self._values[key]

    def get(self, key, default=None):
        return self._values.get(key, default)

    def items(self):
        return self._values.items()


def _form(expression, *, env=None):
    affine = affine_form(expression, env=env)
    if affine is None:
        return None
    return _normalized(affine)


def _normalized(affine):
    constant, coefficients = affine
    ordered = tuple(sorted(coefficients.items()))
    if not ordered:
        return None
    sign = 1 if ordered[0][1] > 0 else -1
    return constant, sign, tuple((name, sign * value) for name, value in ordered)


def _shift(value, offset):
    return None if value is None else value + offset


def _negate(bounds):
    return Bounds(None if bounds.hi is None else -bounds.hi,
                  None if bounds.lo is None else -bounds.lo)


def _meet(facts, key, bound):
    previous = facts.get(key)
    narrowed = bound if previous is None else previous.meet(bound)
    # An inconsistent branch denotes no executions. Forgetting that fact is
    # conservative; infeasibility remains the ordinary transfer's decision.
    if narrowed is None:
        facts.pop(key, None)
    else:
        facts[key] = narrowed


def assume(facts, expression, truth, *, env=None):
    """Add only comparisons necessarily true on the selected edge."""
    result = dict(facts)
    pending = [(expression, truth)]
    while pending:
        expr, wanted = pending.pop()
        converted = truth_preserving_operand(expr, {} if env is None else env)
        if converted is not expr:
            pending.append((converted, wanted))
            continue
        if not isinstance(expr, ir.Op):
            continue
        if expr.op == "not" and len(expr.args) == 1:
            pending.append((expr.args[0], not wanted))
            continue
        if expr.op == "truth" and len(expr.args) == 1:
            # Only Boolean structure below this node is recognized here.
            pending.append((expr.args[0], wanted))
            continue
        if (expr.op == "land" and wanted) or (expr.op == "lor" and not wanted):
            pending.extend((part, wanted) for part in expr.args)
            continue
        if len(expr.args) != 2 or expr.op not in {"lt", "le", "gt", "ge", "eq", "ne"}:
            continue
        op = expr.op if wanted else {
            "lt": "ge", "le": "gt", "gt": "le", "ge": "lt", "eq": "ne", "ne": "eq",
        }[expr.op]
        if op == "ne":
            continue
        operands = (expr.args if env is None else tuple(
            value_preserving_operand(value, env) for value in expr.args))
        difference = _form(ir.Op("sub", *operands), env=env)
        if difference is None:
            continue
        constant, sign, key = difference
        bound = {"lt": Bounds(None, -1), "le": Bounds(None, 0),
                 "gt": Bounds(1, None), "ge": Bounds(0, None),
                 "eq": Bounds(0, 0)}[op]
        shifted = Bounds(_shift(bound.lo, -constant), _shift(bound.hi, -constant))
        _meet(result, key, shifted if sign == 1 else _negate(shifted))
    return _Facts(result)


def join(left, right):
    """A join keeps only facts true in both predecessor domains."""
    # Unrelated branches share the very same immutable conditional facts.
    # Rebuilding their intersection visits every old relation at every join.
    # Mutable caller mappings must still be detached below.
    if left is right and isinstance(left, _Facts):
        return left
    return _Facts((key, bound.join(right[key])) for key, bound in left.items() if key in right)


def conjoin(left, right):
    """Combine simultaneous facts; None denotes contradictory bounds."""
    result = dict(left)
    for key, bound in right.items():
        if key in result:
            bound = result[key].meet(bound)
            if bound is None:
                return None
        result[key] = bound
    return _Facts(result)


def refine_env(env, facts):
    """Apply guard bounds, including copies still equal across source blocks."""
    result = dict(env)
    parents = {}

    def root(name):
        while parents[name] != name:
            parents[name] = parents[parents[name]]
            name = parents[name]
        return name

    for key in facts:
        if len(key) == 1:
            name, _coefficient = key[0]
            value = refine(ir.Var(name), result.get(name), facts)
            if value is not None:
                result[name] = value
        elif (len(key) == 2 and key[0][1] == 1 and key[1][1] == -1
              and facts[key] == Bounds(0, 0)):
            left, right = key[0][0], key[1][0]
            parents.setdefault(left, left)
            parents.setdefault(right, right)
            parents[root(left)] = root(right)
    groups = {}
    for name in parents:
        groups.setdefault(root(name), []).append(name)
    for names in groups.values():
        bound = None
        for name in names:
            value = result.get(name)
            if value is not None:
                bound = Bounds.of(value) if bound is None else bound.meet(Bounds.of(value))
                if bound is None:
                    return None
        if bound is not None and (finite := bound.finite()) is not None:
            for name in names:
                previous = result.get(name)
                # Keep any cell-product precision when its hull is unchanged.
                if previous is None or (previous.lo, previous.hi) != (finite.lo, finite.hi):
                    result[name] = finite
    return result


def bound(expression, facts, *, env=None):
    """Translate a live affine fact before an assignment changes its operands."""
    if not facts:
        return None
    affine = _form(expression, env=env)
    if affine is None:
        return None
    constant, sign, key = affine
    bound = facts.get(key)
    if bound is None:
        return None
    signed = bound if sign == 1 else _negate(bound)
    return Bounds(_shift(signed.lo, constant), _shift(signed.hi, constant))


def refine(expression, interval, facts):
    """Intersect an assignment's interval with its live affine guard fact."""
    translated = bound(expression, facts)
    if interval is None or translated is None:
        return interval
    narrowed = Bounds.of(interval).meet(translated)
    return interval if narrowed is None else narrowed.finite()


def quotient(target, expression, env):
    """Python floor division establishes an exact affine remainder bound.

    Only a proved singleton, nonzero divisor is linear. No bound is invented
    for a varying divisor, truncating division or an overwritten dividend.
    The ordinary write invalidation and predecessor join own its lifetime.
    """
    if not isinstance(expression, ir.Op) or expression.op != 'fdiv' or len(expression.args) != 2:
        return None
    numerator, denominator = (affine_form(part, env=env) for part in expression.args)
    if numerator is None or denominator is None or denominator[1] or not denominator[0]:
        return None
    constant, coefficients = numerator
    if target in coefficients:
        return None
    divisor = denominator[0]
    coefficients = {**coefficients, target: -divisor}
    constant, sign, key = _normalized((constant, coefficients))
    remainder = Bounds(0, divisor - 1) if divisor > 0 else Bounds(divisor + 1, 0)
    shifted = Bounds(remainder.lo - constant, remainder.hi - constant)
    return _Facts({key: shifted if sign == 1 else _negate(shifted)})


def forget(facts, written):
    """A guard about old values says nothing about their replacements."""
    if not isinstance(facts, _Facts):
        facts = _Facts(facts)
    if facts.operands.isdisjoint(written):
        return facts
    return _Facts((key, bound) for key, bound in facts.items()
                  if not any(name in written for name, _coefficient in key))


def copied_name(expression, env):
    """Recognize copies through conversions proved to preserve this value.

    Use the pre-assignment environment. A truncation or sign reinterpretation
    that can change any value is not equality, even if its widths match.
    """
    expression = value_preserving_operand(expression, env)
    return expression.name if isinstance(expression, ir.Var) else None


def copy(facts, target, source):
    """A completed copy equates current values, until either is overwritten."""
    if target == source:
        return facts
    left, right = sorted((target, source))
    return _Facts({**facts, ((left, 1), (right, -1)): Bounds(0, 0)})


def join_conditions(left, right):
    """Keep implications established on every incoming path."""
    return {markers: join(bounds, right[markers])
            for markers, bounds in left.items() if markers in right}


def forget_conditions(conditions, written):
    """A changed marker no longer witnesses its earlier joint replacement."""
    return {markers: forget(bounds, written)
            for markers, bounds in conditions.items()
            if not any(name in written for name, _initial in markers)}


def refine_conditions(env, conditions, facts=()):
    """Apply a joint-write invariant only after its entry case is excluded."""
    facts = dict(facts)
    for markers, bounds in conditions.items():
        if any(name in env and not env[name].lo <= initial <= env[name].hi
               for name, initial in markers):
            facts = conjoin(facts, bounds)
            if facts is None:
                return None
    return refine_env(env, facts) if facts else env
