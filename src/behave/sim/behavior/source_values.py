"""Python expression planning over the shared finite value contracts.

The frontend needs to answer one structural question before it emits IR:
whether an expression is a scalar, a fixed recursive value, or a bounded
sequence whose logical length is decided at run time.  Treating an unknown
name or call as a scalar is unsound, while using independent shape walkers in
the planner and lowerer lets them disagree.  This module owns that answer.

It deliberately does not resolve names or calls.  Callers supply those
lexical decisions. The contract algebra belongs to ``symbolic.value_contract``;
this module retains its old exports for saved records and external callers.
"""

from __future__ import annotations

import ast
from copy import copy as _shallow_copy

from . import budgets
from ..symbolic.value_contract import (
    INCOMPATIBLE,
    NONE,
    SCALAR,
    UNKNOWN,
    ValueContract,
    _cardinalities as _cardinalities,
    _element_join,
    contract_at_record_path,
    contract_contains,
    contract_is_deeply_immutable,
    contract_of_collection_elements,
    contract_of_collection_lookup,
    contract_of_value,
    contract_with_record_path,
    contracts_may_overlap,
    finite_cardinality_add,
    finite_cardinality_product,
    fixed_positional_contracts,
    join_contracts,
    representation_value,
    sequence_elements_are_deeply_immutable,
)
from .source_bytes import BYTE_CARRIERS, is_from_bytes, is_to_bytes, to_bytes_arguments
from .source_calls import (
    has_plain_positional_arity,
    record_copy_source,
    record_items,
    record_key,
)
from .source_intrinsics import binary_digit_count

__all__ = [
    "INCOMPATIBLE",
    "NONE",
    "SCALAR",
    "UNKNOWN",
    "SourceValueBudget",
    "SourceValueBudgetExceeded",
    "ValueContract",
    "contract_at_record_path",
    "contract_contains",
    "contract_is_deeply_immutable",
    "contract_of_collection_elements",
    "contract_of_collection_lookup",
    "contract_of_expression",
    "contract_of_value",
    "contract_with_record_path",
    "contracts_for_target",
    "contracts_may_overlap",
    "finite_cardinality_add",
    "finite_cardinality_product",
    "fixed_positional_contracts",
    "join_contracts",
    "representation_value",
    "sequence_elements_are_deeply_immutable",
    "static_truth",
    "values_for_target",
]


class SourceValueBudgetExceeded(Exception):
    """Source representation planning exceeded its aggregate capability."""

    def __init__(self, node):
        super().__init__("source value planning exceeded its node budget")
        self.node = node


class SourceValueBudget:
    """Aggregate work budget for source analysis.

    Only source facts the analysis actually visits consume this allowance.
    The logical cardinality of a finite sequence is semantic information, not
    work already performed; exact representation planning decides separately
    whether that cardinality can be materialized.
    """

    __slots__ = ("_limit", "left")

    def __init__(self, limit=None):
        limit = budgets.SOURCE_PLAN_NODES if limit is None else limit
        if type(limit) is not int or limit < 1:
            raise ValueError("a source value budget must be positive")
        self._limit = limit
        self.left = limit

    def spend(self, node, amount=1):
        if type(amount) is not int or amount < 0:
            raise ValueError("source value work must be nonnegative")
        self.left -= amount
        if self.left < 0:
            raise SourceValueBudgetExceeded(node)


_UNKNOWN_STATIC = object()


def static_truth(node, *, value_of=None, bindings=None, budget=None):
    """A proved Python truth value, or ``None`` when it can vary.

    This deliberately recognizes fewer expressions than constant folding.  It
    exists to preserve Python's evaluation boundary for ``and``, ``or`` and
    conditional expressions: once a literal or declaration-owned scalar is
    decisive, later expressions do not run.  Literal-container children still
    have to be evaluated by the caller before using this fact.
    """
    if budget is not None:
        budget.spend(node)
    bindings = dict(bindings or {})
    value_of = value_of or (lambda _node, _bindings: None)
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Dict, ast.Set)):
        elements = node.keys if isinstance(node, ast.Dict) else node.elts
        return bool(elements)
    if isinstance(node, ast.Compare):
        def known_value(candidate):
            if isinstance(candidate, ast.Constant) and type(
                    candidate.value) in (bool, int, str, bytes, type(None)):
                return True, candidate.value
            got = value_of(candidate, bindings)
            if type(got) in (bool, int, str, bytes):
                return True, got
            return False, None

        known, left = known_value(node.left)
        if not known:
            return None
        for operator, comparator in zip(node.ops, node.comparators):
            known, right = known_value(comparator)
            if not known:
                return None
            try:
                if isinstance(operator, ast.Eq):
                    result = left == right
                elif isinstance(operator, ast.NotEq):
                    result = left != right
                elif isinstance(operator, ast.Lt):
                    result = left < right
                elif isinstance(operator, ast.LtE):
                    result = left <= right
                elif isinstance(operator, ast.Gt):
                    result = left > right
                elif isinstance(operator, ast.GtE):
                    result = left >= right
                elif isinstance(operator, ast.Is):
                    if type(left) not in (bool, type(None)) \
                            or type(right) not in (bool, type(None)):
                        return None
                    result = left is right
                elif isinstance(operator, ast.IsNot):
                    if type(left) not in (bool, type(None)) \
                            or type(right) not in (bool, type(None)):
                        return None
                    result = left is not right
                else:
                    return None
            except (TypeError, ValueError):
                return None
            if not result:
                return False
            left = right
        return True
    value = value_of(node, bindings)
    if type(value) in (bool, int, str, bytes):
        return bool(value)
    return None


