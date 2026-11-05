"""Finite source sequences, before they are lowered to fixed arrays.

Python lists are variable-length values.  SolverIR arrays are fixed-size values,
so the frontend needs an upper bound before it can represent a list that grows
with ``append``.  This module is the single owner of that source-level fact.

The bound is deliberately an upper bound, not a path prediction.  Mutually
exclusive branches may therefore contribute more space than a particular run
uses; the run still carries its exact length beside the array. Source ``while``
capacities use the same task-domain trip spans proved by lowering.  The source
control interpreter consumes the task's finite input and state ranges directly,
and retained lowering cross-checks the resulting plan.  No global iteration
guess, task name, harness convention, or observed stimulus participates in the
answer.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from itertools import product as cartesian_product

from behave.core import runtime_defaults as defaults

from ..symbolic import ir
from ..symbolic import ranges
from .budgets import (
    SOURCE_INTEGER_BITS,
    SOURCE_TEXT_CHARS,
    UNROLL_LIMIT,
    helper_activation_exceeds,
)
from .extract import LoweringError, _const_int
from .source_batches import (
    BatchContract,
    BatchContractError,
    BatchForm,
    BatchPlan,
    RecordBuilderSummary,
    RecordFieldWrite,
    batch_contract_of_value,
    batch_value_contract,
    join_batch_alternatives,
    join_batch_plans,
    join_transaction_schemas,
    plan_batch_expression,
    record_batch_payload,
)
from .source_calls import (
    CallBindingError,
    bind_helper_arguments,
    has_plain_positional_arity,
    helper_parameters,
    is_append_call,
    is_extend_call,
    is_plain_append_call,
    is_plain_extend_call,
    is_plain_named_call,
    is_plain_pop_call,
    is_pop_call,
    non_rebound_parameters,
    record_copy_source,
    record_items,
    record_key,
    record_update_items,
    resolve_helper,
    sequence_delete_target,
    sequence_method_target,
)
from ..symbolic.value_contract import (
    narrow_none,
    INCOMPATIBLE,
    NONE,
    SCALAR,
    UNKNOWN,
    ValueContract,
    contract_at_record_path,
    contract_of_collection_elements,
    contract_of_collection_lookup,
    contract_of_value,
    contract_with_record_path,
    contracts_may_overlap,
    finite_cardinality_add,
    finite_cardinality_product,
    fixed_positional_contracts,
    join_contracts,
)
from .source_options import none_test
from .source_rom import DEFAULT_ROM_NODES, evaluate_frozen
from .source_syntax import annotation_target_expressions
from .source_values import (
    SourceValueBudget,
    SourceValueBudgetExceeded,
    _record_item_target,
    _record_items_receiver,
    contract_of_expression,
    contracts_for_target,
    static_truth,
    values_for_target,
)
from .while_plan import (
    WhilePlanError,
    exit_ranking_domain,
    loop_container_writes,
    loop_preserves_iterable_values,
    ranking_span,
    trip_span,
)
from .while_plan import analyse as analyse_while
from .while_plan import analyse_exit_ranking as analyse_while_exit_ranking
from .while_plan import analyse_ranking as analyse_while_ranking
from .finite_descent import finite_progress_domain

__all__ = [
    "SequencePlan",
    "SequencePlanError",
    "ValueContract",
    "plan_sequences",
    "static_iterable",
    "static_value",
]


class SequencePlanError(Exception):
    """A source collection whose finite representation is ambiguous."""

    def __init__(self, node, what, code):
        super().__init__(what)
        self.node = node
        self.what = what
        self.code = code


def _fixed_key(value):
    """Typed identity of one statically known scalar, or no such fact.

    Python deliberately equates ``True`` and ``1`` as dictionary keys.  The
    frontend's typed semantics does not, so raw host values cannot identify a
    summary activation.
    """
    if value is None:
        return ("unproved",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is str:
        return ("str", value)
    # Comprehension analysis uses a private sentinel for a value that is bound
    # but not statically known.  Any non-scalar host object has the same status
    # here: it is a contract fact, not a fixed summary discriminator.
    return ("unproved",)


def _fingerprint_key(fingerprint):
    return tuple(
        (owner, name, contract, _fixed_key(fixed))
        for owner, name, contract, fixed, _interval in fingerprint)


def _fingerprint_range_key(fingerprint):
    return tuple(
        (owner, name, _range_key(interval))
        for owner, name, _contract, _fixed, interval in fingerprint)


def _range_key(value):
    return (None if value is None else (value.lo, value.hi))


def _bounds_key(value):
    return (None if value is None else (value.lo, value.hi))


class _ScalarBindings(dict):
    """Exact scalar bindings plus numeric bounds for the same source path.

    Fixed values retain their existing role in source schema selection.  The
    parallel maps are deliberately attached to that path object so every
    control-flow copy and join has one owner.  ``ranges`` contains only finite
    intervals suitable for encodings.  ``bounds`` may be one-sided and is used
    only for structural proofs such as a finite loop ceiling.
    """

    __slots__ = ("bounds", "guards", "lengths", "ranges")

    def __init__(self, values=(), *, scalar_ranges=(), scalar_bounds=(),
                 guard_facts=(), length_facts=()):
        super().__init__(values)
        # n -> sequence key means n still equals that sequence's current
        # length. This is a value relation, not saved syntax to re-evaluate.
        self.lengths = dict(length_facts)
        # Truth values of read-only parameters are path predicates, not
        # concrete Python values.  In particular, a true integer need not be
        # the singleton ``True``.  Keeping the facts separately lets two uses
        # of the same guard correlate without lending an unsound fixed value
        # to arithmetic or shape analysis.
        self.guards = dict(guard_facts)
        self.ranges = dict(scalar_ranges)
        self.bounds = {
            name: ranges.Bounds.of(value)
            for name, value in self.ranges.items()
        }
        self.bounds.update({
            name: ranges.Bounds.of(value)
            for name, value in dict(scalar_bounds).items()
        })
        # ``scalar_bounds`` is the authoritative joined/refined environment.
        # Keep its finite projection in lockstep with the representation-range
        # view; otherwise a branch refinement can silently erase an already
        # finite fact merely because it travelled through ``Bounds``.
        for name, value in self.bounds.items():
            finite = value.finite()
            if finite is None:
                self.ranges.pop(name, None)
            else:
                self.ranges[name] = finite

    def clone(self):
        # Range and bound values are immutable within this analysis: every
        # refinement replaces a map entry.  Copy the already-normalized maps
        # directly instead of converting Bounds -> Range -> Bounds for every
        # control-flow fork.
        out = type(self).__new__(type(self))
        dict.__init__(out, self)
        out.guards = dict(self.guards)
        out.lengths = dict(self.lengths)
        out.ranges = dict(self.ranges)
        out.bounds = dict(self.bounds)
        return out

    def forget_length(self, key):
        """Invalidate a length relation when either endpoint is written."""
        if self.lengths:
            self.lengths = {
                name: sequence for name, sequence in self.lengths.items()
                if name != key and sequence != key
            }

    def forget_numeric(self, name):
        self.ranges.pop(name, None)
        self.bounds.pop(name, None)

    def forget_cells(self, root):
        """A rebind, unknown write or position-changing operation kills cells."""
        prefix = root + "["
        for name in tuple(self.bounds):
            if isinstance(name, str) and name.startswith(prefix):
                self.forget_numeric(name)

    def set_numeric(self, name, interval):
        """Set one finite interval, or forget the numeric fact."""
        if interval is None:
            self.forget_numeric(name)
            return
        self.ranges[name] = interval
        self.bounds[name] = ranges.Bounds.of(interval)

    def set_bounds(self, name, value):
        """Set one structural bound and its finite projection, if any."""
        if value is None:
            self.forget_numeric(name)
            return
        value = ranges.Bounds.of(value)
        self.bounds[name] = value
        finite = value.finite()
        if finite is None:
            self.ranges.pop(name, None)
        else:
            self.ranges[name] = finite


@dataclass(frozen=True)
class _SourceQuerySyntax:
    """Immutable syntax only; no bindings, ranges or activation results."""

    dependencies: frozenset
    environment_keys: tuple
    selected: frozenset
    expressions: tuple


class _SourceQueryCache:
    """Pure source queries shared by bounded-state planning passes.

    Closing a state representation evaluates the same source occurrence at
    several abstract state layers.  Declaration-time scalar evaluation and
    its deliberately small truth projection are pure in the lexical owner
    and exact scalar bindings, so recomputing either cannot reveal a new
    fact.  Keeping this cache outside one ``_FunctionContracts`` instance
    lets the closure pass and the final plan reuse those proofs without
    weakening the aggregate work budget.
    """

    __slots__ = ("augmented", "bounds", "contracts", "syntax",
                 "predicates", "purity", "ranges", "refinements", "static", "truth",
                 "state_free_helpers", "scalar_summaries")

    def __init__(self):
        self.augmented = {}
        self.bounds = {}
        self.contracts = {}
        self.syntax = {}
        self.predicates = {}
        self.purity = {}
        self.ranges = {}
        self.refinements = {}
        self.static = {}
        self.truth = {}
        self.state_free_helpers = {}
        self.scalar_summaries = {}

    def syntax_of(self, node):
        # Source occurrences are immutable throughout planning.  Keep the AST
        # object, not its integer id: temporary proxy nodes must not collide.
        if node not in self.syntax:
            expressions = tuple(child for child in ast.walk(node)
                                if isinstance(child, ast.expr))
            names = frozenset(child.id for child in expressions
                              if isinstance(child, ast.Name))
            fields = frozenset(
                child.attr for child in expressions
                if (isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "self"))
            dependencies = names | frozenset(("self", name) for name in fields)
            numeric_keys = names | frozenset(f"self.{name}" for name in fields)
            selected = frozenset(
                key for child in expressions if isinstance(child, ast.Subscript)
                if (key := _subscript_range_key(child)) is not None)
            # Static evaluation never reads indexed payloads or general calls.
            # A proven str(scalar) conversion does read its argument. Retain
            # that dependency even before lexical builtin resolution, so a
            # loop's changing selector cannot reuse the first iteration's key.
            static_dependencies = set()
            pending = [node]
            while pending:
                child = pending.pop()
                if isinstance(child, ast.Subscript):
                    continue
                if isinstance(child, ast.Call) and not (
                        isinstance(child.func, ast.Name) and child.func.id == "str"
                        and len(child.args) == 1 and not child.keywords):
                    continue
                if isinstance(child, ast.Name):
                    static_dependencies.add(child.id)
                elif (isinstance(child, ast.Attribute)
                      and isinstance(child.value, ast.Name)
                      and child.value.id == 'self'):
                    static_dependencies.add(('self', child.attr))
                pending.extend(ast.iter_child_nodes(child))
            self.syntax[node] = _SourceQuerySyntax(
                frozenset(static_dependencies),
                (names, fields, dependencies | numeric_keys, numeric_keys),
                selected, expressions)
        return self.syntax[node]

    def is_pure(self, node, *, record_names=None):
        # Only a referenced mutable builder can change this syntax predicate.
        # Keep None distinct: callers without record facts do not admit selects.
        if record_names is not None:
            record_names = frozenset(record_names) & self.syntax_of(node).environment_keys[0]
        key = node, record_names
        if key not in self.purity:
            self.purity[key] = _contract_expression_is_pure(
                node, record_names=record_names)
        return self.purity[key]


@dataclass(frozen=True)
class _ContractObservation:
    """One descendant occurrence reproduced by a cached contract query."""

    node: ast.expr
    contract: ValueContract
    fixed: tuple
    bounds: tuple


@dataclass(frozen=True)
class _ContractQueryResult:
    """Result and occurrence evidence of one pure contract evaluation."""

    contract: ValueContract
    observations: tuple
    live_bindings: bool = False


_CONTRACT_PURE_EXPRESSIONS = (
    ast.Attribute,
    ast.BinOp,
    ast.BoolOp,
    ast.Compare,
    ast.Constant,
    ast.IfExp,
    ast.Name,
    ast.UnaryOp,
)


def _contract_expression_is_pure(node, *, record_names=None):
    """Whether contract evaluation has no source-state side effect.

    Calls can update model state through a helper, a field read from a mutable
    record builder freezes that builder's schema, and collection expressions
    carry their own bounded iteration plans.  Ordinary fixed-sequence
    selection has none of those effects and is included: generated tensor
    code otherwise repeats the same recursive selection proof at every
    unrolled loop iteration.
    """
    if isinstance(node, ast.Subscript):
        if record_names is None:
            return False
        if isinstance(node.value, ast.Name) \
                and node.value.id in record_names:
            return False
    elif not isinstance(node, _CONTRACT_PURE_EXPRESSIONS):
        return False
    return all(
        not isinstance(child, ast.expr)
        or _contract_expression_is_pure(
            child, record_names=record_names)
        for child in ast.iter_child_nodes(node)
    )


def _scalar_contract_ignores_bounds(node, env, bindings, abstract, *, expressions=None):
    """Straight-line scalar contracts do not depend on numeric endpoints.

    No selection, collection, call or conditional expression is included:
    those may use ranges to choose a shape or a reached source occurrence.
    A single comparison evaluates both operands without refining either.
    """
    for child in ast.walk(node) if expressions is None else expressions:
        if isinstance(child, ast.Name):
            value = (abstract or {}).get(child.id)
            if value is None:
                value = (contract_of_value(bindings[child.id])
                         if child.id in bindings else env.get(child.id))
            if value != SCALAR:
                return False
        elif isinstance(child, ast.Constant):
            if child.value is None:
                return False
        elif isinstance(child, ast.Compare):
            if len(child.ops) != 1:
                return False
        elif isinstance(child, ast.expr) and not isinstance(
                child, (ast.BinOp, ast.UnaryOp)):
            return False
    return True


_RANGE_BINOPS = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.FloorDiv: "fdiv",
    ast.Mod: "fmod",
    ast.LShift: "shl",
    ast.RShift: "shr",
    ast.BitAnd: "and",
    ast.BitOr: "or",
    ast.BitXor: "xor",
}
_RANGE_UNARY = {ast.UAdd: None, ast.USub: "neg", ast.Invert: "inv"}


def _element_range_key(name):
    """Private range-map key for the integer leaves of a sequence local."""
    return f"#element:{name}"


def _subscript_range_key(node):
    """Range-map key for the integer leaves below one indexed selection."""
    current = node
    while isinstance(current, ast.Subscript):
        current = current.value
    if isinstance(current, ast.Name):
        return _element_range_key(current.id)
    if (isinstance(current, ast.Attribute)
            and isinstance(current.value, ast.Name)
            and current.value.id == "self"):
        return f"self.{current.attr}"
    return None


def _cell_range_key(node, owner, bindings, *, budget, static_value_of=None):
    """One fixed nonnegative position, distinct from its array's leaf hull.

    These are private numeric names, never source bindings or IR storage.
    Python identifiers cannot contain brackets. Negative/dynamic indices,
    locals and nested containers use the conservative aggregate envelope.
    """
    if (not isinstance(node, ast.Subscript)
            or not isinstance(node.value, ast.Attribute)
            or not isinstance(node.value.value, ast.Name)
            or node.value.value.id != 'self'):
        return None
    root = _subscript_range_key(node)
    if root is None or isinstance(node.slice, ast.Slice):
        return None
    index = (static_value_of(node.slice, bindings) if static_value_of is not None else
             _static_value(node.slice, owner, bindings, budget=budget))
    if type(index) is int and index >= 0:
        return f"{root}[{index}]"
    return None


def _numeric_projection(values, roots):
    """Keep scalar dependencies and selected-cell facts owned by those roots."""
    return {name: value for name, value in values.items()
            if name in roots or (isinstance(name, str) and "[" in name
                                 and name.partition("[")[0] in roots)}


def _range_ir(node, owner, bindings, *, budget, observed=None,
              generated=None, static_value_of=None):
    """Translate the side-effect-free integer subset to a range expression."""
    fixed = (static_value_of(node, bindings) if static_value_of is not None else
             _static_value(node, owner, bindings, budget=budget))
    if type(fixed) in (bool, int):
        return ir.Lit(int(fixed))
    if observed is not None:
        interval = observed(node)
        if interval is not None:
            if interval.lo == interval.hi:
                return ir.Lit(interval.lo)
            name = f"#source-range:{id(node)}"
            generated[name] = interval
            return ir.Var(name)
    scalar_bounds = getattr(
        bindings, "bounds", getattr(bindings, "ranges", {}))
    if isinstance(node, ast.Name):
        return ir.Var(node.id)
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        name = f"self.{node.attr}"
        return ir.Var(name)
    if isinstance(node, ast.Subscript):
        cell = _cell_range_key(node, owner, bindings, budget=budget,
                               static_value_of=static_value_of)
        if cell in scalar_bounds:
            return ir.Var(cell)
        name = _subscript_range_key(node)
        if name in scalar_bounds:
            # A leaf envelope is not a shared scalar value. Preserve the
            # selection so x[i] and x[j] cannot cancel or compare as x == x
            # merely because they have the same bounds. Nested selections
            # retain their entire path; bounds validity belongs to lowering.
            index = _range_ir(
                node.slice, owner, bindings, budget=budget,
                observed=observed, generated=generated,
                static_value_of=static_value_of)
            array = (_range_ir(
                node.value, owner, bindings, budget=budget,
                observed=observed, generated=generated,
                static_value_of=static_value_of)
                if isinstance(node.value, ast.Subscript) else ir.Var(name))
            if index is not None and array is not None:
                return ir.Sel(array, index)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _RANGE_UNARY:
        value = _range_ir(
            node.operand, owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        if value is None:
            return None
        op = _RANGE_UNARY[type(node.op)]
        return value if op is None else ir.Op(op, value)
    if isinstance(node, ast.BinOp) and type(node.op) in _RANGE_BINOPS:
        left = _range_ir(
            node.left, owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        right = _range_ir(
            node.right, owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        if left is None or right is None:
            return None
        return ir.Op(_RANGE_BINOPS[type(node.op)], left, right)
    if isinstance(node, ast.IfExp):
        test = _range_ir(
            node.test, owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        body = _range_ir(
            node.body, owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        orelse = _range_ir(
            node.orelse, owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        if test is not None and body is not None and orelse is not None:
            return ir.Op("ite", test, body, orelse)
    if isinstance(node, ast.BoolOp) and node.values:
        parts = [_range_ir(value, owner, bindings, budget=budget,
                           observed=observed, generated=generated,
                           static_value_of=static_value_of)
                 for value in node.values]
        if any(part is None for part in parts):
            return None
        result = parts[-1]
        for value in reversed(parts[:-1]):
            # Python and/or select an operand, not a normalized Boolean.
            # A false represented numeric operand is exactly zero; retaining
            # its unrefined hull would invent a nonzero short-circuit exit.
            result = (ir.Op("ite", ir.Op("truth", value), result, ir.Lit(0))
                      if isinstance(node.op, ast.And)
                      else ir.Op("ite", ir.Op("truth", value), value, result))
        return result
    if isinstance(node, ast.Compare) and len(node.ops) == 1 \
            and len(node.comparators) == 1:
        names = {
            ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge",
            ast.Eq: "eq", ast.NotEq: "ne",
        }
        op = names.get(type(node.ops[0]))
        left = _range_ir(
            node.left, owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        right = _range_ir(
            node.comparators[0], owner, bindings, budget=budget,
            observed=observed, generated=generated, static_value_of=static_value_of)
        if op is not None and left is not None and right is not None:
            return ir.Op(op, left, right)
    return None


def _scalar_range(node, owner, bindings, *, budget, observed=None, static_value_of=None):
    if isinstance(node, ast.BoolOp):
        bounded = _scalar_bounds(node, owner, bindings, budget=budget, observed=observed,
                                 static_value_of=static_value_of)
        return None if bounded is None else bounded.finite()

    def infer(candidate):
        generated = {}
        expression = _range_ir(
            candidate, owner, bindings, budget=budget, observed=observed,
            generated=generated, static_value_of=static_value_of)
        if expression is None:
            return None
        return ranges.of_expression(
            expression,
            {**getattr(bindings, "ranges", {}), **generated})

    # Python integer masking gives this finite envelope independently of the
    # other operand. This is the range fact used by ordinary fixed-width
    # helpers such as ``to_unsigned``; requiring an incoming range for the
    # masked value would discard the very bound the mask establishes.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
        def exact_value(candidate):
            fixed = (static_value_of(candidate, bindings) if static_value_of is not None else
                     _static_value(candidate, owner, bindings, budget=budget))
            if type(fixed) is int:
                return fixed
            interval = infer(candidate)
            return (interval.lo if interval is not None
                    and interval.lo == interval.hi else None)

        left = exact_value(node.left)
        right = exact_value(node.right)
        masks = [value for value in (left, right)
                 if type(value) is int and value >= 0]
        if masks:
            return ranges.Range(0, min(masks))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        # Interval multiplication loses the correlation in ``x * x``.  It is
        # safe to restore it for two identical, side-effect-free scalar reads.
        # Include fixed array selections (the common dot-product form), but
        # exclude calls and assignment expressions whose two evaluations may
        # observe different values.
        def stable_read(candidate):
            if isinstance(candidate, (ast.Name, ast.Constant)):
                return True
            if (isinstance(candidate, ast.Attribute)
                    and isinstance(candidate.value, ast.Name)
                    and candidate.value.id == "self"):
                return True
            if isinstance(candidate, ast.Subscript) \
                    and isinstance(candidate.value, ast.Name):
                return all(not isinstance(inner, (
                    ast.Call, ast.NamedExpr, ast.Await, ast.Yield,
                    ast.YieldFrom, ast.Lambda,
                )) for inner in ast.walk(candidate.slice))
            return False

        same_read = (
            stable_read(node.left)
            and ast.dump(node.left, include_attributes=False)
            == ast.dump(node.right, include_attributes=False))
        if same_read:
            value = infer(node.left)
            if value is not None:
                squares = (value.lo * value.lo, value.hi * value.hi)
                minimum = (0 if value.lo <= 0 <= value.hi else min(squares))
                return ranges.Range(minimum, max(squares))
    if isinstance(node, ast.BinOp) and type(node.op) in _RANGE_BINOPS:
        left = _scalar_range(
            node.left, owner, bindings, budget=budget, observed=observed,
            static_value_of=static_value_of)
        right = _scalar_range(
            node.right, owner, bindings, budget=budget, observed=observed,
            static_value_of=static_value_of)
        if left is not None and right is not None:
            expression = ir.Op(
                _RANGE_BINOPS[type(node.op)], ir.Var("#left"),
                ir.Var("#right"))
            direct = ranges.of_expression(
                expression, {"#left": left, "#right": right})
            if direct is not None:
                return direct
            # A Python division is partial at zero, but every normally
            # produced value still has a finite envelope when both operands
            # do.  Executable lowering separately emits the DivZero branch.
            if isinstance(node.op, ast.FloorDiv) \
                    and not (right.lo == right.hi == 0):
                magnitude = max(abs(left.lo), abs(left.hi))
                return ranges.Range(-magnitude, magnitude)
        return None
    if isinstance(node, ast.IfExp):
        parts = []
        for truth, branch in ((True, node.body), (False, node.orelse)):
            refined = _refine_scalar_ranges(
                node.test, owner, bindings, truth, budget=budget,
                static_value_of=static_value_of)
            if refined is None:
                continue
            interval = _scalar_range(
                branch, owner, refined, budget=budget, observed=observed,
                static_value_of=static_value_of)
            if interval is None:
                return None
            parts.append(interval)
        if parts:
            result = parts[0]
            for interval in parts[1:]:
                result = result.join(interval)
            return result
    return infer(node)


def _scalar_bounds(node, owner, bindings, *, budget, observed=None, static_value_of=None):
    """Possibly one-sided integer bounds of one source expression.

    The finite projection above remains the only fact used for representation
    widths.  This companion keeps facts such as ``x < 32`` long enough to
    prove that ``x if x < 32 else 32`` gives a finite loop ceiling even when
    the task has not supplied a lower bound for ``x``.
    """

    if isinstance(node, ast.BoolOp) and node.values:
        parts = []
        continuing = bindings
        exit_truth = isinstance(node.op, ast.Or)
        for index, operand in enumerate(node.values):
            last = index == len(node.values) - 1
            selected = (continuing if last else _refine_scalar_ranges(
                operand, owner, continuing, exit_truth, budget=budget,
                static_value_of=static_value_of))
            if selected is not None:
                value = _scalar_bounds(
                    operand, owner, selected, budget=budget, observed=observed,
                    static_value_of=static_value_of)
                if value is None:
                    return None
                if not last:
                    selected_value = ranges.assume(
                        ir.Op("truth", ir.Var("#operand")), exit_truth,
                        {"#operand": value})
                    value = (None if selected_value is None else
                             selected_value["#operand"])
                if value is not None:
                    parts.append(value)
            if last:
                break
            continuing = _refine_scalar_ranges(
                operand, owner, continuing, not exit_truth, budget=budget,
                static_value_of=static_value_of)
            if continuing is None:
                break
        if parts:
            result = parts[0]
            for value in parts[1:]:
                result = result.join(value)
            return result
        return None

    if isinstance(node, ast.IfExp):
        parts = []
        for truth, branch in ((True, node.body), (False, node.orelse)):
            refined = _refine_scalar_ranges(
                node.test, owner, bindings, truth, budget=budget,
                static_value_of=static_value_of)
            if refined is None:
                continue
            value = _scalar_bounds(
                branch, owner, refined, budget=budget, observed=observed,
                static_value_of=static_value_of)
            if value is None:
                return None
            parts.append(value)
        if parts:
            result = parts[0]
            for value in parts[1:]:
                result = result.join(value)
            return result

    generated = {}
    expression = _range_ir(
        node, owner, bindings, budget=budget, observed=observed,
        generated=generated, static_value_of=static_value_of)
    if expression is not None:
        result = ranges.bounds_of_expression(
            expression,
            {**getattr(bindings, "bounds", {}), **generated},
        )
        if result is not None:
            return result

    # Masking establishes a nonnegative finite bound independently of the
    # other operand.  Reuse the finite rule rather than requiring that
    # operand to have bounds already.
    finite = _scalar_range(
        node, owner, bindings, budget=budget, observed=observed,
        static_value_of=static_value_of)
    return None if finite is None else ranges.Bounds.of(finite)


def _refine_scalar_ranges(node, owner, bindings, truth, *, budget, static_value_of=None):
    expression = _range_ir(node, owner, bindings, budget=budget,
                           static_value_of=static_value_of)
    if expression is None:
        return bindings.clone()
    refined = ranges.assume(ir.Op("truth", expression), truth, bindings.bounds)
    if refined is None:
        return None
    return _ScalarBindings(
        bindings, scalar_bounds=refined,
        guard_facts=getattr(bindings, "guards", {}))


def _while_body_is_scalar_contract_only(node):
    """Whether one abstract body visit closes its source-value contracts.

    Scalar contracts do not grow with the number of visits.  Collection
    construction, indexing and calls can change a recursive contract or invoke
    another summary, so those retain the path interpreter below.  This split is
    about the source-value lattice, not a trip-count threshold: a million-visit
    scalar loop and a one-visit scalar loop take the same planning work.
    """
    changing = (
        ast.Await,
        ast.Call,
        ast.ClassDef,
        ast.Dict,
        ast.DictComp,
        ast.FunctionDef,
        ast.GeneratorExp,
        ast.Lambda,
        ast.List,
        ast.ListComp,
        ast.NamedExpr,
        ast.Set,
        ast.SetComp,
        ast.Subscript,
        ast.Tuple,
        ast.Yield,
        ast.YieldFrom,
    )
    statements = (
        ast.AnnAssign,
        ast.Assign,
        ast.AugAssign,
        ast.Break,
        ast.Continue,
        ast.Expr,
        ast.If,
        ast.Pass,
        ast.Return,
    )
    roots = node.body if isinstance(node, ast.While) else (node,)
    return all(
        not isinstance(inner, changing)
        and (not isinstance(inner, ast.stmt)
             or isinstance(inner, statements))
        for root in roots for inner in ast.walk(root)
    )


def _fresh_sequence_expression(node):
    """Whether ``node`` allocates a new finite sequence carrier.

    Literal displays and list comprehensions allocate directly.  Repetition
    also allocates a new outer carrier when exactly one operand is such a
    sequence; the ordinary source contract remains responsible for proving the
    other operand to be a finite integer and for rejecting mutable-element
    aliasing during lowering.
    """
    if isinstance(node, (ast.List, ast.Tuple, ast.ListComp)):
        return True
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Mult):
        return False
    left = _fresh_sequence_expression(node.left)
    right = _fresh_sequence_expression(node.right)
    return left != right


def _iteration_local_sequences(node):
    """Unconditional sequence bindings fresh for every iteration.

    Only direct statements in the loop body qualify.  A read before the
    binding or a conditional binding remains on the precise path.  Literal
    and comprehension initializers allocate a new carrier whenever their
    statement executes.  The statement identity lets the transfer checker
    admit that initializing write without admitting a later representation
    change.
    """
    read = set()
    names = set()
    assignments = set()
    for stmt in node.body:
        target = value = None
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(
                stmt.target, ast.Name) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        if (target is not None and target.id not in read
                and _fresh_sequence_expression(value)):
            names.add(target.id)
            assignments.add(id(stmt))
        read.update(
            inner.id for inner in ast.walk(stmt)
            if isinstance(inner, ast.Name)
            and isinstance(inner.ctx, ast.Load))
    return frozenset(names), frozenset(assignments)


def _for_body_has_stable_contract_transfer(
        node, resolver, owner, sequence_names=frozenset(), *,
        allow_loop_exits=False):
    """Whether one visit determines a ``for`` body's contract transfer.

    Read-only indexing, element stores, per-iteration fresh carriers and
    append-only growth without feedback have a monotone finite transfer.
    Replaying it once per possible index is path enumeration, not abstract
    interpretation. Aliases,
    representation changes, opaque effects and nonlocal exits retain the
    precise interpreter.
    """
    if not isinstance(node.target, ast.Name):
        return False
    fresh_sequences, fresh_assignments = \
        _iteration_local_sequences(node)
    effective_sequences = sequence_names | fresh_sequences
    cache = resolver.__dict__.setdefault(
        "_sequence_transfer_stability_cache", {})
    cache_key = (node, owner, effective_sequences, allow_loop_exits)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    append_receivers = {
        inner.func.value for root in node.body for inner in ast.walk(root)
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
        and inner.func.attr == "append" and isinstance(inner.func.value, ast.Name)
    }
    appended = {receiver.id for receiver in append_receivers}
    if any(isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)
           and inner.id in appended and inner not in append_receivers
           for root in node.body for inner in ast.walk(root)):
        # A growing sequence can acquire its element contract on the first
        # visit and feed it back on later visits. One visit with all possible
        # indexes would read the pre-growth empty value on those later paths.
        # Include indirect reads through helpers and length/truth guards;
        # only the append receiver itself is not a read of the old contents.
        cache[cache_key] = False
        return False

    def position(candidate):
        line = getattr(candidate, "lineno", None)
        column = getattr(candidate, "col_offset", None)
        return None if line is None or column is None else (line, column)

    def is_final_use(name, value):
        """Whether a fresh local cannot be mutated after this record read."""
        inside = [position(inner) for inner in ast.walk(value)
                  if isinstance(inner, ast.Name) and inner.id == name]
        outside = [position(inner) for root in node.body
                   for inner in ast.walk(root)
                   if isinstance(inner, ast.Name) and inner.id == name]
        return (inside and None not in inside and None not in outside
                and max(inside) == max(outside))

    def fresh_initializer_is_stable(value):
        """Whether one fresh carrier has one abstract transfer this visit."""
        if not _fresh_sequence_expression(value):
            return False
        for inner in ast.walk(value):
            if isinstance(inner, (ast.Await, ast.Lambda, ast.NamedExpr,
                                  ast.Yield, ast.YieldFrom)):
                return False
            if (isinstance(inner, ast.Name)
                    and isinstance(inner.ctx, ast.Load)
                    and inner.id in effective_sequences):
                return False
            if (isinstance(inner, ast.Call)
                    and not resolver.call_has_no_external_effect(
                        owner, inner)):
                return False
        return True

    def is_element_store(target):
        if not isinstance(target, ast.Subscript):
            return False
        root = target
        while isinstance(root, ast.Subscript):
            root = root.value
        return isinstance(root, ast.Name) and root.id in effective_sequences

    def stable_assignment_target(target):
        if isinstance(target, ast.Name):
            return target.id not in effective_sequences
        if is_element_store(target):
            return True
        if isinstance(target, (ast.Tuple, ast.List)):
            return (not any(isinstance(item, ast.Starred)
                            for item in target.elts)
                    and all(stable_assignment_target(item)
                            for item in target.elts))
        return False

    def stable_unpacking(stmt):
        if (not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1
                or not isinstance(stmt.targets[0], (ast.Tuple, ast.List))
                or not isinstance(stmt.value, (ast.Tuple, ast.List))):
            return None
        target, value = stmt.targets[0], stmt.value
        if (len(target.elts) != len(value.elts)
                or any(isinstance(item, ast.Starred)
                       for item in (*target.elts, *value.elts))):
            return False
        if not stable_assignment_target(target):
            return False
        return all(safe(item) for item in target.elts) and all(
            safe(item) for item in value.elts)

    def stable_record_payload(value):
        """Whether one append contributes one stable record contract.

        The record itself may contain arbitrary scalar expressions, including
        fixed-shape reads and resolved helpers.  Nested collection values and
        a direct reference to a live sequence remain on the precise path: one
        visit would not prove that their recursive element contract has
        reached a fixed point.
        """
        if not isinstance(value, ast.Dict) or not value.keys:
            return False
        if any(not isinstance(key, ast.Constant)
               or type(key.value) is not str for key in value.keys):
            return False
        for field in value.values:
            if any(isinstance(inner, (
                    ast.Dict, ast.DictComp, ast.GeneratorExp, ast.List,
                    ast.ListComp, ast.Set, ast.SetComp, ast.Tuple,
            )) for inner in ast.walk(field)):
                return False
            referenced_sequences = {
                inner.id for inner in ast.walk(field)
                if isinstance(inner, ast.Name)
                and isinstance(inner.ctx, ast.Load)
                and inner.id in effective_sequences
            }
            if referenced_sequences - fresh_sequences:
                return False
            if any(not is_final_use(name, value)
                   for name in referenced_sequences):
                return False
            if not safe(field):
                return False
        return True

    def safe(inner):
        if isinstance(inner, ast.For):
            # A nested stable loop is itself one abstract visit.  Its
            # range expression chooses only how often that stable transfer is
            # applied; the ordinary loop branch still proves the finite bound.
            plain_range = (
                isinstance(inner.iter, ast.Call)
                and isinstance(inner.iter.func, ast.Name)
                and inner.iter.func.id == "range"
                and resolver.is_builtin(owner, "range")
                and not inner.iter.keywords
                and not any(isinstance(arg, ast.Starred)
                            for arg in inner.iter.args)
            )
            return (plain_range
                    and _for_body_has_stable_contract_transfer(
                        inner, resolver, owner, effective_sequences)
                    and all(safe(stmt) for stmt in inner.orelse))
        if isinstance(inner, (ast.Break, ast.Continue)):
            return allow_loop_exits
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ClassDef, ast.Return, ast.Dict,
                              ast.DictComp, ast.GeneratorExp, ast.List,
                              ast.ListComp, ast.Set, ast.SetComp, ast.Tuple)):
            return False
        if isinstance(inner, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            unpacking = stable_unpacking(inner)
            if unpacking is not None:
                return unpacking
            if id(inner) in fresh_assignments:
                value = getattr(inner, "value", None)
                return fresh_initializer_is_stable(value)
            targets = (inner.targets if isinstance(inner, ast.Assign) else
                       (inner.target,))
            if any(not stable_assignment_target(target)
                   for target in targets):
                return False
            if any(isinstance(target, ast.Name)
                   and target.id in effective_sequences
                   for target in targets):
                return False
        if isinstance(inner, ast.NamedExpr) \
                and not isinstance(inner.target, ast.Name):
            return False
        if isinstance(inner, ast.Call):
            reads_length = (
                isinstance(inner.func, ast.Name)
                and inner.func.id == "len"
                and resolver.is_builtin(owner, "len")
                and len(inner.args) == 1
                and not inner.keywords
            )
            appends_scalar = (
                isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "append"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in effective_sequences
                and len(inner.args) == 1
                and not inner.keywords
            )
            appends_record = (
                appends_scalar
                and stable_record_payload(inner.args[0])
            )
            if appends_record:
                # Do not descend through the Dict node: its fields were
                # checked above and the receiver is the already-known growing
                # sequence.  One visit establishes the element contract; the
                # loop summary below accounts for its length delta.
                return True
            helper = scalar_builtin = False
            if isinstance(inner.func, ast.Name):
                kind, _target = resolver.resolve_call(owner, inner.func.id)
                helper = kind in {"nested", "module", "alias", "intrinsic"}
                if kind == "builtin":
                    # Reuse the value-contract authority: only a call whose
                    # result is scalar independently of argument contracts
                    # belongs to this transfer. Children are checked below;
                    # this query neither executes them nor guesses their type.
                    scalar_builtin = contract_of_expression(
                        inner,
                        is_builtin=lambda name, _bindings:
                            resolver.is_builtin(owner, name),
                        child_query=lambda *_args: UNKNOWN,
                    ) == SCALAR
            elif (isinstance(inner.func, ast.Attribute)
                  and isinstance(inner.func.value, ast.Name)
                  and inner.func.value.id == "self"):
                kind, _target = resolver.resolve_method(
                    owner, inner.func.attr)
                helper = kind == "method"
            if helper and not resolver.call_has_no_external_effect(owner, inner):
                return False
            if not (reads_length or appends_scalar or helper or scalar_builtin):
                return False
        return all(safe(child) for child in ast.iter_child_nodes(inner))

    answer = all(safe(root) for root in node.body)
    cache[cache_key] = answer
    return answer


def _for_target_affects_body(node, resolver, owner):
    """Whether a loop target can change one abstract body transfer.

    A syntactic load is the ordinary case.  A call to a nested function is
    conservatively included because it can capture the target without naming
    it at the call site.  Module functions and methods cannot capture a local
    activation of ``owner``.
    """
    if not isinstance(node.target, ast.Name):
        return True
    name = node.target.id
    for root in node.body:
        for inner in ast.walk(root):
            if (isinstance(inner, ast.Name)
                    and isinstance(inner.ctx, ast.Load)
                    and inner.id == name):
                return True
            if isinstance(inner, (ast.Lambda, ast.Yield, ast.YieldFrom,
                                  ast.Await)):
                return True
            if isinstance(inner, ast.Call) and isinstance(
                    inner.func, ast.Name):
                kind, _target = resolver.resolve_call(owner, inner.func.id)
                if kind in {"nested", "alias"}:
                    return True
            if (isinstance(inner, ast.Call)
                    and not resolver.call_has_no_external_effect(
                        owner, inner)):
                return True
    return False


def _bounded_scalar_loop_recurrences(node):
    """Affine loop-carried locals that admit one finite abstract visit.

    The ordinary source interpreter replays a loop once per possible trip.
    That is necessary for an arbitrary recurrence.  A local updated exactly
    once by ``+=`` or ``-=`` with a delta independent of every body-written
    scalar is different: its value before any visit is contained by its entry
    interval plus at most ``maximum - 1`` deltas.  Names freshly assigned by a
    top-level body statement before their first read do not carry a value
    between visits.  Every other read/write name keeps the precise path.

    Return ``(recurrences, written)`` when this syntactic proof applies.
    ``recurrences`` maps a local to its signed delta expression.  Attribute
    state is deliberately excluded; a loop that reads and writes state is
    left to the ordinary interpreter.
    """
    writes = {}
    loads = set()
    unsupported = False

    def spelling(candidate):
        if isinstance(candidate, ast.Name):
            return candidate.id
        if (isinstance(candidate, ast.Attribute)
                and isinstance(candidate.value, ast.Name)
                and candidate.value.id == "self"):
            return f"self.{candidate.attr}"
        return None

    def visit(candidate):
        nonlocal unsupported
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
            return
        if isinstance(candidate, (ast.For, ast.While, ast.AsyncFor)):
            # A nested recurrence has a different trip bound.  The outer
            # summary cannot multiply its delta without a separate proof.
            unsupported = True
            return
        if isinstance(candidate, (ast.Call, ast.Global, ast.Nonlocal,
                                  ast.Try, ast.TryStar, ast.With,
                                  ast.AsyncWith, ast.Match)):
            # Calls and exceptional/dynamic scope can change a local without
            # an assignment visible in this body.  Their ordinary semantics
            # remain supported by the precise interpreter; this acceleration
            # simply does not claim a recurrence for them.
            unsupported = True
            return
        if isinstance(candidate, ast.AugAssign):
            name = spelling(candidate.target)
            if name is not None:
                writes.setdefault(name, []).append(candidate)
                loads.add(name)
            else:
                visit(candidate.target)
            visit(candidate.value)
            return
        if isinstance(candidate, ast.Name):
            if isinstance(candidate.ctx, ast.Load):
                loads.add(candidate.id)
            elif isinstance(candidate.ctx, (ast.Store, ast.Del)):
                writes.setdefault(candidate.id, []).append(candidate)
            return
        if isinstance(candidate, ast.Attribute):
            name = spelling(candidate)
            if name is not None:
                if isinstance(candidate.ctx, ast.Load):
                    loads.add(name)
                elif isinstance(candidate.ctx, (ast.Store, ast.Del)):
                    writes.setdefault(name, []).append(candidate)
                return
        for child in ast.iter_child_nodes(candidate):
            visit(child)

    for statement in node.body:
        visit(statement)
    if unsupported:
        return None

    written = frozenset(writes)
    recurrences = {}
    for name, occurrences in writes.items():
        if (name.startswith("self.") or len(occurrences) != 1
                or not isinstance(occurrences[0], ast.AugAssign)
                or not isinstance(occurrences[0].op, (ast.Add, ast.Sub))):
            continue
        update = occurrences[0]
        if not _contract_expression_is_pure(
                update.value, record_names=frozenset()):
            continue
        read = {
            child.id for child in ast.walk(update.value)
            if isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Load)
        }
        read.update(
            f"self.{child.attr}" for child in ast.walk(update.value)
            if isinstance(child, ast.Attribute)
            and isinstance(child.ctx, ast.Load)
            and isinstance(child.value, ast.Name)
            and child.value.id == "self"
        )
        if read & written:
            continue
        sign = 1 if isinstance(update.op, ast.Add) else -1
        recurrences[name] = (sign, update.value)

    # A direct body assignment before the first read resets a local on every
    # back edge, so it is not loop-carried.  Restrict this proof to a simple
    # top-level assignment; conditional definitions remain on the exact path.
    fresh = set()
    read_so_far = set()
    for statement in node.body:
        target = None
        if (isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)):
            target = statement.targets[0]
        elif (isinstance(statement, ast.AnnAssign)
              and isinstance(statement.target, ast.Name)
              and statement.value is not None):
            target = statement.target
        statement_loads = {
            child.id for child in ast.walk(statement)
            if isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Load)
        }
        if (target is not None and target.id not in read_so_far
                and target.id not in statement_loads):
            fresh.add(target.id)
        read_so_far.update(statement_loads)

    carried = (written & loads) - fresh
    if carried - set(recurrences):
        return None
    return recurrences, written


def _stores_loop_carried_elements(node, resolver, owner, *, widened=frozenset()):
    """Whether a loop stores into a container a value earlier visits can change.

    One abstract body visit computes that value from its loop-entry facts,
    such as an accumulator's initial value. The stored element envelope would
    then hold for the first visit only, so the precise interpreter replays
    the visits instead. ``widened`` names enter the visit with their
    whole-loop envelope and do not count.
    """
    carried = set(_function_scope_store_names(node.body))
    carried.update(loop_container_writes(node, resolver=resolver, owner=owner))
    carried.update(
        f"self.{inner.attr}" for statement in node.body for inner in ast.walk(statement)
        if isinstance(inner, ast.Attribute) and isinstance(inner.ctx, (ast.Store, ast.Del))
        and isinstance(inner.value, ast.Name) and inner.value.id == "self")
    carried -= set(widened)
    for statement in node.body:
        for inner in ast.walk(statement):
            if isinstance(inner, ast.Assign):
                targets, value = inner.targets, inner.value
            elif isinstance(inner, (ast.AugAssign, ast.AnnAssign)):
                targets, value = [inner.target], inner.value
            else:
                continue
            stores = [part for target in targets for part in ast.walk(target)
                      if isinstance(part, ast.Subscript) and isinstance(part.ctx, ast.Store)]
            if not stores or value is None:
                continue
            # An augmented store also reads the element it replaces.
            for read in (value, *(stores if isinstance(inner, ast.AugAssign) else ())):
                for child in ast.walk(read):
                    if isinstance(child, ast.Call) and not resolver.call_has_no_external_effect(
                            owner, child):
                        return True
                    name = (child.id if isinstance(child, ast.Name)
                            and isinstance(child.ctx, ast.Load) else
                            f"self.{child.attr}" if isinstance(child, ast.Attribute)
                            and isinstance(child.value, ast.Name)
                            and child.value.id == "self" else None)
                    if name in carried:
                        return True
    return False


# Exact short loops preserve activation-specific closure facts cheaply.  The
# scalar transfer summary is for the larger finite loops where replaying the
# same monotone contract transfer dominates source planning.  Keeping this
# threshold in runtime rather than treating it as a language capability means
# either route denotes the same admitted source values. It chooses precision
# versus analysis work, not an accepted loop-length limit.
_CONTRACT_LOOP_SUMMARY_MIN_TRIPS = defaults.SIM_BEHAVIOR_LOOP_SUMMARY_MIN_TRIPS


def _function_scope_store_names(nodes):
    """Names assigned by statements, excluding definitions nested within."""
    names = set()

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for node in nodes:
        visit(node)
    return names


def _function_scope_scalar_store_names(nodes):
    """Scalar locals and model fields assigned by the current activation."""
    names = set()

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            return
        if isinstance(getattr(node, "ctx", None), ast.Store):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif (isinstance(node, ast.Attribute)
                  and isinstance(node.value, ast.Name)
                  and node.value.id == "self"):
                names.add(f"self.{node.attr}")
        for child in ast.iter_child_nodes(node):
            visit(child)

    for node in nodes:
        visit(node)
    return names


def _block_reaches_back_edge(statements):
    """Whether one entered path can reach its enclosing loop head.

    This is a control fact, not a trip-count proof.  It lets source-contract
    analysis interpret loops whose entered paths all break or return without
    inventing an induction variable.  Unmodelled compound control is treated
    conservatively as a possible back edge.
    """
    live = True
    for stmt in statements:
        if not live:
            break
        if isinstance(stmt, (ast.Break, ast.Return)):
            live = False
        elif isinstance(stmt, ast.Continue):
            return True
        elif isinstance(stmt, ast.If):
            live = (_block_reaches_back_edge(stmt.body)
                    or _block_reaches_back_edge(stmt.orelse))
        elif isinstance(stmt, (ast.For, ast.While, ast.Try, ast.Match,
                               ast.With, ast.AsyncFor, ast.AsyncWith)):
            return True
    return live


class _Spec:
    __slots__ = (
        "capacity", "element", "fields", "is_batch", "is_growing",
        "is_mutated",
    )

    def __init__(self, capacity, fields=(), is_batch=False, is_growing=True,
                 element=None, is_mutated=False):
        self.capacity = int(capacity)
        if element is not None and not isinstance(element, ValueContract):
            raise TypeError("a sequence element needs a ValueContract")
        self.element = element
        self.fields = None if fields is None else tuple(fields)
        self.is_batch = bool(is_batch)
        self.is_growing = bool(is_growing)
        # Building a comprehension needs a temporary length even when the
        # completed value has an exact length.  A later append/pop is
        # different: the source binding continues to need that run-time
        # length.  Keep the two facts separate rather than treating every
        # builder as a mutable value.
        self.is_mutated = bool(is_mutated)

    @property
    def elem_shape(self):
        """The element's fixed IR shape, retained as a derived view."""
        return None if self.element is None else self.element.fixed_shape


class _ContextKey:
    """Structural activation identity whose recursive hash is computed once."""

    __slots__ = ("_hash", "_parts")

    def __init__(self, parts):
        self._parts = tuple(parts)
        self._hash = hash(self._parts)

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return (self is other or (
            type(other) is type(self) and self._parts == other._parts))

    def __len__(self):
        return len(self._parts)

    def __iter__(self):
        return iter(self._parts)

    def __getitem__(self, index):
        return self._parts[index]

    def __repr__(self):
        return repr(self._parts)

    def __reduce__(self):
        return type(self), (self._parts,)


@dataclass(frozen=True)
class _ContextSet:
    """Several exact source activations represented by one IR body."""

    activations: frozenset

    def __post_init__(self):
        if not self.activations:
            raise ValueError("an aggregate summary context cannot be empty")


def _fixed_may_match(planned, current):
    """Whether a planner's typed fixed fact can equal the current fact."""
    if planned[0] == "unproved" or current is None:
        return True
    return planned == _fixed_key(current)


class SequencePlan:
    """Finite capacities and transaction roles, keyed by lexical variable.

    A spelling is not an identity: a helper and the step may both use ``out``.
    Keys therefore contain the owning ``FunctionDef``.  The AST is the same AST
    consumed by lowering, so object identity is stable for the life of a plan.
    """

    __slots__ = (
        "_batch_plans",
        "_binding_batch_contracts",
        "_binding_batch_plans",
        "_binding_contracts",
        "_binding_occurrence_contracts",
        "_branch_outcomes",
        "_call_contexts",
        "_call_contracts",
        "_call_targets",
        "_computed_keys",
        "_context_functions",
        "_expression_contracts",
        "_expression_ranges",
        "_initial_ranges",
        "_loop_spans",
        "_loop_domains",
        "_mapping_keys",
        "_reached_returns",
        "_record_builders",
        "_record_ranges",
        "_records",
        "_return_capacity",
        "_return_plans",
        "_root_context",
        "_specs",
        "_state_contracts",
        "_state_storage_contracts",
        "_statement_contexts",
        "output_contract",
        "output_fields",
    )

    def __init__(self, specs=(), return_capacity=(), computed_keys=(),
                 mapping_keys=(), output_fields=(), records=(),
                 record_builders=(), return_plans=(),
                 call_contracts=(), call_contexts=(),
                 call_targets=(), binding_contracts=(),
                 binding_occurrence_contracts=(),
                 expression_contracts=(), expression_ranges=(), batch_plans=(),
                 binding_batch_plans=(), binding_batch_contracts=(),
                 context_functions=(),
                 initial_ranges=(), loop_spans=(), root_context=None,
                 output_contract=None, state_contracts=(), state_storage_contracts=None,
                 reached_returns=(), record_ranges=(),
                 statement_contexts=(), branch_outcomes=(), loop_domains=()):
        self._specs = dict(specs)
        self._binding_contracts = dict(binding_contracts)
        self._binding_occurrence_contracts = dict(
            binding_occurrence_contracts)
        self._expression_contracts = dict(expression_contracts)
        self._expression_ranges = dict(expression_ranges)
        self._batch_plans = dict(batch_plans)
        self._binding_batch_plans = dict(binding_batch_plans)
        self._binding_batch_contracts = dict(binding_batch_contracts)
        self._call_contracts = dict(call_contracts)
        # Lowering asks about one call occurrence at a time. Keep its exact
        # activation variants together instead of scanning every source edge.
        self._call_contexts = {}
        for key, contract in dict(call_contexts).items():
            self._call_contexts.setdefault(key[1], []).append((key, contract))
        self._call_targets = dict(call_targets)
        self._context_functions = dict(context_functions)
        self._initial_ranges = dict(initial_ranges)
        self._return_capacity = dict(return_capacity)
        self._computed_keys = dict(computed_keys)
        self._mapping_keys = dict(mapping_keys)
        self._loop_spans = dict(loop_spans)
        self._loop_domains = dict(loop_domains)
        self._records = dict(records)
        self._record_builders = frozenset(record_builders)
        self._record_ranges = dict(record_ranges)
        self._reached_returns = frozenset(reached_returns)
        self._return_plans = dict(return_plans)
        self._root_context = root_context
        self._state_contracts = dict(state_contracts)
        self._state_storage_contracts = dict(
            self._state_contracts if state_storage_contracts is None else
            state_storage_contracts)
        self._statement_contexts = frozenset(statement_contexts)
        self._branch_outcomes = {
            key: frozenset(outcomes)
            for key, outcomes in branch_outcomes
        }
        if output_contract is not None \
                and not isinstance(output_contract, ValueContract):
            raise TypeError("an output contract needs a ValueContract")
        if output_contract is not None and not output_contract.is_record:
            raise ValueError("an output contract must be a record")
        self.output_contract = output_contract
        self.output_fields = tuple(output_fields)

    @property
    def root_context(self):
        return self._root_context

    @property
    def state_contracts(self):
        """Bounded recursive contracts of source state at a step boundary."""
        return dict(self._state_contracts)

    @property
    def state_storage_contracts(self):
        """Backing layouts covering boundary and reached in-step state values."""
        return dict(self._state_storage_contracts)

    def loop_span(self, node, scope=None, context=None):
        """Task-domain trip-count interval for a reached source while."""
        contexts = self._contexts_for(scope, context) if scope is not None \
            else ((self._root_context,) if self._root_context is not None else ())
        found = [self._loop_spans.get((candidate, id(node)))
                 for candidate in contexts
                 if (candidate, id(node)) in self._statement_contexts]
        if not found or any(item is None for item in found):
            return None
        return ranges.Range(
            min(item.lo for item in found),
            max(item.hi for item in found),
        )

    def iteration_span(self, node, iterable, scope=None, context=None):
        """Finite trip-count interval proved for one source iteration.

        Statement loops have a direct control-flow fact.  A comprehension has
        no statement node in that analysis, but its exact iterable occurrence
        still has a logical-length contract.  Both facts describe the same
        source quantity: the number of body visits.
        """
        direct = self.loop_span(node, scope, context)
        if direct is not None:
            return direct
        if scope is None:
            return None
        contract = self.expression_contract(scope, iterable, context)
        if (not contract.is_sequence or contract.minimum is None
                or contract.maximum is None):
            return None
        return ranges.Range(contract.minimum, contract.maximum)

    def loop_domain(self, node, scope, context=None):
        """The proof kind and induction envelope owned by source planning."""
        found = [self._loop_domains.get((candidate, id(node)))
                 for candidate in self._contexts_for(scope, context)
                 if (candidate, id(node)) in self._statement_contexts]
        if not found or any(item is None for item in found):
            return None
        kinds = {kind for kind, _entry in found}
        if len(kinds) != 1:
            return None
        entries = [entry for _kind, entry in found]
        entry = (None if any(item is None for item in entries) else
                 ranges.Range(min(item.lo for item in entries), max(item.hi for item in entries)))
        return found[0][0], entry

    def initial_ranges(self, scope, context=None):
        """Finite scalar facts at one planned activation boundary.

        An aggregate lowering context denotes several exact source
        activations.  Retain only names ranged in every member and join their
        intervals; this is the source-domain envelope seen by the shared IR
        body, never a value inferred from one selected call.
        """
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return {}
        maps = [self._initial_ranges.get(candidate, {})
                for candidate in contexts]
        names = set(maps[0])
        for mapping in maps[1:]:
            names &= set(mapping)
        result = {}
        for name in names:
            interval = maps[0][name]
            for mapping in maps[1:]:
                interval = interval.join(mapping[name])
            result[name] = interval
        return result

    def combine_contexts(self, contexts):
        """One exact context, or a checked envelope of exact contexts."""
        contexts = frozenset(contexts)
        if not contexts:
            return None
        owners = {self._context_functions.get(context)
                  for context in contexts}
        if None in owners or len(owners) != 1:
            return None
        return (next(iter(contexts)) if len(contexts) == 1 else
                _ContextSet(contexts))

    def _contexts_for(self, scope, context):
        """Exact source activations denoted by one lowering context.

        Graph lowering normally carries one exact activation.  A compact
        lowering can represent several finite source activations in one
        command body; it carries their ``frozenset`` and consumes the joined
        representation facts below.  The set contains only contexts that the
        source planner actually reached, never values invented from an
        interval hull.
        """
        if isinstance(context, _ContextSet):
            matches = tuple(sorted(
                (candidate for candidate in context.activations
                 if self._context_functions.get(candidate) is scope),
                key=repr))
            return (matches if len(matches) == len(context.activations)
                    and matches else ())
        if context is not None:
            return ((context,) if self._context_functions.get(context) is scope
                    else ())
        if self._root_context is not None \
                and self._context_functions.get(self._root_context) is scope:
            return (self._root_context,)
        matches = [candidate for candidate, owner
                   in self._context_functions.items() if owner is scope]
        return (matches[0],) if len(matches) == 1 else ()

    def _context_for(self, scope, context):
        """Compatibility view for callers that require one exact context."""
        matches = self._contexts_for(scope, context)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _merge_specs(specs):
        specs = tuple(specs)
        if not specs:
            return None
        first = specs[0]
        identity = (first.fields, first.is_batch, first.is_growing)
        if any((spec.fields, spec.is_batch, spec.is_growing) != identity
               for spec in specs[1:]):
            return None
        element = first.element
        for spec in specs[1:]:
            if element is None:
                element = spec.element
            elif spec.element is not None:
                element = join_contracts(element, spec.element)
                if element is INCOMPATIBLE:
                    return None
        return _Spec(
            max(spec.capacity for spec in specs), first.fields,
            is_batch=first.is_batch, is_growing=first.is_growing,
            element=element,
            is_mutated=any(spec.is_mutated for spec in specs))

    def spec(self, scope, name, context=None):
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return None
        specs = [self._specs.get((candidate, str(name)))
                 for candidate in contexts]
        if len(contexts) == 1:
            return specs[0]
        if all(spec is None for spec in specs):
            return None
        merged = (None if any(spec is None for spec in specs) else
                  self._merge_specs(specs))
        if merged is None:
            raise LoweringError(
                scope, f"the source activations of {name} require different "
                       "sequence representations",
                code="sequence-plan-inconsistent")
        return merged

    def capacity(self, scope, name, context=None):
        got = self.spec(scope, name, context)
        return None if got is None else got.capacity

    def grows(self, scope, name, context=None):
        got = self.spec(scope, name, context)
        return bool(got is not None and got.is_growing)

    def mutates(self, scope, name, context=None):
        """Whether source operations can change this binding's length."""

        got = self.spec(scope, name, context)
        return bool(got is not None and got.is_mutated)

    def element_shape(self, scope, name, context=None):
        """The fixed recursive shape of one element, if the plan proves it."""
        got = self.spec(scope, name, context)
        return None if got is None else got.elem_shape

    def element_contract(self, scope, name, context=None):
        """The full source contract of one element, if one was observed."""
        got = self.spec(scope, name, context)
        return None if got is None else got.element

    def binding_contract(self, scope, name, context=None, occurrence=None):
        """Contract bound by one assignment, or all assignments to ``name``.

        Lowering asks with the source target occurrence.  The aggregate view
        remains useful to audits, but cannot choose a representation at one
        assignment: a variable may deliberately change from tuple to list at
        a later, definitely executed statement.
        """
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return UNKNOWN
        table = (self._binding_occurrence_contracts
                 if occurrence is not None else self._binding_contracts)
        suffix = id(occurrence) if occurrence is not None else str(name)
        # An exact source occurrence can be absent from one activation because
        # that activation takes another branch.  Absence is not an observed
        # UNKNOWN value; definite assignment is checked by the CFG/SSA layer.
        # Aggregate name queries retain the old all-activation requirement.
        present = ([candidate for candidate in contexts
                    if (candidate, suffix) in table]
                   if occurrence is not None else list(contexts))
        if not present:
            return UNKNOWN
        got = table.get((present[0], suffix), UNKNOWN)
        for candidate in present[1:]:
            got = join_contracts(
                got, table.get((candidate, suffix), UNKNOWN))
        if got is INCOMPATIBLE:
            raise LoweringError(
                scope, f"the source activations of {name} bind values with "
                       "incompatible representations",
                code="source-value-contract-disagrees")
        return got

    def binding_layout_contract(self, scope, name, context=None):
        """Common source contract available for choosing physical storage.

        A lexical name may deliberately change Python carrier while retaining
        one SolverIR shape.  That has no common source contract, but it is not
        an error at either assignment: an operation that observes the carrier
        must reject it later.  Compatible alternatives (notably different
        logical lengths of the same carrier) still return their joined
        contract so the lowerer can allocate one bounded layout.
        """
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return UNKNOWN
        values = [self._binding_contracts.get(
            (candidate, str(name)), UNKNOWN) for candidate in contexts]
        got = values[0]
        for value in values[1:]:
            got = join_contracts(got, value)
        return UNKNOWN if got is INCOMPATIBLE else got

    def expression_contract(self, scope, occurrence, context=None):
        """Contract at one evaluated expression under these activations."""
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return UNKNOWN
        key = id(occurrence)
        present = [candidate for candidate in contexts
                   if (candidate, key) in self._expression_contracts]
        if not present:
            return UNKNOWN
        got = self._expression_contracts[(present[0], key)]
        for candidate in present[1:]:
            got = join_contracts(
                got, self._expression_contracts[(candidate, key)])
        if got is INCOMPATIBLE:
            raise LoweringError(
                occurrence, "the source activations evaluate this expression "
                            "with incompatible representations",
                code="source-value-contract-disagrees")
        return got

    def expression_range(self, scope, occurrence, context=None):
        """Finite integer range at one evaluated source occurrence.

        The same context-sensitive source execution that fixes value
        contracts and transaction plans produces this range.  An occurrence
        absent from one activation simply lies on another branch; a reached
        activation with an unproved range makes the aggregate unproved.
        """
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return None
        key = id(occurrence)
        present = [candidate for candidate in contexts
                   if (candidate, key) in self._expression_ranges]
        if not present:
            return None
        values = [self._expression_ranges[(candidate, key)]
                  for candidate in present]
        if any(value is None for value in values):
            return None
        result = values[0]
        for value in values[1:]:
            result = result.join(value)
        return result

    def statement_reached(self, scope, statement, context=None):
        """Whether a statement executes in any represented activation."""

        contexts = self._contexts_for(scope, context)
        return any((candidate, id(statement)) in self._statement_contexts
                   for candidate in contexts)

    def branch_feasible(self, scope, condition, truth, context=None):
        """Return whether one observed condition polarity is reachable.

        ``None`` means that the source plan has no fact for this occurrence.
        A Boolean result is produced only when the source interpreter reached
        the condition; a missing polarity then denotes a proved-empty path.
        """
        if type(truth) is not bool:
            raise TypeError("a source branch polarity is Boolean")
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return None
        key = id(condition)
        observed = [
            self._branch_outcomes[(candidate, key)]
            for candidate in contexts
            if (candidate, key) in self._branch_outcomes
        ]
        if not observed:
            return None
        return any(truth in outcomes for outcomes in observed)

    def batch_fields(self, scope, name, context=None):
        got = self.spec(scope, name, context)
        return (() if got is None or not got.is_batch or got.fields is None
                else got.fields)

    def capacity_of_return(self, node):
        return self._return_capacity.get(id(node), 1)

    def return_reached(self, node):
        """Whether this return is reachable in the planned state layers."""
        return id(node) in self._reached_returns

    def return_plan(self, node, scope=None, context=None):
        """Structured return plan for this exact source activation."""
        if scope is None:
            return self._return_plans.get(
                (self._root_context, id(node)),
                self._return_plans.get(id(node)))
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return None
        found = [self._return_plans.get((candidate, id(node)))
                 for candidate in contexts]
        present = [plan for plan in found if plan is not None]
        if not present:
            return None
        if len(present) != len(found):
            raise LoweringError(
                node, "source activations require different structured "
                      "return plans",
                code="source-plan-inconsistent")
        result = present[0]
        try:
            for plan in present[1:]:
                result = join_batch_plans(result, plan, node)
        except BatchContractError as exc:
            raise LoweringError(
                exc.node or node, exc.what, code=exc.code) from None
        return result

    def batch_plan(self, node, scope, context=None):
        """Structured plan for one reached list-of-records expression.

        This is the internal counterpart of :meth:`return_plan`.  It lets an
        assignment and a return consume the same source-owned batch algebra;
        lowering never has to rediscover concatenation, choice, count, or
        record schema from the expression syntax.
        """
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return None
        found = [self._batch_plans.get((candidate, id(node)))
                 for candidate in contexts]
        present = [plan for plan in found if plan is not None]
        if not present:
            return None
        if len(present) != len(found):
            raise LoweringError(
                node, "source activations require different structured "
                      "batch-value plans",
                code="source-plan-inconsistent")
        result = present[0]
        try:
            for plan in present[1:]:
                result = join_batch_plans(result, plan, node)
        except BatchContractError as exc:
            raise LoweringError(
                exc.node or node, exc.what, code=exc.code) from None
        return result

    def binding_batch_plan(self, target, scope, context=None):
        """Batch plan assigned at one exact source target occurrence."""
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return None
        found = [self._binding_batch_plans.get(
            (candidate, id(target))) for candidate in contexts]
        present = [plan for plan in found if plan is not None]
        if not present:
            return None
        if len(present) != len(found):
            raise LoweringError(
                target, "source activations require different plans at this "
                        "batch binding",
                code="source-plan-inconsistent")
        result = present[0]
        try:
            for plan in present[1:]:
                result = join_batch_plans(result, plan, target)
        except BatchContractError as exc:
            raise LoweringError(
                exc.node or target, exc.what, code=exc.code) from None
        return result

    def binding_batch_contract(self, scope, name, context=None):
        """Batch-domain contract shared by these source activations."""
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return None
        found = [self._binding_batch_contracts.get(
            (candidate, str(name))) for candidate in contexts]
        present = [contract for contract in found if contract is not None]
        if not present:
            return None
        if len(present) != len(found):
            raise LoweringError(
                scope, f"only some source activations represent {name} as "
                       "a transaction batch",
                code="source-plan-inconsistent")
        result = present[0]
        try:
            for candidate in present[1:]:
                result = join_batch_alternatives(
                    result, candidate, scope)
        except BatchContractError as exc:
            raise LoweringError(
                exc.node or scope, exc.what, code=exc.code) from None
        return result

    def computed_keys(self, node):
        """Keys a source-fixed dictionary comprehension emits, in order."""
        return self._computed_keys.get(id(node))

    def output_key_cases(self, node, scope=None, context=None):
        """Finite selector cases for one computed output-field assignment.

        Each case is ``(field, bindings)``.  ``bindings`` records the scalar
        loop/local values under which this occurrence names ``field``; it is
        empty when the occurrence always names the same field.  Lowering uses
        these cases as one finite switch instead of independently re-deriving
        source control flow.
        """
        # New source-owned record plans are activation-specific: one helper
        # occurrence may be called with two fixed prefixes and legitimately
        # construct two different record layouts.  Legacy output mappings are
        # root-only and retain their occurrence key until that path is removed.
        if scope is not None:
            contexts = self._contexts_for(scope, context)
            if contexts:
                found = [self._mapping_keys.get((candidate, id(node)))
                         for candidate in contexts]
                present = [cases for cases in found if cases is not None]
                if present:
                    if len(present) != len(found):
                        raise LoweringError(
                            node, "some source activations lack a record-key "
                                  "plan for this write",
                            code="source-plan-inconsistent")
                    merged = []
                    for cases in present:
                        for case in cases:
                            if case not in merged:
                                merged.append(case)
                    return tuple(merged)
        return self._mapping_keys.get(id(node))

    def record_contract(self, scope, name, context=None):
        """Exact record contract for this lexical binding and activation."""
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return UNKNOWN
        found = [self._records.get((candidate, str(name)), UNKNOWN)
                 for candidate in contexts]
        contracts = [
            item if isinstance(item, ValueContract) else UNKNOWN
            for item in found
        ]
        got = contracts[0]
        for contract in contracts[1:]:
            got = join_contracts(got, contract)
        if got is INCOMPATIBLE:
            raise LoweringError(
                scope, f"the source activations of {name} require different "
                       "record representations",
                code="sequence-plan-inconsistent")
        return got

    def record_fields(self, scope, name, context=None):
        """Fields of a local immutable record used in an output batch."""
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return ()
        found = [self._records.get((candidate, str(name)), ())
                 for candidate in contexts]
        normalized = [
            tuple(field for field, _contract in item.fields)
            if isinstance(item, ValueContract) and item.is_record else
            tuple(item) if isinstance(item, (tuple, list)) else ()
            for item in found
        ]
        if not all(item == normalized[0] for item in normalized[1:]):
            raise LoweringError(
                scope, f"the source activations of {name} require different "
                       "record representations",
                code="sequence-plan-inconsistent")
        return normalized[0]

    def is_record_builder(self, scope, name, context=None):
        contexts = self._contexts_for(scope, context)
        return bool(contexts and all(
            (candidate, str(name)) in self._record_builders
            for candidate in contexts))

    def record_field_ranges(self, scope, name, context=None):
        """Scalar field envelopes of one reached mutable record."""
        contexts = self._contexts_for(scope, context)
        if not contexts:
            return {}
        found = [self._record_ranges.get((candidate, str(name)), {})
                 for candidate in contexts]
        fields = set().union(*(mapping for mapping in found))
        result = {}
        for field in fields:
            values = [mapping.get(field) for mapping in found]
            if any(value is None for value in values):
                result[field] = None
                continue
            interval = values[0]
            for value in values[1:]:
                interval = interval.join(value)
            result[field] = interval
        return result

    def call_contract(self, node, arguments=None, fixed_arguments=None,
                      caller_context=None, fingerprint=None):
        """Return contract for one call occurrence and activation context.

        One helper call AST may run under several inlined activations.  Its
        return type is therefore a property of the call *and* its argument
        contracts, not of the AST node alone.  The context-free form remains
        available to syntax-only planning and is deliberately conservative.
        """
        if arguments is not None:
            variants = self.call_variants(
                node, arguments, fixed_arguments, caller_context)
            if fingerprint is not None:
                variants = tuple(
                    variant for variant in variants
                    if variant[0] == tuple(fingerprint))
            if not variants:
                return UNKNOWN
            got = variants[0][1]
            for _capture, contract, _target in variants[1:]:
                got = join_contracts(got, contract)
            return got
        return self._call_contracts.get(id(node), UNKNOWN)

    def callee_context(self, node, arguments, fixed_arguments,
                       caller_context=None, fingerprint=None):
        """Summary context selected by one exact source call activation."""
        variants = self.call_variants(
            node, arguments, fixed_arguments, caller_context)
        if fingerprint is not None:
            variants = tuple(
                variant for variant in variants
                if variant[0] == tuple(fingerprint))
        targets = {variant[2] for variant in variants}
        if len(targets) == 1:
            return next(iter(targets))
        owners = {self._context_functions.get(target) for target in targets}
        return (_ContextSet(frozenset(targets))
                if targets and len(owners) == 1 else None)

    def has_call(self, node):
        """Whether any source activation planned this call occurrence."""
        return id(node) in self._call_contexts

    def select_call(self, node, arguments, fixed_arguments, caller_context,
                    *, capture_fact):
        """Return the contract and context allowed by the caller's facts.

        The plan owns activation matching and joining. The emitter supplies
        only ``capture_fact(owner_id, name) -> (contract, range)`` for a lexical
        cell; it need not interpret summary fingerprints. Facts are read once
        per selection, never cached across rebindings or helper activations.
        """
        facts = {}

        def matches(fingerprint):
            for owner_id, name, planned, fixed in fingerprint:
                key = owner_id, name
                if key not in facts:
                    facts[key] = capture_fact(owner_id, name)
                current, current_range = facts[key]
                if current is None:
                    raise LoweringError(
                        None, f"the lexical activation carrying {name} is not "
                              "available while selecting its source summary",
                        code="closure-activation-unresolved")
                if not contracts_may_overlap(planned, current):
                    return False
                tag = fixed[0]
                if tag == "unproved":
                    continue
                value = fixed[1]
                if tag in {"bool", "int"}:
                    if (current_range is not None
                            and not current_range.lo <= int(value) <= current_range.hi):
                        return False
                elif tag == "str":
                    # No string range exists in the emitted representation.
                    continue
                else:
                    raise AssertionError(f"unknown fixed-value tag {tag!r}")
            return True

        variants = self.call_variants(
            node, arguments, fixed_arguments, caller_context)
        selected = [variant for variant in variants if matches(variant[0])]
        if not selected:
            return UNKNOWN, None
        contract = selected[0][1]
        for _fingerprint, candidate, _context in selected[1:]:
            contract = join_contracts(contract, candidate)
        if contract is INCOMPATIBLE:
            descriptions = tuple(
                (candidate.kind, candidate.carrier,
                 candidate.minimum, candidate.maximum)
                for _fingerprint, candidate, _context in selected)
            raise LoweringError(
                node, "possible source activations require incompatible "
                      f"return representations: {descriptions!r}",
                code="helper-return-shape-disagrees")
        return contract, self.combine_contexts(match[2] for match in selected)

    def call_variants(self, node, arguments, fixed_arguments,
                      caller_context=None):
        """Capture-sensitive plans for one syntactic source call edge.

        A call in a finite loop can execute more than once while a closure
        cell changes.  Keeping all variants avoids the former last-writer-wins
        map; ``select_call`` reconciles them with current lexical facts.
        """
        callers = (tuple(caller_context.activations) if isinstance(
            caller_context, _ContextSet) else (caller_context,))
        arguments = tuple(arguments)
        fixed_arguments = tuple(fixed_arguments or ())
        variants = []
        for key, contract in self._call_contexts.get(id(node), ()):
            if key[0] not in callers:
                continue
            planned_arguments, planned_fixed = key[2], key[3]
            if (len(planned_arguments) != len(arguments)
                    or not all(contracts_may_overlap(planned, current)
                               for planned, current in zip(
                                   planned_arguments, arguments))):
                continue
            if (len(planned_fixed) != len(fixed_arguments)
                    or not all(_fixed_may_match(planned, current)
                               for planned, current in zip(
                                   planned_fixed, fixed_arguments))):
                continue
            target = self._call_targets.get(key)
            if target is not None:
                variants.append((key[4], contract, target))
        # The same exact edge can be reachable from two members of an
        # aggregate caller context.  Its semantics is still one variant.
        return tuple(dict.fromkeys(variants))

    @property
    def output_capacity(self):
        return max((1, *self._return_capacity.values()))


def _same_scope(root):
    """Nodes under ``root`` without entering another lexical function."""
    stack = list(reversed(root.body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _truth_test_names(node):
    """Bare names whose truth is tested directly by ``node``."""
    while isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        node = node.operand
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.BoolOp):
        for operand in node.values:
            yield from _truth_test_names(operand)


def _literal_list_length(value):
    if isinstance(value, (ast.List, ast.Tuple)):
        return len(value.elts)
    return None


def _value_contract(node, values, owner, bindings=None, *, call=None,
                    is_builtin=None, budget=None):
    """One source expression's contract under the planner's current facts."""
    # Keep the numeric side domain carried by a path binding.  Coercing an
    # ``_ScalarBindings`` to ``dict`` here used to discard the bounds needed
    # by an IfExp inside a sequence multiplier; refining that condition then
    # crashed while trying to read the missing domain.  Plain mappings are
    # lifted into the same path type so the callback has one total contract.
    bindings = (_ScalarBindings() if bindings is None else
                bindings.clone() if isinstance(bindings, _ScalarBindings)
                else _ScalarBindings(bindings))
    builtin = is_builtin or (lambda _name: False)

    def resolve(candidate, env):
        if isinstance(candidate, ast.Name):
            if candidate.id in env:
                return contract_of_value(env[candidate.id])
            return values.get(candidate.id, UNKNOWN)
        return UNKNOWN

    got = contract_of_expression(
        node, resolve=resolve,
        call=(call or (lambda _node, _env, _contracts: UNKNOWN)),
        static_value=lambda candidate, env: _static_value(
            candidate, owner, env, budget=budget),
        static_iterable=lambda candidate, env: _static_iterable(
            candidate, owner, env,
            is_builtin=lambda name: name not in env and builtin(name),
            budget=budget),
        integer_interval=lambda candidate, env: (
            (interval.lo, interval.hi)
            if (interval := _scalar_range(
                candidate, owner, env, budget=budget)) is not None
            else None),
        is_builtin=lambda name, env: name not in env and builtin(name),
        refine_condition=lambda condition, truth, env:
            _refine_scalar_ranges(
                condition, owner, env, truth, budget=budget),
        bindings=bindings, budget=budget)
    return got


def _length_expression_bounds(node, lengths, owner, is_builtin, bindings,
                              numeric_bounds=(), *, budget=None):
    """Integer bounds derived from scalar facts and sequence lengths.

    Source planning records a sequence's maximum logical length without
    inventing its concrete contents.  Loops commonly consume that fact through
    ``range(len(rows))`` (and small affine variants of it).  Treating such a
    bound as wholly unknown loses an already-proved fact and can turn a short
    loop into ``UNROLL_LIMIT`` visits at every nesting level.
    """
    fixed = _static_value(node, owner, bindings, budget=budget)
    if type(fixed) is int:
        return ranges.Bounds(fixed, fixed)
    if isinstance(node, ast.Name) and node.id in numeric_bounds:
        return ranges.Bounds.of(numeric_bounds[node.id])
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        name = f"self.{node.attr}"
        if name in numeric_bounds:
            return ranges.Bounds.of(numeric_bounds[name])
    if isinstance(node, ast.Subscript):
        cell = _cell_range_key(node, owner, bindings, budget=budget)
        if cell in numeric_bounds:
            return ranges.Bounds.of(numeric_bounds[cell])
        name = _subscript_range_key(node)
        if name in numeric_bounds:
            return ranges.Bounds.of(numeric_bounds[name])
    if (is_plain_named_call(node, "len", 1)
            and node.func.id not in bindings
            and is_builtin("len")
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in lengths):
        return ranges.Bounds(0, max(0, int(lengths[node.args[0].id])))
    if isinstance(node, ast.UnaryOp):
        value = _length_expression_bounds(
            node.operand, lengths, owner, is_builtin, bindings,
            numeric_bounds, budget=budget)
        if value is None:
            return None
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return ranges.Bounds(
                None if value.hi is None else -value.hi,
                None if value.lo is None else -value.lo,
            )
        return None
    if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult)):
        left = _length_expression_bounds(
            node.left, lengths, owner, is_builtin, bindings,
            numeric_bounds, budget=budget)
        right = _length_expression_bounds(
            node.right, lengths, owner, is_builtin, bindings,
            numeric_bounds, budget=budget)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return ranges.Bounds(
                None if left.lo is None or right.lo is None
                else left.lo + right.lo,
                None if left.hi is None or right.hi is None
                else left.hi + right.hi,
            )
        if isinstance(node.op, ast.Sub):
            return ranges.Bounds(
                None if left.lo is None or right.hi is None
                else left.lo - right.hi,
                None if left.hi is None or right.lo is None
                else left.hi - right.lo,
            )
        finite_left, finite_right = left.finite(), right.finite()
        if finite_left is None or finite_right is None:
            return None
        products = (
            finite_left.lo * finite_right.lo,
            finite_left.lo * finite_right.hi,
            finite_left.hi * finite_right.lo,
            finite_left.hi * finite_right.hi,
        )
        return ranges.Bounds(min(products), max(products))
    return None


def _range_bound_from_lengths(node, lengths, owner, is_builtin, bindings,
                              numeric_bounds=(), *, budget=None):
    """A safe maximum length for ``range`` from finite length facts."""
    if not (is_plain_named_call(node, "range", 1, 3)
            and node.func.id not in bindings
            and is_builtin("range")):
        return None
    intervals = tuple(
        _length_expression_bounds(
            arg, lengths, owner, is_builtin, bindings, numeric_bounds,
            budget=budget)
        for arg in node.args)
    if any(interval is None for interval in intervals):
        return None
    if len(intervals) == 1:
        start_lo = start_hi = 0
        stop_lo, stop_hi = intervals[0].lo, intervals[0].hi
        step = 1
    else:
        start_lo, start_hi = intervals[0].lo, intervals[0].hi
        stop_lo, stop_hi = intervals[1].lo, intervals[1].hi
        step = 1
        if len(intervals) == 3:
            step_lo, step_hi = intervals[2].lo, intervals[2].hi
            if step_lo != step_hi or step_lo == 0:
                return None
            step = step_lo
    if step > 0:
        if stop_hi is None or start_lo is None:
            return None
        distance = max(0, stop_hi - start_lo)
        return (distance + step - 1) // step
    if start_hi is None or stop_lo is None:
        return None
    distance = max(0, start_hi - stop_lo)
    magnitude = -step
    return (distance + magnitude - 1) // magnitude


def _proved_bound_of(iterable, lengths, owner, is_builtin, bindings=None,
                     numeric_bounds=(), budget=None):
    """An iteration bound proved from facts fixed before this loop."""
    bindings = dict(bindings or {})
    literal = _static_iterable(
        iterable, owner, bindings,
        is_builtin=lambda name: name not in bindings and is_builtin(name),
        budget=budget)
    if literal is not None:
        return len(literal)
    if isinstance(iterable, ast.Name) and iterable.id in lengths:
        return max(0, int(lengths[iterable.id]))
    derived = _range_bound_from_lengths(
        iterable, lengths, owner, is_builtin, bindings, numeric_bounds,
        budget=budget)
    if derived is not None:
        if budget is not None:
            return finite_cardinality_add(derived)
        return derived
    if (is_plain_named_call(iterable, "reversed", 1)
            and "reversed" not in bindings and is_builtin("reversed")):
        return _proved_bound_of(
            iterable.args[0], lengths, owner, is_builtin, bindings,
            numeric_bounds, budget=budget)
    if (is_plain_named_call(iterable, "enumerate", 1, 2)
            and "enumerate" not in bindings and is_builtin("enumerate")):
        return _proved_bound_of(
            iterable.args[0], lengths, owner, is_builtin, bindings,
            numeric_bounds, budget=budget)
    if (isinstance(iterable, ast.Call)
            and is_plain_named_call(iterable, "zip", 0, len(iterable.args))
            and "zip" not in bindings and is_builtin("zip")):
        bounds = [_proved_bound_of(
            argument, lengths, owner, is_builtin, bindings,
            numeric_bounds, budget=budget) for argument in iterable.args]
        known = [bound for bound in bounds if bound is not None]
        return min(known) if known else (None if bounds else 0)
    return None


def _bound_of(iterable, lengths, owner, is_builtin, bindings=None,
              numeric_bounds=(), budget=None):
    """A conservative capacity multiplier for source-value planning.

    The capability ceiling lets representation planning reserve finite space
    before executable lowering decides whether the loop is admissible.  It is
    not a semantic proof and must never be exposed through ``loop_span``.
    """
    proved = _proved_bound_of(
        iterable, lengths, owner, is_builtin, bindings, numeric_bounds,
        budget=budget)
    return UNROLL_LIMIT if proved is None else proved


def _iterable_element_contract(node, observed, is_builtin):
    """Contract of one item yielded by an admitted finite iterable.

    This is the single structural rule used by the source interpreter and by
    the output-capacity projection.  The latter must bind destructured loop
    targets with the same contracts as the former; otherwise an output shape
    can depend on which analysis happened to inspect the loop.
    """
    if observed.is_sequence and observed.element is not None:
        return observed.element
    if is_plain_named_call(node, "range", 1, 3) and is_builtin("range"):
        return SCALAR
    if (is_plain_named_call(node, "reversed", 1)
            and is_builtin("reversed")
            and is_plain_named_call(node.args[0], "range", 1, 3)
            and is_builtin("range")):
        return SCALAR
    if (is_plain_named_call(node, "enumerate", 1, 2)
            and is_builtin("enumerate")):
        child = _iterable_element_contract(
            node.args[0], UNKNOWN, is_builtin)
        if child.is_known:
            return ValueContract.positional("tuple", (SCALAR, child))
    return UNKNOWN


def _comp_bound(node, lengths, owner, is_builtin, bindings=None,
                numeric_bounds=(), budget=None):
    """A safe comprehension capacity, retaining finite index bindings.

    Multiplying independent upper bounds is sound but needlessly loses facts
    for dependent generators: ``for i in range(3) for j in range(i + 1)``
    has six elements, not ``3 * UNROLL_LIMIT``.  Enumerate a generator when
    its declaration-time values are known and carry its target into the next
    generator.  An unknown generator keeps the ordinary conservative bound.
    Filters can only remove elements, so they do not enlarge the capacity.
    """

    budget = SourceValueBudget() if budget is None else budget

    def visit(index, bindings):
        if index == len(node.generators):
            return 1
        gen = node.generators[index]
        budget.spend(gen)
        values = _static_iterable(
            gen.iter, owner, bindings,
            is_builtin=lambda name: name not in bindings and is_builtin(name),
            budget=budget)
        if values is not None and isinstance(gen.target, ast.Name):
            total = 0
            for value in values:
                total = finite_cardinality_add(
                    total,
                    visit(index + 1,
                          {**bindings, gen.target.id: value}))
            return total
        head = _bound_of(
            gen.iter, lengths, owner, is_builtin, bindings,
            numeric_bounds, budget=budget)
        tail = visit(index + 1, bindings)
        return finite_cardinality_product(head, tail)

    return visit(0, dict(bindings or {}))


def _mapping_fields(value):
    if not isinstance(value, ast.Dict):
        return None
    fields = []
    for key in value.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        fields.append(key.value)
    return tuple(fields)


def _bounded_static_value(value):
    if type(value) is int:
        return value if value.bit_length() <= SOURCE_INTEGER_BITS else None
    if type(value) in (str, bytes):
        return value if len(value) <= SOURCE_TEXT_CHARS else None
    return value if type(value) is bool else None


def _format_spec_is_bounded(spec):
    """Reject a format width or precision before ``format`` allocates it."""
    run = 0
    for char in spec:
        if char.isdecimal() and ord(char) < 128:
            run = min(SOURCE_TEXT_CHARS + 1,
                      run * 10 + ord(char) - ord("0"))
            if run > SOURCE_TEXT_CHARS:
                return False
        else:
            run = 0
    return True


def _static_value(node, owner, bindings=None, *, budget=None, value_of=None,
                  is_builtin=None):
    """A declaration-time scalar value, or ``None`` when it is not fixed.

    This is intentionally smaller than expression lowering.  It proves only
    source-fixed planning facts, such as finite loop extents and computed
    output names; it never replaces a run-time payload computation.
    """
    bindings = bindings or {}
    if value_of is None:
        value_of = lambda candidate, active: _static_value(
            candidate, owner, active, budget=budget, is_builtin=is_builtin)
    if budget is not None:
        budget.spend(node)
    if isinstance(node, ast.Constant) and type(node.value) in (bool, int, str, bytes):
        return _bounded_static_value(node.value)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "str" and "str" not in bindings
            and is_builtin is not None and is_builtin("str")
            and len(node.args) == 1 and not node.keywords):
        value = value_of(node.args[0], bindings)
        if type(value) in (bool, int, str):
            return _bounded_static_value(str(value))
        return None
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return _bounded_static_value(bindings[node.id])
        return _bounded_static_value(_const_int(node, {}, owner))
    if isinstance(node, ast.Attribute):
        if (isinstance(node.value, ast.Name) and node.value.id == "self"
                and ("self", node.attr) in bindings):
            return _bounded_static_value(bindings[("self", node.attr)])
        # ``self.WIDTH`` is as fixed as a module constant when the shared
        # owner authority proves it.  Keeping this beside the Name case makes
        # range planning, helper summaries and lowering consume one fact.
        return _bounded_static_value(_const_int(node, {}, owner))
    if isinstance(node, ast.UnaryOp):
        value = value_of(node.operand, bindings)
        if type(value) is not int:
            return None
        if isinstance(node.op, ast.USub):
            return _bounded_static_value(-value)
        if isinstance(node.op, ast.UAdd):
            return _bounded_static_value(value)
        return None
    if isinstance(node, ast.BinOp):
        left = value_of(node.left, bindings)
        # Every represented binary operation needs both operands and none of
        # the accepted primitives has an absorbing-value shortcut at the
        # Python source boundary.  Once the left operand is not fixed,
        # traversing an arbitrarily large right subtree cannot recover a
        # declaration-time value.
        if left is None:
            return None
        right = value_of(node.right, bindings)
        if right is None:
            return None
        try:
            if isinstance(node.op, ast.Add) \
                    and ((type(left) is int and type(right) is int)
                         or (type(left) is str and type(right) is str)):
                if type(left) is str and len(left) + len(right) > \
                        SOURCE_TEXT_CHARS:
                    return None
                return _bounded_static_value(left + right)
            if type(left) is int and type(right) is int:
                if isinstance(node.op, ast.Sub):
                    return _bounded_static_value(left - right)
                if isinstance(node.op, ast.Mult):
                    if (left and right and left.bit_length()
                            + right.bit_length() > SOURCE_INTEGER_BITS + 1):
                        return None
                    return _bounded_static_value(left * right)
                if isinstance(node.op, ast.FloorDiv) and right != 0:
                    return _bounded_static_value(left // right)
                if isinstance(node.op, ast.Mod) and right != 0:
                    return _bounded_static_value(left % right)
                if isinstance(node.op, ast.BitAnd):
                    return _bounded_static_value(left & right)
                if isinstance(node.op, ast.BitOr):
                    return _bounded_static_value(left | right)
                if isinstance(node.op, ast.BitXor):
                    return _bounded_static_value(left ^ right)
                if isinstance(node.op, ast.LShift) and right >= 0:
                    # Check the prospective magnitude before asking Python to
                    # materialise it.  Static planning is a finite proof, not
                    # an unbounded host-integer evaluator.
                    if left and left.bit_length() + right > \
                            SOURCE_INTEGER_BITS:
                        return None
                    return _bounded_static_value(left << right)
                if isinstance(node.op, ast.RShift) and right >= 0:
                    return _bounded_static_value(left >> right)
        except (ArithmeticError, ValueError):
            return None
        return None
    if isinstance(node, ast.IfExp):
        truth = static_truth(
            node.test,
            value_of=value_of,
            bindings=bindings,
            budget=budget,
        )
        if truth is True:
            return value_of(node.body, bindings)
        if truth is False:
            return value_of(node.orelse, bindings)
        left = value_of(node.body, bindings)
        right = value_of(node.orelse, bindings)
        return left if type(left) is type(right) and left == right else None
    if isinstance(node, ast.Compare):
        return static_truth(
            node,
            value_of=value_of,
            bindings=bindings,
            budget=budget,
        )
    if isinstance(node, ast.JoinedStr):
        parts = []
        total_length = 0
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                rendered = part.value
                if len(rendered) > SOURCE_TEXT_CHARS - total_length:
                    return None
                parts.append(rendered)
                total_length += len(rendered)
                continue
            if not isinstance(part, ast.FormattedValue):
                return None
            value = value_of(part.value, bindings)
            if value is None:
                return None
            if part.conversion == ord("r"):
                value = repr(value)
            elif part.conversion == ord("a"):
                value = ascii(value)
            elif part.conversion == ord("s"):
                value = str(value)
            elif part.conversion == -1:
                pass
            else:
                return None
            spec = ""
            if part.format_spec is not None:
                got = value_of(part.format_spec, bindings)
                if not isinstance(got, str):
                    return None
                spec = got
            if not _format_spec_is_bounded(spec):
                return None
            try:
                rendered = format(value, spec)
            except (TypeError, ValueError):
                return None
            if len(rendered) > SOURCE_TEXT_CHARS:
                return None
            if len(rendered) > SOURCE_TEXT_CHARS - total_length:
                return None
            parts.append(rendered)
            total_length += len(rendered)
        return "".join(parts)
    return None


def _destructuring_value(node, owner, bindings=None, *, budget=None):
    """A finite value used only to bind a destructuring assignment.

    The general static-value authority intentionally returns scalar values.
    Python tuple/list unpacking needs one additional, local operation: carry a
    recursively fixed sequence only as far as ``values_for_target`` so each
    scalar leaf receives its own fact.  The aggregate is never installed as a
    scalar binding or used as an executable constant.
    """
    if isinstance(node, (ast.Tuple, ast.List)):
        values = []
        for child in node.elts:
            value = _destructuring_value(
                child, owner, bindings, budget=budget)
            if value is None:
                return None
            values.append(value)
        return tuple(values) if isinstance(node, ast.Tuple) else values
    return _static_value(node, owner, bindings, budget=budget)


def static_value(node, owner, bindings=None, *, budget=None, is_builtin=None):
    """A declaration-time scalar under the source's lexical owner."""
    return _static_value(node, owner, bindings, budget=budget, is_builtin=is_builtin)


def _static_iterable(node, owner, bindings=None, *, is_builtin=None,
                     budget=None):
    """The exact finite values of a declaration-time iterable, if known."""
    bindings = bindings or {}
    is_builtin = is_builtin or (lambda _name: False)
    if budget is not None:
        budget.spend(node)
    if isinstance(node, ast.Constant) and type(node.value) in (str, bytes):
        if budget is not None:
            budget.spend(node, max(1, len(node.value)))
        return tuple(node.value)
    if isinstance(node, (ast.List, ast.Tuple)):
        if len(node.elts) > UNROLL_LIMIT:
            return None
        values = []
        for item in node.elts:
            value = _static_value(item, owner, bindings, budget=budget)
            if value is None and isinstance(item, (ast.List, ast.Tuple)):
                nested = _static_iterable(
                    item, owner, bindings, is_builtin=is_builtin,
                    budget=budget)
                if nested is not None:
                    value = list(nested) if isinstance(item, ast.List) \
                        else tuple(nested)
            values.append(value)
        return None if any(value is None for value in values) else tuple(values)
    if (is_plain_named_call(node, "range", 1, 3)
            and node.func.id not in bindings
            and is_builtin("range")
            ):
        args = [_static_value(arg, owner, bindings, budget=budget)
                for arg in node.args]
        if not all(type(value) is int for value in args):
            return None
        try:
            candidate = range(*args)
            if len(candidate) > UNROLL_LIMIT:
                return None
            values = tuple(candidate)
        except (OverflowError, TypeError, ValueError):
            return None
        return values
    if (is_plain_named_call(node, "reversed", 1)
            and node.func.id not in bindings
            and is_builtin("reversed")
            ):
        values = _static_iterable(
            node.args[0], owner, bindings, is_builtin=is_builtin,
            budget=budget)
        return None if values is None else tuple(reversed(values))
    if (is_plain_named_call(node, "enumerate", 1, 2)
            and node.func.id not in bindings
            and is_builtin("enumerate")):
        values = _static_iterable(
            node.args[0], owner, bindings, is_builtin=is_builtin,
            budget=budget)
        if values is None:
            return None
        start = (0 if len(node.args) == 1 else
                 _static_value(
                     node.args[1], owner, bindings, budget=budget))
        if type(start) is not int:
            return None
        return tuple((start + index, value)
                     for index, value in enumerate(values))
    return None


def static_iterable(node, owner, bindings=None, *, is_builtin=None,
                    budget=None):
    """Exact finite values of a declaration-time iterable, if known."""
    return _static_iterable(
        node, owner, bindings, is_builtin=is_builtin, budget=budget)


class _ContractFlow:
    """Structural control exits from one source block."""

    __slots__ = ("breaks", "continues", "normal", "returned")

    def __init__(self, normal=(), returned=(), breaks=(), continues=()):
        self.normal = list(normal)
        self.returned = list(returned)
        self.breaks = list(breaks)
        self.continues = list(continues)


_STATE_PREFIX = "#state:"


def _state_key(name):
    """Private abstract-environment key for one ``self`` field."""
    return f"{_STATE_PREFIX}{name}"


def _state_name(key):
    return (key[len(_STATE_PREFIX):]
            if isinstance(key, str) and key.startswith(_STATE_PREFIX)
            else None)


def _state_contracts_of(env):
    """Project recursive source-state contracts from an abstract state."""
    return {
        name: value
        for key, value in env.items()
        if (name := _state_name(key)) is not None
        and isinstance(value, ValueContract)
    }


def _join_state_contracts(states):
    """Join state maps reached by alternative normal control exits."""
    states = tuple(states)
    if not states:
        return {}
    names = set().union(*(state for state in states))
    result = {}
    for name in names:
        values = [state[name] for state in states if name in state]
        if len(values) != len(states):
            result[name] = UNKNOWN
            continue
        joined = values[0]
        for value in values[1:]:
            joined = join_contracts(joined, value)
        result[name] = joined
    return result


def _subscript_target_path(target):
    """Return the writable root and depth of an indexed assignment."""
    depth = 0
    current = target
    while isinstance(current, ast.Subscript):
        if isinstance(current.slice, ast.Slice):
            return None
        depth += 1
        current = current.value
    if not depth:
        return None
    if isinstance(current, ast.Name):
        return current, depth
    if (isinstance(current, ast.Attribute)
            and isinstance(current.value, ast.Name)
            and current.value.id == "self"):
        return current, depth
    return None


def _slice_target_path(target):
    """Return the writable root and enclosing depth of a slice assignment."""
    if not (isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Slice)):
        return None
    depth = 0
    current = target.value
    while isinstance(current, ast.Subscript):
        if isinstance(current.slice, ast.Slice):
            return None
        depth += 1
        current = current.value
    if isinstance(current, ast.Name):
        return current, depth
    if (isinstance(current, ast.Attribute)
            and isinstance(current.value, ast.Name)
            and current.value.id == "self"):
        return current, depth
    return None


def _sequence_element_store(container, depth, value):
    """Widen a homogeneous recursive contract after one indexed store."""
    if (depth < 1 or not isinstance(container, ValueContract)
            or not container.is_sequence):
        return INCOMPATIBLE
    previous = container.element
    if depth == 1:
        replacement = value
    elif previous is None:
        replacement = UNKNOWN
    else:
        replacement = _sequence_element_store(previous, depth - 1, value)
    element = replacement if previous is None else join_contracts(
        previous, replacement)
    if element is INCOMPATIBLE:
        return INCOMPATIBLE
    return container.with_interval(
        container.minimum, container.maximum, element)


def _sequence_slice_store(container, depth, value):
    """Widen a recursive contract after a fixed-width slice replacement."""
    if (depth < 0 or not isinstance(container, ValueContract)
            or not container.is_sequence):
        return INCOMPATIBLE
    if depth:
        previous = container.element
        replacement = (
            UNKNOWN if previous is None else
            _sequence_slice_store(previous, depth - 1, value)
        )
    else:
        if not isinstance(value, ValueContract) or not value.is_sequence:
            return INCOMPATIBLE
        if value.maximum == 0:
            return container
        previous = container.element
        replacement = value.element or UNKNOWN
    element = replacement if previous is None else join_contracts(
        previous, replacement)
    if element is INCOMPATIBLE:
        return INCOMPATIBLE
    return container.with_interval(
        container.minimum, container.maximum, element)


def _static_fact_key(value):
    """Exact typed identity for one closed finite source value."""
    if type(value) in (bool, int, str, bytes, type(None)):
        return (type(value).__name__, value)
    if type(value) in (list, tuple):
        return (type(value).__name__, tuple(
            _static_fact_key(item) for item in value))
    if type(value) is dict:
        return ("dict", tuple(
            (_static_fact_key(key), _static_fact_key(item))
            for key, item in value.items()))
    raise TypeError(type(value).__name__)


def _unique_static_values(values):
    """Deduplicate alternatives without Python's Boolean/integer collapse."""
    unique = {}
    for value in values:
        unique.setdefault(_static_fact_key(value), value)
    return tuple(unique.values())


@dataclass(frozen=True)
class _StaticFact:
    """Possible closed values of one lowering-only finite reference."""

    values: tuple

    def __post_init__(self):
        if not self.values:
            raise ValueError("a finite source reference needs a normal value")
        try:
            normalized = _unique_static_values(self.values)
        except TypeError as exc:
            raise TypeError(
                f"a finite source reference contains {exc.args[0]}") from None
        object.__setattr__(self, "values", normalized)

    @property
    def contract(self):
        contracts = tuple(contract_of_value(value) for value in self.values)
        # A mapping with non-string keys is an analysis-only lookup carrier,
        # not a source value the IR must represent.  Its selectors can still
        # produce a conclusive contract later.
        if UNKNOWN in contracts:
            return UNKNOWN
        result = contracts[0]
        for contract in contracts[1:]:
            result = join_contracts(result, contract)
        return result


@dataclass(frozen=True)
class _RecordFact:
    """Path-sensitive facts for one mutable fixed-key source mapping.

    ``fields`` contains every key that can be present, while ``definite`` is
    the subset present on every represented path.  Keeping the two distinct is
    what lets two branches converge after later writes without either treating
    an optional key as guaranteed or multiplying one abstract state per path.
    ``writes`` is the finite selector plan consumed by emission; it is carried
    through the same joins as the schema rather than recovered by another AST
    walk.
    """

    fields: tuple
    definite: frozenset
    writes: tuple = ()
    ranges: tuple = ()

    def __post_init__(self):
        names = tuple(name for name, _contract in self.fields)
        if len(names) != len(set(names)) or any(type(name) is not str
                                                for name in names):
            raise ValueError("record facts need unique string fields")
        if any(not isinstance(contract, ValueContract)
               for _name, contract in self.fields):
            raise TypeError("record facts need ValueContract fields")
        if not self.definite <= frozenset(names):
            raise ValueError("a definite record field must be possible")
        if any(not isinstance(write, RecordFieldWrite)
               for write in self.writes):
            raise TypeError("record facts need RecordFieldWrite entries")
        range_names = tuple(name for name, _interval in self.ranges)
        if len(range_names) != len(set(range_names)) \
                or not set(range_names) <= set(names):
            raise ValueError("record ranges must name unique record fields")

    @classmethod
    def empty(cls):
        return cls((), frozenset())

    @property
    def field_contracts(self):
        return dict(self.fields)

    @property
    def field_ranges(self):
        return dict(self.ranges)

    @property
    def contract(self):
        names = frozenset(self.field_contracts)
        if not names or self.definite != names:
            return UNKNOWN
        try:
            return ValueContract.record(self.field_contracts)
        except ValueError:
            return UNKNOWN

    def write(self, field, contract, occurrence, bindings=(), interval=None):
        fields = self.field_contracts
        previous = fields.get(field)
        if previous is not None:
            contract = join_contracts(previous, contract)
            if contract is INCOMPATIBLE:
                raise SequencePlanError(
                    occurrence,
                    f"the output field {field!r} receives incompatible values",
                    "record-field-representation-unsupported")
        fields[field] = contract
        write = RecordFieldWrite(
            occurrence, field, contract, tuple(bindings))
        writes = tuple(dict.fromkeys((*self.writes, write)))
        field_ranges = self.field_ranges
        field_ranges[field] = interval
        return _RecordFact(
            tuple(sorted(fields.items())), self.definite | {field}, writes,
            tuple(sorted(field_ranges.items())))

    @classmethod
    def join(cls, facts):
        facts = tuple(facts)
        if not facts:
            raise ValueError("cannot join no record facts")
        names = set().union(*(fact.field_contracts for fact in facts))
        fields = {}
        for name in names:
            contracts = [fact.field_contracts[name] for fact in facts
                         if name in fact.field_contracts]
            got = contracts[0]
            for contract in contracts[1:]:
                got = join_contracts(got, contract)
            if got is INCOMPATIBLE:
                # Preserve the contradiction until the record is consumed, so
                # a later overwrite can establish one exact field contract.
                got = UNKNOWN
            fields[name] = got
        definite = set(facts[0].definite)
        for fact in facts[1:]:
            definite &= set(fact.definite)
        writes = tuple(dict.fromkeys(
            write for fact in facts for write in fact.writes))
        field_ranges = {}
        for name in names:
            values = [fact.field_ranges.get(name)
                      for fact in facts if name in fact.field_contracts]
            if (len(values) != len(facts)
                    or any(value is None for value in values)):
                field_ranges[name] = None
                continue
            interval = values[0]
            for value in values[1:]:
                interval = interval.join(value)
            field_ranges[name] = interval
        return cls(
            tuple(sorted(fields.items())), frozenset(definite), writes,
            tuple(sorted(field_ranges.items())))

    def summary(self, node):
        contract = self.contract
        if not contract.is_record:
            raise SequencePlanError(
                node, "the returned output mapping does not have one nonempty "
                      "field schema on every reachable path",
                "outputs-disagree-across-returns")
        unproved = next(
            (write for write in self.writes
             if not write.contract.is_known),
            None,
        )
        if unproved is not None:
            raise SequencePlanError(
                unproved.occurrence,
                f"the output field {unproved.field!r} has no finite source "
                "value contract",
                "record-field-representation-unsupported",
            )
        return RecordBuilderSummary(
            contract, self.writes, tuple(sorted(self.field_ranges.items())))


@dataclass(frozen=True)
class _BatchFact:
    """Path-joined source forms sharing one transaction-batch carrier."""

    value: ValueContract
    batch: BatchContract

    def __post_init__(self):
        if not isinstance(self.value, ValueContract):
            raise TypeError("a batch fact needs a source value contract")
        if not isinstance(self.batch, BatchContract):
            raise TypeError("a batch fact needs a batch contract")

    @property
    def contract(self):
        return self.value


@dataclass(frozen=True)
class _ContractReturn:
    """One source return reached in one exact abstract activation."""

    node: ast.Return
    contract: ValueContract
    record_name: str | None = None
    record: _RecordFact | None = None
    batch: BatchPlan | None = None
    interval: ranges.Range | None = None
    state: tuple[tuple[str, ValueContract], ...] = ()
    state_ranges: tuple[tuple[str, ranges.Range], ...] = ()
    array_arguments: tuple[tuple[str, ValueContract, ranges.Range | None], ...] = ()


def _state_ranges_of(bindings):
    """Project finite ``self``-field ranges from one abstract path."""
    prefix = "self."
    return {
        name[len(prefix):]: interval
        for name, interval in getattr(bindings, "ranges", {}).items()
        if isinstance(name, str) and name.startswith(prefix)
    }


def _join_state_ranges(states):
    """Join finite state ranges known on every normal control exit."""
    states = tuple(states)
    if not states:
        return {}
    names = set(states[0])
    for state in states[1:]:
        names &= set(state)
    answer = {}
    for name in names:
        interval = states[0][name]
        for state in states[1:]:
            interval = interval.join(state[name])
        answer[name] = interval
    return answer


def _state_presence_key(state):
    """Keep missing fields and present None values in distinct alternatives."""
    return (frozenset(state),
            frozenset(name for name, contract in state.items() if contract.is_none))


def _join_state_entries(entries, budget, node):
    """Join shapes/ranges together within each source absence alternative.

    Joining each field independently loses implications such as a pending flag
    guarding an optional byte. These partitions retain those correlations;
    ranges within a partition remain conservative, not sampled state values.
    The ordinary source-work budget owns the cost, with no additional limit.
    """
    groups = {}
    for state, intervals in entries:
        budget.spend(node)
        groups.setdefault(_state_presence_key(state), []).append((state, intervals))
    return tuple((
        _join_state_contracts(state for state, _ranges in group),
        _join_state_ranges(intervals for _state, intervals in group),
    ) for group in groups.values())


class _FunctionContracts:
    """Reachable helper return contracts under their actual call arguments.

    This is a structural abstract interpreter, not a second value lowerer.  It
    follows only facts needed to choose a finite sequence representation:
    scalar/sequence shape, carrier, and logical length.  Unknown syntax loses
    a fact; it never becomes a scalar guess.  Calls are memoized by callee and
    argument contracts, so the same helper may legitimately return different
    fixed shapes at different constant call sites.
    """

    def __init__(self, model, resolver, declarations=None,
                 declaration_contracts=None, input_ranges=None, budget=None,
                 query_cache=None, *, state_only=False, state_entries=None):
        self.model = model
        self.resolver = resolver
        if type(state_only) is not bool:
            raise TypeError("state_only must be Boolean")
        self.state_only = state_only
        self._state_entries = state_entries
        self._final_state_entries = {}
        self.declarations = dict(declaration_contracts or {})
        self.declaration_values = {
            ("self", name): value
            for name, value in dict(declarations or {}).items()
            if type(value) in (bool, int, str)
        }
        concrete = {
            name: contract_of_value(value)
            for name, value in dict(declarations or {}).items()
        }
        # A construction value is stronger evidence than a shape-only
        # declaration because it also proves the Python carrier.  Syntax-only
        # callers retain the recursive physical contract supplied by the
        # declaration authority instead of losing the field entirely.
        self.declarations.update(concrete)
        self.reachable = set()
        self._active = set()
        self._cache = {}
        self._context = []
        self._context_functions = {}
        self._context_initial_bindings = {}
        self._context_initial_env = {}
        self._context_initial_ranges = {}
        self._dependency_cache = {}
        self._repeated_truth_parameters = {}
        self._saved_structural_predicates = {}
        self._frames = []
        self.root_context = None
        self._at_binding = {}
        self._binding_contexts = {}
        self._binding_occurrence_contexts = {}
        self._at_call = {}
        self._call_contexts = {}
        self._call_targets = {}
        self._at_expression = {}
        self._expression_contexts = {}
        # Finite integer ranges use the same activation and occurrence
        # identities as value contracts.  A helper return is summarized once
        # per call context, then attached to the exact Call occurrence in its
        # caller.  ``None`` is retained explicitly: if any reaching execution
        # lacks a finite range, a previously observed range must not survive.
        self._range_cache = {}
        self._final_state = {}
        self._final_state_ranges = {}
        self._array_parameters = {}
        self._final_array_arguments = {}
        self._expression_ranges = {}
        # Exact scalar facts at an expression occurrence belong to the same
        # path-sensitive interpreter as its value contract.  The later
        # capacity projection consumes these facts instead of walking the
        # syntax again with every loop pessimistically set to UNROLL_LIMIT.
        # Across repeated visits, retain only bindings that have the same
        # typed value on every reaching path.
        self._expression_fixed = {}
        self._expression_bounds = {}
        self._statement_contexts = set()
        self._branch_outcomes = {}
        self._loop_spans = {}
        self._loop_domains = {}
        self._root_state_ranges = _source_state_ranges(
            self.declarations, model.inputs, input_ranges)
        # Output-boundary facts produced by the same control interpreter as
        # ordinary value contracts.  No syntax scan is allowed to add a return,
        # field, or selector that this interpreter did not reach.
        self._return_facts = {}
        self._context_batch_contracts = {}
        self._call_batch_contracts = {}
        self._record_summaries = {}
        self._return_plans = {}
        self._batch_plans = {}
        self._binding_batch_plans = {}
        self._binding_batch_contracts = {}
        self._non_batch_bindings = set()
        # One budget covers helper activation, expression recursion and every
        # later capacity projection for this procedure.  Per-expression
        # limits do not compose under calls and nested comprehensions.
        self.budget = budget if budget is not None else SourceValueBudget()
        self._query_cache = (query_cache if query_cache is not None else
                             _SourceQueryCache())
        self._contract_query_cache = self._query_cache.contracts
        self._scalar_refinement_cache = self._query_cache.refinements

    @staticmethod
    def _value_of_fact(value):
        return value.contract if isinstance(
            value, (_RecordFact, _BatchFact, _StaticFact)) else value

    @staticmethod
    def _batch_of_fact(value):
        if isinstance(value, _BatchFact):
            return value.batch
        contract = (value.contract if isinstance(
            value, (_RecordFact, _StaticFact)) else value)
        try:
            return batch_contract_of_value(contract)
        except BatchContractError:
            return None

    @staticmethod
    def _narrow_batch_none(batch, is_none):
        """Restrict a batch fact after an exact ``is None`` test."""
        if is_none:
            return (BatchContract.empty(BatchForm.NONE)
                    if BatchForm.NONE in batch.forms else None)
        forms = batch.forms - {BatchForm.NONE}
        if not forms:
            return None
        minimum = (1 if forms == frozenset({BatchForm.MAPPING}) else
                   batch.minimum)
        return BatchContract(
            minimum, batch.maximum, batch.transaction, frozenset(forms))

    @staticmethod
    def _merge_envs(*envs):
        if not envs:
            return {}
        # A name has a value after a join only when every reaching path bound
        # it.  Absence is a control-flow fact, not an UNKNOWN source value:
        # preserving a one-sided binding here masks the exact CFG/SSA
        # unassigned-variable check with an unrelated contract disagreement.
        names = set(envs[0])
        for env in envs[1:]:
            names &= set(env)
        out = {}
        for name in names:
            first = envs[0][name]
            # Ordinary identical carriers already are their own join. Optional
            # carriers still need the batch-form normalization below, as do
            # record builders and independently retained batch evidence.
            if (isinstance(first, ValueContract) and not first.is_option
                    and all(env[name] is first for env in envs[1:])):
                out[name] = first
                continue
            values = [env[name] for env in envs]
            if all(isinstance(value, _StaticFact) for value in values):
                got = _StaticFact(_unique_static_values(
                    item for value in values for item in value.values))
            elif all(isinstance(value, _RecordFact) for value in values):
                got = _RecordFact.join(values)
            else:
                contracts = [
                    _FunctionContracts._value_of_fact(value)
                    for value in values]
                got = contracts[0]
                for value in contracts[1:]:
                    got = join_contracts(got, value)
                if _state_name(name) is not None:
                    # A persistent None | record is an ordinary optional
                    # value, not a transaction batch. State closure and reads
                    # consume ValueContract; wrapping it as _BatchFact drops
                    # the state write and incorrectly preserves its reset type.
                    out[name] = got
                    continue
                batches = [
                    _FunctionContracts._batch_of_fact(value)
                    for value in values]
                if all(batch is not None for batch in batches):
                    batch = batches[0]
                    try:
                        for candidate in batches[1:]:
                            batch = join_batch_alternatives(
                                batch, candidate)
                    except BatchContractError:
                        got = INCOMPATIBLE
                    else:
                        # A known single source carrier already has an exact
                        # ordinary ValueContract.  Otherwise retain the
                        # independent batch fact: a mapping schema can be
                        # known even while one field's value contract is not.
                        # Dropping that fact at a one-path join makes a later
                        # ``None | mapping`` join spuriously incompatible.
                        if len(batch.forms) > 1:
                            got = _BatchFact(
                                batch_value_contract(batch), batch)
                        elif (any(isinstance(value, _BatchFact)
                                  for value in values)
                              and batch_contract_of_value(got) is None):
                            got = _BatchFact(got, batch)
                elif any(isinstance(value, _RecordFact)
                         for value in values):
                    # A mutable builder joined with an ordinary value is not
                    # a faithful fixed record.  Preserve the incompatible
                    # value fact rather than silently retaining the builder.
                    got = INCOMPATIBLE
            out[name] = got
        return out

    @staticmethod
    def _merge_bindings(*bindings):
        if not bindings:
            return _ScalarBindings()
        if len(bindings) == 1 and isinstance(bindings[0], _ScalarBindings):
            # A single path needs no interval join or Bounds -> Range rebuild.
            # A join still discards unproved fixed values, unlike a path copy.
            out = bindings[0].clone()
            for name, value in bindings[0].items():
                if _fixed_key(value) == ("unproved",):
                    del out[name]
            return out
        common = set(bindings[0])
        for env in bindings[1:]:
            common &= set(env)
        out = {}
        for name in common:
            value = bindings[0][name]
            identity = _fixed_key(value)
            if identity == ("unproved",):
                continue
            if all(_fixed_key(env[name]) == identity
                   for env in bindings[1:]):
                out[name] = value
        common_bounds = set(getattr(bindings[0], "bounds", {}))
        for env in bindings[1:]:
            common_bounds &= set(getattr(env, "bounds", {}))
        joined = {
            name: bindings[0].bounds[name]
            for name in common_bounds
        }
        for env in bindings[1:]:
            for name in tuple(joined):
                joined[name] = joined[name].join(env.bounds[name])
        common_guards = dict(getattr(bindings[0], "guards", {}))
        common_lengths = dict(getattr(bindings[0], "lengths", {}))
        for env in bindings[1:]:
            current = getattr(env, "guards", {})
            common_guards = {
                key: value for key, value in common_guards.items()
                if key in current and current[key] == value
            }
            common_lengths = {
                name: sequence for name, sequence in common_lengths.items()
                if getattr(env, "lengths", {}).get(name) == sequence
            }
        return _ScalarBindings(
            out, scalar_bounds=joined, guard_facts=common_guards,
            length_facts=common_lengths)

    @staticmethod
    def _copy_bindings(bindings):
        return (bindings.clone() if isinstance(bindings, _ScalarBindings)
                else _ScalarBindings(bindings))

    @staticmethod
    def _state_signature(states, ignored=()):
        """Canonical abstract states for a loop-transfer fixed-point test."""
        ignored = frozenset(ignored)
        answer = []
        for env, bindings in states:
            values = tuple(sorted(
                (name, value) for name, value in env.items()
                if name not in ignored))
            fixed = tuple(sorted(
                (name, _fixed_key(value))
                for name, value in bindings.items()
                if name not in ignored))
            intervals = tuple(sorted(
                (name, _bounds_key(value))
                for name, value in getattr(bindings, "bounds", {}).items()
                if name not in ignored))
            guards = tuple(sorted(
                getattr(bindings, "guards", {}).items(), key=repr))
            lengths = tuple(sorted(getattr(bindings, "lengths", {}).items()))
            answer.append((values, fixed, intervals, guards, lengths))
        return tuple(sorted(answer, key=repr))

    def _bounded_scalar_loop_entries(
            self, fn, stmt, entered, maximum, recurrences):
        """Widen proved affine locals to every possible loop-head value.

        ``entered`` already carries the finite abstract range of the loop
        target.  A recurrence delta is independent of every body-written
        scalar by :func:`_bounded_scalar_loop_recurrences`; evaluating it once
        therefore bounds every visit.  Before the final possible visit, the
        recurrence has executed between zero and ``maximum - 1`` times.
        Returning ``None`` leaves the caller on the precise replay path.
        """
        visits = max(0, maximum - 1)
        widened = []
        for one_env, one_bindings in entered:
            next_bindings = self._copy_bindings(one_bindings)
            envelopes = {}
            for name, (sign, expression) in recurrences.items():
                initial = next_bindings.ranges.get(name)
                delta_bounds = self._bounds_of(
                    fn, expression,
                    self._structural_bindings(next_bindings))
                delta = (None if delta_bounds is None else
                         delta_bounds.finite())
                if initial is None or delta is None:
                    return None
                dlo, dhi = delta.lo * sign, delta.hi * sign
                if dlo > dhi:
                    dlo, dhi = dhi, dlo
                envelopes[name] = ranges.Range(
                    initial.lo + min(0, visits * dlo),
                    initial.hi + max(0, visits * dhi),
                )
            for name, interval in envelopes.items():
                next_bindings.forget_length(name)
                next_bindings.pop(name, None)
                next_bindings.set_numeric(name, interval)
            widened.append((dict(one_env), next_bindings))
        return widened

    def _structural_bindings(self, bindings, node=None):
        """Exact locals over immutable construction-time scalar facts.

        ``declaration_values`` contains only construction fields that the
        step cannot write.  They remain valid in every helper activation,
        while a path-local binding takes precedence over a declaration with
        the same source spelling.  Preserve the range side table because
        fixed-value and interval planning share this authority.  A pure
        expression query needs only the names it reads; projecting that set
        avoids copying a procedure's complete input environment at every
        recursive expression occurrence.
        """
        if node is None:
            out = (bindings.clone()
                   if isinstance(bindings, _ScalarBindings)
                   else _ScalarBindings(bindings))
            declarations = self.declaration_values.items()
        else:
            names, _fields, binding_keys, numeric_keys = \
                self._query_dependency_sets(node)
            source = {} if bindings is None else bindings
            active_bounds = getattr(bindings, "bounds", {})
            active_guards = getattr(bindings, "guards", {})
            out = _ScalarBindings(
                {name: source[name]
                 for name in binding_keys if name in source},
                scalar_bounds=_numeric_projection(active_bounds, numeric_keys),
                guard_facts={
                    guard: value
                    for guard, value in active_guards.items()
                    if (type(guard) is tuple and len(guard) == 2
                        and guard[1] in names)
                },
                length_facts={
                    name: sequence for name, sequence in
                    getattr(bindings, "lengths", {}).items() if name in names
                },
            )
            declarations = (
                (name, value)
                for name, value in self.declaration_values.items()
                if name in binding_keys)
        for name, value in declarations:
            out.setdefault(name, value)
        if node is None:
            out.update(bindings)
        return out

    def _static_dependencies(self, node):
        """Bindings whose presence or value can affect one static query.

        The declaration-time evaluator has no binders: it reads bare names
        and ``self.X`` construction fields, and otherwise descends through a
        fixed scalar expression.  Locals unrelated to those reads cannot
        change its answer.  Projecting them out is important in long basic
        blocks, where the full abstract environment grows after every
        assignment even though an earlier expression's meaning does not.
        """
        return self._query_cache.syntax_of(node).dependencies

    def _query_dependency_sets(self, node):
        """Source spellings read by one pure query, by environment kind."""
        syntax = self._query_cache.syntax_of(node)
        names, fields, bindings, numeric = syntax.environment_keys
        return names, fields, bindings, numeric | syntax.selected

    @staticmethod
    def _static_binding_key(bindings, dependencies=None):
        """Exact relevant part of a source environment for a pure query."""
        bindings = {} if bindings is None else bindings
        names = bindings if dependencies is None else frozenset(dependencies)
        return tuple(sorted(
            ((repr(name), _fixed_key(bindings[name]))
             for name in names if name in bindings),
            key=repr,
        ))

    def _static_query_key(self, fn, node, bindings):
        # ``fn`` fixes lexical ownership and ``node`` fixes point-sensitive
        # construction facts in ScopeResolver.  A present but unproved
        # binding remains in the key because it shadows an owner constant.
        # Keep strong references to the AST objects.  Several lowering paths
        # synthesize short-lived proxy expressions; caching only ``id(node)``
        # lets CPython reuse that integer for a different proxy later in the
        # same plan and would turn memoization into a semantic collision.
        return (fn, node, self._static_binding_key(
            bindings, self._static_dependencies(node)))

    def _static_scalar(self, fn, node, bindings):
        """Memoized declaration-time scalar evaluation."""
        bindings = self._structural_bindings(bindings, node)
        return self._static_scalar_from_bindings(fn, node, bindings)

    def _static_scalar_from_bindings(self, fn, node, bindings):
        # Descendants have their own relevant bindings. A changed loop index
        # must not make us reevaluate every unchanged scalar subtree. Reuse
        # the already prepared environment, without introducing new facts.
        key = self._static_query_key(fn, node, bindings)
        if key not in self._query_cache.static:
            self._query_cache.static[key] = _static_value(
                node, self.resolver.owner_of(fn), bindings,
                budget=self.budget,
                is_builtin=lambda name: self.resolver.is_builtin(fn, name),
                value_of=lambda child, active: self._static_scalar_from_bindings(
                    fn, child, active))
        return self._query_cache.static[key]

    def _static_truth(self, fn, node, bindings):
        """Memoized truth projection of declaration-time scalar facts."""
        bindings = self._structural_bindings(bindings, node)
        key = self._static_query_key(fn, node, bindings)
        if key not in self._query_cache.truth:
            self._query_cache.truth[key] = static_truth(
                node,
                value_of=lambda candidate, active: self._static_scalar(
                    fn, candidate, active),
                bindings=bindings,
                budget=self.budget,
            )
        return self._query_cache.truth[key]

    def _contract_query_key(self, fn, node, env, bindings, abstract):
        """Identity of one side-effect-free contract query in an activation."""
        if not self._context:
            return None
        syntax = self._query_cache.syntax_of(node)
        names, fields, binding_keys, numeric_keys = self._query_dependency_sets(node)
        record_names = frozenset(
            name for name in names if isinstance(env.get(name), _RecordFact))
        if not self._query_cache.is_pure(node, record_names=record_names):
            return None
        environment_keys = names | {
            _state_key(name) for name in fields
        }
        lengths = tuple(sorted(
            (name, sequence) for name, sequence in bindings.lengths.items()
            if name in names))
        environment_keys |= {sequence for _name, sequence in lengths}
        live_bindings = _scalar_contract_ignores_bounds(
            node, env, bindings, abstract, expressions=syntax.expressions)
        bounds = getattr(bindings, "bounds", {})
        abstract = abstract or {}
        try:
            key = (
                # State-only summaries omit layout-binding observations. They
                # may be reused by other state layers, never by the final pass.
                self.state_only, fn, node,
                lengths,
                tuple(sorted(
                    (name, env[name]) for name in environment_keys if name in env)),
                self._static_binding_key(bindings, binding_keys),
                None if live_bindings else tuple(sorted(
                    (repr(name), _bounds_key(value))
                    for name, value in _numeric_projection(bounds, numeric_keys).items()
                )),
                tuple(sorted((
                    (guard, value) for guard, value in
                    getattr(bindings, "guards", {}).items()
                    if (type(guard) is tuple and len(guard) == 2
                        and guard[1] in names)
                ), key=repr)),
                tuple(sorted(
                    (name, abstract[name]) for name in names if name in abstract)),
                live_bindings,
            )
            hash(key)
        except (TypeError, ValueError):
            return None
        return key

    def _finish_contract_query(self, key, node, contract):
        if key is not None:
            context = self._context[-1]
            (_names, _fields, binding_keys,
             numeric_keys) = self._query_dependency_sets(node)
            observations = []
            for occurrence in self._query_cache.syntax_of(node).expressions:
                observed = (contract if occurrence is node else
                            self._expression_contexts.get(
                                (context, id(occurrence))))
                if observed is None:
                    continue
                fixed = self._expression_fixed.get(
                    (context, id(occurrence)), {})
                bounds = self._expression_bounds.get(
                    (context, id(occurrence)), {})
                observations.append(_ContractObservation(
                    occurrence, observed,
                    tuple((name, fixed[name]) for name in sorted(
                        binding_keys, key=repr) if name in fixed),
                    tuple(sorted(_numeric_projection(bounds, numeric_keys).items())),
                ))
            self._contract_query_cache[key] = _ContractQueryResult(
                contract, tuple(observations), live_bindings=key[-1])
        return self._remember_expression(node, contract)

    def _replay_contract_query(self, result, bindings):
        """Install cached descendant facts in the current activation.

        A contract is consumed later by exact source occurrence, not only by
        its root expression.  Replaying the descendant observations preserves
        that evidence.  Only dependencies of the query come from the cached
        path; unrelated bindings are taken from the current state layer.
        """
        if self.state_only:
            # Preliminary state closure consumes value contracts/ranges, not
            # the occurrence-binding snapshots used by the final layout pass.
            for observation in result.observations:
                self._remember_expression(observation.node, observation.contract)
            return result.contract
        if result.live_bindings:
            for observation in result.observations:
                self._remember_fixed(observation.node, bindings)
                self._remember_expression(observation.node, observation.contract)
            return result.contract
        for observation in result.observations:
            (names, _fields, _binding_keys,
             numeric_keys) = self._query_dependency_sets(observation.node)
            active = _ScalarBindings(
                observation.fixed,
                scalar_bounds=observation.bounds,
                guard_facts={
                    guard: value for guard, value in
                    getattr(bindings, "guards", {}).items()
                    if (type(guard) is tuple and len(guard) == 2
                        and guard[1] in names)
                },
            )
            # An observation already contains the exact dependency projection
            # from the cached visit.  Reconstructing the caller's entire
            # abstract environment here used O(number-of-locals) work per AST
            # descendant and then discarded those unrelated facts below.
            relevant = _numeric_projection(active.bounds, numeric_keys)
            for name in tuple(active.bounds):
                if name not in relevant:
                    active.forget_numeric(name)
            self._remember_fixed(observation.node, active)
            self._remember_expression(
                observation.node, observation.contract)
        return result.contract

    def _refine_scalar_condition(self, fn, node, bindings, truth):
        """Refine one pure scalar predicate without unrelated-path work.

        The range transfer reads only names occurring in the predicate.  A
        loop may carry many other induction and construction facts, but they
        cannot change this edge.  Cache only the relevant output bounds, then
        apply them to a clone of the current environment so unrelated facts
        are neither discarded nor borrowed from another path.
        """
        selected = self._query_cache.syntax_of(node).selected
        if not self._query_cache.is_pure(node):
            answer = _refine_scalar_ranges(
                node, self.resolver.owner_of(fn), bindings, truth,
                budget=self.budget, static_value_of=lambda child, active:
                self._static_scalar_from_bindings(fn, child, active))
            if answer is None:
                return None
            for name in selected:
                if name in bindings.bounds:
                    answer.set_bounds(name, bindings.bounds[name])
                else:
                    answer.forget_numeric(name)
            return answer
        (_names, _fields, binding_keys,
         numeric_keys) = self._query_dependency_sets(node)
        key = (
            fn, node, bool(truth),
            self._static_binding_key(bindings, binding_keys),
            tuple(sorted(
                (repr(name), _bounds_key(value))
                for name, value in _numeric_projection(bindings.bounds, numeric_keys).items()
            )),
        )
        if key not in self._scalar_refinement_cache:
            # Translating a predicate depends on its fixed values, not on
            # the changing interval of an ordinary scalar variable.  Reuse
            # that expression across state layers, but apply the assumption
            # to this layer's bounds afresh.  Selection translation also
            # depends on whether a leaf envelope exists, not its endpoints.
            expression_key = (
                self._static_query_key(fn, node, bindings),
                frozenset(_numeric_projection(bindings.bounds, selected)),
            )
            if expression_key not in self._query_cache.predicates:
                self._query_cache.predicates[expression_key] = _range_ir(
                    node, self.resolver.owner_of(fn), bindings,
                    budget=self.budget, static_value_of=lambda child, active:
                    self._static_scalar_from_bindings(fn, child, active))
            expression = self._query_cache.predicates[expression_key]
            # The cached path needs the same Python truth conversion as the
            # uncached range transfer. A source integer used as a condition is
            # nonzero/zero, not a Boolean-typed IR variable.
            refined = (bindings.bounds if expression is None else
                       ranges.assume(ir.Op("truth", expression), truth,
                                     bindings.bounds))
            self._scalar_refinement_cache[key] = (
                None if refined is None else tuple(
                    sorted(_numeric_projection(refined, numeric_keys).items())
                )
            )
        updates = self._scalar_refinement_cache[key]
        if updates is None:
            return None
        answer = bindings.clone()
        # ``#element:x`` and ``self.x`` describe the envelope of every cell in
        # an array.  A test of one selected cell may use that envelope to prove
        # the branch impossible, but taking the branch refines only that
        # selection, not every other cell. Selected-position keys may be
        # refined, but never write that refinement back to the aggregate.
        for name, value in updates:
            if name not in selected:
                answer.set_bounds(name, value)
        return answer

    def _join_states(self, states):
        """Join paths without erasing finite record keys or Boolean selectors.

        Ordinary values need one abstract join.  A live record builder is
        different: a later computed key may depend on a finite local selected
        by earlier control flow.  Keep those exact binding alternatives until
        the write records them, while joining states that carry the same fixed
        bindings. A saved Boolean also selects the path on which presence was
        tested; keep its two values instead of losing that correlation. Later
        writes still transfer each path normally, including rebinding the
        selector itself. Expression planning spends the shared source budget once
        per retained path, which bounds this partition compositionally.
        """
        states = list(states)
        if not states:
            return []
        record_sensitive = any(any(isinstance(value, _RecordFact)
                                   for value in env.values())
                               for env, _bindings in states)
        grouped = {}
        for state in states:
            _env, bindings = state
            guards = frozenset(getattr(bindings, "guards", {}).items())
            fixed = tuple(sorted(
                (name, _fixed_key(value))
                for name, value in bindings.items()
                if record_sensitive or type(value) is bool))
            # A local None alternative also selects a path: a saved optional
            # read must retain the bounds that chose its arm until use.
            presence = (
                _state_presence_key(_state_contracts_of(_env)),
                frozenset(name for name, value in _env.items()
                          if self._value_of_fact(value).is_none),
            )
            grouped.setdefault((guards, fixed, presence), []).append(state)
        if len(grouped) > self.budget.left:
            node = (self._frames[-1]["function"]
                    if self._frames else self.model.step)
            raise SourceValueBudgetExceeded(node)
        groups = grouped.values()
        return [(
            self._merge_envs(*(env for env, _bindings in group)),
            self._merge_bindings(*(bindings for _env, bindings in group)),
        ) for group in groups]

    @staticmethod
    def _sequence_reference(node):
        """Abstract-environment key named by ``len(node)``."""
        if isinstance(node, ast.Name):
            return node.id
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            return _state_key(node.attr)
        return None

    def _mapping_binding(self, node, env):
        key = self._sequence_reference(node)
        contract = env.get(key)
        if isinstance(contract, ValueContract) and contract.is_mapping:
            return key, contract
        return None

    def _length_reference(self, fn, node, bindings):
        if isinstance(node, ast.Name):
            return bindings.lengths.get(node.id)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "len" and self.resolver.is_builtin(fn, "len")
                and has_plain_positional_arity(node, 1)):
            return self._sequence_reference(node.args[0])
        return None

    def _refine_sequence_length(self, node, states, truth):
        """Narrow a finite container through one comparison with ``len``."""
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1
                and len(node.comparators) == 1):
            return None
        fn = self._frames[-1]["function"] if self._frames else self.model.step

        def bounds(contract, limit, operator):
            lo, hi = contract.minimum, contract.maximum
            limit_lo, limit_hi = limit.lo, limit.hi
            positive = truth
            op = operator
            if isinstance(op, ast.NotEq):
                positive = not positive
                op = ast.Eq()
            if isinstance(op, ast.Eq):
                if positive:
                    lo = max(lo, limit_lo)
                    hi = min(hi, limit_hi)
                elif limit_lo == limit_hi:
                    # An interval can exclude a singleton only at its ends.
                    # Interior holes must keep both sides of the interval.
                    if lo == limit_lo:
                        lo += 1
                    if hi == limit_lo:
                        hi -= 1
                return lo, hi
            if isinstance(op, ast.Lt):
                return ((lo, min(hi, limit_hi - 1)) if positive
                        else (max(lo, limit_lo), hi))
            if isinstance(op, ast.LtE):
                return ((lo, min(hi, limit_hi)) if positive
                        else (max(lo, limit_lo + 1), hi))
            if isinstance(op, ast.Gt):
                return ((max(lo, limit_lo + 1), hi) if positive
                        else (lo, min(hi, limit_hi)))
            if isinstance(op, ast.GtE):
                return ((max(lo, limit_lo), hi) if positive
                        else (lo, min(hi, limit_hi - 1)))
            return lo, hi

        out = []
        recognized = False
        for env, bindings in states:
            left = self._length_reference(fn, node.left, bindings)
            right = self._length_reference(fn, node.comparators[0], bindings)
            if (left is None) == (right is None):
                out.append((env, bindings))
                continue
            operator = node.ops[0]
            key, limit_node = left, node.comparators[0]
            if right is not None:
                key, limit_node = right, node.left
                reverse = {ast.Lt: ast.Gt, ast.LtE: ast.GtE,
                           ast.Gt: ast.Lt, ast.GtE: ast.LtE,
                           ast.Eq: ast.Eq, ast.NotEq: ast.NotEq}
                kind = reverse.get(type(operator))
                if kind is None:
                    out.append((env, bindings))
                    continue
                operator = kind()
            recognized = True
            structural = self._structural_bindings(bindings)
            limit = self._range_of(fn, limit_node, structural)
            if limit is None:
                out.append((env, bindings))
                continue
            current = env.get(key, UNKNOWN)
            if not isinstance(current, ValueContract) or not (current.is_sequence or current.is_mapping):
                out.append((env, bindings))
                continue
            interval = bounds(current, limit, operator)
            if interval is None or interval[0] > interval[1]:
                continue
            env[key] = current.with_interval(
                interval[0], interval[1], current.element)
            out.append((env, bindings))
        return out if recognized else None

    def _condition_states(self, node, states, truth):
        """Refine locals on paths where ``node`` has ``truth``.

        Exact ``None`` tags and finite sequence lengths are both representation
        facts.  Boolean composition is interpreted with its real short-circuit
        paths, so refinements reach only the source paths on which they hold.
        """
        states = [(dict(env), self._copy_bindings(bindings))
                  for env, bindings in states]
        # An unwritten parameter denotes the same value throughout one
        # activation.  Preserve its truth as a path fact so a later matching
        # test can use values defined in the corresponding earlier arm.  This
        # is not a fixed-value fact: integer truth means nonzero, not ``True``.
        if isinstance(node, ast.Name):
            fn = (self._frames[-1]["function"]
                  if self._frames else self.model.step)
            if node.id in self._repeated_truth_parameter_names(fn):
                context = (self._frames[-1]["context"]
                           if self._frames else self.root_context)
                key = (context, node.id)
                matching = []
                for env, bindings in states:
                    previous = bindings.guards.get(key)
                    if previous is not None and previous != truth:
                        continue
                    bindings.guards[key] = truth
                    matching.append((env, bindings))
                states = matching
                if not states:
                    return []
        # Literal and declaration-owned scalar predicates are source facts,
        # not merely optimization opportunities.  Retaining the impossible
        # arm would let dead returns and dead record writes participate in the
        # public transaction schema.  Use the same deliberately small truth
        # authority as conditional-expression planning, before applying the
        # path-sensitive refinements below.
        fn = (self._frames[-1]["function"]
              if self._frames else self.model.step)
        fixed = self._merge_bindings(
            *(bindings for _env, bindings in states)) if states else {}
        fixed = self._structural_bindings(fixed)
        known = self._static_truth(fn, node, fixed)
        if type(known) is bool:
            return states if known is truth else []
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self._condition_states(node.operand, states, not truth)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "bool"
                and self.resolver.is_builtin(fn, "bool")
                and has_plain_positional_arity(node, 1)):
            return self._condition_states(node.args[0], states, truth)
        key = self._sequence_reference(node)
        if key is not None:
            matching = []
            for env, bindings in states:
                sequence = bindings.lengths.get(key, key)
                current = env.get(sequence, UNKNOWN)
                if isinstance(current, ValueContract) and (current.is_sequence or current.is_mapping):
                    lo = max(current.minimum, 1) if truth else current.minimum
                    hi = current.maximum if truth else 0
                    if hi is not None and lo > hi:
                        continue
                    env[sequence] = current.with_interval(lo, hi, current.element)
                matching.append((env, bindings))
            states = matching
        if isinstance(node, ast.BoolOp) and node.values:
            want_all = ((isinstance(node.op, ast.And) and truth)
                        or (isinstance(node.op, ast.Or) and not truth))
            continuing = states
            exits = []
            for value in node.values:
                if want_all:
                    continuing = self._condition_states(
                        value, continuing, truth)
                else:
                    exits.extend(self._condition_states(
                        value, continuing, truth))
                    continuing = self._condition_states(
                        value, continuing, not truth)
                if not continuing:
                    break
            return continuing if want_all else exits

        # Refine scalar facts at the same atomic control edge as sequence and
        # option facts.  Applying this only to the enclosing Boolean formula
        # loses useful conjuncts: on ``limit > 0 and len(xs) < limit`` the
        # second comparison must see that ``limit`` is positive.
        scalar_refined = []
        for env, bindings in states:
            refined = self._refine_scalar_condition(
                fn, node, bindings, truth)
            if refined is not None:
                scalar_refined.append((env, refined))
        states = scalar_refined
        if not states:
            return []

        length_refined = self._refine_sequence_length(node, states, truth)
        if length_refined is not None:
            return length_refined

        test = none_test(node)
        if test is None:
            return states
        desired_none = test.none_when_true == truth
        out = []
        for env, bindings in states:
            root, path = self._record_path(test.operand)
            if root is None:
                return states
            current = env.get(root, UNKNOWN)
            if isinstance(current, _StaticFact):
                if path:
                    return states
                selected = tuple(
                    value for value in current.values
                    if (value is None) == desired_none)
                if not selected:
                    continue
                env[root] = _StaticFact(selected)
                bindings.pop(root, None)
                if desired_none:
                    bindings.forget_numeric(root)
                out.append((env, bindings))
                continue
            batch_fact = current if isinstance(current, _BatchFact) else None
            if batch_fact is not None:
                if path:
                    return states
                current = batch_fact.value
            if isinstance(current, _RecordFact):
                record = current
                record_contract = record.contract
            else:
                record = None
                record_contract = current
            current = (contract_at_record_path(record_contract, path)
                       if path else record_contract)
            if current is None:
                return states
            refined_batch = (None if batch_fact is None else
                             self._narrow_batch_none(
                                 batch_fact.batch, desired_none))
            if batch_fact is None:
                refined = narrow_none(current, is_none=desired_none)
            elif desired_none:
                refined = NONE
            elif refined_batch is not None and refined_batch.forms == \
                    frozenset({BatchForm.MAPPING}):
                refined = refined_batch.transaction
            elif refined_batch is not None:
                refined = batch_value_contract(refined_batch)
            else:
                refined = INCOMPATIBLE
            if refined is INCOMPATIBLE or (batch_fact is not None
                                           and refined_batch is None):
                continue
            if path:
                replacement = contract_with_record_path(
                    record_contract, path, refined)
                if replacement is None:
                    return states
                env[root] = replacement if record is None else _RecordFact(
                    replacement.fields, record.definite, record.writes,
                    record.ranges)
            else:
                env[root] = (_BatchFact(refined, refined_batch)
                             if batch_fact is not None else refined)
            # ``None`` is carried by the contract.  The fixed-value map uses
            # Python None to mean "no fixed fact", so retaining an old entry
            # here would falsely describe the selected arm.
            bindings.pop(root, None)
            if desired_none:
                bindings.forget_numeric(root)
            out.append((env, bindings))
        return out

    def _known_truth_fact(self, node, bindings):
        """Truth of a previously partitioned direct parameter test."""
        positive = True
        while isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            positive = not positive
            node = node.operand
        if not isinstance(node, ast.Name):
            return None
        context = (self._frames[-1]["context"]
                   if self._frames else self.root_context)
        key = (context, node.id)
        guards = getattr(bindings, "guards", {})
        if key not in guards:
            return None
        value = guards[key]
        return value if positive else not value

    def _repeated_truth_parameter_names(self, fn):
        """Unwritten parameters tested for truth at least twice in ``fn``.

        Retaining a path split is useful only when a later guard can consume
        it.  Restricting the partition to repeated predicates also preserves
        the ordinary joined source plan at a single conditional, where the
        downstream operation-specific rule owns any refusal.
        """
        key = id(fn)
        cached = self._repeated_truth_parameters.get(key)
        if cached is not None:
            return cached
        counts = {}
        for candidate in _same_scope(fn):
            tests = ()
            if isinstance(candidate, (ast.If, ast.IfExp, ast.While)):
                tests = (candidate.test,)
            elif isinstance(candidate, ast.comprehension):
                tests = tuple(candidate.ifs)
            for test in tests:
                for name in _truth_test_names(test):
                    if self.resolver.is_read_only_parameter(fn, name):
                        counts[name] = counts.get(name, 0) + 1
        answer = frozenset(
            name for name, count in counts.items() if count > 1)
        self._repeated_truth_parameters[key] = answer
        return answer

    @staticmethod
    def _record_path(node):
        """Return ``(root, fields)`` for a fixed record-selection chain."""
        fields = []
        current = node
        while isinstance(current, ast.Subscript):
            key = current.slice
            if not isinstance(key, ast.Constant) \
                    or type(key.value) is not str:
                return None, ()
            fields.append(key.value)
            current = current.value
        if (isinstance(current, ast.Attribute)
                and isinstance(current.value, ast.Name)
                and current.value.id == "self"):
            return _state_key(current.attr), tuple(reversed(fields))
        if not isinstance(current, ast.Name):
            return None, ()
        return current.id, tuple(reversed(fields))

    def _saved_structural_predicate(self, fn, node, bindings):
        """An exact Boolean whose saved value can select structural facts.

        Reuse the existing path partition, rather than retaining syntax to
        re-evaluate at a later use. In particular, ``and``/``or`` may return an
        integer operand: only proved Boolean results can be stored as the
        singleton True/False facts used by this partition.
        """
        names = self._query_dependency_sets(node)[0]
        saved_lengths = frozenset(names & bindings.lengths.keys())
        key = fn, node, saved_lengths
        if key in self._saved_structural_predicates:
            return self._saved_structural_predicates[key]

        def builtin_call(candidate, name):
            return (isinstance(candidate, ast.Call)
                    and isinstance(candidate.func, ast.Name)
                    and candidate.func.id == name
                    and self.resolver.is_builtin(fn, name)
                    and has_plain_positional_arity(candidate, 1))

        def boolean(candidate):
            if isinstance(candidate, ast.Compare):
                return True
            if isinstance(candidate, ast.UnaryOp) and isinstance(candidate.op, ast.Not):
                return True
            if isinstance(candidate, ast.Constant):
                return type(candidate.value) is bool
            if isinstance(candidate, ast.BoolOp):
                return bool(candidate.values) and all(map(boolean, candidate.values))
            if isinstance(candidate, ast.IfExp):
                return boolean(candidate.body) and boolean(candidate.orelse)
            return builtin_call(candidate, "bool")

        structural = bool(saved_lengths)
        answer = boolean(node)
        if answer:
            for child in self._query_cache.syntax_of(node).expressions:
                if isinstance(child, ast.Call):
                    if not self.resolver.call_has_no_external_effect(fn, child):
                        answer = False
                        break
                    if builtin_call(child, "len") or builtin_call(child, "bool"):
                        structural |= self._sequence_reference(child.args[0]) is not None
                elif not isinstance(child, (*_CONTRACT_PURE_EXPRESSIONS, ast.Subscript)):
                    answer = False
                    break
                test = none_test(child)
                if test is not None:
                    structural |= self._record_path(test.operand)[0] is not None
                if isinstance(child, ast.UnaryOp) and isinstance(child.op, ast.Not):
                    structural |= self._sequence_reference(child.operand) is not None
        answer = answer and structural
        self._saved_structural_predicates[key] = answer
        return answer

    def _condition_states_with_ranges(self, fn, node, states, truth):
        """Apply the path refinements owned by the condition interpreter."""
        result = self._condition_states(node, states, truth)
        if self._context:
            outcomes = self._branch_outcomes.setdefault(
                (self._context[-1], id(node)), set())
            if result:
                outcomes.add(truth)
        return result

    def _resolve(self, fn, env):
        def resolve(node, bindings):
            if isinstance(node, ast.Name):
                if node.id in bindings:
                    return contract_of_value(bindings[node.id])
                if node.id in env:
                    value = env[node.id]
                    return (value.contract if isinstance(
                        value, (_RecordFact, _BatchFact, _StaticFact))
                            else value)
                # Structural planning and scalar lowering must use the same
                # lexical owner.  In particular, integer masks in fp_lib are
                # scalar values, not unknown source objects merely because
                # they live outside the model's module.
                kind, value = self.resolver.resolve_const(fn, node.id)
                if kind == "const":
                    return contract_of_value(value)
                if (self.resolver.resolves_model_module(fn, node.id)
                        and node.id in self.resolver.tables):
                    return contract_of_value(
                        self.resolver.table_source_values[node.id])
                return UNKNOWN
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"):
                current = env.get(_state_key(node.attr))
                if isinstance(current, ValueContract):
                    return current
                declared = self.declarations.get(node.attr, UNKNOWN)
                if declared is not UNKNOWN:
                    return declared
                if self._static_reference_candidate(fn, node, env, bindings):
                    # A readonly class table has the same recursive contract
                    # when passed as a helper argument as when read directly.
                    # Nested expression queries use this resolver without the
                    # root _expr finite-reference projection.
                    return contract_of_value(
                        self.resolver.static_field_values[node.attr])
                fixed = self.resolver.owner_of(fn)(node)
                return (UNKNOWN if fixed is None
                        else contract_of_value(fixed))
            fixed = self.resolver.class_attribute(fn, node, shadowed=bindings)
            if type(fixed) in (int, bool):
                return contract_of_value(fixed)
            return UNKNOWN
        return resolve

    def _static_collection_literal(self, fn, node, bindings):
        """A closed literal table, preserving Python list/tuple carriers."""
        if not isinstance(node, (ast.Dict, ast.List, ast.Tuple)):
            return None
        frozen = evaluate_frozen(
            node, lambda leaf: self._static_scalar(fn, leaf, bindings),
            budget=self.budget)
        if frozen is None or not frozen.fits_expansion(
                DEFAULT_ROM_NODES, budget=self.budget, node=node):
            return None
        return frozen.to_source_value(budget=self.budget, node=node)

    def _static_reference_candidate(self, fn, node, env, bindings):
        """Whether ``node`` is rooted in a finite lookup-table value."""
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and not self.resolver.is_local(fn, "self")):
            return node.attr in self.resolver.static_field_values
        if isinstance(node, ast.Dict):
            value = self._static_collection_literal(
                fn, node, self._structural_bindings(bindings))
            return (type(value) is dict and bool(value)
                    and any(type(key) is not str for key in value))
        if isinstance(node, ast.Name):
            return (isinstance(env.get(node.id), _StaticFact)
                    or (node.id not in bindings
                        and self.resolver.resolves_model_module(fn, node.id)
                        and node.id in self.resolver.tables))
        if isinstance(node, ast.IfExp):
            return (self._static_reference_candidate(
                        fn, node.body, env, bindings)
                    or self._static_reference_candidate(
                        fn, node.orelse, env, bindings))
        if isinstance(node, ast.Subscript):
            return self._static_reference_candidate(
                fn, node.value, env, bindings)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"):
            return self._static_reference_candidate(
                fn, node.func.value, env, bindings)
        return False

    def _static_reference_values(self, fn, node, env, bindings):
        """Normal values of one closed finite-reference expression.

        The source planner needs these values only while a local denotes a
        node inside an immutable module lookup table.  They remain analysis
        facts: executable lowering separately assigns an integer tag and
        eliminates the reference at ``len``, iteration, or a final selector.
        """
        structural = self._structural_bindings(bindings)

        def closed(candidate):
            if (isinstance(candidate, ast.Constant)
                    and candidate.value is None):
                return (None,)
            value = (self._static_collection_literal(fn, candidate, structural)
                     if isinstance(candidate, (ast.Dict, ast.List, ast.Tuple)) else
                     self._static_scalar(fn, candidate, structural))
            if value is None:
                # ``_static_value`` uses None for an unproved expression;
                # the literal singleton was handled explicitly above.
                return None
            try:
                _static_fact_key(value)
            except TypeError:
                return None
            return (value,)

        def visit(candidate):
            if (isinstance(candidate, ast.Attribute)
                    and self._static_reference_candidate(fn, candidate, env, bindings)):
                return (self.resolver.static_field_values[candidate.attr],)
            if isinstance(candidate, ast.Name):
                fact = env.get(candidate.id)
                if isinstance(fact, _StaticFact):
                    return fact.values
                if (candidate.id not in bindings
                        and self.resolver.resolves_model_module(
                            fn, candidate.id)
                        and candidate.id in self.resolver.tables):
                    return (self.resolver.table_source_values[candidate.id],)
                return None

            if isinstance(candidate, ast.IfExp):
                known = self._static_truth(
                    fn, candidate.test, structural)
                if type(known) is bool:
                    return visit(candidate.body if known else candidate.orelse)
                left = visit(candidate.body)
                right = visit(candidate.orelse)
                if left is None:
                    left = closed(candidate.body)
                if right is None:
                    right = closed(candidate.orelse)
                if left is None or right is None:
                    return None
                return _unique_static_values((*left, *right))

            if (isinstance(candidate, ast.Call)
                    and isinstance(candidate.func, ast.Attribute)
                    and candidate.func.attr == "get"
                    and not candidate.keywords
                    and len(candidate.args) in (1, 2)):
                containers = visit(candidate.func.value)
                if containers is None:
                    return None
                defaults = ((None,) if len(candidate.args) == 1 else
                            visit(candidate.args[1]))
                if defaults is None and len(candidate.args) == 2:
                    defaults = closed(candidate.args[1])
                if defaults is None:
                    return None
                key = self._static_scalar(
                    fn, candidate.args[0], structural)
                known = type(key) in (bool, int, str)
                values = []
                for mapping in containers:
                    if type(mapping) is not dict:
                        # This alternative raises before returning a value.
                        continue
                    if known:
                        values.extend((mapping[key],)
                                      if key in mapping else defaults)
                    else:
                        values.extend(mapping.values())
                        values.extend(defaults)
                return (_unique_static_values(values) if values else None)

            if (isinstance(candidate, ast.Subscript)
                    and not isinstance(candidate.slice, ast.Slice)):
                containers = visit(candidate.value)
                if containers is None:
                    return None
                key = self._static_scalar(
                    fn, candidate.slice, structural)
                known = type(key) in (bool, int, str)
                values = []
                for container in containers:
                    if type(container) not in (dict, list, tuple):
                        continue
                    if known:
                        try:
                            values.append(container[key])
                        except (IndexError, KeyError, TypeError):
                            continue
                    else:
                        values.extend(container.values() if type(
                            container) is dict else container)
                return (_unique_static_values(values) if values else None)

            return closed(candidate)

        try:
            return visit(node)
        except SourceValueBudgetExceeded:
            raise
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _retains_static_reference(values):
        """Whether a local must carry finite collection identity onward."""
        return bool(values) and any(type(value) in (dict, list, tuple, str, bytes)
                                    for value in values) and all(
            value is None or type(value) in (dict, list, tuple, str, bytes)
            for value in values)

    @staticmethod
    def _numeric_static_range(values):
        """Exact integer hull of finite scalar lookup alternatives."""
        if not values or any(type(value) not in (bool, int)
                             for value in values):
            return None
        integers = tuple(int(value) for value in values)
        return ranges.Range(min(integers), max(integers))

    def _fixed_slice_contract(self, fn, node, bindings):
        """Exact contract of a proved in-bounds affine slice, if any."""
        if not isinstance(node.slice, ast.Slice):
            return UNKNOWN
        base = self._observed_contract(node.value)
        length = base.exact_length
        if length is None:
            return UNKNOWN
        structural = self._structural_bindings(bindings)
        owner = self.resolver.owner_of(fn)
        step = (1 if node.slice.step is None else self._static_scalar(
            fn, node.slice.step, structural))
        if type(step) is not int or step <= 0:
            return UNKNOWN

        def form(bound, default):
            if bound is None:
                return ir.Lit(default)
            return _range_ir(
                bound, owner, structural, budget=self.budget,
                generated={})

        lower = form(node.slice.lower, 0)
        upper = form(node.slice.upper, length)
        if lower is None or upper is None:
            return UNKNOWN
        width_form = ranges.affine_form(ir.Op("sub", upper, lower))
        width = (None if width_form is None or width_form[1]
                 else width_form[0])
        lower_bounds = ranges.bounds_of_expression(lower, structural.bounds)
        upper_bounds = ranges.bounds_of_expression(upper, structural.bounds)

        def inside(value):
            return (value is not None and value.lo is not None
                    and value.lo >= 0 and value.hi is not None
                    and value.hi <= length)

        if not inside(lower_bounds) or not inside(upper_bounds):
            return UNKNOWN
        distance = (ranges.Range(width, width) if width is not None else
                    ranges.bounds_of_expression(
                        ir.Op("sub", upper, lower), structural.bounds))
        if distance is None or distance.lo is None or distance.hi is None:
            return UNKNOWN
        minimum = max(0, (distance.lo + step - 1) // step)
        maximum = max(0, (distance.hi + step - 1) // step)
        if minimum != maximum:
            return UNKNOWN
        return base.with_interval(minimum, maximum, base.element)

    def _closed_collection_values(self, fn, node, bindings):
        """Normal values of a nested read rooted in one immutable table."""
        path = []
        current = node
        while isinstance(current, ast.Subscript) and not isinstance(
                current.slice, ast.Slice):
            path.append(current.slice)
            current = current.value
        if (not isinstance(current, ast.Name)
                or current.id in bindings
                or not self.resolver.resolves_model_module(fn, current.id)
                or current.id not in self.resolver.tables):
            return None
        values = (self.resolver.table_source_values[current.id],)
        structural = self._structural_bindings(bindings)
        for selector in reversed(path):
            key = self._static_scalar(fn, selector, structural)
            following = []
            for value in values:
                if not isinstance(value, (dict, list, tuple)):
                    return None
                if type(key) in (bool, int, str):
                    try:
                        following.append(value[key])
                    except (IndexError, KeyError, TypeError):
                        # This alternative terminates at the read and has no
                        # normal value contract.
                        continue
                else:
                    following.extend(value.values() if isinstance(
                        value, dict) else value)
            values = tuple(following)
            if not values:
                return ()
        return values

    def _select(self, fn, env):
        """Contract of a proved slice or closed collection read."""
        def select(node, bindings, _contracts):
            structural = self._structural_bindings(bindings)
            base = node.value
            sliced = self._fixed_slice_contract(fn, node, bindings)
            if sliced is not UNKNOWN:
                return sliced
            if isinstance(node.slice, ast.Slice):
                return UNKNOWN  # The shared sequence contract owns clipping.
            if not isinstance(node.slice, ast.Slice):
                base_contract = self._resolve(fn, env)(base, structural)
                if (isinstance(base_contract, ValueContract)
                        and base_contract.is_sequence):
                    # Selection preserves the represented element contract;
                    # index validity is a separate fallible operation owned
                    # by executable lowering.  This applies equally to local
                    # and state arrays, including a comprehension whose trip
                    # count is established only by bounded state closure.
                    return base_contract.element or UNKNOWN
            # A local may carry one of several immutable module collections
            # behind a finite tag.  The executable lowerer eliminates that
            # reference at the selector; the source planner must do the same
            # even when the selection is nested inside a larger expression
            # such as ``total |= table[key] << shift``.  Looking only for a
            # module-rooted subscript here lost the scalar contract as soon as
            # the selected value became an operand of another expression.
            alternatives = self._static_reference_values(
                fn, node, env, bindings)
            if alternatives:
                result = contract_of_value(alternatives[0])
                for value in alternatives[1:]:
                    result = join_contracts(result, contract_of_value(value))
                return result
            selected = self._closed_collection_values(fn, node, bindings)
            if selected:
                result = contract_of_value(selected[0])
                for value in selected[1:]:
                    result = join_contracts(
                        result, contract_of_value(value))
                return result
            if isinstance(base, ast.Name) and isinstance(
                    env.get(base.id), _RecordFact):
                key = self._static_scalar(fn, node.slice, structural)
                fact = env[base.id]
                if type(key) is str and key in fact.definite:
                    # A mutable fixed-schema record can be consumed inside a
                    # step without itself becoming an output transaction.
                    # Freeze its representation at the first field read just
                    # as a boundary return freezes it at the return.  This is
                    # also the point at which every required field is proved
                    # present; the empty initializer alone has no schema.
                    self._remember_record_summary(base.id, fact, node)
                    return fact.field_contracts.get(key, UNKNOWN)
                return UNKNOWN
            if not isinstance(base, ast.Name) or base.id in bindings:
                return UNKNOWN
            if (not self.resolver.resolves_model_module(fn, base.id)
                    or base.id not in self.resolver.tables):
                return UNKNOWN
            table = self.resolver.table_source_values[base.id]
            key = self._static_scalar(fn, node.slice, structural)
            known = type(key) in (bool, int, str)
            return contract_of_collection_lookup(
                table, key, key_known=known)
        return select

    def _observed_contract(self, node):
        """Contract already produced while evaluating this occurrence."""
        if node is None:
            return NONE
        if self._context:
            found = self._expression_contexts.get(
                (self._context[-1], id(node)))
            if found is not None:
                return found
        return self._at_expression.get(id(node), UNKNOWN)

    def _record_literal(self, fn, node, bindings):
        """Builder fact for one fixed-string-key dictionary literal."""
        items = record_items(node, is_builtin=lambda name:
                             name not in bindings and self.resolver.is_builtin(fn, name))
        if items is None:
            return None
        fact = _RecordFact.empty()
        structural = self._structural_bindings(bindings)
        for key, value in items:
            if key is None:
                return None
            field = key if type(key) is str else self._static_scalar(fn, key, structural)
            if type(field) is not str:
                return None
            fact = fact.write(
                field, self._observed_contract(value), value if type(key) is str else key, (),
                self._range_of(fn, value, structural))
        return fact

    @staticmethod
    def _record_names(node, env):
        """Builders used as whole mutable mappings in ``node``.

        A name in the base position of ``record[key]`` denotes one field
        value, not the record object.  Treating that occurrence as an escape
        rejected ordinary reads and calls on already extracted immutable
        fields.  Every other load of the name still exposes the mutable
        mapping itself and remains an alias/escape.
        """
        if node is None:
            return frozenset()
        found = set()

        def visit(item, parent=None):
            if isinstance(item, ast.Name) \
                    and isinstance(item.ctx, ast.Load) \
                    and isinstance(env.get(item.id), _RecordFact) \
                    and not (isinstance(parent, ast.Subscript)
                             and parent.value is item):
                found.add(item.id)
            for child in ast.iter_child_nodes(item):
                visit(child, item)

        visit(node)
        return frozenset(found)

    def _reject_record_escape(self, fn, node, env):
        """Refuse calls that can observe a live mutable record builder.

        Direct arguments and receivers are explicit.  A resolved nested helper
        may also capture a builder from an enclosing activation; that closure
        edge is obtained from the same lexical resolver used by value-summary
        calls.  Unknown calls with no path to a local builder do not become a
        blanket refusal.
        """
        if record_copy_source(
                node, is_builtin=lambda name: self.resolver.is_builtin(fn, name)) is not None:
            # Copying reads a builder's current fields but cannot mutate it.
            # Shared children remain the points-to analysis's responsibility.
            return
        if self._record_names(node.func, env) or any(
                self._record_names(arg, env) for arg in node.args) or any(
                self._record_names(keyword.value, env)
                for keyword in node.keywords):
            raise SequencePlanError(
                node, "a mutable output-record builder escapes to a call",
                "record-builder-mutation-unsupported")
        target = self._call_target(fn, node)
        if target is None:
            return
        for _owner_id, name, owner in self._capture_names(
                target, transitive=True):
            frame = next((candidate for candidate in reversed(self._frames)
                          if candidate["function"] is owner), None)
            if frame is not None and isinstance(
                    frame["env"].get(name), _RecordFact):
                raise SequencePlanError(
                    node, "a closure can observe or mutate a caller's "
                          "output-record builder",
                    "record-builder-mutation-unsupported")

    def _record_store(self, fn, target, value_node, contract, env, bindings):
        """Apply one fixed-key subscript assignment to a live builder."""
        if not (isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)):
            return False
        name = target.value.id
        fact = env.get(name)
        if not isinstance(fact, _RecordFact):
            return False
        if isinstance(target.slice, ast.Slice):
            raise SequencePlanError(
                target, "a record builder is written through a slice",
                "record-builder-mutation-unsupported")
        structural = self._structural_bindings(bindings)
        interval = self._range_of(fn, value_node, structural)
        field = self._static_scalar(fn, target.slice, structural)
        cases = []
        if type(field) is str:
            selector = []
            for item in ast.walk(target.slice):
                if not (isinstance(item, ast.Name)
                        and isinstance(item.ctx, ast.Load)
                        and item.id in structural):
                    continue
                value = structural[item.id]
                if type(value) not in (bool, int):
                    raise SequencePlanError(
                        target.slice, "a computed output key depends on a "
                                      "non-scalar finite binding",
                        "output-key-selector-not-scalar")
                selector.append((item.id, value))
            cases.append((field, tuple(sorted(set(selector)))))
        else:
            # A formatted key may depend on a bounded integer local even when
            # it is not constant on this path.  Enumerate that local's proved
            # finite range, retain the scalar discriminator, and let lowering
            # implement the mapping write as one exhaustive switch.  Strings
            # themselves never enter SolverIR.
            varying = []
            fixed_selector = []
            builtin_callees = {
                id(item.func) for item in ast.walk(target.slice)
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                and item.func.id == "str" and "str" not in structural
                and self.resolver.is_builtin(fn, "str")
            }
            for item in ast.walk(target.slice):
                if not (isinstance(item, ast.Name)
                        and isinstance(item.ctx, ast.Load)) or id(item) in builtin_callees:
                    continue
                if item.id in structural:
                    value = structural[item.id]
                    if type(value) not in (bool, int):
                        varying = []
                        fixed_selector = []
                        break
                    if (item.id, value) not in fixed_selector:
                        fixed_selector.append((item.id, value))
                    continue
                interval = structural.ranges.get(item.id)
                if interval is None:
                    varying = []
                    break
                if item.id not in varying:
                    varying.append(item.id)
            cardinalities = [
                structural.ranges[variable].hi
                - structural.ranges[variable].lo + 1
                for variable in varying
            ]
            count = (0 if not varying else
                     finite_cardinality_product(*cardinalities))
            if count > UNROLL_LIMIT:
                raise SequencePlanError(
                    target.slice, "a computed output key has too many finite "
                                  "selector cases",
                    "output-key-selector-too-large")
            domains = [range(structural.ranges[variable].lo,
                             structural.ranges[variable].hi + 1)
                       for variable in varying]
            for values in cartesian_product(*domains):
                active = structural.clone()
                active.update(zip(varying, values))
                candidate = self._static_scalar(
                    fn, target.slice, active)
                if type(candidate) is not str:
                    cases = []
                    break
                selector = tuple(sorted(
                    (*fixed_selector, *zip(varying, values))))
                cases.append((candidate, selector))
        if not cases:
            raise SequencePlanError(
                target.slice, "an output record key has no finite string "
                              "selector plan",
                ("output-name-not-static" if not fact.fields else
                 "output-key-not-static"))
        cases = tuple(dict.fromkeys(cases))
        if not fact.fields and len({field for field, _selector in cases}) > 1:
            raise SequencePlanError(
                target.slice, "an output record created from an empty "
                              "mapping has a data-dependent field schema",
                "output-name-not-static")
        # The selector cases are alternatives for one execution of this
        # assignment, not a sequence of writes.  Joining one write from each
        # incoming fact keeps pre-existing fields definite while a field
        # introduced by only some selector values remains merely possible.
        fact = _RecordFact.join(
            fact.write(field, contract, target, selector, interval)
            for field, selector in cases)
        env[name] = fact
        bindings.forget_length(name)
        self._remember_binding(fn, name, env[name].contract)
        bindings.pop(name, None)
        bindings.forget_numeric(name)
        return True

    def _record_update(self, fn, node, env, bindings):
        items = record_update_items(node)
        if items is None or not isinstance(env.get(node.func.value.id), _RecordFact):
            return False
        name = node.func.value.id
        values = []
        for item in items:
            # Python retains the receiver object before evaluating arguments.
            # A rebind or escape needs heap identity, not fieldwise IR copies.
            if (self._record_names(item.value, env)
                    or any(isinstance(inner, ast.Name) and inner.id == name
                           and isinstance(inner.ctx, (ast.Store, ast.Del))
                           for inner in ast.walk(item.value))):
                raise SequencePlanError(node, "record update arguments alias or rebind the receiver",
                                        "record-builder-alias-unsupported")
            contract = self._expr(fn, item.value, env, bindings)
            interval = self._range_of(fn, item.value, self._structural_bindings(bindings))
            values.append((item, contract, interval))
        # Every argument sees the old record; writes start only after all RHS
        # evaluations. In particular update(x=out['y'], y=out['x']) swaps.
        fact = env[name]
        for item, contract, interval in values:
            fact = fact.write(item.arg, contract, item, interval=interval)
        env[name] = fact
        bindings.forget_length(name)
        bindings.pop(name, None)
        bindings.forget_numeric(name)
        self._remember_binding(fn, name, fact.contract)
        return True

    def _record_assignment(self, fn, target, value_node, value_contract,
                           env, bindings):
        """Handle a name assignment that creates or aliases a record builder."""
        copied = record_copy_source(
            value_node, is_builtin=lambda name:
            name not in bindings and self.resolver.is_builtin(fn, name))
        if not isinstance(target, ast.Name):
            if copied is None and self._record_names(value_node, env):
                raise SequencePlanError(
                    value_node, "a mutable output-record builder is rebound "
                                "through another target",
                    "record-builder-alias-unsupported")
            return False
        initial = self._record_literal(fn, value_node, bindings)
        if (initial is None and (isinstance(value_node, ast.DictComp)
                                 or copied is not None)
                and value_contract.is_record):
            # Comprehensions and shallow copies allocate fresh outer mappings.
            # Their proved contract supplies the complete fixed
            # schema; later subscript writes decide whether this local needs
            # the mutable fieldwise carrier.
            fields = tuple(value_contract.fields)
            initial = _RecordFact(
                fields,
                frozenset(name for name, _contract in fields),
            )
        if initial is not None:
            # A fixed mapping that is only read is an ordinary immutable
            # RecordValue in SolverIR.  The mutable builder representation is
            # needed only when this lexical local is later updated through a
            # subscript.  Classifying every dict literal as a builder made
            # normal field reads, equality and precise KeyError guards look
            # like unsupported builder escapes.
            def mutates_in(scope):
                return any(
                    (isinstance(candidate, ast.Subscript)
                    and isinstance(candidate.ctx, ast.Store)
                    and isinstance(candidate.value, ast.Name)
                    and candidate.value.id == target.id)
                    or sequence_method_target(candidate, "update") == ("local", target.id)
                    for candidate in _same_scope(scope))

            # Mutating a captured mapping does not require ``nonlocal`` in
            # Python.  Classify the outer literal as a builder before a call
            # to that closure is summarized; otherwise the empty literal is
            # lowered as a schema-less immutable record and the actual escape
            # is missed.  The lexical owner check excludes a nested local with
            # the same spelling.
            mutated = mutates_in(fn) or any(
                isinstance(scope, (ast.FunctionDef,
                                   ast.AsyncFunctionDef))
                and scope is not fn
                and self.resolver.local_owner(scope, target.id) is fn
                and mutates_in(scope)
                for scope in ast.walk(fn))
            if not mutated:
                return False
            env[target.id] = initial
            bindings.forget_length(target.id)
            self._remember_binding(
                fn, target.id, initial.contract, occurrence=target)
            bindings.pop(target.id, None)
            bindings.forget_numeric(target.id)
            return True
        # A proved None result cannot alias the mapping it just updated.
        # Argument escapes have already been checked during evaluation.
        if value_contract.is_none:
            return False
        referenced = self._record_names(value_node, env)
        if referenced:
            if (isinstance(value_node, ast.Name)
                    and value_node.id == target.id
                    and referenced == {target.id}):
                return True
            raise SequencePlanError(
                value_node, "a mutable output-record builder is aliased",
                "record-builder-alias-unsupported")
        return False

    def _sync_frame(self, fn, env, bindings):
        """Expose the current abstract state to lexically nested calls."""
        for frame in reversed(self._frames):
            if frame["function"] is fn:
                frame["env"] = env
                frame["bindings"] = bindings
                return

    def _capture_names(self, target, *, transitive=False, boundary=None,
                       active=frozenset()):
        """Lexical cells read directly or through nested callees."""
        boundary = target if boundary is None else boundary
        cache_key = (id(target), bool(transitive), id(boundary))
        if cache_key in self._dependency_cache:
            return self._dependency_cache[cache_key]
        if id(target) in active:
            raise SequencePlanError(
                target, "a recursive nested-helper closure whose finite "
                        "summary has no proved fixed point",
                "recursive-helper-unsupported")
        nodes = tuple(_same_scope(target))
        nested_calls = {}
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            callee = self._call_target(target, node)
            if (callee is not None
                    and self.resolver.lexical_parent(callee) is not None):
                nested_calls[id(node.func)] = callee

        def internal(owner):
            current = owner
            while current is not None:
                if current is boundary:
                    return True
                current = self.resolver.lexical_parent(current)
            return False

        names = set()
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                owner = self.resolver.local_owner(target, node.id)
                # A resolved nested-function spelling denotes code, not a
                # source value contract.  Its data dependencies enter through
                # the recursive call edge below.
                if id(node) in nested_calls:
                    continue
                if (owner is not None and owner is not target
                        and not internal(owner)):
                    names.add((id(owner), node.id, owner))
            if not (transitive and isinstance(node, ast.Call)):
                continue
            callee = nested_calls.get(id(node.func))
            if (callee is not None
                    and self.resolver.lexical_parent(callee) is not None):
                names.update(self._capture_names(
                    callee, transitive=True, boundary=boundary,
                    active=active | {id(target)}))
        result = frozenset(names)
        self._dependency_cache[cache_key] = result
        return result

    def _capture_facts(self, names):
        captured = []
        for owner_id, name, owner in sorted(
                names, key=lambda item: (item[0], item[1])):
            frame = next((candidate for candidate in reversed(self._frames)
                          if candidate["function"] is owner), None)
            if frame is None:
                raise SequencePlanError(
                    owner, f"the lexical activation carrying {name} is not "
                           "available while its closure is summarized",
                    "closure-activation-unresolved")
            contract = self._value_of_fact(
                frame["env"].get(name, UNKNOWN))
            fixed = frame["bindings"].get(name)
            # A closure may capture a one-sided path fact used only to prove
            # finite control (for example ``x > 0``).  It is not an encoding
            # range, but dropping it at the helper boundary can make a proved
            # terminating loop appear unbounded.
            interval = getattr(frame["bindings"], "bounds", {}).get(name)
            captured.append((owner_id, name, contract, fixed, interval))
        return tuple(captured)

    def _captures(self, target):
        """Direct inputs and full closure fingerprint of one activation.

        A sibling helper can call another closure without reading its cells
        itself.  Its summary still depends on those transitive cells, so the
        cache key carries the complete reachable closure environment while
        only directly free names are installed as locals of ``target``.
        """
        direct = self._capture_facts(
            self._capture_names(target, transitive=False))
        fingerprint = self._capture_facts(
            self._capture_names(target, transitive=True))
        return direct, fingerprint

    def _target_effects(self, fn, target, env, bindings):
        """Visit expressions evaluated while assigning to ``target``."""
        if isinstance(target, ast.Subscript):
            self._expr(fn, target.value, env, bindings)
            if isinstance(target.slice, ast.Slice):
                for bound in (target.slice.lower, target.slice.upper,
                              target.slice.step):
                    if bound is not None:
                        self._expr(fn, bound, env, bindings)
            else:
                self._expr(fn, target.slice, env, bindings)
            return
        if isinstance(target, ast.Attribute):
            self._expr(fn, target.value, env, bindings)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._target_effects(fn, item, env, bindings)

    def _augmented_expression(self, statement):
        """Stable expression denoted by one augmented assignment.

        An ``AugAssign`` evaluates its target as a load, combines that value
        with the RHS, then writes it back.  Source planning needs the combined
        expression in several bounded-state passes.  Reconstructing a fresh
        proxy on every pass destroys occurrence identity and prevents pure
        query reuse, so the procedure-wide query authority owns one proxy per
        source statement.
        """
        if statement not in self._query_cache.augmented:
            target = statement.target
            if isinstance(target, ast.Name):
                loaded = ast.Name(id=target.id, ctx=ast.Load())
            elif isinstance(target, ast.Attribute):
                loaded = ast.Attribute(
                    value=target.value, attr=target.attr, ctx=ast.Load())
            elif isinstance(target, ast.Subscript):
                loaded = ast.Subscript(
                    value=target.value, slice=target.slice, ctx=ast.Load())
            else:
                raise TypeError(
                    "an augmented-expression proxy needs a simple target")
            ast.copy_location(loaded, target)
            proxy = ast.BinOp(
                left=loaded, op=statement.op, right=statement.value)
            ast.copy_location(proxy, statement)
            self._query_cache.augmented[statement] = proxy
        return self._query_cache.augmented[statement]

    def _expr(self, fn, node, env, bindings, abstract=None, *, evaluated_items=None):
        self._sync_frame(fn, env, bindings)
        self._remember_fixed(node, bindings)

        def capture_item(child, contract, active):
            parent = self.resolver._parents.get(id(child))
            if isinstance(parent, (ast.Tuple, ast.List)):
                evaluated_items[child] = self._argument_facts(
                    fn, child, env, active)

        def replay(result, active):
            got = self._replay_contract_query(result, active)
            if evaluated_items is not None:
                # Cached queries are pure. Their descendants can therefore
                # take value snapshots from this activation's unchanged frame.
                for observation in result.observations:
                    capture_item(observation.node, observation.contract, active)
            return got

        query_key = self._contract_query_key(
            fn, node, env, bindings, abstract)
        if query_key is not None and query_key in self._contract_query_cache:
            return replay(
                self._contract_query_cache[query_key], bindings)
        if isinstance(node, ast.Call):
            if self._record_update(fn, node, env, bindings):
                return self._remember_expression(node, NONE)
            self._reject_record_escape(fn, node, env)
        if isinstance(node, ast.IfExp):
            # Value contracts, as well as scalar bounds, belong to the chosen
            # arm. Keeping only numeric refinements loses ``self.x is None``
            # and helper writes in an expression with stateful alternatives.
            self._expr(fn, node.test, env, bindings, abstract)
            outgoing = []
            result = None
            for truth, arm in ((True, node.body), (False, node.orelse)):
                states = self._condition_states_with_ranges(
                    fn, node.test, [(env, bindings)], truth)
                for one_env, one_bindings in states:
                    got = self._expr(fn, arm, one_env, one_bindings, abstract)
                    result = got if result is None else join_contracts(result, got)
                    outgoing.append((one_env, one_bindings))
            if outgoing:
                joined_env = self._merge_envs(*(item[0] for item in outgoing))
                joined = self._merge_bindings(*(item[1] for item in outgoing))
                env.clear()
                env.update(joined_env)
                bindings.clear()
                bindings.update(joined)
                bindings.bounds = joined.bounds
                bindings.ranges = joined.ranges
                bindings.guards = joined.guards
                bindings.lengths = joined.lengths
                self._sync_frame(fn, env, bindings)
            return self._finish_contract_query(
                query_key, node, UNKNOWN if result is None else result)
        if isinstance(node, ast.BoolOp) and node.values:
            # Python reaches the next operand only through the non-decisive
            # outcome of the previous one.  Interpret that path explicitly so
            # an occurrence such as ``use(x)`` in ``x is None or use(x)`` sees
            # the payload contract, not the joined ``None | T`` contract.
            states = [(env, bindings)]
            result = None
            continue_on = isinstance(node.op, ast.And)
            for operand in node.values:
                observed = None
                for one_env, one_bindings in states:
                    got = self._expr(
                        fn, operand, one_env, one_bindings, abstract)
                    observed = (got if observed is None else
                                join_contracts(observed, got))
                if observed is not None:
                    result = (observed if result is None else
                              join_contracts(result, observed))
                states = self._condition_states(
                    operand, states, continue_on)
                if not states:
                    break
            return self._finish_contract_query(
                query_key, node, UNKNOWN if result is None else result)
        owner = self.resolver.owner_of(fn)
        def refine_condition(condition, truth, active):
            states = self._condition_states_with_ranges(
                fn, condition,
                [(dict(env), self._copy_bindings(active))], truth)
            if not states:
                return None
            return self._merge_bindings(
                *(candidate for _candidate_env, candidate in states))

        argument_facts = {}

        def child_query(child, active, contracts, evaluate):
            # A helper call itself may have effects, but its pure arguments
            # still use the same lexical query cache as a standalone expression.
            # Do not re-enter _expr: evaluate retains the current caller's
            # resolver, refinements and once-only argument evaluation.
            key = self._contract_query_key(fn, child, env, active, contracts)
            if isinstance(child, ast.IfExp) or record_update_items(child) is not None:
                got = self._expr(fn, child, env, active, contracts)
            elif key is None:
                got = evaluate()
            elif key in self._contract_query_cache:
                got = replay(self._contract_query_cache[key], active)
            else:
                self._remember_fixed(child, active)
                got = self._finish_contract_query(key, child, evaluate())
            parent = self.resolver._parents.get(id(child))
            # AugAssign uses a synthetic BinOp without lexical parent links.
            operation = (node if isinstance(node, ast.BinOp)
                         and child in (node.left, node.right) else parent)
            if (isinstance(operation, ast.BinOp)
                    and (got.is_none or got.is_option)
                    # None-only calls retain their producer-specific refusal
                    # (for example, a consumed helper without a return value).
                    and not (got.is_none and isinstance(child, ast.Call))):
                # Use this evaluated operand, not the joined occurrence map:
                # another path or helper activation may prove it present.
                # Otherwise its UNKNOWN result reaches the output-schema join
                # first and conceals the actual unsupported operation.
                raise SequencePlanError(
                    operation, "a binary operation consumes an optional value "
                    "without a path proving it is present",
                    "optional-value-not-unwrapped")
            if evaluated_items is not None:
                # An unpacked literal retains each value as it is evaluated;
                # a later helper may overwrite a field read by an earlier item.
                capture_item(child, got, active)
            if isinstance(parent, (ast.Starred, ast.keyword)):
                parent = self.resolver._parents.get(id(parent))
            if (isinstance(parent, ast.Call) and child is not parent.func
                    and (self._call_target(fn, parent) is not None
                         or isinstance(parent.func, ast.Attribute)
                         and parent.func.attr == 'get'
                         and self._static_reference_candidate(
                             fn, parent.func.value, env, active))):
                # Values passed to a helper are fixed when each argument is
                # evaluated, not after subsequent arguments mutate state.
                # Retain only the value facts, not a copy of the whole frame.
                argument_facts[child] = self._argument_facts(fn, child, env, active)
            return got

        got = contract_of_expression(
            node,
            resolve=self._resolve(fn, env),
            select=self._select(fn, env),
            call_evaluated=(
                lambda call, call_bindings, call_contracts,
                       argument_contracts, keyword_contracts: self._call(
                    fn, call, env, call_bindings, call_contracts,
                    scalar_bindings=bindings,
                    evaluated_arguments=argument_contracts,
                    evaluated_keywords=keyword_contracts,
                    argument_facts=argument_facts)
            ),
            static_value=lambda candidate, fixed: self._static_scalar(
                fn, candidate, fixed),
            static_iterable=lambda candidate, fixed: self._static_iterable(
                fn, candidate, owner, fixed),
            bounded_iterable=lambda candidate, fixed:
                self._bounded_iterable_contract(
                    fn, candidate, env, fixed),
            integer_interval=lambda candidate, fixed: (
                (interval.lo, interval.hi)
                if (interval := self._range_of(
                    fn, candidate, fixed)) is not None
                else None),
            is_builtin=lambda name, fixed: (name not in fixed and
                self.resolver.is_builtin(fn, name)),
            known_truth=self._known_truth_fact,
            refine_condition=refine_condition,
            bindings=bindings, contracts=abstract,
            observe=self._remember_expression, budget=self.budget,
            child_query=child_query)
        static_values = (
            self._static_reference_values(fn, node, env, bindings)
            if self._static_reference_candidate(
                fn, node, env, bindings) else None)
        if static_values:
            got = _StaticFact(static_values).contract
        # A list and a tuple may have the same recursive SolverIR layout but
        # Python does not concatenate them.  The generic contract join marks
        # that fact incompatible; retain the operation-specific refusal rather
        # than letting a later plan/retained-lowering cross-check report an
        # internal disagreement.
        if (got is INCOMPATIBLE
                and isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Add)
                and self._context):
            context = self._context[-1]
            left = self._expression_contexts.get(
                (context, id(node.left)), UNKNOWN)
            right = self._expression_contexts.get(
                (context, id(node.right)), UNKNOWN)
            if (left.is_sequence and right.is_sequence
                    and len(left.observed_carriers
                            | right.observed_carriers) > 1):
                raise SequencePlanError(
                    node, "sequence concatenation with different Python "
                          "carriers",
                    "sequence-concat-carrier-mismatch")
        return self._finish_contract_query(query_key, node, got)

    def _remember_fixed(self, node, bindings):
        """Remember scalar facts visible at one reached occurrence."""
        # Every preliminary bounded-state layer is discarded after its state
        # postcondition is joined. Only the final representation pass consumes
        # these occurrence snapshots; computing them in all earlier layers
        # repeats layout-only work without contributing to state closure.
        if self.state_only or not self._context:
            return
        names, _fields, binding_keys, numeric_keys = \
            self._query_dependency_sets(node)
        values = {}
        for name in binding_keys:
            if name in bindings:
                values[name] = bindings[name]
            elif name in self.declaration_values:
                values[name] = self.declaration_values[name]
        active_bounds = getattr(bindings, "bounds", {})
        active_guards = getattr(bindings, "guards", {})
        current = _ScalarBindings(
            values,
            scalar_bounds=_numeric_projection(active_bounds, numeric_keys),
            guard_facts={
                guard: value for guard, value in active_guards.items()
                if (type(guard) is tuple and len(guard) == 2
                    and guard[1] in names)
            },
        )
        key = (self._context[-1], id(node))
        previous = self._expression_fixed.get(key)
        merged = (
            current if previous is None else
            self._merge_bindings(previous, current))
        self._expression_fixed[key] = merged
        self._expression_bounds[key] = dict(merged.bounds)

    def _bounded_loop_heads(self, fn, states, invariants):
        """Apply proved head facts without constraining transient body values."""
        if not invariants:
            return states
        result = []
        for env, bindings in states:
            bindings = self._copy_bindings(bindings)
            for expression, bound in invariants:
                name = (expression.id if isinstance(expression, ast.Name) else
                        _cell_range_key(expression, self.resolver.owner_of(fn),
                                        bindings, budget=self.budget))
                previous = bindings.bounds.get(name, ranges.Bounds())
                narrowed = previous.meet(bound)
                if narrowed is None:
                    break
                bindings.set_bounds(name, narrowed)
            else:
                result.append((env, bindings))
        return result

    def _static_iterable(self, fn, node, owner, bindings):
        """Evaluate one iterable under the enclosing expression snapshot."""
        structural = self._structural_bindings(bindings, node)
        return _static_iterable(
            node, owner, structural,
            is_builtin=lambda name: (
                name not in structural and self.resolver.is_builtin(fn, name)),
            budget=self.budget)

    def _bounded_iterable_contract(self, fn, node, env, bindings):
        """Finite iterable shape proved without materialising its values."""
        sources = self._static_reference_values(fn, node, env, bindings)
        if sources and all(type(value) in (dict, list, tuple, str, bytes)
                           for value in sources):
            element = None
            for value in sources:
                self.budget.spend(node, max(1, len(value)))
                for item in value:
                    child = contract_of_value(item)
                    element = child if element is None else join_contracts(element, child)
            lengths = tuple(map(len, sources))
            # An iteration view does not change a str/bytes source value into
            # a Python tuple, or a mapping's values into its keys.
            return ValueContract.sequence(None, min(lengths), max(lengths), element)
        if not (is_plain_named_call(node, "range", 1, 3)
                and self.resolver.is_builtin(fn, "range")):
            return UNKNOWN
        structural = self._structural_bindings(bindings, node)
        maximum = _proved_bound_of(
            node,
            {name: value.maximum for name, value in env.items()
             if isinstance(value, ValueContract) and value.is_sequence},
            self.resolver.owner_of(fn),
            lambda name: self.resolver.is_builtin(fn, name),
            structural, getattr(bindings, "bounds", {}),
            budget=self.budget)
        if maximum is None:
            return UNKNOWN
        return ValueContract.sequence("range", 0, maximum, SCALAR)

    def _remember_expression(self, node, contract):
        """Record one evaluated occurrence in aggregate and exact contexts."""
        key = id(node)
        previous = self._at_expression.get(key)
        self._at_expression[key] = (
            contract if previous is None else
            join_contracts(previous, contract))
        if self._context:
            contextual_key = (self._context[-1], key)
            previous = self._expression_contexts.get(contextual_key)
            self._expression_contexts[contextual_key] = (
                contract if previous is None else
                join_contracts(previous, contract))
        return contract

    def _remember_expression_range(self, node, interval):
        """Join one scalar range at its exact reached occurrence."""
        if not self._context:
            return
        key = (self._context[-1], id(node))
        if key not in self._expression_ranges:
            self._expression_ranges[key] = interval
            return
        previous = self._expression_ranges[key]
        self._expression_ranges[key] = (
            None if previous is None or interval is None else
            previous.join(interval))

    def _numeric_query_key(self, fn, node, bindings):
        """Exact inputs read by one side-effect-free range projection."""
        (_names, _fields, binding_keys,
         numeric_keys) = self._query_dependency_sets(node)
        syntax = self._query_cache.syntax_of(node)
        numeric_keys = numeric_keys | syntax.selected
        context = self._context[-1] if self._context else None
        observed = tuple(
            (child, _range_key(self._expression_ranges[key]))
            for child in syntax.expressions
            if (key := (context, id(child))) in self._expression_ranges
        )
        return (
            fn, node,
            self._static_binding_key(bindings, binding_keys),
            tuple(sorted(
                (repr(name), _bounds_key(value))
                for name, value in _numeric_projection(bindings.bounds, numeric_keys).items()
            )),
            observed,
        )

    def _range_of(self, fn, node, bindings):
        """Finite integer range of one expression in the current activation.

        Calls are not rediscovered syntactically.  ``_expr`` first visits the
        expression and records each callee's path-sensitive return range; this
        projection composes those recorded facts with ordinary scalar ranges.
        """
        context = self._context[-1] if self._context else None

        def observed(candidate):
            if context is None:
                return None
            return self._expression_ranges.get((context, id(candidate)))

        structural = self._structural_bindings(bindings, node)
        key = self._numeric_query_key(fn, node, structural)
        if key not in self._query_cache.ranges:
            self._query_cache.ranges[key] = _scalar_range(
                node, self.resolver.owner_of(fn), structural,
                budget=self.budget, observed=observed,
                static_value_of=lambda child, active:
                self._static_scalar_from_bindings(fn, child, active))
        return self._query_cache.ranges[key]

    def _bounds_of(self, fn, node, bindings):
        """Integer bounds used only for structural termination proofs."""
        context = self._context[-1] if self._context else None

        def observed(candidate):
            if context is None:
                return None
            return self._expression_ranges.get((context, id(candidate)))

        structural = self._structural_bindings(bindings, node)
        key = self._numeric_query_key(fn, node, structural)
        if key not in self._query_cache.bounds:
            self._query_cache.bounds[key] = _scalar_bounds(
                node, self.resolver.owner_of(fn), structural,
                budget=self.budget, observed=observed,
                static_value_of=lambda child, active:
                self._static_scalar_from_bindings(fn, child, active))
        return self._query_cache.bounds[key]

    def _sequence_element_range(self, fn, node, bindings):
        """Finite envelope containing every integer leaf of a sequence."""
        if isinstance(node, ast.Name):
            return bindings.ranges.get(_element_range_key(node.id))
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            return bindings.ranges.get(f"self.{node.attr}")
        if isinstance(node, ast.Subscript):
            key = _subscript_range_key(node)
            return None if key is None else bindings.ranges.get(key)
        if isinstance(node, (ast.List, ast.Tuple)):
            if not node.elts:
                return None
            parts = []
            for item in node.elts:
                part = self._range_of(fn, item, bindings)
                if part is None:
                    part = self._sequence_element_range(fn, item, bindings)
                parts.append(part)
        elif isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            # ``_expr`` has already interpreted every admitted comprehension
            # path and joined helper-call ranges at their exact occurrences.
            part = self._range_of(fn, node.elt, bindings)
            if part is None:
                part = self._sequence_element_range(
                    fn, node.elt, bindings)
            parts = [part]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            parts = [self._sequence_element_range(fn, node.left, bindings),
                     self._sequence_element_range(fn, node.right, bindings)]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            parts = [self._sequence_element_range(fn, node.left, bindings),
                     self._sequence_element_range(fn, node.right, bindings)]
            parts = [part for part in parts if part is not None]
        elif isinstance(node, ast.IfExp):
            parts = [self._sequence_element_range(fn, node.body, bindings),
                     self._sequence_element_range(
                         fn, node.orelse, bindings)]
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"list", "tuple", "reversed"}
                and self.resolver.is_builtin(fn, node.func.id)
                and has_plain_positional_arity(node, 1)):
            parts = [self._sequence_element_range(
                fn, node.args[0], bindings)]
        elif (isinstance(node, ast.Call)
                and is_plain_named_call(node, "zip", 0, len(node.args))
                and self.resolver.is_builtin(fn, "zip")):
            parts = [self._loop_element_range(fn, argument, bindings)
                     for argument in node.args]
        elif (is_plain_named_call(node, "enumerate", 1, 2)
                and self.resolver.is_builtin(fn, "enumerate")):
            sequence = self._observed_contract(node.args[0])
            maximum = (sequence.maximum if sequence.is_sequence else
                       _proved_bound_of(
                           node.args[0], {}, self.resolver.owner_of(fn),
                           lambda name: self.resolver.is_builtin(fn, name),
                           bindings, getattr(bindings, "bounds", {}),
                           budget=self.budget))
            start = (ranges.Range(0, 0) if len(node.args) == 1 else
                     self._range_of(fn, node.args[1], bindings))
            if maximum is None or maximum <= 0 or start is None:
                return None
            # This abstraction is the hull of *all* integer leaves, including
            # the index. Destructuring may retain it for each scalar leaf;
            # using the element range alone would incorrectly constrain i.
            parts = [self._loop_element_range(fn, node.args[0], bindings),
                     ranges.Range(start.lo, start.hi + maximum - 1)]
        else:
            return None
        if not parts or any(part is None for part in parts):
            return None
        result = parts[0]
        for part in parts[1:]:
            result = result.join(part)
        return result

    def _remember_binding(self, fn, name, contract, occurrence=None):
        """Join the contracts actually bound to one lexical local."""
        key = (id(fn), str(name))
        previous = self._at_binding.get(key)
        self._at_binding[key] = (contract if previous is None else
                                 join_contracts(previous, contract))
        if self._context:
            contextual_key = (self._context[-1], str(name))
            previous = self._binding_contexts.get(contextual_key)
            self._binding_contexts[contextual_key] = (
                contract if previous is None else
                join_contracts(previous, contract))
            if occurrence is not None:
                occurrence_key = (self._context[-1], id(occurrence))
                previous = self._binding_occurrence_contexts.get(
                    occurrence_key)
                self._binding_occurrence_contexts[occurrence_key] = (
                    contract if previous is None else
                    join_contracts(previous, contract))

    def _remember_record_summary(self, name, fact, node):
        if not self._context:
            return
        summary = fact.summary(node)
        key = (self._context[-1], str(name))
        previous = self._record_summaries.get(key)
        if previous is None:
            self._record_summaries[key] = summary
            return
        merged = join_contracts(previous.contract, summary.contract)
        if merged is INCOMPATIBLE or not merged.is_record:
            raise SequencePlanError(
                node, f"the returned record {name} has different schemas on "
                      "reachable paths",
                "helper-return-shape-disagrees")
        writes = tuple(dict.fromkeys((*previous.writes, *summary.writes)))
        left = dict(previous.field_ranges)
        right = dict(summary.field_ranges)
        field_ranges = {}
        for field in merged.field_contracts:
            first = left.get(field)
            second = right.get(field)
            field_ranges[field] = (
                None if first is None or second is None else
                first.join(second))
        self._record_summaries[key] = RecordBuilderSummary(
            merged, writes, tuple(sorted(field_ranges.items())))

    def _remember_live_records(self, env, node):
        # A last write still executes even when only an earlier copy is read.
        # Preserve its source plan at normal scope exits, not just at reads.
        for name, fact in env.items():
            if isinstance(fact, _RecordFact) and fact.contract.is_record:
                self._remember_record_summary(name, fact, node)

    @staticmethod
    def _is_record_batch_value(contract):
        """Whether a source value needs the parallel record-batch carrier."""
        return record_batch_payload(contract) is not None

    def _batch_for_expression(self, node, env):
        """Batch-domain fact already established for one evaluated value."""
        if isinstance(node, ast.Name):
            return self._batch_of_fact(env.get(node.id, UNKNOWN))
        if isinstance(node, ast.Call) and self._context:
            return self._call_batch_contracts.get(
                (self._context[-1], id(node)))
        return None

    def _plan_batch_expression(self, fn, value, env, bindings):
        """Plan one evaluated batch expression under this exact path."""
        def value_contract(candidate):
            if isinstance(candidate, ast.Name) and isinstance(
                    env.get(candidate.id), (_RecordFact, _BatchFact)):
                return env[candidate.id].contract
            return self._observed_contract(candidate)

        def batch_contract(candidate):
            return self._batch_for_expression(candidate, env)

        try:
            plan = plan_batch_expression(
                value,
                value_contract=value_contract,
                batch_contract=batch_contract,
                is_builtin=lambda name: name not in bindings
                    and self.resolver.is_builtin(fn, name),
                truth_of=lambda test: self._static_truth(
                    fn, test, self._structural_bindings(bindings)),
                budget=self.budget,
            )
        except BatchContractError as exc:
            raise SequencePlanError(exc.node, exc.what, exc.code) from None
        if plan is not None and value is not None:
            # A record local embedded in a returned list is just as much a
            # boundary value as a record returned directly.  Freeze its exact
            # activation-specific layout now; lowering must not rediscover it
            # by scanning assignments later.
            for candidate in ast.walk(value):
                if not (isinstance(candidate, ast.Name)
                        and isinstance(candidate.ctx, ast.Load)):
                    continue
                fact = env.get(candidate.id)
                if isinstance(fact, _RecordFact):
                    self._remember_record_summary(
                        candidate.id, fact, candidate)
        return plan

    def _plan_return_batch(self, fn, node, env, bindings):
        """Plan one reached return from the facts of this exact path."""
        internal = self._observed_contract(node.value)
        empty_list = bool(
            internal.is_sequence and internal.carrier == "list"
            and internal.maximum == 0)
        if (fn is not self.model.step and not internal.is_none
                and not internal.is_record and not empty_list
                and not self._is_record_batch_value(internal)):
            # Internal scalar and ordinary sequence values already have direct
            # SolverIR representations.  Retain record plans even
            # though a pure-record helper does not consume them: once another
            # return arm contributes None, the joined helper result is a
            # zero-or-one transaction batch and needs the exact record arm.
            # Lists of records need the same parallel-array plan later used at
            # the transaction boundary.  A proved empty list is schema-neutral,
            # so retain its plan as well: another return path may establish the
            # transaction schema while source code still observes that this
            # path returned a list rather than None.
            return None
        return self._plan_batch_expression(
            fn, node.value, env, bindings)

    def _remember_batch_plan(self, node, plan):
        """Freeze one activation-specific internal batch expression plan."""
        if plan is None or not self._context:
            return
        key = (self._context[-1], id(node))
        previous = self._batch_plans.get(key)
        if previous is None:
            self._batch_plans[key] = plan
        else:
            try:
                self._batch_plans[key] = join_batch_plans(
                    previous, plan, node)
            except BatchContractError as exc:
                raise SequencePlanError(
                    exc.node or node, exc.what, exc.code) from None

    def _remember_batch_binding(self, target, plan):
        """Bind one evaluated batch plan to one exact assignment target.

        A ``None`` assignment is retained even before another path proves the
        local to be a transaction batch.  It is projected into storage only
        when the joined bindings of that local carry records.
        """
        if not self._context:
            return
        if not isinstance(target, ast.Name):
            # A compound target does not bind a batch local.  In particular,
            # assigning ``None | Record`` into a fixed record field is handled
            # by the record product plan; remembering the same expression as a
            # standalone transaction batch here would create a second physical
            # owner for one source value.  Ordinary compound-target validity is
            # checked by the target and record-store rules themselves.
            return
        name_key = (self._context[-1], str(target.id))
        if plan is None:
            self._non_batch_bindings.add(name_key)
            return
        previous_contract = self._binding_batch_contracts.get(name_key)
        if previous_contract is None:
            self._binding_batch_contracts[name_key] = plan.contract
        else:
            try:
                self._binding_batch_contracts[name_key] = \
                    join_batch_alternatives(
                        previous_contract, plan.contract, target)
            except BatchContractError:
                self._non_batch_bindings.add(name_key)
        key = (self._context[-1], id(target))
        previous = self._binding_batch_plans.get(key)
        if previous is None:
            self._binding_batch_plans[key] = plan
        else:
            try:
                self._binding_batch_plans[key] = join_batch_plans(
                    previous, plan, target)
            except BatchContractError as exc:
                raise SequencePlanError(
                    exc.node or target, exc.what, exc.code) from None

    def _remember_return(self, fact):
        if not self._context:
            return
        context = self._context[-1]
        self._return_facts.setdefault(context, []).append(fact)
        if fact.record is not None and fact.record_name is not None:
            self._remember_record_summary(
                fact.record_name, fact.record, fact.node)
        if fact.batch is None:
            return
        key = (context, id(fact.node))
        previous = self._return_plans.get(key)
        if previous is None:
            self._return_plans[key] = fact.batch
            return
        try:
            self._return_plans[key] = join_batch_plans(
                previous, fact.batch, fact.node)
        except BatchContractError as exc:
            raise SequencePlanError(
                exc.node or fact.node, exc.what, exc.code) from None

    def _remember_record_rebindings(self, target, env):
        """Freeze a record before a nonordinary binder overwrites its name."""
        if isinstance(target, ast.Name):
            fact = env.get(target.id)
            if isinstance(fact, _RecordFact) and fact.contract.is_record:
                self._remember_record_summary(target.id, fact, target)
            return
        if isinstance(target, ast.Starred):
            self._remember_record_rebindings(target.value, env)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._remember_record_rebindings(item, env)

    def _target(self, fn, target, contract, env, bindings, fixed=None,
                interval=None, element_interval=None, *, bounds_interval=None,
                positional_facts=None):
        if isinstance(target, ast.Subscript):
            mapping = self._mapping_binding(target.value, env)
            if mapping is not None:
                key, current = mapping
                element = (contract if current.element is None else
                           join_contracts(current.element, contract))
                env[key] = ValueContract.mapping(
                    max(1, current.minimum), current.maximum + 1, element)
                # A later deletion/clear may hide this live peak from the
                # boundary state. Keep it in the existing storage envelope.
                state = _state_name(key)
                self._remember_binding(
                    fn, key if state is None else f"self.{state}",
                    env[key], occurrence=target)
                bindings.forget_length(key)
                return
        if isinstance(target, ast.Name):
            bindings.forget_cells(_element_range_key(target.id))
            bindings.forget_length(target.id)
            env[target.id] = contract
            self._remember_binding(
                fn, target.id, contract, occurrence=target)
            if fixed is None:
                bindings.pop(target.id, None)
            else:
                bindings[target.id] = fixed
            if type(fixed) in (int, bool):
                bindings.set_numeric(target.id, ranges.Range(fixed, fixed))
            elif bounds_interval is not None:
                bindings.set_bounds(target.id, bounds_interval)
            else:
                bindings.set_numeric(target.id, interval)
            element_key = _element_range_key(target.id)
            if contract.is_sequence and element_interval is not None:
                bindings.set_numeric(element_key, element_interval)
            else:
                bindings.forget_numeric(element_key)
            return
        if (isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"):
            bindings.forget_length(_state_key(target.attr))
            env[_state_key(target.attr)] = contract
            name = f"self.{target.attr}"
            bindings.forget_cells(name)
            self._remember_binding(fn, name, contract, occurrence=target)
            if contract.is_sequence:
                bindings.set_numeric(name, element_interval)
            elif type(fixed) in (int, bool):
                bindings.set_numeric(name, ranges.Range(fixed, fixed))
            elif bounds_interval is not None:
                bindings.set_bounds(name, bounds_interval)
            else:
                bindings.set_numeric(name, interval)
            return
        indexed = _subscript_target_path(target)
        sliced = _slice_target_path(target)
        if indexed is not None or sliced is not None:
            root, depth = indexed if indexed is not None else sliced
            if isinstance(root, ast.Name):
                key = root.id
                if sliced is not None:
                    bindings.forget_length(key)
                range_key = _element_range_key(key)
                current = env.get(key, UNKNOWN)
                # Transaction batches and live record builders have their own
                # fieldwise plans.  An ordinary recursive contract is the
                # only representation this homogeneous element update owns.
                if not isinstance(current, ValueContract):
                    return
                updated = (
                    _sequence_element_store(current, depth, contract)
                    if indexed is not None else
                    _sequence_slice_store(current, depth, contract)
                )
                # The operation-specific lowering reports an incompatible
                # carrier or shape at the store itself.  Do not turn that
                # precise refusal into a later binding-plan disagreement.
                if updated is INCOMPATIBLE:
                    return
                self._remember_binding(
                    fn, key, updated, occurrence=target)
                env[key] = updated
                bindings.pop(key, None)
                bindings.forget_numeric(key)
                # ``range_key`` bounds every integer leaf of this local, so
                # the stored leaves must join it. Keeping the old envelope
                # let a [0]*n initializer survive later stores of a counter.
                stored = (interval if contract == SCALAR else
                          element_interval if getattr(contract, "is_sequence", False)
                          else None)
                previous = bindings.ranges.get(range_key)
                if previous is not None and stored is not None:
                    bindings.set_numeric(range_key, previous.join(stored))
                else:
                    bindings.forget_numeric(range_key)
            else:
                name = root.attr
                range_key = f"self.{name}"
                key = _state_key(name)
                if sliced is not None:
                    bindings.forget_length(key)
                current = env.get(
                    key, self.declarations.get(name, UNKNOWN))
                updated = (
                    _sequence_element_store(current, depth, contract)
                    if indexed is not None else
                    _sequence_slice_store(current, depth, contract)
                )
                if updated is INCOMPATIBLE:
                    return
                env[key] = updated
                bindings.pop(f"self.{name}", None)
            stored = (element_interval if contract.is_sequence else interval)
            cell = (_cell_range_key(target, self.resolver.owner_of(fn), bindings,
                                    budget=self.budget)
                    if indexed is not None and depth == 1 and contract == SCALAR else None)
            if cell is None:
                bindings.forget_cells(range_key)
            else:
                bindings.set_numeric(cell, stored)
            previous = bindings.ranges.get(range_key)
            bindings.set_numeric(
                range_key,
                None if previous is None or stored is None
                else previous.join(stored))
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            # Occurrence keys retain positional values even when two targets
            # write the same name. Attributes and indexed locations are real
            # assignments too, not lexical names to skip during state closure.
            def leaf_key(item):
                return (item if isinstance(
                    item, (ast.Name, ast.Attribute, ast.Subscript)) else None)

            planned = contracts_for_target(target, contract, leaf_key=leaf_key)
            concrete = (None if fixed is None else
                        values_for_target(target, fixed, leaf_key=leaf_key))

            def bind(item):
                if isinstance(item, ast.Starred):
                    bind(item.value)
                    return
                if leaf_key(item) is not None:
                    value = (UNKNOWN if planned is None else
                             planned.get(item, UNKNOWN))
                    known = (None if concrete is None else
                             concrete.get(item))
                    scalar_bounds = None
                    scalar_range = element_interval if value == SCALAR else None
                    leaf_range = element_interval if value.is_sequence else None
                    if positional_facts is not None:
                        value, known, scalar_bounds, leaf_range = positional_facts[item]
                        scalar_range = (None if scalar_bounds is None else
                                        scalar_bounds.finite())
                    self._target_effects(fn, item, env, bindings)
                    self._target(
                        fn, item, value, env, bindings, known,
                        interval=scalar_range, element_interval=leaf_range,
                        bounds_interval=scalar_bounds)
                    return
                if isinstance(item, (ast.Tuple, ast.List)):
                    for child in item.elts:
                        bind(child)

            for item in target.elts:
                bind(item)

    def _literal_destructuring_facts(self, target, value, evaluated_items):
        """Evaluation-time facts for one literal unpacking boundary.

        SolverIR arrays are homogeneous, but Python tuple/list literals may
        contain values with different recursive types.  A destructuring
        assignment eliminates that carrier immediately, so retain the
        contract and value facts of each evaluated child rather than first
        joining the literal or re-reading fields after a later helper's writes.
        """
        out = {}

        def visit(pattern, expression):
            if isinstance(pattern, (ast.Name, ast.Attribute, ast.Subscript)):
                out[pattern] = (
                    self._observed_contract(expression),
                    *evaluated_items.get(expression, (None, None, None)))
                return True
            if (not isinstance(pattern, (ast.Tuple, ast.List))
                    or not isinstance(expression, (ast.Tuple, ast.List))
                    or any(isinstance(item, ast.Starred)
                           for item in pattern.elts)
                    or any(isinstance(item, ast.Starred)
                           for item in expression.elts)
                    or len(pattern.elts) != len(expression.elts)):
                return False
            return all(visit(left, right) for left, right in zip(
                pattern.elts, expression.elts))

        return out if visit(target, value) else None

    def _append(self, sequence, item, node):
        if not sequence.is_sequence or sequence.carrier not in {"list", "bytearray"}:
            return UNKNOWN
        element = item if sequence.element is None \
            else join_contracts(sequence.element, item)
        if element is INCOMPATIBLE:
            return INCOMPATIBLE
        return ValueContract.sequence(
            sequence.carrier,
            finite_cardinality_add(sequence.minimum, 1),
            finite_cardinality_add(sequence.maximum, 1),
            element)

    @staticmethod
    def _extend(sequence, items, node):
        if (not sequence.is_sequence or sequence.carrier != "list"
                or not items.is_sequence
                or items.carrier not in {"list", "tuple", "range"}):
            raise SequencePlanError(
                node, "extend requires a list and a represented finite sequence",
                "extend-iterable-unsupported")
        element = (sequence.element if items.maximum == 0 else
                   items.element if sequence.maximum == 0 else
                   join_contracts(sequence.element, items.element))
        if element is INCOMPATIBLE:
            return INCOMPATIBLE
        return ValueContract.sequence(
            "list", finite_cardinality_add(sequence.minimum, items.minimum),
            finite_cardinality_add(sequence.maximum, items.maximum), element)

    @staticmethod
    def _pop(sequence):
        """Normal-result contracts of one list ``pop``.

        A possibly empty list has a failing execution, but every normal
        execution starts with at least one element.  The normal successor
        therefore loses exactly one element; the executable lowering records
        the corresponding ``IndexError`` branch.
        """
        if (not sequence.is_sequence or sequence.carrier != "list"
                or sequence.element is None):
            return UNKNOWN, UNKNOWN
        successor = ValueContract.sequence(
            "list", max(0, sequence.minimum - 1),
            max(0, sequence.maximum - 1), sequence.element)
        return successor, sequence.element

    def _remove_item(self, fn, node, target, env, bindings):
        """One normal indexed removal, shared by pop and del."""
        kind, name = target
        key = name if kind == "local" else _state_key(name)
        current = env.get(
            key, self.declarations.get(name, UNKNOWN)
            if kind == "state" else UNKNOWN)
        if isinstance(current, ValueContract) and current.is_mapping:
            successor = ValueContract.mapping(
                max(0, current.minimum - 1), max(0, current.maximum - 1),
                current.element)
            result = current.element or SCALAR
        else:
            successor, result = self._pop(current)
        bindings.forget_length(key)
        env[key] = successor
        binding_name = name if kind == "local" else f"self.{name}"
        bindings.pop(binding_name, None)
        bindings.forget_numeric(binding_name)
        bindings.forget_numeric(_element_range_key(binding_name))
        bindings.forget_cells(binding_name if kind == "state" else _element_range_key(name))
        self._remember_binding(fn, binding_name, successor, occurrence=node)
        return result

    def _call_target(self, fn, node):
        return resolve_helper(self.resolver, fn, node)

    def _loop_element_contract(self, fn, node, observed):
        """Contract of one item yielded by an admitted finite iterable."""
        return _iterable_element_contract(
            node, observed,
            lambda spelling: self.resolver.is_builtin(fn, spelling))

    def _loop_element_range(self, fn, node, bindings):
        """Finite envelope of values produced by one admitted iterable."""
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "range"
                and self.resolver.is_builtin(fn, "range")
                and has_plain_positional_arity(node, 1, 3)):
            arguments = [self._range_of(fn, argument, bindings)
                         for argument in node.args]
            if any(argument is None for argument in arguments):
                return None
            if len(arguments) == 1:
                stop = arguments[0]
                return (None if stop.hi <= 0 else
                        ranges.Range(0, stop.hi - 1))
            start, stop = arguments[:2]
            if len(arguments) == 2:
                return (None if stop.hi <= start.lo else
                        ranges.Range(start.lo, stop.hi - 1))
            # For a variable signed stride, every normally yielded value lies
            # between an endpoint of ``start`` and an endpoint of ``stop``.
            # Executable lowering separately handles the zero-stride failure.
            return ranges.Range(
                min(start.lo, stop.lo), max(start.hi, stop.hi))
        return self._sequence_element_range(fn, node, bindings)

    def _range_progression(self, fn, node, bindings):
        """Known start/stride of an entered range, without fixing its length."""
        if (not is_plain_named_call(node, "range", 1, 3)
                or "range" in bindings or not self.resolver.is_builtin(fn, "range")
                or not all(self._query_cache.is_pure(arg) for arg in node.args)):
            return None
        start = (ranges.Range(0, 0) if len(node.args) == 1 else
                 self._range_of(fn, node.args[0], bindings))
        stride = (ranges.Range(1, 1) if len(node.args) < 3 else
                  self._range_of(fn, node.args[2], bindings))
        if (start is None or stride is None or start.lo != start.hi
                or stride.lo != stride.hi or stride.lo == 0):
            return None
        return start.lo, stride.lo

    def _builtin_call_range(self, fn, node, bindings):
        """Finite range of a small, total integer builtin call."""
        if not isinstance(node.func, ast.Name) \
                or not self.resolver.is_builtin(fn, node.func.id) \
                or node.keywords \
                or any(isinstance(argument, ast.Starred)
                       for argument in node.args):
            return None
        name = node.func.id
        arguments = [self._range_of(fn, argument, bindings)
                     for argument in node.args]
        if name == "abs" and len(arguments) == 1 \
                and arguments[0] is not None:
            value = arguments[0]
            maximum = max(abs(value.lo), abs(value.hi))
            minimum = 0 if value.lo <= 0 <= value.hi else \
                min(abs(value.lo), abs(value.hi))
            return ranges.Range(minimum, maximum)
        if name in {"int", "round"} and len(arguments) == 1:
            return arguments[0]
        if name == "bool" and len(arguments) == 1:
            return ranges.Range(0, 1)
        if name in {"min", "max"} and arguments \
                and all(value is not None for value in arguments):
            lows = [value.lo for value in arguments]
            highs = [value.hi for value in arguments]
            return (ranges.Range(min(lows), min(highs)) if name == "min"
                    else ranges.Range(max(lows), max(highs)))
        if name == "len" and len(node.args) == 1:
            context = self._context[-1] if self._context else None
            contract = self.expression_contract(node.args[0], context)
            if contract.is_sequence or contract.is_mapping:
                return ranges.Range(contract.minimum, contract.maximum)
        return None

    def _builtin_sequence_contract(self, fn, node, bindings):
        """Finite cardinality of a ranged, but not necessarily fixed, range.

        ``static_iterable`` owns ranges whose values can be materialised.  A
        comprehension needs only the cardinality when an endpoint is dynamic;
        retain that weaker fact instead of requiring every yielded integer to
        be known during planning.
        """
        if not (isinstance(node.func, ast.Name)
                and node.func.id == "range"
                and self.resolver.is_builtin(fn, "range")
                and has_plain_positional_arity(node, 1, 3)):
            return None
        arguments = [self._range_of(fn, argument, bindings)
                     for argument in node.args]
        if any(argument is None for argument in arguments):
            return None

        def ceiling(distance, stride):
            distance = max(0, distance)
            return (distance + stride - 1) // stride

        if len(arguments) == 1:
            stop = arguments[0]
            minimum, maximum = max(0, stop.lo), max(0, stop.hi)
        else:
            start, stop = arguments[:2]
            if len(arguments) == 2:
                minimum = max(0, stop.lo - start.hi)
                maximum = max(0, stop.hi - start.lo)
            else:
                step = arguments[2]
                positive = step.hi >= 1
                negative = step.lo <= -1
                if not positive and not negative:
                    # Every execution fails at ``range(..., 0)``.  The
                    # executable lowering owns that failure; there is no
                    # normally produced iterable to summarize here.
                    return None
                minima, maxima = [], []
                if positive:
                    smallest = max(1, step.lo)
                    largest = step.hi
                    minima.append(ceiling(
                        stop.lo - start.hi, largest))
                    maxima.append(ceiling(
                        stop.hi - start.lo, smallest))
                if negative:
                    smallest = abs(min(-1, step.hi))
                    largest = abs(step.lo)
                    minima.append(ceiling(
                        start.lo - stop.hi, largest))
                    maxima.append(ceiling(
                        start.hi - stop.lo, smallest))
                minimum = 0 if positive and negative else min(minima)
                maximum = max(maxima)
        finite_cardinality_add(maximum)
        return ValueContract.sequence(
            "range", minimum, maximum, SCALAR)

    def _argument_facts(self, fn, source, env, bindings):
        """Fixed value and numeric bounds at one argument evaluation."""
        reference_values = (
            self._static_reference_values(fn, source, env, bindings)
            if self._static_reference_candidate(
                fn, source, env, bindings) else None)
        value = (reference_values[0]
                 if reference_values is not None
                 and len(reference_values) == 1 else
                 self._static_scalar(fn, source, bindings))
        return (value, self._bounds_of(fn, source, bindings),
                self._sequence_element_range(fn, source, bindings))

    def _call(self, fn, node, env, bindings, abstract=None,
              scalar_bindings=None, evaluated_arguments=None,
              evaluated_keywords=None, argument_facts=None):
        if isinstance(node.func, ast.Attribute):
            mapping = self._mapping_binding(node.func.value, env)
            if mapping is not None:
                key, current = mapping
                name = node.func.attr
                if name == "clear" and has_plain_positional_arity(node, 0):
                    env[key] = ValueContract.mapping(0, 0, current.element)
                    bindings.forget_length(key)
                    return NONE
                if name in {"get", "pop"} and has_plain_positional_arity(node, 1, 2):
                    result = current.element or SCALAR
                    default = (NONE if len(node.args) == 1 else
                               evaluated_arguments[1] if evaluated_arguments is not None else
                               self._expr(fn, node.args[1], env, bindings, abstract))
                    if name == "get" or len(node.args) == 2:
                        result = default if current.maximum == 0 else join_contracts(result, default)
                    if name == "pop":
                        env[key] = ValueContract.mapping(
                            max(0, current.minimum - 1), current.maximum,
                            current.element)
                        bindings.forget_length(key)
                    return result
        numeric = scalar_bindings if scalar_bindings is not None else bindings
        range_bindings = self._structural_bindings(_ScalarBindings(
            bindings,
            scalar_bounds=getattr(
                numeric, "bounds", getattr(numeric, "ranges", {})),
        ))
        pop_target = sequence_method_target(node, "pop")
        if pop_target is not None:
            if not is_plain_pop_call(node):
                return UNKNOWN
            return self._remove_item(fn, node, pop_target, env, bindings)
        if isinstance(node.func, ast.Name):
            kind, _target = self.resolver.resolve_call(fn, node.func.id)
            if kind == "intrinsic":
                # Registered source intrinsics are scalar-total operations.
                # Argument contracts were evaluated by the enclosing Call;
                # call shape and refined domains are enforced by value
                # lowering.
                return SCALAR
            builtin_range = self._builtin_call_range(
                fn, node, range_bindings)
            if builtin_range is not None:
                self._remember_expression_range(node, builtin_range)
            builtin_sequence = self._builtin_sequence_contract(
                fn, node, range_bindings)
            if builtin_sequence is not None:
                return builtin_sequence
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr in {"bit_count", "bit_length"}
                and not node.args and not node.keywords):
            receiver = self._expr(
                fn, node.func.value, env, bindings, abstract)
            # These are the two integer methods the executable lowerer maps to
            # total SolverIR primitives.  Their result representation is
            # scalar whenever the receiver's is; keeping this fact here makes
            # the source contract and value lowering describe the same call.
            interval = self._range_of(fn, node.func.value, range_bindings)
            if interval is not None:
                maximum = max(abs(interval.lo), abs(interval.hi)).bit_length()
                self._remember_expression_range(
                    node, ranges.Range(0, maximum))
            return SCALAR if receiver.kind == "scalar" else UNKNOWN
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and has_plain_positional_arity(node, 1, 2)):
            # The receiver's proved finite values, not its spelling or scope,
            # own this call. Arithmetic recursively asks here too; only
            # recognizing module names lost local/literal table operands.
            tables = self._static_reference_values(fn, node.func.value, env, bindings)
            if tables and all(type(table) is dict for table in tables):
                default = (NONE if len(node.args) == 1 else
                           evaluated_arguments[1] if evaluated_arguments is not None else
                           self._expr(fn, node.args[1], env, bindings, abstract))
                key, bounds, _ = (
                    argument_facts[node.args[0]]
                    if argument_facts is not None and node.args[0] in argument_facts
                    else self._argument_facts(fn, node.args[0], env, range_bindings))
                interval = None if bounds is None else bounds.finite()
                result = None
                for table in tables:
                    if type(key) in (bool, int, str):
                        selected = (contract_of_value(table[key]) if key in table else default)
                    elif interval is not None:
                        # Numeric keys cover an exact integer interval only
                        # when every integer slot is present. No input is
                        # sampled and missing slots still include the default.
                        possible = {k: v for k, v in table.items()
                                    if type(k) in (bool, int) and interval.lo <= k <= interval.hi}
                        selected = contract_of_collection_elements(possible) if possible else default
                        if len(possible) < interval.hi - interval.lo + 1:
                            selected = join_contracts(selected, default)
                    else:
                        selected = (join_contracts(contract_of_collection_elements(table), default)
                                    if table else default)
                    result = selected if result is None else join_contracts(result, selected)
                return result
        target = self._call_target(fn, node)
        if target is None:
            return UNKNOWN
        is_method = id(target) in self.resolver._method_ids

        contracts = []
        fixed = []
        argument_ranges = []
        argument_sources = []
        keywords = []
        supported = True
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        for index, argument in enumerate(arguments):
            source = (argument.value if isinstance(argument, ast.Starred)
                      else argument)
            evaluated = (evaluated_arguments if index < len(node.args)
                         else None if evaluated_keywords is None else
                         tuple(value for _name, value in evaluated_keywords))
            offset = index if index < len(node.args) else index - len(node.args)
            contract = (evaluated[offset]
                        if evaluated is not None else
                        self._expr(fn, source, env, bindings, abstract))
            # Function summaries carry structural Bounds, not only finite
            # representation Ranges.  One-sided facts established by the
            # caller may be exactly the fact that proves a callee's loop
            # terminates; the callee still exposes only finite projections to
            # value encoding.
            value, interval, element_range = (
                argument_facts[source]
                if argument_facts is not None and source in argument_facts
                else self._argument_facts(fn, source, env, range_bindings))
            if not isinstance(argument, ast.Starred):
                if contract.is_sequence:
                    interval = element_range
                if index < len(node.args):
                    contracts.append(contract)
                    fixed.append(value)
                    argument_ranges.append(interval)
                    argument_sources.append(source)
                else:
                    keywords.append((node.keywords[offset].arg,
                                     (contract, value, interval, source)))
                continue

            elements = fixed_positional_contracts(contract)
            if elements is None:
                supported = False
                continue
            size = len(elements)
            if type(value) in (list, tuple) and len(value) == size:
                element_values = tuple(value)
            else:
                element_values = (None,) * size
            contracts.extend(elements)
            fixed.extend(element_values)
            argument_ranges.extend((element_range,) * size)
            argument_sources.extend((None,) * size)

        if not supported:
            return UNKNOWN

        def default(name, expression):
            source = self.resolver.helper_default(target, name, expression)
            if source is None:
                raise CallBindingError(
                    f"default for {target.name}.{name} has no frozen scalar representation",
                    "call-default-unrepresented")
            value = source.value
            interval = (ranges.Bounds(int(value), int(value))
                        if isinstance(value, int) else None)
            return contract_of_value(value), value, interval, None

        try:
            _params, bound = bind_helper_arguments(
                target, tuple(zip(contracts, fixed, argument_ranges, argument_sources)), keywords,
                receiver=is_method, default=default)
        except CallBindingError as exc:
            raise SequencePlanError(node, str(exc), exc.code) from None
        contracts = tuple(item[0] for item in bound)
        fixed = tuple(item[1] for item in bound)
        argument_ranges = tuple(item[2] for item in bound)
        argument_sources = tuple(item[3] for item in bound)
        caller_context = self._context[-1] if self._context else None
        captures, fingerprint = self._captures(target)
        state_contracts = _state_contracts_of(env)
        state_ranges = _state_ranges_of(bindings)
        callee_context = self.context_key(
            target, contracts, fixed, fingerprint, argument_ranges,
            state_contracts, state_ranges)
        got = self.summarize(
            target, contracts, fixed, captures, fingerprint,
            argument_ranges, state_contracts, state_ranges)
        post_state = self._final_state.get(callee_context, {})
        post_ranges = self._final_state_ranges.get(callee_context, {})
        if not self.resolver.call_has_no_external_effect(fn, node):
            # A helper can mutate state or captured containers. Its summary
            # carries post-state contracts, not identities of saved lengths.
            bindings.lengths.clear()
        post_arguments = self._final_array_arguments.get(callee_context, {})
        for parameter, source in zip(_params, argument_sources):
            if parameter not in post_arguments or not isinstance(source, ast.Name):
                continue
            contract, interval = post_arguments[parameter]
            # The helper updates the original object, not a copied formal.
            # Retire the caller's old constant and carry every normal exit's
            # postcondition (including early returns) to the next occurrence.
            env[source.id] = contract
            bindings.pop(source.id, None)
            bindings.forget_numeric(source.id)
            bindings.forget_length(source.id)
            bindings.forget_cells(_element_range_key(source.id))
            bindings.set_numeric(_element_range_key(source.id), interval)
        for name, contract in post_state.items():
            env[_state_key(name)] = contract
            bindings.pop(f"self.{name}", None)
            # A helper call is a state transition, not a range barrier.  Its
            # summary carries finite post-state ranges beside value contracts;
            # forget only fields whose helper result is genuinely unbounded.
            bindings.set_numeric(
                f"self.{name}", post_ranges.get(name))
            bindings.forget_cells(f"self.{name}")
        for name, interval in post_ranges.items():
            if "[" in name:
                bindings.set_numeric(f"self.{name}", interval)
        batch_result = self._context_batch_contracts.get(callee_context)
        if batch_result is not None and caller_context is not None:
            self._call_batch_contracts[(caller_context, id(node))] = \
                batch_result
        self._remember_expression_range(
            node, self._range_cache.get(callee_context))
        context = (caller_context, id(node), contracts,
                   tuple(_fixed_key(value) for value in fixed),
                   _fingerprint_key(fingerprint),
                   tuple(_range_key(value) for value in argument_ranges),
                   _fingerprint_range_key(fingerprint), callee_context)
        # A single call occurrence can observe several bounded state
        # alternatives with identical arguments and closure cells. Keep each
        # callee activation: replacing its target with the last one would let
        # lowering prune branches using another alternative's state facts.
        contextual = self._call_contexts.get(context)
        self._call_contexts[context] = (
            got if contextual is None else join_contracts(contextual, got))
        self._call_targets[context] = callee_context
        previous = self._at_call.get(id(node))
        self._at_call[id(node)] = (got if previous is None else
                                   join_contracts(previous, got))
        return got

    def contract_at(self, node, context=None):
        if context is None:
            return self._at_call.get(id(node), UNKNOWN)
        return self._expression_contexts.get((context, id(node)), UNKNOWN)

    def expression_contract(self, node, context=None):
        """Joined contract when this exact expression is actually evaluated."""
        if context is None:
            return self._at_expression.get(id(node), UNKNOWN)
        return self._expression_contexts.get((context, id(node)), UNKNOWN)

    def expression_reached(self, node, context=None):
        """Whether control can evaluate this exact expression occurrence."""
        return ((id(node) in self._at_expression) if context is None else
                (context, id(node)) in self._expression_contexts)

    def returns(self, context):
        """Reachable returns for one exact source activation."""
        return tuple(self._return_facts.get(context, ()))

    def return_reached(self, node, context):
        return any(fact.node is node for fact in self.returns(context))

    def record_summaries(self):
        """Fixed record builders keyed by activation and lexical local."""
        return tuple(self._record_summaries.items())

    def record_summary(self, context, name):
        return self._record_summaries.get((context, str(name)))

    def return_plans(self, context):
        """Output BatchPlans keyed by their reached Return occurrence."""
        return tuple(
            (return_id, plan)
            for (candidate, return_id), plan in self._return_plans.items()
            if candidate == context)

    def all_return_plans(self):
        """Every activation-specific structured return plan."""
        return tuple(self._return_plans.items())

    def all_batch_plans(self):
        """Every activation-specific internal batch-expression plan."""
        return tuple(self._batch_plans.items())

    def all_binding_batch_plans(self):
        """Every activation-specific batch assignment plan."""
        return tuple(self._binding_batch_plans.items())

    def all_binding_batch_contracts(self):
        """Batch contracts keyed by activation and lexical local."""
        return tuple(
            (key, contract)
            for key, contract in self._binding_batch_contracts.items()
            if key not in self._non_batch_bindings)

    def batch_binding_names(self):
        """Locals whose joined source contract needs a batch carrier.

        The answer comes from the same path-joined binding contracts that size
        the carrier.  Recording only the individual assignments misses the
        ordinary ``None``-then-record pattern: neither assignment alone is a
        batch, while their joined value is exactly ``Option[Record]``.
        """
        planned = {
            key for key, contract in self._binding_batch_contracts.items()
            if key not in self._non_batch_bindings
            and contract.transaction is not None
            and contract.forms != frozenset({BatchForm.MAPPING})
        }
        legacy = {
            (context, name)
            for (context, name), contract in self._binding_contexts.items()
            if record_batch_payload(contract) is not None
        }
        return frozenset(planned | legacy)

    def binding_batch_contract(self, context, name):
        """Joined batch-domain contract of one lexical local."""
        key = (context, str(name))
        if key in self._non_batch_bindings:
            return None
        return self._binding_batch_contracts.get(key)

    def binding_contract(self, fn, name, context=None):
        """Joined contract actually bound to ``name`` in ``fn``."""
        if context is None:
            return self._at_binding.get((id(fn), str(name)), UNKNOWN)
        return self._binding_contexts.get((context, str(name)), UNKNOWN)

    def call_contracts(self):
        """Contracts keyed by the stable identities of reachable call nodes."""
        return tuple(self._at_call.items())

    def call_contexts(self):
        """Contracts keyed by call occurrence and concrete abstract inputs."""
        return tuple(self._call_contexts.items())

    def call_targets(self):
        """Callee summary selected by each context-sensitive call edge."""
        return tuple(self._call_targets.items())

    def binding_contracts(self):
        """Reachable binding contracts keyed by lexical function and name."""
        return tuple(self._binding_contexts.items())

    def binding_occurrence_contracts(self):
        """Reachable binding contracts keyed by activation and target AST."""
        return tuple(self._binding_occurrence_contexts.items())

    def expression_contracts(self):
        """Contracts keyed by activation and exact expression occurrence."""
        return tuple(self._expression_contexts.items())

    def expression_ranges(self):
        """Scalar ranges keyed by activation and exact occurrence."""
        return tuple(self._expression_ranges.items())

    def statement_contexts(self):
        """Reached source statements keyed by exact activation."""
        return tuple(self._statement_contexts)

    def statement_reached(self, node, context):
        return (context, id(node)) in self._statement_contexts

    def branch_outcomes(self):
        """Reachable Boolean outcomes keyed by activation and condition."""
        return tuple(self._branch_outcomes.items())

    def fixed_bindings(self, node, context):
        """Exact typed scalar facts common to every visit of ``node``."""
        return dict(self._expression_fixed.get((context, id(node)), {}))

    def numeric_bounds(self, node, context):
        """Integer bounds common to every visit of ``node``."""
        return dict(self._expression_bounds.get((context, id(node)), {}))

    def loop_span(self, node, context):
        """Finite task-domain trip interval proved for this activation."""
        return self._loop_spans.get((context, id(node)))

    def loop_spans(self):
        return tuple(self._loop_spans.items())

    def loop_domains(self):
        return tuple(self._loop_domains.items())

    @staticmethod
    def context_key(fn, arguments=(), fixed_arguments=(), fingerprint=(),
                    argument_ranges=(), state_contracts=None,
                    state_ranges=None):
        """Identity of one abstract source-function activation.

        A nested function may see different closure cells even when its own
        arguments are identical.  Those captured contracts and fixed values
        therefore belong to the cache key rather than a later global join.
        """
        return _ContextKey((
            id(fn), tuple(arguments),
            tuple(_fixed_key(value) for value in fixed_arguments),
            _fingerprint_key(fingerprint),
            tuple(_range_key(value) for value in argument_ranges),
            _fingerprint_range_key(fingerprint),
            tuple(sorted(dict(state_contracts or {}).items())),
            tuple(sorted(
                (name, _range_key(value))
                for name, value in dict(state_ranges or {}).items())),
        ))

    def contexts(self):
        return tuple(self._context_functions)

    def function_of(self, context):
        return self._context_functions[context]

    def context_functions(self):
        return tuple(self._context_functions.items())

    def initial_contracts(self, context):
        """Facts visible at entry to this exact abstract activation."""
        return dict(self._context_initial_env.get(context, {}))

    def final_state(self, context):
        """Path-joined source state after one normal helper or step call."""
        return dict(self._final_state.get(context, {}))

    def final_state_ranges(self, context):
        """Finite scalar-state ranges after one normal activation."""
        return dict(self._final_state_ranges.get(context, {}))

    def final_state_entries(self, context):
        """Normal exits, with each state shape paired with its own ranges."""
        return self._final_state_entries.get(context, ())

    def initial_bindings(self, context):
        """Fixed scalar facts visible at activation entry."""
        return dict(self._context_initial_bindings.get(context, {}))

    def initial_ranges(self):
        """Finite scalar facts keyed by exact abstract activation."""
        return tuple(
            (context, dict(values))
            for context, values in self._context_initial_ranges.items())

    def _scalar_summary_key(self, fn, arguments, fixed_arguments,
                            fingerprint, argument_ranges):
        """A state-prepass scalar result independent of carried model state.

        The resolver owns effects and lexical call closure. Both absence of
        writes and absence of reads are required: a pure getter is not state
        independent. The final layout pass still records every occurrence.
        """
        if not self.state_only or fn is self.model.step:
            return None
        if any(value != SCALAR for value in arguments) or any(
                contract != SCALAR
                for _owner, _name, contract, _fixed, _range in fingerprint):
            return None
        independent = self._query_cache.state_free_helpers
        if fn not in independent:
            closure = self.resolver.reachable_functions((fn,))
            independent[fn] = (
                all(self.resolver._function_has_no_external_effect(target)
                    for target in closure)
                and not any(
                    isinstance(node, ast.Name) and node.id == "self"
                    for target in closure
                    for node in ast.walk(target)))
        if not independent[fn]:
            return None
        return (len(self._context), self.context_key(
            fn, arguments, fixed_arguments, fingerprint, argument_ranges))

    def summarize(self, fn, arguments=(), fixed_arguments=(), captures=(),
                  fingerprint=None, argument_ranges=(), state_contracts=None,
                  state_ranges=None):
        fingerprint = captures if fingerprint is None else fingerprint
        key = self.context_key(
            fn, arguments, fixed_arguments, fingerprint, argument_ranges,
            state_contracts, state_ranges)
        if self.root_context is None:
            self.root_context = key
        if key in self._cache:
            return self._cache[key]
        if key in self._active:
            raise SequencePlanError(
                fn, "a recursive helper has no finite source-summary fixed "
                    "point", "recursive-helper-unsupported")
        # The root context is the public step.  Before pushing a helper,
        # ``len(_context)`` is exactly the candidate number of helper
        # activations below it: one for the first helper, two for its callee,
        # and so on.
        if helper_activation_exceeds(len(self._context)):
            raise SequencePlanError(
                fn, "a source helper chain deeper than the certified "
                    "summary limit", "source-summary-depth-exceeded")
        scalar_key = self._scalar_summary_key(
            fn, arguments, fixed_arguments, fingerprint, argument_ranges)
        if scalar_key in self._query_cache.scalar_summaries:
            result, returned_range = self._query_cache.scalar_summaries[scalar_key]
            self._cache[key] = result
            self._range_cache[key] = returned_range
            # A stateless helper transfers the caller's *current* state, not
            # the state at which its scalar result was first computed.
            self._final_state[key] = dict(
                self.declarations if state_contracts is None else state_contracts)
            self._final_state_ranges[key] = (
                dict(state_ranges) if state_ranges is not None
                else dict(self._root_state_ranges))
            self._context_functions[key] = fn
            self.reachable.add(fn)
            return result
        self._active.add(key)
        self.reachable.add(fn)
        try:
            is_method = id(fn) in self.resolver._method_ids
            params = helper_parameters(fn, receiver=is_method)
            env = {name: contract for _owner, name, contract, _fixed, _range
                   in captures}
            env.update(zip(params, arguments))
            env.update({
                _state_key(name): contract
                for name, contract in dict(
                    self.declarations if state_contracts is None
                    else state_contracts).items()
                if isinstance(contract, ValueContract)
            })
            bindings = {name: fixed for _owner, name, _contract, fixed, _range
                        in captures if fixed is not None}
            bindings.update({
                name: value for name, value
                in zip(params, fixed_arguments) if value is not None})
            scalar_ranges = {
                name: interval
                for _owner, name, _contract, _fixed, interval in captures
                if interval is not None
            }
            scalar_ranges.update({
                (_element_range_key(name) if contract.is_sequence else name): interval
                for name, contract, interval in zip(params, arguments, argument_ranges)
                if interval is not None
            })
            entry_state_ranges = (
                dict(state_ranges) if state_ranges is not None
                else dict(self._root_state_ranges))
            scalar_ranges.update({
                f"self.{name}": interval
                for name, interval in entry_state_ranges.items()
                if interval is not None
            })
            bindings = _ScalarBindings(
                bindings, scalar_bounds=scalar_ranges)
            self._context_initial_env[key] = dict(env)
            self._context_initial_bindings[key] = dict(bindings)
            self._context_initial_ranges[key] = dict(bindings.ranges)
            self._context.append(key)
            self._frames.append({
                "function": fn,
                "context": key,
                "env": env,
                "bindings": bindings,
            })
            entries = [(env, bindings)]
            if fn is self.model.step and self._state_entries is not None:
                entries = []
                for state, intervals in self._state_entries:
                    # A partition owns its complete field-presence image.
                    # Overlaying it on the joined state invents fields that
                    # only a different history has created.
                    selected = {name: value for name, value in env.items()
                                if _state_name(name) is None}
                    selected.update((_state_key(name), value)
                                    for name, value in state.items())
                    selected_ranges = {
                        name: value for name, value in scalar_ranges.items()
                        if not name.startswith("self.")}
                    selected_ranges.update((f"self.{name}", value)
                                           for name, value in intervals.items())
                    entries.append((selected, _ScalarBindings(
                        bindings, scalar_bounds=selected_ranges)))
            flow = self._block(fn, fn.body, entries)
            if not self.state_only:
                for one_env, _one_bindings in flow.normal:
                    self._remember_live_records(one_env, fn)
            normal_states = [
                _state_contracts_of(one_env)
                for one_env, _one_bindings in flow.normal
            ]
            normal_states.extend(dict(returned.state)
                                 for returned in flow.returned)
            self._final_state[key] = _join_state_contracts(normal_states)
            normal_ranges = [
                _state_ranges_of(one_bindings)
                for _one_env, one_bindings in flow.normal
            ]
            normal_ranges.extend(dict(returned.state_ranges)
                                 for returned in flow.returned)
            self._final_state_ranges[key] = _join_state_ranges(normal_ranges)
            argument_exits = [self._array_argument_facts(fn, one_env, one_bindings)
                              for one_env, one_bindings in flow.normal]
            argument_exits.extend(returned.array_arguments for returned in flow.returned)
            post_arguments = {}
            for facts in argument_exits:
                for name, contract, interval in facts:
                    if name in post_arguments:
                        previous, prior_range = post_arguments[name]
                        contract = join_contracts(previous, contract)
                        interval = (None if prior_range is None or interval is None
                                    else prior_range.join(interval))
                    post_arguments[name] = contract, interval
            self._final_array_arguments[key] = post_arguments
            if fn is self.model.step:
                self._final_state_entries[key] = _join_state_entries(
                    zip(normal_states, normal_ranges), self.budget, fn)
            if flow.breaks or flow.continues:
                result = UNKNOWN
                returned_range = None
            else:
                # Python falling off a helper is an internal ``None`` result.
                # A complete process returning it is interpreted separately
                # by the transaction boundary as "no output".
                values = [returned.contract for returned in flow.returned]
                if flow.normal or not values:
                    values.append(NONE)
                result = values[0]
                for value in values[1:]:
                    result = join_contracts(result, value)
                intervals = [returned.interval for returned in flow.returned]
                returned_range = (
                    None if flow.normal or not intervals
                    or any(interval is None for interval in intervals)
                    else intervals[0])
                if returned_range is not None:
                    for interval in intervals[1:]:
                        returned_range = returned_range.join(interval)
                batch_values = [
                    returned.batch.contract
                    for returned in flow.returned
                    if returned.batch is not None]
                complete_batch = len(batch_values) == len(flow.returned)
                if flow.normal or not flow.returned:
                    batch_values.append(
                        BatchContract.empty(BatchForm.NONE))
                if complete_batch and batch_values:
                    batch_result = batch_values[0]
                    try:
                        for candidate in batch_values[1:]:
                            batch_result = join_batch_alternatives(
                                batch_result, candidate, fn)
                    except BatchContractError as exc:
                        if fn is self.model.step:
                            raise SequencePlanError(
                                exc.node or fn, exc.what, exc.code) from None
                        raise SequencePlanError(
                            exc.node or fn, exc.what,
                            "helper-return-shape-disagrees") from None
                    # A mapping returned by an internal helper is still an
                    # ordinary record value.  It becomes one transaction only
                    # when the complete process returns it.  Lists and mixed
                    # source forms need the batch summary here because an
                    # ordinary ValueContract cannot retain their carrier
                    # alternatives across the call boundary.
                    if batch_result.forms != frozenset({BatchForm.MAPPING}):
                        self._context_batch_contracts[key] = batch_result
                        if batch_result.transaction is not None:
                            result = batch_value_contract(batch_result)
            self._cache[key] = result
            self._range_cache[key] = returned_range
            self._context_functions[key] = fn
            if scalar_key is not None and result == SCALAR:
                self._query_cache.scalar_summaries[scalar_key] = result, returned_range
            return result
        finally:
            self._frames.pop()
            self._context.pop()
            self._active.discard(key)

    def _array_argument_facts(self, fn, env, bindings):
        """Postconditions of incoming fixed arrays whose binding is retained."""
        if id(fn) not in self.resolver._method_ids:
            return ()
        if fn not in self._array_parameters:
            self._array_parameters[fn] = (
                () if self.resolver._function_has_no_external_effect(fn)
                else non_rebound_parameters(fn, receiver=True))
        initial = self._context_initial_env[self._context[-1]]
        return tuple(
            (name, env[name], bindings.ranges.get(_element_range_key(name)))
            for name in sorted(self._array_parameters[fn])
            if (contract := initial.get(name, UNKNOWN)).is_sequence
            and contract.carrier in {"list", "bytearray"}
            and contract.element == SCALAR and contract.exact_length is not None)

    def _block(self, fn, body, states):
        live = list(states)
        returned = []
        breaks = []
        continues = []
        for stmt in body:
            following = []
            for env, bindings in live:
                flow = self._stmt(
                    fn, stmt, dict(env), self._copy_bindings(bindings))
                following.extend(flow.normal)
                returned.extend(flow.returned)
                breaks.extend(flow.breaks)
                continues.extend(flow.continues)
            if not following:
                return _ContractFlow((), returned, breaks, continues)
            # Join paths with the same retained finite selectors; unrelated
            # numeric distinctions do not multiply the structural state.
            live = self._join_states(following)
        return _ContractFlow(live, returned, breaks, continues)

    def _assign_expression(self, fn, targets, node, env, bindings, *, choices=()):
        """Evaluate once, then bind each target on that expression's paths.

        Joining a conditional RHS before its assignment loses the relation
        between the selected arm and its value. Bind first; the ordinary path
        join then retains presence alternatives while merging unrelated scalar
        differences. Later writes transfer these snapshots normally, without
        re-evaluating the original condition or selected read.
        """
        if isinstance(node, ast.IfExp):
            self._remember_fixed(node, bindings)
            self._expr(fn, node.test, env, bindings)
            normal = []
            result = None
            for truth, arm in ((True, node.body), (False, node.orelse)):
                for selected, facts in self._condition_states_with_ranges(
                        fn, node.test, [(env, bindings)], truth):
                    flow, value = self._assign_expression(
                        fn, targets, arm, selected, facts,
                        choices=(*choices, (node, truth)))
                    normal.extend(flow.normal)
                    result = (value if result is None else
                              join_contracts(result, value))
            result = self._remember_expression(
                node, UNKNOWN if result is None else result)
            return _ContractFlow(self._join_states(normal)), result
        if (all(isinstance(target, ast.Name) for target in targets)
                and self._saved_structural_predicate(fn, node, bindings)):
            # Save the evaluated Boolean, not an expression to re-evaluate
            # after later writes. Each arm carries the source state at the
            # test; ordinary assignment/mutation then updates that state.
            self._expr(fn, node, env, bindings)
            normal = []
            for truth in (False, True):
                for selected, facts in self._condition_states_with_ranges(
                        fn, node, [(env, bindings)], truth):
                    for target in targets:
                        self._target(fn, target, SCALAR, selected, facts,
                                     fixed=truth)
                    normal.append((selected, facts))
            return _ContractFlow(self._join_states(normal)), SCALAR
        evaluated_items = ({} if any(isinstance(target, (ast.Tuple, ast.List))
                                    for target in targets) else None)
        value = self._expr(
            fn, node, env, bindings, evaluated_items=evaluated_items)
        length_reference = self._length_reference(fn, node, bindings)
        # Only scalar-name targets preserve this RHS snapshot. In a
        # chained assignment the sequence itself may be overwritten.
        if (not all(isinstance(target, ast.Name) for target in targets)
                or any(target.id == length_reference for target in targets)
                or getattr(env.get(length_reference), "kind", None) not in {"sequence", "mapping"}):
            length_reference = None
        static_values = (
            self._static_reference_values(
                fn, node, env, bindings)
            if self._static_reference_candidate(
                fn, node, env, bindings) else None)
        structural = self._structural_bindings(bindings)
        bounds_interval = self._bounds_of(fn, node, structural)
        interval = (None if bounds_interval is None else
                    bounds_interval.finite())
        static_interval = self._numeric_static_range(static_values)
        if static_interval is not None:
            interval = static_interval
            bounds_interval = ranges.Bounds.of(static_interval)
        element_interval = self._sequence_element_range(
            fn, node, structural)
        carries_records = self._is_record_batch_value(value)
        empty_list = (isinstance(node, ast.List)
                      and not node.elts)
        items = record_items(node, is_builtin=lambda name:
                             name not in bindings and self.resolver.is_builtin(fn, name))
        record_literal = (bool(items)
                          and all(record_key(key) is not None
                                  for key, _value in items))
        batch_plan = None
        known_batch = self._batch_for_expression(node, env)
        if (value.is_none or value.is_record or record_literal or empty_list
                or carries_records or known_batch is not None):
            batch_plan = self._plan_batch_expression(
                fn, node, env, bindings)
            self._remember_batch_plan(
                node, batch_plan)
            # The target belongs to the original RHS, not an individual arm.
            # Retain that choice tree when its None/record plans are joined
            # across paths; do not join different AST leaves as one occurrence.
            for choice, truth in reversed(choices):
                batch_plan = BatchPlan(
                    "choice", choice, batch_plan.contract, (batch_plan,), truth)
                self._remember_batch_plan(choice, batch_plan)
        if carries_records and len(targets) != 1:
            raise SequencePlanError(
                node, "one mutable transaction batch is assigned to "
                      "several names",
                "batch-value-alias-unsupported")
        owner = self.resolver.owner_of(fn)
        fixed = (
            None
            if all(isinstance(target, (ast.Tuple, ast.List))
                   for target in targets)
            else self._static_scalar(fn, node, structural)
        )
        destructured = (
            _destructuring_value(
                node, owner, structural, budget=self.budget)
            if any(isinstance(target, (ast.Tuple, ast.List))
                   for target in targets)
            else None
        )
        literal = self._record_literal(fn, node, bindings)
        if len(targets) > 1 and literal is not None:
            raise SequencePlanError(
                node, "a mutable output-record builder is multiply bound",
                "record-builder-alias-unsupported")
        for target in targets:
            self._remember_batch_binding(target, batch_plan)
            if isinstance(target, (ast.Tuple, ast.List)):
                self._remember_record_rebindings(target, env)
            # Python evaluates the RHS once before walking assignment
            # targets from left to right.
            if not isinstance(target, (ast.Tuple, ast.List)):
                self._target_effects(fn, target, env, bindings)
            if self._record_store(
                    fn, target, node, value, env, bindings):
                continue
            if self._record_assignment(
                    fn, target, node, value, env, bindings):
                continue
            target_fixed = (
                destructured
                if isinstance(target, (ast.Tuple, ast.List)) else fixed)
            positional_facts = (
                self._literal_destructuring_facts(
                    target, node, evaluated_items)
                if isinstance(target, (ast.Tuple, ast.List)) else None)
            self._target(
                fn, target, value, env, bindings, target_fixed, interval,
                element_interval, bounds_interval=bounds_interval,
                positional_facts=positional_facts)
            if length_reference is not None:
                bindings.lengths[target.id] = length_reference
            if (isinstance(target, ast.Name)
                    and self._retains_static_reference(static_values)):
                env[target.id] = _StaticFact(static_values)
            if isinstance(target, ast.Name) and batch_plan is not None:
                # The batch algebra can retain a fixed mapping schema
                # even when one field has no ordinary value contract yet
                # (for example, a read whose failing path is lowered
                # explicitly).  Keep that independent fact on the local.
                # A known single-carrier record/list continues to use its
                # ordinary contract; mixed carriers need the common
                # count-and-buffers value contract after path joins.
                direct = batch_contract_of_value(value)
                if direct is None or len(batch_plan.contract.forms) > 1:
                    env[target.id] = _BatchFact(
                        value, batch_plan.contract)
        return _ContractFlow([(env, bindings)]), value

    def _loop_exit(self, fn, stmt, normal, returned, broken=()):
        """Run else only on exhaustion; its break/continue belong to the parent."""
        escaped_breaks = []
        escaped_continues = []
        if stmt.orelse and normal:
            otherwise = self._block(fn, stmt.orelse, normal)
            normal = otherwise.normal
            returned.extend(otherwise.returned)
            escaped_breaks.extend(otherwise.breaks)
            escaped_continues.extend(otherwise.continues)
        return _ContractFlow(
            [*broken, *normal], returned, escaped_breaks, escaped_continues)

    def _for_statement(self, fn, stmt, env, bindings):
        """Plan for-loop iteration, natural exhaustion and escaped control flow."""
        if (isinstance(stmt.iter, ast.Call)
                and isinstance(stmt.iter.func, ast.Name)
                and stmt.iter.func.id in {"range", "reversed"}
                and self.resolver.is_builtin(fn, stmt.iter.func.id)
                and (stmt.iter.keywords or any(
                    isinstance(arg, ast.Starred)
                    for arg in stmt.iter.args))):
            raise SequencePlanError(
                stmt.iter, "a structural builtin loop call uses keyword "
                           "or unpacked arguments",
                "call-argument-form")
        structural = self._structural_bindings(bindings)
        self._remember_fixed(stmt.iter, bindings)
        values = _static_iterable(
            stmt.iter, self.resolver.owner_of(fn), structural,
            is_builtin=lambda name: (name not in bindings and
                self.resolver.is_builtin(fn, name)),
            budget=self.budget)
        iterable = self._expr(fn, stmt.iter, env, bindings)
        if not iterable.is_sequence:
            iterable = self._bounded_iterable_contract(fn, stmt.iter, env, bindings)
        element_interval = self._loop_element_range(
            fn, stmt.iter, structural)
        if not loop_preserves_iterable_values(
                stmt, resolver=self.resolver, owner=fn,
                scalar_names={name for name, contract in env.items()
                              if contract == SCALAR},
                is_flat_sequence=lambda expression:
                    self._observed_contract(expression).element == SCALAR):
            # An entry-list range is not an inductive fact when later
            # next() calls can see appended or overwritten values.
            element_interval = None
        exact = values is not None
        if values is None:
            maximum = _proved_bound_of(
                stmt.iter,
                {name: value.maximum for name, value in env.items()
                 if isinstance(value, ValueContract)
                 and value.is_sequence},
                self.resolver.owner_of(fn),
                lambda name: self.resolver.is_builtin(fn, name),
                structural, getattr(bindings, "bounds", {}),
                budget=self.budget)
            if iterable.is_sequence and iterable.maximum is not None:
                maximum = (iterable.maximum if maximum is None else
                           min(maximum, iterable.maximum))
            # Representation planning may conservatively replay up to the
            # capability ceiling.  Only a bound proved at this program
            # point is exported to executable lowering, however; the
            # ceiling itself says nothing about the source's semantics.
            values = tuple([None] * (
                UNROLL_LIMIT if maximum is None else maximum))
            span = (None if maximum is None else
                    ranges.Range(0, maximum))
        else:
            span = ranges.Range(len(values), len(values))
        span_key = (self._context[-1], id(stmt))
        missing = object()
        previous = self._loop_spans.get(span_key, missing)
        if span is None or previous is None:
            # Preserve an unproved visit permanently.  A later visit on a
            # better-refined path cannot lend its assumption to this one.
            self._loop_spans[span_key] = None
        elif previous is missing:
            self._loop_spans[span_key] = span
        else:
            self._loop_spans[span_key] = previous.join(span)
        sequence_names = frozenset(
            name for name, contract in env.items()
            if isinstance(contract, ValueContract)
            and contract.is_sequence)
        fresh_sequence_names, _fresh_sequence_assignments = \
            _iteration_local_sequences(stmt)
        scalar_summary = (
            _bounded_scalar_loop_recurrences(stmt)
            if (not exact and len(values)
                >= _CONTRACT_LOOP_SUMMARY_MIN_TRIPS
                and _for_body_has_stable_contract_transfer(
                    stmt, self.resolver, fn, sequence_names,
                    allow_loop_exits=True))
            else None
        )
        if scalar_summary is not None and _stores_loop_carried_elements(
                stmt, self.resolver, fn, widened=frozenset(scalar_summary[0])):
            scalar_summary = None
        if scalar_summary is not None:
            # A finite dynamic loop may branch to ``break`` while a local
            # accumulator records how many preceding visits completed.
            # Widen those proved affine recurrences to the complete trip
            # envelope, then interpret one ordinary body visit.  That one
            # visit still owns every reached expression, break state and
            # representation fact; only duplicate planner replay is gone.
            recurrences, _written = scalar_summary
            incoming = [(dict(env), self._copy_bindings(bindings))]
            entered = []
            for one_env, one_bindings in incoming:
                next_env = dict(one_env)
                next_bindings = self._copy_bindings(one_bindings)
                item = self._loop_element_contract(
                    fn, stmt.iter, iterable)
                self._remember_record_rebindings(stmt.target, next_env)
                self._target(
                    fn, stmt.target, item, next_env, next_bindings,
                    None, interval=element_interval)
                entered.append((next_env, next_bindings))
            entered = self._bounded_scalar_loop_entries(
                fn, stmt, entered, len(values), recurrences)
            if entered is not None:
                body = self._block(fn, stmt.body, entered)
                exhaustion = self._join_states(
                    incoming + body.normal + body.continues)
                returned = list(body.returned)
                return self._loop_exit(
                    fn, stmt, exhaustion, returned, body.breaks)
        if (_for_body_has_stable_contract_transfer(
                stmt, self.resolver, fn, sequence_names)
                and (not exact or len(values)
                     >= _CONTRACT_LOOP_SUMMARY_MIN_TRIPS)
                and not _stores_loop_carried_elements(stmt, self.resolver, fn)):
            # One abstract visit determines the contract transfer for
            # every entered iteration.  Exact scalar bindings written in
            # the body are deliberately forgotten afterwards: one visit
            # does not prove an accumulator's final value.  Executable
            # lowering and its range analysis still own every iteration.
            incoming = [(dict(env), self._copy_bindings(bindings))]
            if not values:
                normal = incoming
                returned = []
            else:
                entered = []
                numeric_values = (
                    tuple(value for value in values
                          if type(value) is int)
                    if exact else ())
                for one_env, one_bindings in incoming:
                    next_env = dict(one_env)
                    next_bindings = self._copy_bindings(one_bindings)
                    item = self._loop_element_contract(
                        fn, stmt.iter, iterable)
                    self._target(
                        fn, stmt.target, item, next_env, next_bindings,
                        None, interval=element_interval)
                    if isinstance(stmt.target, ast.Name):
                        next_bindings.pop(stmt.target.id, None)
                        if numeric_values:
                            next_bindings.set_numeric(
                                stmt.target.id,
                                ranges.Range(
                                    min(numeric_values),
                                    max(numeric_values)))
                        elif element_interval is not None:
                            next_bindings.set_numeric(
                                stmt.target.id, element_interval)
                    entered.append((next_env, next_bindings))
                body = self._block(fn, stmt.body, entered)
                returned = list(body.returned)
                normal = self._join_states(body.normal + body.continues)
                written = _function_scope_store_names(stmt.body)
                written.discard(stmt.target.id)
                if len(values) > 1 and normal:
                    # The first visit establishes the abstract transfer,
                    # but it need not reach every expression that a later
                    # visit reaches.  For example, the first iteration of
                    # ``best is None or value < best`` stops at the None
                    # test, while every later iteration evaluates the
                    # comparison.  Observe one steady-state visit after
                    # forgetting exact scalar facts written by the body.
                    # This explores both outcomes of such loop-carried
                    # control without replaying the finite trip count.
                    steady = []
                    for one_env, one_bindings in normal:
                        next_env = dict(one_env)
                        next_bindings = self._copy_bindings(one_bindings)
                        # Occurrence facts describe every visit, not just
                        # the two visits used to discover this transfer.
                        # Enter the steady visit with the full range of
                        # append-only loop heads. Otherwise an append in
                        # a long builder records a maximum of two, even
                        # though the loop postcondition has the right size.
                        for name in sequence_names - fresh_sequence_names:
                            before = env.get(name)
                            after = next_env.get(name)
                            if (isinstance(before, ValueContract)
                                    and isinstance(after, ValueContract)
                                    and before.is_sequence
                                    and after.is_sequence
                                    and before.carrier == after.carrier
                                    and after.maximum >= before.maximum):
                                maximum = finite_cardinality_add(
                                    before.maximum,
                                    finite_cardinality_product(
                                        after.maximum - before.maximum,
                                        len(values) - 1))
                                next_env[name] = after.with_interval(
                                    after.minimum, maximum, after.element)
                                next_bindings.forget_length(name)
                        for name in written:
                            next_bindings.forget_length(name)
                            next_bindings.pop(name, None)
                            next_bindings.forget_numeric(name)
                        steady.append((next_env, next_bindings))
                    self._block(fn, stmt.body, steady)
                # Append-only sequence transfers are monotone.  One body
                # visit proves the per-iteration length delta and joined
                # element contract; apply that delta to the finite trip
                # bound without enumerating each visit.
                iterations = len(values)
                for one_env, one_bindings in normal:
                    for name in sequence_names - fresh_sequence_names:
                        before = env.get(name)
                        after = one_env.get(name)
                        if (not isinstance(before, ValueContract)
                                or not isinstance(after, ValueContract)
                                or not before.is_sequence
                                or not after.is_sequence
                                or before.carrier != after.carrier
                                or after.minimum < before.minimum
                                or after.maximum < before.maximum):
                            continue
                        delta_min = after.minimum - before.minimum
                        delta_max = after.maximum - before.maximum
                        minimum = (before.minimum if not exact else
                            finite_cardinality_add(
                                before.minimum,
                                finite_cardinality_product(
                                    delta_min, iterations)))
                        maximum = finite_cardinality_add(
                            before.maximum,
                            finite_cardinality_product(
                                delta_max, iterations))
                        one_env[name] = after.with_interval(
                            minimum, maximum, after.element)
                        one_bindings.forget_length(name)
                        # This is the semantic post-binding of the loop,
                        # not another source assignment occurrence.  Make
                        # it visible to the capacity projection so a list
                        # recreated on each outer iteration is sized per
                        # instance rather than by multiplying all visits.
                        self._remember_binding(
                            fn, name, one_env[name])
                if not exact:
                    normal = self._join_states(incoming + normal)
                for _one_env, one_bindings in normal:
                    for name in written:
                        one_bindings.forget_length(name)
                        one_bindings.pop(name, None)
                        one_bindings.forget_numeric(name)
            return self._loop_exit(fn, stmt, normal, returned)
        exhaustion = ([(dict(env), self._copy_bindings(bindings))]
                      if not exact or not values else [])
        current = [(env, bindings)]
        returned = []
        broken = []
        target_affects_body = _for_target_affects_body(
            stmt, self.resolver, fn)
        progression = (None if exact else
                       self._range_progression(fn, stmt.iter, structural))
        for iteration, value in enumerate(values):
            if progression is not None:
                value = progression[0] + iteration * progression[1]
            entered = []
            for one_env, one_bindings in current:
                next_env = dict(one_env)
                next_bindings = self._copy_bindings(one_bindings)
                item = (contract_of_value(value) if value is not None else
                        self._loop_element_contract(
                            fn, stmt.iter, iterable))
                self._remember_record_rebindings(stmt.target, next_env)
                self._target(fn, stmt.target, item, next_env,
                             next_bindings, value,
                             interval=element_interval)
                entered.append((next_env, next_bindings))
            head_signature = (
                None if target_affects_body else
                self._state_signature(entered, {stmt.target.id}))
            body = self._block(fn, stmt.body, entered)
            returned.extend(body.returned)
            broken.extend(body.breaks)
            current = self._join_states(body.normal + body.continues)
            if not exact:
                exhaustion.extend(
                    (dict(e), self._copy_bindings(b))
                    for e, b in current)
            if not current:
                break
            if (head_signature is not None
                    and head_signature == self._state_signature(
                        current, {stmt.target.id})):
                # The target is not observable in the body and its whole
                # source-value transfer has reached a fixed point.  Later
                # iterations can add no contract, return, or break fact.
                # Executable lowering remains responsible for every
                # concrete iteration; this only stops planner replay.
                break
        if exact:
            exhaustion.extend(current)
        normal = self._join_states(exhaustion)
        return self._loop_exit(fn, stmt, normal, returned, broken)

    def _while_statement(self, fn, stmt, env, bindings):
        """Plan while-loop bounds, condition visits and escaped control flow."""
        if not _block_reaches_back_edge(stmt.body):
            # This source loop executes its test once and its body at most
            # once.  False-test exhaustion reaches ``else``; an entered
            # break bypasses it.  Executable lowering still needs its own
            # finite loop plan and may refuse this source form.
            reached = [(env, bindings)]
            self._expr(fn, stmt.test, env, bindings)
            exhaustion = self._condition_states_with_ranges(
                fn, stmt.test, reached, False)
            entered = self._condition_states_with_ranges(
                fn, stmt.test, reached, True)
            body = self._block(fn, stmt.body, entered)
            if body.normal or body.continues:
                raise SequencePlanError(
                    stmt, "an exit-only while body reached its back edge",
                    "source-plan-inconsistent")
            normal = self._join_states(exhaustion)
            returned = list(body.returned)
            return self._loop_exit(fn, stmt, normal, returned, body.breaks)
        structural = self._structural_bindings(bindings)
        ranking = None
        exit_ranking = None
        descent = None
        entry_range = None
        try:
            progress = analyse_while(
                stmt, resolver=self.resolver, owner=fn)
        except WhilePlanError as exact_error:
            progress = None
            try:
                ranking = analyse_while_ranking(
                    stmt, resolver=self.resolver, owner=fn)
            except WhilePlanError as rank_error:
                ranking = None
                try:
                    exit_ranking = analyse_while_exit_ranking(
                        stmt, resolver=self.resolver, owner=fn)
                except WhilePlanError as exit_error:
                    try:
                        descent = finite_progress_domain(
                            stmt, resolver=self.resolver, owner=fn,
                            contract_of=lambda value: self._expr(fn, value, env, bindings),
                            bounds_of=lambda value: self._bounds_of(fn, value, structural))
                    except WhilePlanError as descent_error:
                        raise SequencePlanError(
                            stmt, f"{exact_error}; no finite sum ranking: "
                                  f"{rank_error}; no finite cutoff ranking: "
                                  f"{exit_error}; no finite-table descent: "
                                  f"{descent_error}",
                            "while-trip-count-unproved") from None
            if ranking is not None:
                inductions = [
                    bindings.bounds.get(component.name)
                    for component in ranking.components
                ]
                bounds = [
                    self._bounds_of(fn, component.bound, structural)
                    for component in ranking.components
                ]
                if any(value is None for value in (*inductions, *bounds)):
                    raise SequencePlanError(
                        stmt, "the ranked while has no finite task-domain "
                              "trip ceiling",
                        "while-trip-range-unproved")
                try:
                    span = ranking_span(ranking, inductions, bounds)
                except WhilePlanError as exc:
                    raise SequencePlanError(
                        stmt, str(exc),
                        "while-trip-range-unproved") from None
            elif descent is not None:
                span = ranges.Range(0, descent.maximum)
            else:
                induction = bindings.ranges.get(exit_ranking.name)
                cutoff_bounds = tuple(
                    tuple(self._range_of(fn, cutoff.bound, structural)
                          for cutoff in path.cutoffs)
                    for path in exit_ranking.paths)
                if induction is None or any(
                        value is None for row in cutoff_bounds
                        for value in row):
                    raise SequencePlanError(
                        stmt, "the cutoff-ranked while has no finite "
                              "task-domain value range",
                        "while-trip-range-unproved")
                try:
                    span, entry_range = exit_ranking_domain(
                        exit_ranking, induction, cutoff_bounds)
                except WhilePlanError as exc:
                    raise SequencePlanError(
                        stmt, str(exc),
                        "while-trip-range-unproved") from None
        else:
            induction = bindings.bounds.get(progress.name)
            bound = self._bounds_of(fn, progress.bound, structural)
            if induction is None or bound is None:
                raise SequencePlanError(
                    stmt, "the while trip count has no finite task-domain "
                          "ceiling",
                    "while-trip-range-unproved")
            try:
                span = trip_span(progress, induction, bound)
            except WhilePlanError as exc:
                raise SequencePlanError(
                    stmt, str(exc), "while-trip-range-unproved") from None
        if progress is None and span.hi > UNROLL_LIMIT:
            raise SequencePlanError(
                stmt, "the while ranking exceeds the source planning "
                      f"limit of {UNROLL_LIMIT} visits",
                "while-trip-range-unproved")
        context = self._context[-1]
        span_key = (context, id(stmt))
        previous = self._loop_spans.get(span_key)
        self._loop_spans[span_key] = (
            span if previous is None else previous.join(span))
        if exit_ranking is not None or descent is not None:
            kind = 'cutoff' if exit_ranking is not None else 'finite-progress'
            prior_domain = self._loop_domains.get(span_key)
            if prior_domain is not None and entry_range is not None:
                entry_range = entry_range.join(prior_domain[1])
            self._loop_domains[span_key] = kind, entry_range
        current = [(env, bindings)]
        exhaustion = []
        returned = []
        broken = []
        if progress is not None and _while_body_is_scalar_contract_only(stmt):
            # The scalar contract lattice reaches its fixed point after
            # one abstract body visit.  Widen the induction fact over all
            # possible visits, then interpret that body once so its
            # expressions and control exits remain ordinary reached
            # occurrences.  Iterating ``span.hi`` times here would make a
            # retained one-node IR loop unavailable merely because its
            # task carrier is wide.
            reached = []
            for one_env, one_bindings in current:
                self._expr(fn, stmt.test, one_env, one_bindings)
                reached.append((one_env, one_bindings))
            exhaustion.extend(self._condition_states_with_ranges(
                fn, stmt.test, reached, False))
            entered = self._condition_states_with_ranges(
                fn, stmt.test, reached, True)
            # A scalar type reaches a fixed point after one visit; an
            # accumulator range generally does not.  Forget every value
            # changed in the body before interpreting that abstract visit.
            # A replacement such as ``x = i`` can then derive a sound range
            # from the widened induction envelope, while ``x += i`` remains
            # unknown instead of retaining a one-iteration under-approximation.
            written_scalars = _function_scope_scalar_store_names(
                stmt.body)
            widened = []
            for one_env, one_bindings in entered:
                one_bindings = self._copy_bindings(one_bindings)
                initial = one_bindings.ranges.get(progress.name)
                for name in written_scalars:
                    one_bindings.forget_length(name)
                    one_bindings.pop(name, None)
                    if name.startswith("self."):
                        one_bindings.pop(("self", name[5:]), None)
                    one_bindings.forget_numeric(name)
                if initial is not None:
                    if progress.operation in {"rshift", "parent"}:
                        one_bindings.set_numeric(
                            progress.name, ranges.Range(0, initial.hi))
                    elif progress.operation == "lshift":
                        # An entered iteration starts below the invariant
                        # cutoff. Only its final shift can cross that cutoff.
                        last = bound.hi - int(not progress.inclusive)
                        one_bindings.set_numeric(progress.name, ranges.Range(
                            initial.lo, max(initial.hi, max(0, last) << progress.amount)))
                    else:
                        shift = progress.amount * span.hi
                        one_bindings.set_numeric(
                            progress.name,
                            ranges.Range(
                                initial.lo + min(0, shift),
                                initial.hi + max(0, shift),
                            ))
                    one_bindings.pop(progress.name, None)
                    one_bindings.forget_length(progress.name)
                widened.append((one_env, one_bindings))
            body = self._block(fn, stmt.body, widened)
            returned.extend(body.returned)
            broken.extend(body.breaks)
            looping = self._join_states(body.normal + body.continues)
            reached = []
            for one_env, one_bindings in looping:
                self._expr(fn, stmt.test, one_env, one_bindings)
                reached.append((one_env, one_bindings))
            exhaustion.extend(self._condition_states_with_ranges(
                fn, stmt.test, reached, False))
            normal = self._join_states(exhaustion)
            return _ContractFlow(broken + normal, returned)
        for _ in range(span.hi):
            if descent is not None:
                current = self._bounded_loop_heads(fn, current, descent.head_bounds)
            head_signature = self._state_signature(current)
            reached = []
            for one_env, one_bindings in current:
                self._expr(fn, stmt.test, one_env, one_bindings)
                reached.append((one_env, one_bindings))
            exhaustion.extend(
                self._condition_states_with_ranges(
                    fn, stmt.test, reached, False))
            entered = self._condition_states_with_ranges(
                fn, stmt.test, reached, True)
            if not entered:
                current = []
                break
            body = self._block(fn, stmt.body, entered)
            returned.extend(body.returned)
            broken.extend(body.breaks)
            current = self._join_states(body.normal + body.continues)
            if not current:
                break
            if descent is not None:
                current = self._bounded_loop_heads(fn, current, descent.head_bounds)
            if head_signature == self._state_signature(current):
                # The complete abstract head (contracts, constants,
                # bounds, guards and length relations) is unchanged.
                # Replaying it cannot add an occurrence or exit fact.
                # Retain the proved trip span for executable lowering;
                # this stops only duplicate source planning, not the loop.
                break
        if exit_ranking is not None and not exit_ranking.guarded:
            # The cutoff ranking proves that no path reaches one more
            # back-edge.  A constant-true loop has no condition-false
            # exit and therefore never executes its ``else`` suite;
            # only the accumulated break and return exits survive.
            return _ContractFlow(broken, returned)
        # The span is a task-domain upper bound.  A still-live path now
        # reaches the next source condition, which must be false; record
        # that evaluation and its natural-exhaustion state without an
        # invented extra body execution.
        reached = []
        for one_env, one_bindings in current:
            self._expr(fn, stmt.test, one_env, one_bindings)
            reached.append((one_env, one_bindings))
        exhaustion.extend(self._condition_states_with_ranges(
            fn, stmt.test, reached, False))
        normal = self._join_states(exhaustion)
        return self._loop_exit(fn, stmt, normal, returned, broken)

    def _stmt(self, fn, stmt, env, bindings):
        if self._context:
            self._statement_contexts.add((self._context[-1], id(stmt)))
        if isinstance(stmt, ast.Delete):
            for target in stmt.targets:
                binding = sequence_delete_target(target)
                if binding is None:
                    raise SequencePlanError(
                        target, "deletion needs an indexed finite list binding",
                        "delete-target-unsupported")
                self._target_effects(fn, target, env, bindings)
                self._remove_item(fn, target, binding, env, bindings)
                self._remember_expression(target, NONE)
            return _ContractFlow([(env, bindings)])
        if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
            for expression in annotation_target_expressions(stmt.target):
                self._expr(fn, expression, env, bindings)
            return _ContractFlow([(env, bindings)])
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else (stmt.target,)
            flow, _value = self._assign_expression(
                fn, targets, stmt.value, env, bindings)
            return flow
        if isinstance(stmt, ast.AugAssign):
            if (self._state_entries is not None
                    and isinstance(stmt.target, ast.Attribute)
                    and isinstance(stmt.target.value, ast.Name)
                    and stmt.target.value.id == "self"
                    and _state_key(stmt.target.attr) not in env
                    and stmt.target.attr not in self.resolver.static_field_values):
                # This presence partition raises on the target load, before
                # either the RHS or write. It has no normal successor whose
                # unknown payload may contaminate the bounded state closure.
                # Executable lowering still emits the actual AttributeError.
                return _ContractFlow(())
            root = stmt.target
            while isinstance(root, (ast.Subscript, ast.Attribute)):
                root = root.value
            if isinstance(root, ast.Name) and isinstance(
                    env.get(root.id), _RecordFact):
                # A fixed-key augmented field update is the same finite record
                # operation as ``r[k] = r[k] op value``.  Evaluate the read and
                # RHS once in Python order, then update the existing field
                # contract.  More elaborate lvalues remain outside this finite
                # record representation rather than being approximated.
                if (not isinstance(stmt.target, ast.Subscript)
                        or not isinstance(stmt.target.value, ast.Name)
                        or stmt.target.value.id != root.id):
                    raise SequencePlanError(
                        stmt, "an augmented assignment has a compound mutable "
                              "record target",
                        "record-builder-mutation-unsupported")
                proxy = self._augmented_expression(stmt)
                value = self._expr(fn, proxy, env, bindings)
                if not self._record_store(
                        fn, stmt.target, proxy, value, env, bindings):
                    raise SequencePlanError(
                        stmt, "a live record field update lost its source "
                              "record contract",
                        "source-plan-inconsistent")
                return _ContractFlow([(env, bindings)])
            scalar_target = isinstance(stmt.target, ast.Name) or (
                isinstance(stmt.target, ast.Attribute)
                and isinstance(stmt.target.value, ast.Name)
                and stmt.target.value.id == "self")
            if scalar_target:
                proxy = self._augmented_expression(stmt)
                value = self._expr(fn, proxy, env, bindings)
                batch_plan = self._plan_batch_expression(
                    fn, proxy, env, bindings)
                self._remember_batch_binding(stmt.target, batch_plan)
                structural = self._structural_bindings(bindings)
                bounds_interval = self._bounds_of(fn, proxy, structural)
                interval = (None if bounds_interval is None else
                            bounds_interval.finite())
                fixed = self._static_scalar(fn, proxy, structural)
                self._target(
                    fn, stmt.target, value, env, bindings, fixed, interval,
                    bounds_interval=bounds_interval)
            else:
                # Augmented assignment evaluates the target location before
                # its RHS, combines the two values, then stores through that
                # same location.  The stable proxy expresses exactly that
                # evaluation order.  Feeding its result through the ordinary
                # target transfer also updates the collection's element
                # contract and invalidates any literal-initializer fact.
                proxy = self._augmented_expression(stmt)
                value = self._expr(fn, proxy, env, bindings)
                structural = self._structural_bindings(bindings)
                bounds_interval = self._bounds_of(fn, proxy, structural)
                interval = (None if bounds_interval is None else
                            bounds_interval.finite())
                element_interval = self._sequence_element_range(
                    fn, proxy, structural)
                self._target(
                    fn, stmt.target, value, env, bindings,
                    self._static_scalar(fn, proxy, structural), interval,
                    element_interval, bounds_interval=bounds_interval)
            return _ContractFlow([(env, bindings)])
        if isinstance(stmt, ast.Expr) and (
                is_plain_append_call(stmt.value)
                or is_plain_extend_call(stmt.value)):
            # The call occurrence itself is reachable even though its Python
            # result is discarded.  The capacity scan asks this question of
            # the call node, not merely of its payload expression.
            self._remember_expression(stmt.value, UNKNOWN)
            operation = stmt.value.func.attr
            kind, name = sequence_method_target(stmt.value, operation)
            key = name if kind == "local" else _state_key(name)
            if kind == "local" and isinstance(env.get(key), _RecordFact):
                raise SequencePlanError(
                    stmt.value, "a list operation observes a mutable output "
                                "record",
                    "record-builder-mutation-unsupported")
            item = self._expr(fn, stmt.value.args[0], env, bindings)
            current = env.get(
                key, self.declarations.get(name, UNKNOWN)
                if kind == "state" else UNKNOWN)
            bindings.forget_length(key)
            env[key] = (self._append(current, item, stmt.value)
                        if operation == "append" else
                        self._extend(current, item, stmt.value))
            binding_name = name if kind == "local" else f"self.{name}"
            # These calls change the source value even though they do not
            # rebind the Python name syntactically.  Treat the call as the
            # binding occurrence for the source/lowered contract bridge.
            self._remember_binding(
                fn, binding_name, env[key], occurrence=stmt.value)
            bindings.pop(binding_name, None)
            bindings.forget_numeric(binding_name)
            bindings.forget_numeric(_element_range_key(binding_name))
            bindings.forget_cells(binding_name if kind == "state" else _element_range_key(name))
            return _ContractFlow([(env, bindings)])
        if isinstance(stmt, ast.Expr):
            self._expr(fn, stmt.value, env, bindings)
            return _ContractFlow([(env, bindings)])
        if isinstance(stmt, ast.If):
            self._expr(fn, stmt.test, env, bindings)
            incoming = [(env, bindings)]
            true_states = self._condition_states_with_ranges(
                fn, stmt.test, incoming, True)
            false_states = self._condition_states_with_ranges(
                fn, stmt.test, incoming, False)
            left = self._block(
                fn, stmt.body, true_states)
            right = self._block(
                fn, stmt.orelse, false_states)
            return _ContractFlow(
                left.normal + right.normal,
                left.returned + right.returned,
                left.breaks + right.breaks,
                left.continues + right.continues)
        if isinstance(stmt, ast.For):
            return self._for_statement(fn, stmt, env, bindings)
        if isinstance(stmt, ast.While):
            return self._while_statement(fn, stmt, env, bindings)
        if isinstance(stmt, ast.Return):
            if self.state_only and fn is self.model.step:
                # The process result is irrelevant to the preliminary state
                # closure, but evaluating it is not: a helper called from the
                # return expression may update state before control leaves the
                # step.  Helper return contracts themselves remain precise,
                # since later state assignments can consume their values.
                if stmt.value is not None:
                    self._expr(fn, stmt.value, env, bindings)
                returned = _ContractReturn(
                    stmt,
                    UNKNOWN,
                    state=tuple(sorted(_state_contracts_of(env).items())),
                    state_ranges=tuple(sorted(
                        _state_ranges_of(bindings).items())),
                )
                return _ContractFlow(returned=[returned])
            record_name = None
            record = None
            if isinstance(stmt.value, ast.Name) and isinstance(
                    env.get(stmt.value.id), _RecordFact):
                record_name = stmt.value.id
                record = env[record_name]
                contract = record.contract
                if not contract.is_record:
                    # The same control interpreter that reached this return
                    # owns whether all fields are definitely present.
                    record.summary(stmt)
                self._remember_expression(stmt.value, contract)
            else:
                contract = (NONE if stmt.value is None else
                            self._expr(fn, stmt.value, env, bindings))
            interval = (None if stmt.value is None else
                        self._range_of(fn, stmt.value, bindings))
            batch = self._plan_return_batch(fn, stmt, env, bindings)
            self._remember_live_records(env, stmt)
            returned = _ContractReturn(
                stmt, contract, record_name, record, batch, interval,
                tuple(sorted(_state_contracts_of(env).items())),
                tuple(sorted(_state_ranges_of(bindings).items())),
                self._array_argument_facts(fn, env, bindings))
            self._remember_return(returned)
            return _ContractFlow(returned=[returned])
        if isinstance(stmt, ast.FunctionDef):
            return _ContractFlow([(env, bindings)])
        if isinstance(stmt, ast.Pass):
            return _ContractFlow([(env, bindings)])
        if isinstance(stmt, ast.Break):
            return _ContractFlow(breaks=[(env, bindings)])
        if isinstance(stmt, ast.Continue):
            return _ContractFlow(continues=[(env, bindings)])
        # Preserve soundness for syntax this structural interpreter does not
        # model: evaluate children to discover reachable calls, then forget
        # every local the statement may bind.
        bindings.lengths.clear()
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                self._expr(fn, node, env, bindings)
            if isinstance(node, ast.Name) and isinstance(
                    node.ctx, (ast.Store, ast.Del)):
                env[node.id] = UNKNOWN
                bindings.forget_length(node.id)
                bindings.pop(node.id, None)
                bindings.forget_numeric(node.id)
                bindings.forget_numeric(_element_range_key(node.id))
        return _ContractFlow([(env, bindings)])


_UNPROVED_RECORD_ITEM = object()


def _dictcomp_fields(value, owner, is_builtin, record_contract=None,
                     budget=None):
    """The exact key order of a finite computed mapping, if source-fixed."""
    fields = []

    def visit(index, bindings):
        if index == len(value.generators):
            key = _static_value(
                value.key, owner, bindings, budget=budget, is_builtin=is_builtin)
            if not isinstance(key, str):
                raise SequencePlanError(
                    value.key, "a computed output name that is not a fixed string",
                    "output-name-not-static")
            fields.append(key)
            return
        gen = value.generators[index]
        receiver = _record_items_receiver(gen.iter)
        contract = (None if receiver is None or record_contract is None else
                    record_contract(receiver))
        if contract is not None and contract.is_record:
            for field, child in contract.fields:
                planned = _record_item_target(gen.target, field, child)
                if planned is None:
                    raise SequencePlanError(
                        gen.target,
                        "a record-items target that is not a key/value pair",
                        "comprehension-target-shape-unsupported")
                fixed, target_contracts = planned
                nested = dict(bindings)
                nested.update(fixed)
                # Bind the payload name as unknown as well as the key as
                # fixed.  This shadows a same-spelled module constant without
                # claiming a heterogeneous record field has one host value.
                for name in target_contracts:
                    nested.setdefault(name, _UNPROVED_RECORD_ITEM)
                selected = True
                for condition in gen.ifs:
                    truth = static_truth(
                        condition,
                        value_of=lambda node, env: _static_value(
                            node, owner, env, budget=budget),
                        bindings=nested,
                        budget=budget)
                    if truth is None:
                        raise SequencePlanError(
                            condition,
                            "a computed output key set selected by an "
                            "unproved record-item filter",
                            "output-fields-data-dependent")
                    if not truth:
                        selected = False
                        break
                if selected:
                    visit(index + 1, nested)
            return
        values = _static_iterable(
            gen.iter, owner, bindings,
            is_builtin=lambda name: name not in bindings and is_builtin(name),
            budget=budget)
        if values is None:
            raise SequencePlanError(
                gen.iter, "a computed output key set with no fixed finite iterable",
                "output-fields-not-static")
        for item in values:
            target_values = values_for_target(gen.target, item)
            if target_values is None:
                raise SequencePlanError(
                    gen.target, "a computed output target has no fixed tuple "
                                "destructuring value",
                    "comprehension-target-shape-unsupported")
            nested = {**bindings, **target_values}
            selected = True
            for condition in gen.ifs:
                truth = static_truth(
                    condition,
                    value_of=lambda node, env: _static_value(
                        node, owner, env, budget=budget),
                    bindings=nested,
                    budget=budget)
                if truth is None:
                    raise SequencePlanError(
                        condition,
                        "a computed output key set selected by an "
                        "unproved filter",
                        "output-fields-data-dependent")
                if not truth:
                    selected = False
                    break
            if selected:
                visit(index + 1, nested)

    visit(0, {})
    return tuple(fields)


def _merge_fields(current, found, node):
    if found is None:
        raise SequencePlanError(
            node, "an output batch contains something other than a mapping",
            "output-batch-element-unsupported")
    if current is None:
        return tuple(dict.fromkeys(found))
    if set(current) != set(found):
        raise SequencePlanError(
            node, "output transactions name different fields",
            "outputs-disagree-across-returns")
    return current


def _scan_scope(fn, resolver, contracts=None, context=None):
    """Return capacities and appended mapping fields for one invocation."""
    owner = resolver.owner_of(fn)
    capacity_budget = (contracts.budget if contracts is not None
                       else SourceValueBudget())
    initial: dict[str, int] = {}
    appends: dict[str, int] = {}
    pops: set[str] = set()
    fields: dict[str, tuple | None] = {}
    # Capacity projection must reuse the context-sensitive source summary.
    # In particular, a list may grow conditionally in one loop and then bound
    # a later ``range(len(xs))``.  Counting syntax alone sees only one append;
    # the joined sequence contract already proves the finite maximum.
    lengths: dict[str, int] = {}
    if contracts is not None and context is not None:
        lengths.update({
            name: contract.maximum
            for (binding_context, name), contract
            in contracts.binding_contracts()
            if (binding_context == context
                and isinstance(contract, ValueContract)
                and contract.is_sequence)
        })
    # The public step's inputs are scalar in the current Behavior interface.
    # Helper parameters are deliberately unknown until a call-site summary
    # binds them; assuming every unresolved name is scalar was the old bug.
    is_method = id(fn) in resolver._method_ids
    parameters = helper_parameters(fn, receiver=is_method)
    if context is not None and contracts is not None:
        values: dict[str, ValueContract] = contracts.initial_contracts(
            context)
    elif context is not None:
        values = dict(zip(parameters, context[1]))
    else:
        values = ({name: SCALAR for name in parameters}
                  if fn is resolver.model.step else {})
    element_contracts: dict[str, ValueContract] = {}
    comprehensions = set()

    def fixed_at(node):
        return ({} if contracts is None or context is None else
                contracts.fixed_bindings(node, context))

    def bounds_at(node):
        return ({} if contracts is None or context is None else
                contracts.numeric_bounds(node, context))

    def value_contract(node, bindings=None):
        contextual = _value_contract(
            node, values, owner, bindings,
            call=(None if contracts is None else
                  lambda call, _env, _abstract:
                  contracts.contract_at(call, context)),
            is_builtin=lambda spelling: resolver.is_builtin(fn, spelling),
            budget=capacity_budget)
        observed = (UNKNOWN if contracts is None else
                    contracts.expression_contract(node, context))
        # A contextual static binding is more precise for one unrolled
        # comprehension copy.  Otherwise the control interpreter's joined
        # occurrence contract is authoritative because it follows reachability
        # and helper call contexts rather than this capacity walk's syntax.
        got = contextual if bindings and contextual is not UNKNOWN else observed
        return contextual if got is UNKNOWN else got

    def remember_element_contract(name, contract, node):
        if contract is INCOMPATIBLE:
            raise SequencePlanError(
                node, "a sequence contains elements of different shapes",
                "sequence-element-shape-disagrees")
        previous = element_contracts.get(name)
        previous_shape = None if previous is None else previous.fixed_shape
        shape = contract.fixed_shape
        if (previous_shape is not None and shape is not None
                and previous_shape != shape):
            raise SequencePlanError(
                node, f"the elements appended to {name} have shapes "
                      f"{previous_shape} and {shape}",
                "sequence-element-shape-disagrees")
        merged = contract if previous is None else join_contracts(
            previous, contract)
        if merged is INCOMPATIBLE:
            raise SequencePlanError(
                node, f"the elements appended to {name} have incompatible "
                      f"source value contracts {previous!r} and "
                      f"{contract!r}",
                "sequence-element-shape-disagrees")
        element_contracts[name] = merged

    def remember_element_shape(name, value, bindings=None):
        remember_element_contract(
            name,
            value_contract(value, bindings),
            value)

    def expression(node, factor):
        # Capacity is a projection of the context-sensitive source summary,
        # not a second syntactic reachability analysis.  Check every nested
        # occurrence: Python may skip a Boolean operand, an IfExp arm, or the
        # tail of a chained comparison even when its parent expression runs.
        if (contracts is not None
                and not contracts.expression_reached(node, context)):
            return
        deletion = sequence_delete_target(node)
        if deletion is not None and deletion[0] == "local":
            pops.add(deletion[1])
        # Comprehension bodies execute once per produced element.  A filter can
        # only reduce that number, so the product remains an upper bound.
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                             ast.GeneratorExp)):
            inner = factor * _comp_bound(
                node, lengths, owner,
                lambda spelling: resolver.is_builtin(fn, spelling),
                bindings=fixed_at(node),
                numeric_bounds=bounds_at(node),
                budget=capacity_budget)
            for gen in node.generators:
                expression(gen.iter, factor)
                for cond in gen.ifs:
                    expression(cond, inner)
            if isinstance(node, ast.DictComp):
                expression(node.key, inner)
                expression(node.value, inner)
            else:
                expression(node.elt, inner)
            return
        if isinstance(node, ast.Call):
            target = node.func.value if isinstance(node.func, ast.Attribute) else None
            if isinstance(target, ast.Name) and is_plain_append_call(node):
                name = target.id
                appends[name] = appends.get(name, 0) + factor
                remember_element_shape(name, node.args[0])
                got = _mapping_fields(node.args[0])
                if name not in fields:
                    fields[name] = got
                elif fields[name] is not None:
                    fields[name] = (None if got is None else _merge_fields(
                        fields[name], got, node))
            if isinstance(target, ast.Name) and is_plain_extend_call(node):
                items = value_contract(node.args[0])
                if not items.is_sequence or items.maximum is None:
                    raise SequencePlanError(
                        node, "extend has no finite iterable contract",
                        "extend-iterable-unsupported")
                name = target.id
                appends[name] = appends.get(name, 0) + factor * items.maximum
                if items.maximum and items.element is not None:
                    remember_element_contract(name, items.element, node.args[0])
            pop_target = sequence_method_target(node, "pop")
            if pop_target is not None and pop_target[0] == "local" \
                    and is_plain_pop_call(node):
                pops.add(pop_target[1])
            # Arguments and the callable are still evaluated.  Do not descend
            # into a resolved function body here: its local sequences have a
            # plan of their own, while mutation through a passed list is outside
            # SolverIR's value-array subset and lowering refuses the alias.
            expression(node.func, factor)
            for arg in node.args:
                expression(arg, factor)
            for kw in node.keywords:
                expression(kw.value, factor)
            return
        if isinstance(node, (ast.Lambda, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            return
        for child in ast.iter_child_nodes(node):
            expression(child, factor)

    def block(nodes, factor=1):
        for stmt in nodes:
            if (contracts is not None and context is not None
                    and not contracts.statement_reached(stmt, context)):
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Delete):
                for target in stmt.targets:
                    expression(target, factor)
                continue
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                value = stmt.value
                for target in targets:
                    if isinstance(target, ast.Name) and value is not None:
                        contract = value_contract(value)
                        values[target.id] = contract
                        size = _literal_list_length(value)
                        if size is None and isinstance(value, ast.ListComp):
                            size = _comp_bound(
                                value, lengths, owner,
                                lambda spelling: resolver.is_builtin(
                                    fn, spelling),
                                bindings=fixed_at(value),
                                numeric_bounds=bounds_at(value),
                                budget=capacity_budget)
                        if size is not None:
                            initial[target.id] = max(initial.get(target.id, 0), size)
                            lengths[target.id] = max(lengths.get(target.id, 0), size)
                            elements = (value.elts if isinstance(
                                value, (ast.List, ast.Tuple)) else
                                [])
                            for element in elements:
                                remember_element_shape(target.id, element)
                        if isinstance(value, ast.ListComp):
                            comprehensions.add(target.id)
                            if (contract.is_sequence
                                    and contract.maximum
                                    and contract.element is None):
                                raise SequencePlanError(
                                    value.elt,
                                    "a nonempty comprehension has no proved "
                                    "element contract",
                                    "sequence-element-contract-unresolved")
                            if (contract.is_sequence
                                    and contract.element is not None):
                                # A zero-iteration comprehension contributes
                                # no element to the sequence.  Its absent
                                # element contract is therefore neutral, not
                                # an UNKNOWN value that conflicts with a later
                                # append whose element is fully known.
                                remember_element_contract(
                                    target.id,
                                    contract.element,
                                    value.elt)
                            got = _mapping_fields(value.elt)
                            if target.id not in fields:
                                fields[target.id] = got
                            elif fields[target.id] is not None:
                                fields[target.id] = (
                                    None if got is None else _merge_fields(
                                        fields[target.id], got, value.elt))
                if value is not None:
                    expression(value, factor)
                continue
            if isinstance(stmt, ast.AugAssign):
                # ``seq += rhs`` grows one persistent carrier.  The
                # comprehension or literal on the right is a separate carrier
                # whose own bound is checked while it is inspected; account
                # here for the accumulated left-hand sequence as well.  The
                # path interpreter's binding contract remains authoritative
                # when available, so a sequence recreated in an outer loop is
                # not confused with one that survives its iterations.
                expression(stmt.target, factor)
                expression(stmt.value, factor)
                if (isinstance(stmt.target, ast.Name)
                        and isinstance(stmt.op, ast.Add)):
                    contract = value_contract(stmt.value)
                    growth = (contract.maximum
                              if contract.is_sequence else None)
                    if growth is None:
                        growth = _literal_list_length(stmt.value)
                    if growth is None and isinstance(
                            stmt.value, ast.ListComp):
                        growth = _comp_bound(
                            stmt.value, lengths, owner,
                            lambda spelling: resolver.is_builtin(
                                fn, spelling),
                            bindings=fixed_at(stmt.value),
                            numeric_bounds=bounds_at(stmt.value),
                            budget=capacity_budget)
                    if growth is not None:
                        name = stmt.target.id
                        appends[name] = (
                            appends.get(name, 0) + factor * growth)
                        lengths[name] = max(
                            lengths.get(name, 0),
                            initial.get(name, 0) + appends[name])
                        if contract.is_sequence \
                                and contract.element is not None:
                            remember_element_contract(
                                name, contract.element, stmt.value)
                continue
            if isinstance(stmt, ast.For):
                expression(stmt.iter, factor)
                bound = _bound_of(
                    stmt.iter, lengths, owner,
                    lambda spelling: resolver.is_builtin(fn, spelling),
                    bindings=fixed_at(stmt.iter),
                    numeric_bounds=bounds_at(stmt.iter),
                    budget=capacity_budget)
                missing = object()
                target_names = tuple(dict.fromkeys(
                    child.id for child in ast.walk(stmt.target)
                    if isinstance(child, ast.Name)
                    and isinstance(child.ctx, ast.Store)))
                old_contracts = {
                    name: values.get(name, missing) for name in target_names}
                iterable = value_contract(stmt.iter)
                item = _iterable_element_contract(
                    stmt.iter, iterable,
                    lambda spelling: resolver.is_builtin(fn, spelling))
                planned = contracts_for_target(stmt.target, item)
                for name in target_names:
                    if planned is None or name not in planned:
                        values.pop(name, None)
                    else:
                        values[name] = planned[name]
                block(stmt.body, factor * bound)
                for name, previous in old_contracts.items():
                    if previous is missing:
                        values.pop(name, None)
                    else:
                        values[name] = previous
                block(stmt.orelse, factor)
                continue
            if isinstance(stmt, ast.While):
                expression(stmt.test, factor)
                span = (None if contracts is None or context is None else
                        contracts.loop_span(stmt, context))
                if span is None:
                    raise SequencePlanError(
                        stmt, "the source capacity plan has no task-domain "
                              "while span",
                        "while-trip-plan-unavailable")
                block(stmt.body, factor * span.hi)
                block(stmt.orelse, factor)
                continue
            if isinstance(stmt, ast.If):
                expression(stmt.test, factor)
                # Sum is conservative: the arms are mutually exclusive, but
                # reserving both can never make the fixed array too short.
                body_falls = block(stmt.body, factor)
                else_falls = (block(stmt.orelse, factor)
                              if stmt.orelse else True)
                if not body_falls and not else_falls:
                    return False
                continue
            if isinstance(stmt, ast.Return):
                if stmt.value is not None:
                    expression(stmt.value, factor)
                return False
            if isinstance(stmt, (ast.Break, ast.Continue)):
                return False
            expression(stmt.value if isinstance(stmt, ast.Expr) else stmt,
                       factor)
        return True

    block(fn.body)
    specs = {}
    planned = (set(initial) & (set(appends) | pops)) | comprehensions
    for name in sorted(planned):
        capacity = initial[name] + appends.get(name, 0)
        element = element_contracts.get(name)
        observed = (UNKNOWN if contracts is None else
                    contracts.binding_contract(fn, name, context))
        if observed is INCOMPATIBLE:
            raise SequencePlanError(
                fn, f"{name} has incompatible contracts on reachable paths",
                "sequence-value-contract-disagrees")
        if observed.is_sequence:
            # The control interpreter owns the logical length after every
            # reached binding and loop summary.  The syntax projection above
            # remains a fallback for unresolved contracts, but multiplying
            # lexical visit counts is not a valid capacity for a sequence
            # recreated inside an outer loop.
            capacity = max(initial.get(name, 0), observed.maximum)
            element = observed.element
        capacity = finite_cardinality_add(capacity)
        specs[(context if context is not None else id(fn), name)] = _Spec(
            capacity, fields.get(name), element=element,
            is_mutated=(name in appends or name in pops))
    return specs, lengths


def _walk_batch_plan(plan):
    """Yield a frozen batch plan in source evaluation order."""
    yield plan
    for child in plan.children:
        yield from _walk_batch_plan(child)


def _output_boundary_layout(
        contracts, context, specs, resolver, declared_outputs=(),
        declared_output_contracts=None):
    """Derive storage solely from reached, activation-specific return plans.

    Control reachability, schemas and count bounds already belong to
    ``_FunctionContracts`` and ``BatchPlan``.  This projection chooses storage
    names and marks source locals that need the parallel-array representation;
    it performs no second walk over return syntax.
    """
    plans = dict(contracts.return_plans(context))
    returned = contracts.returns(context)
    missing = next((fact for fact in returned
                    if fact.batch is None
                    and fact.contract is INCOMPATIBLE), None)
    if missing is not None:
        raise SequencePlanError(
            missing.node,
            "a returned value lost its finite source representation",
            "source-plan-inconsistent",
        )
    if plans:
        missing = next((fact for fact in returned
                        if fact.batch is None), None)
        if missing is not None:
            raise SequencePlanError(
                missing.node,
                "some reached returns are transaction batches and others "
                "are not",
                "output-batch-arm-disagrees",
            )
    # One occurrence can be reached through several retained control paths.
    # Its joined plan, not the last path visited, owns the backing capacity.
    capacities = {id(fact.node): 1 for fact in returned}
    capacities.update((key, plan.contract.maximum)
                      for key, plan in plans.items())

    schema = None
    for plan in plans.values():
        candidate = plan.contract.transaction
        if candidate is None:
            continue
        try:
            schema = join_transaction_schemas(
                schema, candidate, plan.node)
        except BatchContractError as exc:
            raise SequencePlanError(
                exc.node, exc.what, exc.code) from None

    declared_output_contracts = dict(declared_output_contracts or {})
    # The legacy flat Behavior boundary declares output names rather than
    # explicit ValueTypes; each such leaf is a scalar.  Typed bindings replace
    # these defaults with their recursive contracts.  Either way the boundary
    # schema exists even when this bounded horizon reaches only empty returns.
    boundary_contracts = {
        name: declared_output_contracts.get(name, SCALAR)
        for name in declared_outputs
    }
    declared_schema = (ValueContract.record(boundary_contracts)
                       if boundary_contracts else None)
    # A bounded domain may contain no output-producing step even though the
    # complete source can emit transactions later.  The task signature still
    # fixes the physical boundary in that domain; it is the authority for an
    # otherwise unobserved empty batch, never a replacement for an observed
    # source schema.
    if schema is None:
        schema = declared_schema

    output_fields = tuple(declared_outputs or ())
    if schema is not None:
        try:
            output_fields = BatchContract.one(schema).ordered_fields(
                output_fields)
        except ValueError as exc:
            raise SequencePlanError(
                contracts.function_of(context), str(exc),
                "outputs-disagree-across-returns") from None

    batch_names = set()
    computed_keys = {}
    fn = contracts.function_of(context)
    owner = resolver.owner_of(fn)
    for root in plans.values():
        for plan in _walk_batch_plan(root):
            if isinstance(plan.node, ast.DictComp):
                keys = _dictcomp_fields(
                    plan.node, owner,
                    lambda spelling: resolver.is_builtin(fn, spelling),
                    record_contract=lambda occurrence: (
                        contracts.expression_contract(
                            occurrence, context)),
                    budget=contracts.budget)
                computed_keys[id(plan.node)] = keys
            if not isinstance(plan.node, ast.Name) \
                    or plan.kind not in {"sequence", "summary"}:
                continue
            name = plan.node.id
            batch_names.add(name)
            key = (context, name)
            previous = specs.get(key)
            fields = plan.contract.field_names
            element = (previous.element if previous is not None else
                       plan.contract.transaction)
            if not fields and element is None and declared_schema is not None:
                fields = tuple(name for name, _contract in declared_schema.fields)
                element = declared_schema
            specs[key] = _Spec(
                plan.contract.maximum, fields, is_batch=True,
                is_growing=(previous.is_growing
                            if previous is not None else False),
                is_mutated=(previous.is_mutated
                            if previous is not None else False),
                element=element,
            )
    return (output_fields, schema, batch_names, capacities, computed_keys,
            plans)


def _contains_sequence(contract):
    if not isinstance(contract, ValueContract):
        return False
    if contract.is_sequence:
        return True
    if contract.is_option:
        return _contains_sequence(contract.payload)
    if contract.is_record:
        return any(_contains_sequence(child)
                   for _name, child in contract.fields)
    return False


def _source_state_ranges(fields, params, input_ranges):
    """Source attributes use qualified keys, separate from same-named inputs.

    Unqualified state facts remain unambiguous only without that parameter.
    """
    facts = dict(input_ranges or {})
    state = {}
    for name in fields:
        qualified = f"self.{name}"
        if qualified in facts:
            state[name] = facts[qualified]
        elif name not in params and name in facts:
            state[name] = facts[name]
    return state


def _bounded_state_contracts(
        model, resolver, declarations, declaration_contracts,
        input_contracts, input_ranges, state_bound, budget, query_cache):
    """Close state representations and scalar ranges over a finite horizon."""
    current = dict(declaration_contracts or {})
    input_ranges = dict(input_ranges or {})
    mutable = tuple(model.step_state_contract())
    current_ranges = _source_state_ranges(mutable, model.inputs, input_ranges)
    entries = ((current, current_ranges),)
    # With no mutable state, the bounded closure is already complete.  This
    # is the safe performance boundary for combinational procedures: skipping
    # individual loops in a stateful procedure is unsound because a local
    # computed by the loop may later determine a state write or whether that
    # write is reached.
    if not mutable:
        return current, current, current_ranges, current_ranges, entries
    if state_bound is None:
        return current, current, current_ranges, current_ranges, None
    if type(state_bound) is not int or state_bound < 0:
        raise TypeError("a source state bound must be a nonnegative integer")
    if state_bound == 0:
        return current, current, current_ranges, current_ranges, entries

    params = tuple(model.inputs)
    input_contracts = dict(input_contracts or {})
    step_args = tuple(input_contracts.get(name, SCALAR) for name in params)
    root_ranges = tuple(
        dict(input_ranges or {}).get(name) for name in params)

    step_entry = current
    step_entry_ranges = current_ranges
    step_entries = entries
    for _step in range(state_bound):
        step_entry = current
        step_entry_ranges = current_ranges
        step_entries = entries
        iteration_ranges = dict(input_ranges)
        iteration_ranges.update({f"self.{name}": value
                                 for name, value in current_ranges.items()})
        transfer = _FunctionContracts(
            model, resolver, declarations, current,
            input_ranges=iteration_ranges, budget=budget,
            query_cache=query_cache, state_only=True, state_entries=entries)
        state = {name: current[name] for name in mutable if name in current}
        context = transfer.context_key(
            model.step, step_args, tuple(None for _ in step_args), (),
            root_ranges, state, current_ranges)
        transfer.summarize(
            model.step, step_args, tuple(None for _ in step_args),
            argument_ranges=root_ranges, state_contracts=state,
            state_ranges=current_ranges)
        post_entries = transfer.final_state_entries(context)
        post_ranges = transfer.final_state_ranges(context)
        widened_entries = _join_state_entries(
            (*entries, *post_entries), budget, model.step)
        widened = dict(current)
        changed = False
        for name in mutable:
            values = [state[name] for state, _intervals in post_entries if name in state]
            if not values:
                continue
            after = values[0]
            for value in values[1:]:
                after = join_contracts(after, value)
            before = current.get(name)
            joined = after if before is None else join_contracts(before, after)
            if joined is INCOMPATIBLE:
                raise SequencePlanError(
                    model.step,
                    f"the state {name!r} has incompatible recursive values "
                    "on reachable bounded steps "
                    f"({before!r} versus {after!r})",
                    "state-value-contract-disagrees")
            if joined != before:
                widened[name] = joined
                changed = True
        current = widened
        widened_ranges = {}
        for name, before in current_ranges.items():
            after = post_ranges.get(name)
            if after is not None:
                widened_ranges[name] = before.join(after)
        if widened_ranges != current_ranges:
            changed = True
        current_ranges = widened_ranges
        if widened_entries != entries:
            changed = True
        entries = widened_entries
        if not changed:
            break
    # ``current`` contains the boundary values S_0..S_N. The last
    # transition is entered from S_0..S_{N-1}; using S_N as another input
    # invents an (N+1)st append and can falsely enlarge an intermediate value.
    return current, step_entry, current_ranges, step_entry_ranges, step_entries


def _plan_sequences(model, resolver, declared_outputs=(), declarations=None,
                    declaration_contracts=None, input_contracts=None,
                    input_ranges=None, declared_output_contracts=None,
                    state_bound=None,
                    budget=None):
    """Build finite sequence plans for the step and reachable helpers only."""
    budget = budget if budget is not None else SourceValueBudget()
    params = tuple(model.inputs)
    input_contracts = dict(input_contracts or {})
    unknown_inputs = tuple(sorted(set(input_contracts) - set(params)))
    if unknown_inputs:
        raise SequencePlanError(
            model.step, "input contracts name parameters absent from the "
                        f"step signature: {unknown_inputs!r}",
            "signature-inputs-mismatch")
    if any(not isinstance(contract, ValueContract)
           for contract in input_contracts.values()):
        raise TypeError("input contracts must be source ValueContracts")
    declared_outputs = tuple(declared_outputs or ())
    declared_output_contracts = dict(declared_output_contracts or {})
    if declared_output_contracts:
        if set(declared_output_contracts) != set(declared_outputs):
            raise ValueError(
                "declared output contracts must match the declared outputs")
        if any(not isinstance(contract, ValueContract)
               for contract in declared_output_contracts.values()):
            raise TypeError("output contracts must be source ValueContracts")
    query_cache = _SourceQueryCache()
    (declaration_contracts, step_state_contracts, _state_ranges,
     step_state_ranges, step_entries) = _bounded_state_contracts(
        model, resolver, declarations, declaration_contracts,
        input_contracts, input_ranges, state_bound, budget, query_cache)
    step_input_ranges = dict(input_ranges or {})
    step_input_ranges.update({f"self.{name}": value
                              for name, value in step_state_ranges.items()})
    contracts = _FunctionContracts(
        model, resolver, declarations, declaration_contracts,
        input_ranges=step_input_ranges, budget=budget,
        query_cache=query_cache, state_entries=step_entries)
    step_args = tuple(input_contracts.get(name, SCALAR) for name in params)
    root_ranges = tuple(
        dict(input_ranges or {}).get(name) for name in params)
    root_context = contracts.context_key(
        model.step, step_args, tuple(None for _ in step_args), (),
        root_ranges, step_state_contracts, step_state_ranges)
    contracts.summarize(model.step, step_args,
                        tuple(None for _ in step_args),
                        argument_ranges=root_ranges,
                        state_contracts=step_state_contracts,
                        state_ranges=step_state_ranges)
    # Discovery is deterministic source evaluation order. Stable sorting keeps
    # that order between activations of one helper without rendering their
    # (possibly very large) integer ranges as decimal strings merely to sort.
    contexts = sorted(
        contracts.contexts(),
        key=lambda context: (
            contracts.function_of(context).lineno,
            contracts.function_of(context).col_offset,
            contracts.function_of(context).name,
        ))
    context_order = {context: index for index, context in enumerate(contexts)}
    functions = sorted(contracts.reachable,
                       key=lambda fn: (fn.lineno, fn.col_offset, fn.name))

    # Validate represented variable-length list operations before capacity
    # planning. ``append`` and ``extend`` return None, for which SolverIR has no
    # ordinary scalar value; ``pop`` returns the removed element and may be
    # used in an expression.
    for fn in functions:
        for node in _same_scope(fn):
            append = is_append_call(node)
            extend = is_extend_call(node)
            pop = is_pop_call(node)
            if not append and not extend and not pop:
                continue
            if not contracts.expression_reached(node):
                continue
            if append and not is_plain_append_call(node):
                raise SequencePlanError(
                    node, "append with anything other than one positional "
                          "argument", "call-argument-form")
            pop_target = sequence_method_target(node, "pop") if pop else None
            mapping_pop = (pop_target is not None and pop_target[0] == "state"
                           and declaration_contracts.get(pop_target[1], UNKNOWN).is_mapping
                           and has_plain_positional_arity(node, 1, 2))
            if pop and not is_plain_pop_call(node) and not mapping_pop:
                raise SequencePlanError(
                    node, "pop with anything other than one optional "
                          "positional index", "call-argument-form")
            if extend and not is_plain_extend_call(node):
                raise SequencePlanError(
                    node, "extend requires one positional iterable",
                    "call-argument-form")
            parent = resolver._parents.get(id(node))
            if (append or extend) and not (
                    isinstance(parent, ast.Expr) and parent.value is node):
                raise SequencePlanError(
                    node, f"the result of {node.func.attr} is used as a value",
                    "append-result-used" if append else "extend-result-used")

    specs = {}
    for context in contexts:
        fn = contracts.function_of(context)
        got, _lengths = _scan_scope(fn, resolver, contracts, context)
        specs.update(got)

    # Helper-returned transaction lists are first-class source values.  Their
    # assignment plans, not a later return-syntax scan, own the local storage
    # representation.  An existing append-built spec retains its growing
    # flag; merely having a Python list carrier does not imply mutation.
    for context, name in sorted(
            contracts.batch_binding_names(),
            key=lambda item: (context_order[item[0]], item[1])):
        fn = contracts.function_of(context)
        batch_contract = contracts.binding_batch_contract(context, name)
        if batch_contract is None:
            contract = contracts.binding_contract(fn, name, context)
            payload = record_batch_payload(contract)
            if payload is None:
                raise SequencePlanError(
                    fn, f"the batch local {name} has no one finite "
                        "list-of-records contract",
                    "source-plan-inconsistent")
            maximum = payload.maximum
            transaction = payload.element
        else:
            maximum = batch_contract.maximum
            transaction = batch_contract.transaction
            if transaction is None:
                continue
        key = (context, name)
        previous = specs.get(key)
        fields = tuple(transaction.field_contracts)
        if (previous is not None and previous.fields is not None
                and set(previous.fields) != set(fields)):
            raise SequencePlanError(
                fn, f"the batch local {name} changes transaction schema",
                "outputs-disagree-across-returns")
        specs[key] = _Spec(
            max(maximum,
                0 if previous is None else previous.capacity),
            fields, is_batch=True,
            is_growing=False if previous is None else previous.is_growing,
            is_mutated=False if previous is None else previous.is_mutated,
            element=transaction)

    planned_records = {}
    planned_record_ranges = {}
    record_builders = set()
    planned_mapping_keys = {}
    for (record_context, name), summary in contracts.record_summaries():
        planned_records[(record_context, name)] = summary.contract
        planned_record_ranges[(record_context, name)] = dict(
            summary.field_ranges)
        record_builders.add((record_context, name))
        occurrences = tuple(dict.fromkeys(
            write.occurrence for write in summary.writes
            if isinstance(write.occurrence, ast.Subscript)))
        for occurrence in occurrences:
            cases = list(summary.key_cases(occurrence))
            by_selector = {}
            for field, selector in cases:
                previous = by_selector.setdefault(selector, field)
                if previous != field:
                    raise SequencePlanError(
                        occurrence, "one finite output selector names "
                                    "different fields",
                        "output-key-plan-inconsistent")
            distinct = {field for field, _selector in cases}
            if len(distinct) == 1:
                cases = [(next(iter(distinct)), ())]
            elif any(not selector or any(
                    type(value) not in (bool, int)
                    for _variable, value in selector)
                    for _field, selector in cases):
                raise SequencePlanError(
                    occurrence, "a computed output selector has no finite "
                                "integer representation",
                    "output-key-selector-not-scalar")
            planned_mapping_keys[(record_context, id(occurrence))] = tuple(cases)

    (output_fields, output_contract, batches, capacities, computed_keys,
     _return_plans) = _output_boundary_layout(
         contracts, root_context, specs, resolver, declared_outputs,
         declared_output_contracts)
    records = planned_records
    mapping_keys = planned_mapping_keys
    for name in batches:
        spec = specs[(root_context, name)]
        specs[(root_context, name)] = _Spec(
            spec.capacity, spec.fields, is_batch=True,
            is_growing=spec.is_growing, is_mutated=spec.is_mutated,
            element=spec.element)
    all_return_plans = contracts.all_return_plans()
    all_batch_plans = contracts.all_batch_plans()
    reached_returns = tuple(
        id(fact.node) for fact in contracts.returns(root_context))
    # A queue can append its final item and drain in the same activation.
    # That peak is absent from both boundary states, but the executing IR
    # still needs room for it. Reuse the reached binding facts (including
    # helper mutations), without feeding the storage envelope back into the
    # boundary-state fixed point or enlarging the semantic input domain.
    state_storage = dict(declaration_contracts)
    for (context, name), contract in contracts.binding_contracts():
        if not name.startswith("self."):
            continue
        field = name[5:]
        joined = (join_contracts(state_storage[field], contract)
                  if field in state_storage else contract)
        if joined is INCOMPATIBLE:
            raise SequencePlanError(
                contracts.function_of(context),
                f"the state {field!r} has incompatible in-step storage values",
                "state-value-contract-disagrees")
        state_storage[field] = joined
    return SequencePlan(
        specs=specs.items(), return_capacity=capacities.items(),
        computed_keys=computed_keys.items(), mapping_keys=mapping_keys.items(),
        output_fields=output_fields, records=records.items(),
        record_builders=record_builders, return_plans=all_return_plans,
        batch_plans=all_batch_plans,
        binding_batch_plans=contracts.all_binding_batch_plans(),
        binding_batch_contracts=contracts.all_binding_batch_contracts(),
        call_contracts=contracts.call_contracts(),
        call_contexts=contracts.call_contexts(),
        call_targets=contracts.call_targets(),
        binding_contracts=contracts.binding_contracts(),
        binding_occurrence_contracts=contracts.binding_occurrence_contracts(),
        expression_contracts=contracts.expression_contracts(),
        expression_ranges=contracts.expression_ranges(),
        initial_ranges=contracts.initial_ranges(),
        loop_spans=contracts.loop_spans(),
        loop_domains=contracts.loop_domains(),
        context_functions=contracts.context_functions(),
        root_context=root_context, output_contract=output_contract,
        state_contracts=declaration_contracts.items(),
        state_storage_contracts=state_storage.items(),
        reached_returns=reached_returns,
        record_ranges=planned_record_ranges.items(),
        statement_contexts=contracts.statement_contexts(),
        branch_outcomes=contracts.branch_outcomes())


def plan_sequences(model, resolver, declared_outputs=(), declarations=None,
                   declaration_contracts=None, input_contracts=None,
                   input_ranges=None, *, declared_output_contracts=None,
                   state_bound=None,
                   budget=None):
    """Build one bounded, context-sensitive source representation plan."""
    try:
        return _plan_sequences(
            model, resolver, declared_outputs, declarations,
            declaration_contracts, input_contracts, input_ranges,
            declared_output_contracts,
            state_bound=state_bound, budget=budget)
    except SourceValueBudgetExceeded as exc:
        raise SequencePlanError(
            exc.node, "source representation planning exceeded its aggregate "
                      "work budget",
            "source-plan-budget-exceeded") from None