def _sequence_literal(node, recurse):
    if isinstance(node, ast.Tuple):
        return ValueContract.positional("tuple", (recurse(item) for item in node.elts))
    carrier = "list" if isinstance(node, ast.List) else "tuple"
    element = None
    for item in node.elts:
        got = recurse(item)
        element = _element_join(element, got)
        if element is INCOMPATIBLE:
            break
    return ValueContract.sequence(carrier, len(node.elts), len(node.elts),
                                  element)


def contracts_for_target(target, value, *, leaf_key=None):
    """Contracts bound by one exact-length sequence unpacking target.

    Python destructuring is structural: ``for (x, y) in rows`` binds the two
    elements of each row, not the row contract to both names.  A homogeneous
    sequence contract contains enough information to model that only when its
    logical length is exact.  Starred targets receive the Python list that
    unpacking constructs.  ``None`` means the contract cannot prove that every
    represented value unpacks with this target; callers then refuse or lose the
    structural fact rather than inventing element types.

    ``leaf_key`` lets an assignment planner retain target occurrences,
    including attributes and subscripts. The default binds lexical names.
    """
    key = (leaf_key(target) if leaf_key is not None else
           target.id if isinstance(target, ast.Name) else None)
    if key is not None:
        return {key: value}
    if not isinstance(target, (ast.List, ast.Tuple)) \
            or not value.is_sequence or value.exact_length is None \
            or (value.element is None and value.components is None):
        return None

    starred = [index for index, item in enumerate(target.elts)
               if isinstance(item, ast.Starred)]
    if len(starred) > 1:
        return None
    if not starred:
        if value.exact_length != len(target.elts):
            return None
        pieces = [(item, value.component(i)) for i, item in enumerate(target.elts)]
    else:
        position = starred[0]
        fixed = len(target.elts) - 1
        if value.exact_length < fixed:
            return None
        rest = value.exact_length - fixed
        pieces = []
        for index, item in enumerate(target.elts):
            if index == position:
                pieces.append((
                    item.value,
                    ValueContract.positional("list", (
                        value.component(i) for i in range(position, position + rest)))))
            else:
                source_index = index if index < position else index + rest - 1
                pieces.append((item, value.component(source_index)))

    result = {}
    for item, contract in pieces:
        nested = contracts_for_target(item, contract, leaf_key=leaf_key)
        if nested is None:
            return None
        result.update(nested)
    return result


def values_for_target(target, value, *, leaf_key=None):
    """Concrete counterpart of :func:`contracts_for_target`.

    Declaration-time enumeration carries actual safe Python values for
    constant folding in dependent generators.  Keep those values distinct
    from their contracts, including Python's rule that a starred target is a
    newly allocated list.
    """
    key = (leaf_key(target) if leaf_key is not None else
           target.id if isinstance(target, ast.Name) else None)
    if key is not None:
        return {key: value}
    if not isinstance(target, (ast.List, ast.Tuple)) \
            or type(value) not in (list, tuple, range):
        return None
    items = list(value)
    starred = [index for index, item in enumerate(target.elts)
               if isinstance(item, ast.Starred)]
    if len(starred) > 1:
        return None
    if not starred:
        if len(items) != len(target.elts):
            return None
        pieces = list(zip(target.elts, items))
    else:
        position = starred[0]
        fixed = len(target.elts) - 1
        if len(items) < fixed:
            return None
        suffix = len(target.elts) - position - 1
        pieces = []
        for index, item in enumerate(target.elts):
            if index < position:
                pieces.append((item, items[index]))
            elif index == position:
                end = len(items) - suffix if suffix else len(items)
                pieces.append((item.value, items[position:end]))
            else:
                source_index = len(items) - (len(target.elts) - index)
                pieces.append((item, items[source_index]))

    result = {}
    for item, concrete in pieces:
        nested = values_for_target(item, concrete, leaf_key=leaf_key)
        if nested is None:
            return None
        result.update(nested)
    return result


def _target_names(target):
    return {
        node.id for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _copy_environment(values):
    """Copy path bindings without discarding an attached fact domain."""
    return {} if values is None else _shallow_copy(values)


def _bind_environment(values, additions):
    """Extend path bindings and invalidate side facts for rebound names."""
    result = _copy_environment(values)
    forget = getattr(result, "forget_numeric", None)
    if forget is not None:
        for name in additions:
            forget(name)
    result.update(additions)
    return result


def _record_items_receiver(node):
    """Receiver of one plain ``record.items()`` call, or ``None``.

    A fixed-schema record is represented as a product, so its items view is
    eliminated while lowering a comprehension rather than encoded as a
    heterogeneous SolverIR array.
    """
    if (isinstance(node, ast.Call) and not node.args and not node.keywords
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "items"):
        return node.func.value
    return None


def _record_item_target(target, field, contract):
    """Bindings made by ``for key, value in record.items()``.

    String keys are compile-time facts; field payloads retain their individual
    contracts.  More general target shapes would require a first-class
    heterogeneous tuple value, which this product-elimination rule deliberately
    does not invent.
    """
    if (not isinstance(target, (ast.Tuple, ast.List))
            or len(target.elts) != 2
            or not all(isinstance(item, ast.Name) for item in target.elts)):
        return None
    key, value = (item.id for item in target.elts)
    if key == value:
        return None
    return ({key: field}, {key: SCALAR, value: contract})


def contract_of_expression(node, *, resolve=None, call=None, select=None,
                           call_evaluated=None,
                           static_value=None, static_iterable=None,
                           bounded_iterable=None, integer_interval=None,
                           is_builtin=None, known_truth=None,
                           refine_condition=None, bindings=None,
                           contracts=None, observe=None, budget=None,
                           child_query=None):
    """Return the recursive value contract of one source expression.

    The callbacks carry lexical authority:

    - ``resolve(node, bindings)`` handles names and attributes;
    - ``call(node, bindings, contracts)`` handles a resolved source helper;
    - ``call_evaluated`` is the same boundary with the already evaluated
      positional and keyword contracts appended, so a stateful planner need
      not execute source arguments twice;
    - ``select(node, bindings, contracts)`` handles a closed collection read;
    - ``static_value`` and ``static_iterable`` expose declaration-time facts;
    - ``bounded_iterable`` supplies a finite logical-length contract when the
      elements themselves need not be enumerated;
    - ``integer_interval`` supplies finite inclusive bounds for an integer
      expression whose exact value is not fixed at this occurrence;
    - ``is_builtin(name, bindings)`` confirms that a spelling still denotes a
      builtin under the active comprehension/structural bindings.
    - ``known_truth(node, bindings)`` supplies a path-proved truth value
      without pretending that the expression has one fixed Python value.
    - ``refine_condition(node, truth, bindings)`` returns the bindings on one
      feasible conditional edge, or ``None`` when that edge is unreachable.
    - ``observe(node, contract)`` records contracts for recursively evaluated
      occurrences when a caller needs a per-occurrence plan.
    - ``child_query(node, bindings, contracts, evaluate)`` may reuse a pure
      child query in its lexical owner; otherwise it invokes ``evaluate``.
      It must retain the same occurrence observations as a fresh evaluation.

    Missing facts yield :data:`UNKNOWN`, never a scalar guess.
    """
    bindings = _copy_environment(bindings)
    contracts = dict(contracts or {})
    resolve = resolve or (lambda _node, _bindings: UNKNOWN)
    call = call or (lambda _node, _bindings, _contracts: UNKNOWN)
    select = select or (lambda _node, _bindings, _contracts: UNKNOWN)
    static_value = static_value or (lambda _node, _bindings: None)
    static_iterable = static_iterable or (lambda _node, _bindings: None)
    bounded_iterable = bounded_iterable or (
        lambda _node, _bindings: UNKNOWN)
    integer_interval = integer_interval or (
        lambda _node, _bindings: None)
    is_builtin = is_builtin or (lambda _name, _bindings: False)
    known_truth = known_truth or (lambda _node, _bindings: None)
    budget = SourceValueBudget() if budget is None else budget

    def truth_of(candidate, env):
        proved = known_truth(candidate, env)
        if type(proved) is bool:
            return proved
        return static_truth(
            candidate, value_of=static_value, bindings=env, budget=budget)

    def recurse(child, extra=None, extra_contracts=None):
        active = bindings if extra is None else extra
        abstract = contracts if extra_contracts is None else extra_contracts

        def evaluate():
            return contract_of_expression(
                child, resolve=resolve, call=call,
                call_evaluated=call_evaluated, static_value=static_value,
                select=select, static_iterable=static_iterable,
                bounded_iterable=bounded_iterable,
                integer_interval=integer_interval,
                is_builtin=is_builtin, known_truth=known_truth,
                refine_condition=refine_condition,
                bindings=active, contracts=abstract, observe=observe,
                budget=budget, child_query=child_query)

        got = (evaluate() if child_query is None or child is None else
               child_query(child, active, abstract, evaluate))
        if child is not None and observe is not None:
            observe(child, got)
        return got

    if node is None:
        return UNKNOWN
    budget.spend(node)
    if isinstance(node, ast.Constant):
        if type(node.value) is bytes:
            return contract_of_value(node.value)
        return NONE if node.value is None else SCALAR
    if isinstance(node, ast.Name) and node.id in contracts:
        return contracts[node.id]
    if isinstance(node, (ast.Name, ast.Attribute)):
        return resolve(node, bindings)
    if isinstance(node, (ast.List, ast.Tuple)):
        return _sequence_literal(node, recurse)
    items = record_items(node, is_builtin=lambda name: is_builtin(name, bindings))
    if items is not None:
        if isinstance(node, ast.Call):
            recurse(node.func)
        if not items:
            return ValueContract.mapping(0, 0)
        fields = {}
        for key, value in items:
            if isinstance(key, ast.AST):
                recurse(key)
            child = recurse(value)
            name = record_key(key)
            if name is None:
                return UNKNOWN
            # Python evaluates every pair in order and the last occurrence of
            # a key determines the final mapping value.  Preserve that rule
            # in the contract instead of treating a duplicate as a new
            # unsupported schema.
            fields[name] = child
        if not fields or any(value in (UNKNOWN, INCOMPATIBLE)
                             for value in fields.values()):
            return UNKNOWN
        return ValueContract.record(fields)
    copied = record_copy_source(
        node, is_builtin=lambda name: is_builtin(name, bindings))
    if copied is not None:
        recurse(node.func)
        source = recurse(copied)
        return source if source.is_record else UNKNOWN
    if isinstance(node, ast.IfExp):
        recurse(node.test)
        truth = truth_of(node.test, bindings)
        if truth is True:
            return recurse(node.body)
        if truth is False:
            return recurse(node.orelse)
        if refine_condition is None:
            return join_contracts(recurse(node.body), recurse(node.orelse))
        left = refine_condition(node.test, True, bindings)
        right = refine_condition(node.test, False, bindings)
        if left is None:
            return UNKNOWN if right is None else recurse(node.orelse, right)
        if right is None:
            return recurse(node.body, left)
        return join_contracts(
            recurse(node.body, left), recurse(node.orelse, right))
    if isinstance(node, ast.Subscript):
        base = recurse(node.value)
        if isinstance(node.slice, ast.Slice):
            for bound in (node.slice.lower, node.slice.upper, node.slice.step):
                if bound is not None:
                    recurse(bound)
        else:
            recurse(node.slice)
        resolved = select(node, bindings, contracts)
        if resolved is not UNKNOWN:
            return resolved
        if base.is_mapping and not isinstance(node.slice, ast.Slice):
            return base.element or SCALAR
        if base.is_record and not isinstance(node.slice, ast.Slice) \
                and isinstance(node.slice, ast.Constant) \
                and type(node.slice.value) is str:
            return base.field_contracts.get(node.slice.value, UNKNOWN)
        if not base.is_sequence:
            return UNKNOWN
        if not isinstance(node.slice, ast.Slice):
            if base.components is not None:
                index = (node.slice.value if isinstance(node.slice, ast.Constant)
                         else static_value(node.slice, bindings))
                if type(index) is int and -len(base.components) <= index < len(base.components):
                    return base.component(index)
                return UNKNOWN
            return base.element or UNKNOWN
        lo = None if node.slice.lower is None else static_value(
            node.slice.lower, bindings)
        open_upper = node.slice.upper is None
        hi = None if open_upper else static_value(
            node.slice.upper, bindings)
        step = 1 if node.slice.step is None else static_value(
            node.slice.step, bindings)
        if type(step) is not int or step == 0:
            return UNKNOWN
        endpoints_are_static = (
            (node.slice.lower is None or type(lo) is int)
            and (open_upper or type(hi) is int))
        if not endpoints_are_static:
            # A finite integer endpoint does not change the element
            # representation, and clipping keeps every nonzero-step slice within
            # the source capacity.  Retain that bounded representation even
            # when the exact length is selected at run time.
            endpoint_nodes = tuple(
                bound for bound in (node.slice.lower, node.slice.upper)
                if bound is not None)
            if any(
                    integer_interval(bound, bindings) is None
                    for bound in endpoint_nodes):
                return UNKNOWN
            return base.with_interval(0, base.maximum, base.element)
        if node.slice.lower is None and node.slice.upper is None \
                and node.slice.step is None:
            # A full slice copies the carrier without changing its logical
            # length.  Requiring one exact input length here discarded an
            # already-proved interval such as ``list[0..32]``.
            return base.with_interval(
                base.minimum, base.maximum, base.element)
        if (base.exact_length is None and step == 1
                and (lo is None or lo >= 0)
                and (open_upper or hi >= 0)):
            # For nonnegative unit-step bounds the slice length is monotone in
            # the input length.  Preserve that finite interval instead of the
            # loose ``0..capacity`` fallback; bounded state such as a rolling
            # window relies on ``values[1:]`` decreasing its maximum by one.
            def size(length):
                stop = length if open_upper else min(length, hi)
                return max(0, stop - min(length, 0 if lo is None else lo))

            return base.with_interval(
                size(base.minimum), size(base.maximum), base.element)
        if base.exact_length is not None:
            size = len(range(*slice(lo, hi, step).indices(base.maximum)))
            return base.with_interval(size, size, base.element)
        # Every slice of a sequence contains at most all of its elements.
        # Keeping the carrier and element facts is sound even when negative
        # or data-dependent endpoints prevent a tighter interval.
        return base.with_interval(0, base.maximum, base.element)
    if isinstance(node, ast.BinOp):
        left, right = recurse(node.left), recurse(node.right)
        if isinstance(node.op, ast.Mult):
            for sequence, count_node in ((left, node.right),
                                         (right, node.left)):
                if not sequence.is_sequence:
                    continue
                count = static_value(count_node, bindings)
                interval = ((count, count) if type(count) is int else
                            integer_interval(count_node, bindings))
                if (type(interval) in (tuple, list)
                        and len(interval) == 2
                        and all(type(value) is int for value in interval)
                        and interval[0] <= interval[1]):
                    # Python repeats a sequence zero times when the multiplier
                    # is negative.  Keep that source rule in the shared
                    # contract rather than letting lowering invent a second
                    # interpretation of the count.
                    minimum = max(0, interval[0])
                    maximum = max(0, interval[1])
                    return sequence.with_interval(
                        finite_cardinality_product(
                            sequence.minimum, minimum),
                        finite_cardinality_product(
                            sequence.maximum, maximum),
                        sequence.element)
        if isinstance(node.op, ast.Add) and left.is_sequence \
                and right.is_sequence:
            observed = left.observed_carriers | right.observed_carriers
            if left.carrier in BYTE_CARRIERS and right.carrier in BYTE_CARRIERS:
                observed = left.observed_carriers
            if len(observed) > 1:
                return INCOMPATIBLE
            element = _element_join(left.element, right.element)
            if element is INCOMPATIBLE:
                return INCOMPATIBLE
            return ValueContract._sequence_from_evidence(
                observed,
                left.unproved_carrier or right.unproved_carrier,
                finite_cardinality_add(left.minimum, right.minimum),
                finite_cardinality_add(left.maximum, right.maximum),
                element)
        if left.kind == right.kind == "scalar":
            return SCALAR
        # Normal-result contracts do not assert that operands are valid.
        # A successful shift of admitted plain Python values is an integer,
        # even when an earlier possibly failing read has unknown structure.
        # Bitwise operations have the same result when one operand is scalar;
        # do not infer it for two unknowns (which could be mappings or sets).
        if UNKNOWN in (left, right) and (
                isinstance(node.op, (ast.LShift, ast.RShift))
                or isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor))
                and SCALAR in (left, right)):
            return SCALAR
        return UNKNOWN
    if isinstance(node, ast.UnaryOp):
        operand = recurse(node.operand)
        if isinstance(node.op, ast.Not):
            return SCALAR
        return SCALAR if operand.kind == "scalar" else UNKNOWN
    if isinstance(node, ast.Compare):
        recurse(node.left)
        previous = node.left
        for index, (operator, comparator) in enumerate(
                zip(node.ops, node.comparators)):
            recurse(comparator)
            # Truth is needed only to decide whether another operand runs.
            # The final pair has no successor, and every normally completed
            # comparison has the same scalar result contract.
            if index + 1 == len(node.ops):
                break
            pair = ast.Compare(
                left=previous, ops=[operator], comparators=[comparator])
            if truth_of(pair, bindings) is False:
                break
            previous = comparator
        return SCALAR
    if isinstance(node, ast.BoolOp):
        # ``and`` and ``or`` return an operand, not a Boolean, and operands
        # after the decisive one are not evaluated.  Besides preserving the
        # result contract, respecting that order is necessary for the source
        # summary: an unreachable helper call must not create an activation.
        def suffix(index):
            current = recurse(node.values[index])
            if index + 1 == len(node.values):
                return current
            truth = truth_of(node.values[index], bindings)
            if isinstance(node.op, ast.And):
                if truth is False:
                    return current
                following = suffix(index + 1)
                return (following if truth is True else
                        join_contracts(current, following))
            if truth is True:
                return current
            following = suffix(index + 1)
            return (following if truth is False else
                    join_contracts(current, following))

        return suffix(0) if node.values else UNKNOWN
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            recurse(value)
        return SCALAR
    if isinstance(node, ast.FormattedValue):
        recurse(node.value)
        if node.format_spec is not None:
            recurse(node.format_spec)
        return SCALAR
    if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
        # Concrete declaration-time values and abstract value contracts are
        # deliberately separate.  A row drawn from ``[[1, 2]]`` may have a
        # fully known recursive contract even when the declaration evaluator
        # cannot materialize the iterable's Python values.  Passing that
        # contract through ``bindings`` would confuse a type fact with a source
        # value and make constant folding unsound.
        environments = [(_copy_environment(bindings), dict(contracts), 1, 1)]

        def filter_result(gen, env, abstract):
            """Whether this iteration survives, and whether that is certain."""
            guaranteed = True
            for condition in gen.ifs:
                recurse(condition, env, abstract)
                truth = truth_of(condition, env)
                if truth is False:
                    return False, False
                if truth is None:
                    guaranteed = False
            return True, guaranteed

        def unresolved_target(gen, env, abstract, index):
            """Visit the remaining expressions with unknown target values."""

            unknown, unknown_contracts = (
                _copy_environment(env), dict(abstract))
            for name in _target_names(gen.target):
                forget = getattr(unknown, "forget_numeric", None)
                if forget is not None:
                    forget(name)
                unknown[name] = _UNKNOWN_STATIC
                unknown_contracts.pop(name, None)
            selected, _guaranteed = filter_result(
                gen, unknown, unknown_contracts)
            if not selected:
                return
            for remaining in node.generators[index + 1:]:
                recurse(remaining.iter, unknown, unknown_contracts)
                for name in _target_names(remaining.target):
                    forget = getattr(unknown, "forget_numeric", None)
                    if forget is not None:
                        forget(name)
                    unknown[name] = _UNKNOWN_STATIC
                    unknown_contracts.pop(name, None)
                selected, _guaranteed = filter_result(
                    remaining, unknown, unknown_contracts)
                if not selected:
                    return
            recurse(node.elt, unknown, unknown_contracts)

        for index, gen in enumerate(node.generators):
            next_environments = []
            for env, abstract, lower, upper in environments:
                iterable_contract = recurse(gen.iter, env, abstract)
                values = static_iterable(gen.iter, env)

                if values is not None:
                    for value in values:
                        budget.spend(gen.target)
                        concrete = values_for_target(gen.target, value)
                        if concrete is None:
                            unresolved_target(gen, env, abstract, index)
                            return UNKNOWN
                        nested = _bind_environment(env, concrete)
                        nested_contracts = dict(abstract)
                        for name in concrete:
                            nested_contracts.pop(name, None)
                        selected, guaranteed = filter_result(
                            gen, nested, nested_contracts)
                        if not selected:
                            continue
                        next_environments.append((
                            nested, nested_contracts,
                            lower if guaranteed else 0, upper))
                    continue
                if (iterable_contract.is_sequence
                        and iterable_contract.maximum == 0):
                    # A proved-empty iterable executes neither its filters
                    # nor the comprehension body.  Its element contract is
                    # therefore immaterial (and is normally absent).
                    continue
                if not iterable_contract.is_sequence \
                        or iterable_contract.element is None:
                    unresolved_target(gen, env, abstract, index)
                    return UNKNOWN
                target_contracts = contracts_for_target(
                    gen.target, iterable_contract.element)
                if target_contracts is None:
                    unresolved_target(gen, env, abstract, index)
                    return UNKNOWN
                nested = _copy_environment(env)
                nested_contracts = dict(abstract)
                for name, contract in target_contracts.items():
                    forget = getattr(nested, "forget_numeric", None)
                    if forget is not None:
                        forget(name)
                    nested.pop(name, None)
                    nested_contracts[name] = contract
                selected, guaranteed = filter_result(
                    gen, nested, nested_contracts)
                if not selected:
                    continue
                next_environments.append((
                    nested, nested_contracts,
                    (finite_cardinality_product(
                        lower, iterable_contract.minimum)
                     if guaranteed else 0),
                    finite_cardinality_product(
                        upper, iterable_contract.maximum)))
            environments = next_environments
        element = None
        for env, abstract, _lower, _upper in environments:
            element = _element_join(
                element, recurse(node.elt, env, abstract))
        if element is INCOMPATIBLE:
            return INCOMPATIBLE
        minimum = finite_cardinality_add(*(
            lower for _env, _abstract, lower, _upper in environments))
        maximum = finite_cardinality_add(*(
            upper for _env, _abstract, _lower, upper in environments))
        # A generator has a finite iteration domain, not a materialized list.
        # Its consumer supplies the concrete carrier and evaluation schedule.
        return ValueContract.sequence(
            "list" if isinstance(node, ast.ListComp) else None,
            minimum, maximum, element)
    if isinstance(node, ast.DictComp):
        # A dictionary comprehension has a fixed-schema record contract when
        # every generator is a declaration-time finite iterable, every filter
        # has a proved truth value, and every reached key is a fixed string.
        # Enumerating those source iterations preserves Python's last-write
        # rule for repeated keys; joining every syntactic key would not.
        environments = [(_copy_environment(bindings), dict(contracts))]
        for gen in node.generators:
            following = []
            for env, abstract in environments:
                receiver = _record_items_receiver(gen.iter)
                if receiver is not None:
                    record = recurse(receiver, env, abstract)
                    if not record.is_record:
                        recurse(gen.iter, env, abstract)
                        return UNKNOWN
                    for field, child in record.fields:
                        planned = _record_item_target(
                            gen.target, field, child)
                        if planned is None:
                            recurse(gen.iter, env, abstract)
                            return UNKNOWN
                        fixed, target_contracts = planned
                        nested = _copy_environment(env)
                        nested_contracts = dict(abstract)
                        for name in _target_names(gen.target):
                            forget = getattr(nested, "forget_numeric", None)
                            if forget is not None:
                                forget(name)
                            nested.pop(name, None)
                            nested_contracts.pop(name, None)
                        nested.update(fixed)
                        nested_contracts.update(target_contracts)
                        selected = True
                        for condition in gen.ifs:
                            recurse(condition, nested, nested_contracts)
                            truth = truth_of(condition, nested)
                            if truth is None:
                                return UNKNOWN
                            if not truth:
                                selected = False
                                break
                        if selected:
                            following.append((nested, nested_contracts))
                    continue
                recurse(gen.iter, env, abstract)
                values = static_iterable(gen.iter, env)
                if values is None:
                    # Still visit the remaining occurrences before forgetting
                    # the shape, so helper reachability does not depend on
                    # whether a collection contract was representable.
                    unknown = _copy_environment(env)
                    for name in _target_names(gen.target):
                        forget = getattr(unknown, "forget_numeric", None)
                        if forget is not None:
                            forget(name)
                        unknown[name] = _UNKNOWN_STATIC
                    for condition in gen.ifs:
                        recurse(condition, unknown, abstract)
                    recurse(node.key, unknown, abstract)
                    recurse(node.value, unknown, abstract)
                    return UNKNOWN
                for value in values:
                    budget.spend(gen.target)
                    concrete = values_for_target(gen.target, value)
                    if concrete is None:
                        return UNKNOWN
                    nested = _bind_environment(env, concrete)
                    nested_contracts = dict(abstract)
                    for name in concrete:
                        nested_contracts.pop(name, None)
                    selected = True
                    for condition in gen.ifs:
                        recurse(condition, nested, nested_contracts)
                        truth = truth_of(condition, nested)
                        if truth is None:
                            return UNKNOWN
                        if not truth:
                            selected = False
                            break
                    if selected:
                        following.append((nested, nested_contracts))
            environments = following
        fields = {}
        for env, abstract in environments:
            key = static_value(node.key, env)
            if type(key) is not str:
                recurse(node.key, env, abstract)
                recurse(node.value, env, abstract)
                return UNKNOWN
            recurse(node.key, env, abstract)
            fields[key] = recurse(node.value, env, abstract)
        if not fields or any(not contract.is_known
                             for contract in fields.values()):
            return UNKNOWN
        return ValueContract.record(fields)
    if isinstance(node, ast.SetComp):
        # These carriers are outside the finite SolverIR array subset, but all
        # of their expressions are still executable source occurrences.  Walk
        # them so a helper reached only here is not classified as unused.
        nested = _copy_environment(bindings)
        for gen in node.generators:
            recurse(gen.iter, nested)
            for name in ast.walk(gen.target):
                if (isinstance(name, ast.Name)
                        and isinstance(name.ctx, ast.Store)):
                    forget = getattr(nested, "forget_numeric", None)
                    if forget is not None:
                        forget(name.id)
                    nested[name.id] = _UNKNOWN_STATIC
            for condition in gen.ifs:
                recurse(condition, nested)
        recurse(node.elt, nested)
        return UNKNOWN
    if isinstance(node, ast.Call):
        counted = binary_digit_count(
            node, is_builtin=lambda name: is_builtin(name, bindings))
        if counted is not None:
            # The formatting and literal needle have no source effects. Visit
            # the numeric operand once through normal recursion, so helper
            # activations and state updates remain in this same source plan.
            return SCALAR if recurse(counted[0]).kind == "scalar" else UNKNOWN
        # Evaluate each source child exactly once and retain its contract for
        # both a resolved helper and the builtin projection below.  Rewalking
        # arguments in the callback used to multiply source-plan work and,
        # more importantly, gave a stateful helper argument two abstract
        # executions for one Python evaluation.
        receiver_contract = (recurse(node.func.value)
                             if isinstance(node.func, ast.Attribute)
                             and node.func.attr in {"copy", "to_bytes"} else None)
        if receiver_contract is None:
            recurse(node.func)
        name = node.func.id if isinstance(node.func, ast.Name) else None
        iterable_arguments = name in {"zip", "enumerate", "reversed", "list", "tuple", "bytes", "bytearray"} \
            and is_builtin(name, bindings)
        argument_iterables = []
        argument_contracts = []
        for index, argument in enumerate(node.args):
            source = argument.value if isinstance(argument, ast.Starred) else argument
            observed = recurse(source)
            # Capture the iterable view at this argument's evaluation point;
            # a later argument may rebind its source name through a helper.
            view = observed
            if iterable_arguments and (name == "zip" or index == 0) \
                    and not view.is_sequence:
                view = bounded_iterable(source, bindings)
            argument_iterables.append(view)
            argument_contracts.append(observed)
        argument_contracts = tuple(argument_contracts)
        keyword_contracts = tuple(
            (keyword.arg, recurse(keyword.value))
            for keyword in node.keywords
        )
        resolved = (
            call_evaluated(
                node, bindings, contracts,
                argument_contracts, keyword_contracts)
            if call_evaluated is not None else
            call(node, bindings, contracts)
        )
        if resolved is not UNKNOWN:
            return resolved
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "copy"
                and has_plain_positional_arity(node, 0)
                and receiver_contract.is_sequence
                and receiver_contract.carrier in {"list", "bytearray"}):
            return receiver_contract
        if is_from_bytes(node, lambda name: is_builtin(name, bindings)):
            return SCALAR
        if is_to_bytes(node) and receiver_contract.kind == "scalar":
            arguments = to_bytes_arguments(node)
            if arguments is None:
                return UNKNOWN
            length = dict(arguments).get("length")
            interval = (1, 1) if length is None else integer_interval(length, bindings)
            if interval is not None:
                return ValueContract.sequence(
                    "bytes", max(0, interval[0]), max(0, interval[1]), SCALAR)
            return UNKNOWN
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name is None or not is_builtin(name, bindings):
            return UNKNOWN
        if name == "sorted" and len(node.args) == 1 \
                and not isinstance(node.args[0], ast.Starred):
            keywords = [keyword.arg for keyword in node.keywords]
            if (None in keywords or len(keywords) != len(set(keywords))
                    or not set(keywords) <= {"key", "reverse"}):
                return UNKNOWN
            source = argument_contracts[0]
            if not source.is_sequence:
                return UNKNOWN
            key = next((keyword.value for keyword in node.keywords
                        if keyword.arg == "key"), None)
            if source.maximum and isinstance(key, ast.Lambda) \
                    and len(key.args.args) == 1:
                # A key is evaluated on an element, not when the lambda is
                # created. Visit its body in that parameter environment so
                # helper activations and partial operations share the same
                # source plan as the retained sorting translation.
                parameter = key.args.args[0].arg
                nested = _copy_environment(bindings)
                forget = getattr(nested, "forget_numeric", None)
                if forget is not None:
                    forget(parameter)
                nested[parameter] = _UNKNOWN_STATIC
                nested_contracts = dict(contracts)
                nested_contracts[parameter] = source.element
                recurse(key.body, nested, nested_contracts)
            # Sorting changes order, never the finite carrier length or the
            # element representation.  The executable lowerer separately
            # validates the key and reverse semantics.
            return ValueContract.sequence(
                "list", source.minimum, source.maximum, source.element)
        if name in BYTE_CARRIERS and has_plain_positional_arity(node, 0):
            return ValueContract.sequence(name, 0, 0, SCALAR)
        if name in BYTE_CARRIERS and has_plain_positional_arity(node, 1):
            source = argument_iterables[0]
            if source.is_sequence:
                return ValueContract.sequence(name, source.minimum, source.maximum, SCALAR)
            interval = integer_interval(node.args[0], bindings)
            if interval is not None:
                return ValueContract.sequence(name, max(0, interval[0]), max(0, interval[1]), SCALAR)
            return UNKNOWN
        if name in {"list", "tuple"} and has_plain_positional_arity(node, 0):
            return ValueContract.sequence(name, 0, 0, None)
        if name in {"list", "tuple", "reversed"} \
                and has_plain_positional_arity(node, 1):
            source = argument_iterables[0]
            if not source.is_sequence:
                return UNKNOWN
            carrier = ("tuple" if name == "tuple" else
                       "list" if name == "list" else
                       source.carrier)
            if name == "reversed":
                return source.with_interval(
                    source.minimum, source.maximum, source.element)
            return ValueContract.sequence(
                carrier, source.minimum, source.maximum, source.element)
        if name == "range" and has_plain_positional_arity(node, 1, 3):
            values = static_iterable(node, bindings)
            if values is not None:
                return ValueContract.sequence(
                    "range", len(values), len(values), SCALAR)
            bounded = bounded_iterable(node, bindings)
            if bounded is not UNKNOWN:
                return bounded
            return UNKNOWN
        if name == "zip" and has_plain_positional_arity(node, 0, len(node.args)):
            if not argument_contracts:
                return ValueContract.sequence(None, 0, 0, None)
            if any(not source.is_sequence for source in argument_iterables):
                return UNKNOWN
            maxima = [source.maximum for source in argument_iterables
                      if source.maximum is not None]
            if 0 in maxima:
                return ValueContract.sequence(None, 0, 0, None)
            return ValueContract.sequence(
                None, min(source.minimum for source in argument_iterables),
                min(maxima) if maxima else None,
                ValueContract.positional("tuple", tuple(
                    source.element or UNKNOWN for source in argument_iterables)))
        if name == "enumerate" and has_plain_positional_arity(node, 1, 2):
            source = argument_iterables[0]
            if not source.is_sequence:
                return UNKNOWN
            if source.maximum == 0:
                # No next() succeeds, so neither a pair nor an element type
                # is needed. Retain the proved empty cardinality instead of
                # making the planner explore a fictitious loop body.
                return ValueContract.sequence(None, 0, 0, None)
            if source.element is None:
                return UNKNOWN
            pair = ValueContract.positional("tuple", (SCALAR, source.element))
            # The enumerate object is consumed as an iterable and is never a
            # represented source list, tuple, or range.  Keep its cardinality
            # and item contract without claiming one of those carriers.
            return ValueContract.sequence(
                None, source.minimum, source.maximum, pair)
        if name == "divmod" and has_plain_positional_arity(node, 2):
            return ValueContract.positional("tuple", (SCALAR, SCALAR))
        scalar_arities = {
            "len": (1, 1), "abs": (1, 1), "bool": (1, 1),
            "int": (1, 1), "sum": (1, 2), "all": (1, 1),
            "any": (1, 1), "bin": (1, 1), "hex": (1, 1),
            "oct": (1, 1), "round": (1, 2),
            "pow": (2, 3), "ord": (1, 1), "chr": (1, 1),
            "float": (0, 1), "str": (0, 1),
        }
        arity = scalar_arities.get(name)
        if arity is not None and has_plain_positional_arity(
                node, arity[0], arity[1]):
            return SCALAR
        if name in {"min", "max"} and has_plain_positional_arity(
                node, 1, len(node.args)):
            return SCALAR
        return UNKNOWN
    return UNKNOWN
