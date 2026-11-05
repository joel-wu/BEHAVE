"""Encode one shared SolverIR transition as a single formula.

Concolic execution follows one path, so what it learns about a goal it did not
reach is nothing. A formula built from the syntax covers every path at once,
which is what lets an unsatisfiable query mean that no stimulus reaches a goal
rather than that the paths tried did not. This module builds that formula.

A command's symbolic result is four things: the condition under which it
completes, the valuation it leaves when it does, the error it carries when it
does not, and, for each goal, the condition under which the step reaches it.
Conditionals encode both arms and merge their changed fields with ``ite``.
Writes inside an arm use its entry condition; the join retains the inactive
state once, rather than at every assignment. A semantic loop keeps one body in
the IR; its EncodingPlan supplies the finite domain-relative maximum, and this
module emits that many guarded copies. Unreached commands leave the state alone
and contribute no hit. No path is enumerated: one formula covers all of them.

Widths come from the range analysis. A subterm is represented at a width
injective on the values it can hold, so nothing wraps unless the source wrote a
mask, and a subterm whose range is unknown has no width and the step is refused
rather than encoded at a guess.
"""

from __future__ import annotations

import operator
from contextlib import contextmanager

import z3

from ..symbolic.exact_formula import ExactFormula, FormulaDefinition
from ..core.construction_checkpoint import ConstructionCheckpoint
from . import ir, primitives
from .bitvectors import (
    widen as widen,
    _extend as _extend,
    _floor_div as _floor_div,
    _floor_mod as _floor_mod,
    _positive_power_of_two as _positive_power_of_two,
)
from . import ranges
from . import typecheck as value_types
from .capabilities import SCALAR_BITS, TERM_BUDGET
from .capabilities import scalar_magnitude_bits
from .functional_array import FunctionalArray, _ArraySelection
from .validation import (
    EncodingError,
    _ProcedureValidation as _ProcedureValidation,
    _checked_procedure_validation,
    _procedure_validation,
    validate_procedure,
)

__all__ = ["COUNT", "ERROR_CODES", "EncodingError", "Fault", "StepEncoding",
           "decode_value", "encode_concrete_value", "encode_goal_query", "encode_step", "error_name",
           "validate_procedure", "validated_width",
           "verify_encoding", "widen"]

#: The name the transaction count is reported under in a step's post state. A
#: name of its own rather than the lowering's variable, because it is part of
#: what a step is observed to do and the other side has to be able to name the
#: same thing without knowing how this side computed it.
COUNT = "#count"

#: Compatibility alphabet for expression-level encoder unit tests that do not
#: carry a Procedure.  Production encoding replaces it with the validated
#: ProcedureContract alphabet before any command is encoded.
#:
#: It is an index into this tuple in the formula, not a string. The appendix
#: says the encoding is QF_BV and it was not: `z3.StringVal` brings in the
#: theory of strings, which is not a bitvector theory, and a solver asked a
#: question in QF_BV about a formula that is not in it is being asked something
#: other than what was claimed. Nothing needed the string; what the error is
#: compared for is equality, and an index compares the same way.
ERROR_CODES = ("", "DivZero", "NegativeShift", "IndexError", "KeyError",
               "CapacityExceeded", "Unwound", "AssertionError")

#: Which code the model raising a given exception corresponds to, where the two
#: are talking about the same event.
#:
#: The model audit could only compare whether a step failed, because the model
#: raises a Python exception and this side halts with a code, and nothing said
#: they were the same thing. Two guards firing on one stimulus in different
#: orders halt with different codes, so "both failed" is a comparison that passes
#: on a step that failed for the wrong reason.
#:
#: Read off the guards rather than guessed: `//` and `%` are guarded against a
#: zero divisor, a subscript against being out of range, a sparse table against a
#: missing key. `IndexError` and `KeyError` are the names Python uses for the same
#: events, which is why they are the codes.
EXCEPTION_CODES = {
    "AttributeError": "AttributeError",
    "AssertionError": "AssertionError",
    "ZeroDivisionError": "DivZero",
    "ValueError": "ValueError",
    "OverflowError": "OverflowError",
    "IndexError": "IndexError",
    "KeyError": "KeyError",
}

DEFAULT_WIDTH = 64


def _error_width(codes):
    return max(1, (len(codes) - 1).bit_length())


def _code(name, codes=ERROR_CODES):
    """The bitvector standing for an error code.

    A code this does not know is refused rather than widened into the encoding,
    since a failure the formula cannot name is one a query cannot ask about.
    """
    try:
        return z3.BitVecVal(codes.index(name), _error_width(codes))
    except ValueError:
        raise EncodingError(f"{name} is not one of the declared error codes: "
                            f"{', '.join(codes[1:])}") from None


def error_name(value, codes=ERROR_CODES):
    """The code a solver's value for an error term stands for."""
    index = value.as_long() if hasattr(value, "as_long") else int(value)
    return codes[index] if 0 <= index < len(codes) else f"?{index}"


def _brief(obj, width=60):
    """A short printable form, for a message about a term.

    `f"{e!r:.60}"` builds the whole repr and then truncates it, and a model in the
    population holds an integer whose decimal form is past Python's own limit on
    printing one, so the message about the term raised instead of describing it. A
    diagnostic that fails on the thing it is diagnosing is worse than no diagnostic.
    """
    try:
        text = repr(obj)
    except ValueError:
        return f"<{type(obj).__name__}, too large to print>"
    return text[:width]


class Fault:
    """One reason a step cannot be encoded, as a record rather than as a raise.

    `kind` is `range`, `shape`, `type`, `width`, or `capability`.  The first
    three are M3's completeness questions; the last two are separately
    reported capabilities rather than unresolved ranges.
    """

    __slots__ = ("kind", "message")

    def __init__(self, kind, message):
        self.kind = kind
        self.message = message

    def as_dict(self):
        return {"kind": self.kind, "message": self.message}

    def __repr__(self):
        return f"{self.kind}: {self.message[:70]}"


class StepEncoding:
    """The formula of one step, with the variables it reads and writes."""

    __slots__ = (
        "_constraint",
        "array_selections",
        "definitions",
        "error",
        "error_codes",
        "final",
        "hit_occurrences",
        "hits",
        "ok",
        "output_checks",
        "output_valid",
        "post",
        "pre",
        "restrictions",
        "state_post",
        "widths",
    )

    def __init__(self, pre, post, hits, ok, error, constraint, widths,
                 final=None, output_checks=None, state_post=None,
                 error_codes=ERROR_CODES, definitions=(), restrictions=(),
                 hit_occurrences=None, array_selections=()):
        self.array_selections = tuple(array_selections)
        self.pre = pre                  # variable -> z3 term of the state read
        self.post = post                # variable -> z3 term of the state left
        # Kept separately because a public output may have the same spelling as
        # an instance-state field.  ``post`` is the public combined view; only
        # this map may be chained into the next step.
        self.state_post = dict(state_post or {})
        self.hits = hits                # goal -> z3 condition of reaching it
        occurrences = {} if hit_occurrences is None else hit_occurrences
        if type(occurrences) is not dict or any(
                type(goal) is not str or type(values) not in (tuple, list)
                for goal, values in occurrences.items()):
            raise TypeError("goal occurrences are a mapping to finite sequences")
        if set(occurrences) - set(hits):
            raise ValueError("goal occurrences belong to supported goals")
        self.hit_occurrences = {
            goal: tuple(occurrences.get(goal, ())) for goal in hits
        }
        self.ok = ok                    # condition under which the step completes
        self.error = error              # error code carried when it does not
        self.error_codes = tuple(error_codes)
        self._constraint = constraint   # the definitions, and the contract
        # Exact producer metadata for the frontend-neutral definitional seam.
        # Keeping it beside ``constraint`` prevents a consumer from guessing
        # which equalities are definitions after Z3 has flattened the formula.
        self.definitions = tuple(definitions)
        self.restrictions = tuple(restrictions)
        self.widths = widths
        # Every variable the step leaves, not only the ones it exports. What a
        # step computes on the way is what an audit against a run of the
        # representation compares, and the first variable the two differ on is
        # the rule to read.
        self.final = dict(final or {})
        # Conditions a normally completing step must satisfy at its declared
        # output boundary.  They deliberately are *not* conjoined to
        # ``constraint``: doing that would erase an illegal execution and let
        # an UNSAT query certify the filtered program.  The admission query
        # instead asks whether ``ok and not output_valid`` is satisfiable.
        self.output_checks = dict(output_checks or {})
        self.output_valid = (z3.And(tuple(self.output_checks.values()))
                             if self.output_checks else z3.BoolVal(True))

    @property
    def constraint(self):
        """Materialize the complete SSA relation only when it is consumed.

        Expression templates substitute the defining RHSs instead of asserting
        their equalities. They still own every definition and restriction; a
        consumer asking for the relation gets the same complete conjunction.
        """
        if self._constraint is None:
            self._constraint = z3.And(
                *(definition.assertion for definition in self.definitions),
                *self.restrictions)
        return self._constraint

    def exact_formula(self):
        """Return this standalone step's registered exact formula.

        Fresh input and state leaves are the independently assignable boundary
        atoms.  Every internal name is already registered by ``bind`` and every
        interface restriction is retained separately.  If a caller supplied a
        non-boundary pre-state term, ExactFormula's audit conservatively falls
        back to the complete formula rather than guessing its origin.
        """
        return ExactFormula(
            definitions=self.definitions,
            restrictions=self.restrictions,
            boundary_atoms=self._formula_boundary_atoms(),
        )

    def _query_formula(self, *, checkpoint):
        """Register a freshly encoded local query, before handing it to callers.

        Only the closed encoder's product uses ordered QF_BV registration.
        Ordinary ``exact_formula`` callers keep the generic audit. Both paths
        retain every hard restriction and fall back on an unregistered term.
        No formula is cached on this mutable StepEncoding object.
        """
        return ExactFormula._from_generated_qfbv(
            definitions=self.definitions,
            restrictions=self.restrictions,
            boundary_atoms=self._formula_boundary_atoms(checkpoint),
            _checkpoint=checkpoint,
        )

    def _start_query_formula(self, *, checkpoint, prefix=None):
        """Own registration of an ordered product, optionally extending a prefix."""
        from ..symbolic.exact_formula import _FormulaConstruction

        return _FormulaConstruction(
            self.definitions, self.restrictions, self._formula_boundary_atoms(checkpoint),
            prefix=prefix)

    def _formula_boundary_atoms(self, checkpoint=None):
        boundary = {}
        pending = list(self.pre.values())
        while pending:
            if checkpoint is not None:
                checkpoint()
            value = pending.pop()
            if isinstance(value, FunctionalArray):
                pending.extend(value.formula_terms)
                continue
            if isinstance(value, list):
                pending.extend(value)
                continue
            if z3.is_app(value) and value.decl().kind() in (
                    z3.Z3_OP_SIGN_EXT, z3.Z3_OP_ZERO_EXT):
                # Injective carrier wiring preserves its underlying free
                # boundary atom. Do not descend arbitrary supplied terms:
                # their arithmetic/definition dependencies still need audit.
                pending.append(value.arg(0))
                continue
            if z3.is_const(value) \
                    and value.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                boundary[value.get_id()] = value
        return tuple(boundary.values())

    @property
    def goals(self):
        """Exactly the goals this encoding can answer about.

        The supported set, stated, because the alternative is that every caller infers it
        from whether the answer came back false -- and "false" is also what a goal the
        step genuinely cannot reach looks like.
        """
        return frozenset(self.hits)

    def supports(self, goal):
        return goal in self.hits

    def goal_condition(self, goal):
        """The condition under which this step reaches ``goal``.

        **Raises for a goal this encoding does not have**, and that is the whole point.
        It used to return `BoolVal(False)`, which a caller then asserted and got `unsat`
        from -- reported as `LOCAL_UNSAT`, "no admissible input reaches it from this
        snapshot". That is an absence dressed as a negative answer: the step was never
        asked about the goal, and saying nothing reaches it is a claim the query does not
        support. `exact.py` even had a guard for it, `if condition is None`, which could
        never fire because nothing ever returned `None`.

        A value for a caller that can proceed with a non-answer, an exception for one that
        cannot -- and a caller asking for a condition it is about to assert cannot. Ask
        `supports` first, and report the goal as unsupported rather than unreachable.
        """
        if goal not in self.hits:
            raise EncodingError(
                f"{goal!r} is not a goal this step encodes, so there is no condition to "
                f"assert; it encodes {sorted(self.hits)[:6]}"
                f"{' and more' if len(self.hits) > 6 else ''}",
                kind="goal")
        return self.hits[goal]

    def goal_components(self, goal):
        """The occurrence conditions whose disjunction is one public hit bit."""

        if goal not in self.hits:
            self.goal_condition(goal)  # preserve the typed unsupported diagnostic
        values = self.hit_occurrences[goal]
        return values if values else (z3.BoolVal(False),)


def _bits(rng):
    """A width injective on ``rng``, with room for a sign.

    Every term here is read as a signed bitvector: widening sign-extends and
    comparison is signed. So the width has to carry a sign bit even for a
    range that never goes negative, or the largest value of that range would
    read as a negative number and a range check would fire on it.
    """
    return ranges.width_for(rng, signed=True)


def _const(value, width):
    return z3.BitVecVal(value, width)


def validated_width(rng, what="a scalar"):
    """The finite carrier width for ``rng``, before any solver allocation."""
    if rng is None:
        raise EncodingError(f"{what} has no range, so it has no width",
                            kind="range")
    width = ranges.encoding_width(rng)
    # Public values have SCALAR_BITS magnitude bits; their signed encoding
    # already owns one more bit. A reusable template spans that entire carrier,
    # including -2**SCALAR_BITS, without allocating any additional bits.
    # Source/literal admission still checks the declared magnitude capability.
    if width > SCALAR_BITS + 1:
        raise EncodingError(
            f"{what} needs {width} signed carrier bits, over the "
            f"scalar encoding capacity of {SCALAR_BITS + 1}", kind="width")
    return width


def _at(index, j, n):
    """Whether ``index`` names cell ``j`` of an array of ``n``.

    Compared at a width holding both. An index written as a literal carries
    only the width its own value needs, and asking at that width would make
    every cell number that agrees on those bits look like a match.

    An index the step already knows is answered rather than asked about. This is
    not a special case for speed: the question is whether cell ``j`` is the one,
    and where the index is a number that has an answer, so returning a formula
    for it hands the solver a fact it was given. What it costs to do otherwise is
    the whole of the array cost: a store builds a term per cell, so a table of
    sixteen written in a loop of sixteen is two hundred and fifty-six guarded
    updates of which sixteen can happen, and after unrolling nearly every index
    into a table is a literal.
    """
    if isinstance(index, z3.BitVecNumRef):
        return z3.BoolVal(index.as_signed_long() == j)
    width = max(index.size(), _bits(ranges.Range(0, max(n - 1, 0))), 1)
    return _extend(index, width) == _const(j, width)


def _choose(cond, new, old):
    """``new`` where ``cond`` holds and ``old`` where it does not.

    A conditional only where the condition is one. Every guarded write goes
    through here, so a condition the encoding already knows the value of costs
    nothing rather than costing a node and a definition.
    """
    if z3.is_true(cond):
        return new
    if z3.is_false(cond):
        return old
    return z3.If(cond, *widen(new, old))


def _copy_tree(value):
    if isinstance(value, FunctionalArray):
        return value
    if isinstance(value, list):
        return [_copy_tree(item) for item in value]
    return value


def _materialize_functional(value):
    """Expose a compact scalar array as its exact componentwise value.

    ``FunctionalArray`` is an encoder optimization, not a distinct SolverIR
    value shape.  A control join may pair a compact uniform array with an
    ordinary componentwise array; materializing only at that representation
    boundary preserves the source array semantics without making compactness
    an admission requirement.
    """
    if isinstance(value, FunctionalArray):
        width = max(1, value.length.bit_length() + 1)
        return [value.select(_const(index, width))
                for index in range(value.length)]
    return value


def _flatten_tree(value):
    if isinstance(value, FunctionalArray):
        yield from value.data_terms
        return
    if isinstance(value, list):
        for item in value:
            yield from _flatten_tree(item)
    else:
        yield value


def _supplied_state_range(value):
    """Literal values stay exact; unknown data keeps its full signed carrier.

    A compact array's ``data_terms`` contains a width-only zero placeholder,
    not its base value. Inspect actual uniform data instead; indexed/choice
    bases retain the full element carrier without walking their history.
    """
    if isinstance(value, FunctionalArray):
        try:
            terms = (value.default, *(row.value for row in value.writes))
        except AttributeError:
            terms = ()
        if terms and all(z3.is_bv_value(term) for term in terms):
            values = [term.as_signed_long() for term in terms]
            return ranges.Range(min(values), max(values))
        width = value.element_width
    elif isinstance(value, list):
        result = None
        for child in value:
            part = _supplied_state_range(child)
            if part is not None:
                result = part if result is None else result.join(part)
        return result
    elif z3.is_bv_value(value):
        return ranges.Range(value.as_signed_long(), value.as_signed_long())
    else:
        width = value.size()
    return ranges.Range(-(1 << (width - 1)), (1 << (width - 1)) - 1)


def _signed_between(term, low, high):
    """A signed bit-vector membership test without truncating either bound."""
    if not z3.is_bv(term):
        raise EncodingError("an output cell is not a scalar bit-vector",
                            kind="shape")
    bound_width = ranges.encoding_width(ranges.Range(low, high))
    value, lower = widen(term, _const(low, bound_width))
    value2, upper = widen(term, _const(high, bound_width))
    # Both calls widen to max(term.width, bound_width), hence ``value`` and
    # ``value2`` are the same signed interpretation of the cell.
    return z3.And(value >= lower, value2 <= upper)


def _signed_greater_than(term, value):
    bound_width = ranges.encoding_width(ranges.Range(value, value))
    left, right = widen(term, _const(value, bound_width))
    return left > right


def _output_contract_checks(proc, final):
    """Symbolic checks for the task-owned active output prefix.

    Source batch planning owns the count and physical buffers.  The task
    signature owns the finite carrier of every active field.  Keeping the
    checks named lets an exact counterexample say whether the count or which
    field escaped, without making either fact an assumption of the formula.
    """
    semantics = proc.semantics
    domains = () if semantics is None else semantics.output_domains
    if not domains or not getattr(proc, "boundary_complete", True):
        return {}
    expected = tuple(name for name, _finite in domains)
    if tuple(proc.outputs) != expected:
        raise EncodingError(
            "the lowered output boundary does not match its procedure domain",
            kind="type")

    capacity = getattr(proc, "output_capacity", None)
    if capacity is None:
        # Legacy hand-built procedures have no explicit batch metadata.  A
        # scalar return is one transaction; a counted batch needs the capacity
        # that only lowering can provide and is therefore not guessed.
        if proc.count is not None:
            raise EncodingError(
                "a counted output has no declared logical capacity",
                kind="shape")
        capacity = 1
    if type(capacity) is not int or capacity < 0:
        raise EncodingError("the output capacity is not a natural number",
                            kind="shape")

    if proc.count is None:
        count = _const(1, ranges.encoding_width(ranges.Range(1, 1)))
    else:
        count = final.get(proc.count)
        if count is None or isinstance(count, list) or not z3.is_bv(count):
            raise EncodingError("the output count is not a scalar value",
                                kind="shape")
    checks = {"count": _signed_between(count, 0, capacity)}

    physical = max(capacity, 1)
    zero = _const(0, count.size())

    def batch_cell(buffer, index, label):
        if isinstance(buffer, FunctionalArray):
            if buffer.length != physical:
                raise EncodingError(
                    f"{label} does not have its declared batch shape",
                    kind="shape")
            width = max(1, physical.bit_length() + 1)
            return buffer.select(_const(index, width))
        if not isinstance(buffer, list) or len(buffer) != physical:
            raise EncodingError(
                f"{label} does not have its declared batch shape",
                kind="shape")
        return buffer[index]

    bindings = tuple(binding for role, binding in semantics.value_bindings
                     if role == "output")
    if bindings:
        public = {
            variable for binding in bindings
            for variable in binding.solver_variables
        }
        if set(proc.output_vars) != public:
            raise EncodingError(
                "the physical output buffers do not match their ValueBindings",
                kind="type")
        for binding in bindings:
            leaves = {leaf.path: leaf for leaf in binding.layout.leaves()}
            for logical, solver in binding.leaf_bindings:
                variable = proc.output_vars[solver.variable]
                buffer = final.get(variable)
                leaf = leaves[logical]
                for index in range(capacity):
                    cell = batch_cell(
                        buffer, index,
                        f"physical output {solver.variable!r}")
                    for child in solver.indices:
                        if isinstance(cell, FunctionalArray) and 0 <= child < cell.length:
                            width = max(1, cell.length.bit_length() + 1)
                            cell = cell.select(_const(child, width))
                            continue
                        if not isinstance(cell, list) or not 0 <= child < len(cell):
                            raise EncodingError(
                                f"physical output {solver.variable!r} has no "
                                f"declared leaf path {solver.indices!r}",
                                kind="shape")
                        cell = cell[child]
                    if isinstance(cell, list) or not z3.is_bv(cell):
                        raise EncodingError(
                            f"output {binding.name!r} leaf {logical!r} is not "
                            "one scalar term", kind="shape")
                    if leaf.semantic_type == value_types.BOOLEAN:
                        low, high = 0, 1
                    elif leaf.carrier is not None:
                        low, high = leaf.carrier.bounds
                    else:
                        raise EncodingError(
                            f"output {binding.name!r} leaf {logical!r} has no "
                            "finite task carrier", kind="type")
                    active = _signed_greater_than(count, index)
                    label = "".join(
                        f"[{part!r}]" if type(part) is str else f"[{part}]"
                        for part in logical)
                    checks[f"{binding.name}{label}[{index}]"] = z3.Or(
                        count <= zero, z3.Not(active),
                        _signed_between(cell, low, high))
    else:
        if set(proc.output_vars) != set(expected):
            raise EncodingError(
                "the lowered output buffers do not match the procedure domain",
                kind="type")
        for name, finite in domains:
            variable = proc.output_vars[name]
            buffer = final.get(variable)
            low, high = finite.bounds
            for index in range(capacity):
                cell = batch_cell(buffer, index, f"the output {name!r}")
                if isinstance(cell, list):
                    raise EncodingError(
                        f"the output {name!r} carries a nested value where its task "
                        "type is scalar", kind="shape")
                active = _signed_greater_than(count, index)
                checks[f"{name}[{index}]"] = z3.Or(
                    count <= zero, z3.Not(active),
                    _signed_between(cell, low, high))
    return checks


def _same_shape(left, right):
    if isinstance(left, FunctionalArray) or isinstance(right, FunctionalArray):
        if isinstance(left, FunctionalArray) \
                and isinstance(right, FunctionalArray):
            return left.length == right.length
        compact = left if isinstance(left, FunctionalArray) else right
        ordinary = right if compact is left else left
        return (isinstance(ordinary, list)
                and len(ordinary) == compact.length
                and all(not isinstance(item, (list, FunctionalArray))
                        for item in ordinary))
    if isinstance(left, list) != isinstance(right, list):
        return False
    if not isinstance(left, list):
        return True
    return (len(left) == len(right)
            and all(_same_shape(a, b) for a, b in zip(left, right)))


def _symbolic_value(name, kind, width, tag, path=(), *, compact=None,
                    leaf_width=None):
    """Fresh leaves, optionally carried by narrower atoms with exact wiring.

    The caller retains the original interval restrictions. A sufficient signed
    leaf carrier removes only values that those restrictions already exclude;
    sign extension keeps the same horizon-wide logical sort. Supplied state
    never enters this fresh-symbol factory.
    """
    if isinstance(kind, value_types.Array):
        if (compact is not None and not path and kind.cells > 0
                and kind.elem in (value_types.BOOLEAN, value_types.INTEGER)):
            try:
                return FunctionalArray.symbolic(
                    kind.cells, f"{name}$packed{tag}", width,
                    compact.lo, compact.hi)
            except ValueError:
                # Only carriers with an exact compact QF_BV invariant take
                # this representation.  Every other finite array retains the
                # ordinary component product; no interval is widened here.
                pass
        return [_symbolic_value(
                    name, kind.elem, width, tag, path + (j,), compact=None,
                    leaf_width=leaf_width)
                for j in range(kind.cells)]
    suffix = "".join(f"[{j}]" for j in path)
    bits = width if leaf_width is None else leaf_width
    if type(bits) is not int or not 1 <= bits <= width:
        raise ValueError("a fresh leaf carrier must fit its logical width")
    atom = z3.BitVec(f"{name}{suffix}{tag}", bits)
    return atom if bits == width else z3.SignExt(width - bits, atom)


def _internal_symbol(prefix, tag, index):
    """An injective name for one encoder-local definition.

    A plain concatenation is ambiguous: tag ``@1`` with index ``11`` and tag
    ``@11`` with index ``1`` both spell ``@111``.  Bounded unrolling then
    identifies definitions from different steps and can turn a satisfiable
    trace formula into UNSAT.  Length-framing the arbitrary tag keeps every
    ``(tag, index)`` pair distinct.
    """
    return f"_{prefix}{len(tag)}:{tag}:{index}"


def _type_shape(kind):
    if not isinstance(kind, value_types.Array):
        return ()
    return (kind.cells,) + _type_shape(kind.elem)


def _matches_type(value, kind):
    if isinstance(kind, value_types.Array):
        if isinstance(value, FunctionalArray):
            return (value.length == kind.cells
                    and kind.elem in (value_types.BOOLEAN, value_types.INTEGER))
        return (isinstance(value, list) and len(value) == kind.cells
                and all(_matches_type(item, kind.elem) for item in value))
    # A supplied pre-state is already an encoding, not an arbitrary Python
    # value.  Accepting a tuple, mapping or Boolean here merely postpones the
    # shape fault until z3 happens to consume it, outside this typed boundary.
    return z3.is_bv(value)


def _semantic_type_constraints(value, kind):
    """Formulae saying a symbolic value inhabits its SolverIR type.

    Integer ranges are representation facts supplied separately.  Boolean is
    different: ``{0, 1}`` is the exact symbolic image of the semantic type,
    even when the horizon-wide implementation happens to allocate more than
    one bit.  In particular, an unconstrained bit-vector value ``2`` must not
    become a Boolean merely because branch encoding tests it for nonzero.
    """
    if isinstance(kind, value_types.Array):
        if isinstance(value, FunctionalArray):
            if value.length != kind.cells:
                return ()
            if kind.elem == value_types.BOOLEAN:
                return tuple(
                    z3.Or(term == _const(0, term.size()),
                          term == _const(1, term.size()))
                    for term in value.data_terms
                )
            return ()
        if not isinstance(value, list) or len(value) != kind.cells:
            return ()
        return tuple(
            constraint
            for item in value
            for constraint in _semantic_type_constraints(item, kind.elem)
        )
    if kind == value_types.BOOLEAN and z3.is_bv(value):
        return (z3.Or(
            value == _const(0, value.size()),
            value == _const(1, value.size()),
        ),)
    return ()


def _signed(model, term):
    value = model.eval(term, model_completion=True)
    return value.as_signed_long() if z3.is_bv_value(value) else int(str(value))



def _unsigned(model, term):
    value = model.eval(term, model_completion=True)
    return value.as_long() if z3.is_bv_value(value) else int(str(value))



def decode_value(model, value, kind, *, compact=False):
    """Decode a model value using its exact recursive SolverIR type."""
    def decode(item, item_kind):
        if isinstance(item_kind, value_types.Array):
            if isinstance(item, FunctionalArray):
                if item.length != item_kind.cells \
                        or item_kind.elem not in (value_types.BOOLEAN, value_types.INTEGER):
                    raise TypeError(
                        "a functional symbolic state has the wrong type")
                convert = item.to_uniform if compact else item.materialize
                return convert(
                    model, lambda current_model, term: decode(
                        term, item_kind.elem))
            if not isinstance(item, list) or len(item) != item_kind.cells:
                raise TypeError("a symbolic state has the wrong recursive shape")
            return [decode(child, item_kind.elem) for child in item]
        if item_kind == value_types.BOOLEAN:
            scalar = _unsigned(model, item)
            if scalar not in (0, 1):
                raise ValueError("a symbolic Boolean decoded outside {0,1}")
            return bool(scalar)
        if item_kind == value_types.INTEGER:
            return _signed(model, item)
        raise TypeError("a symbolic state leaf has no SolverIR semantic type")

    return decode(value, kind)



def encode_concrete_value(value, rng, name, *, kind=None, width=None, context=None):
    """Encode one concrete recursive SolverIR value at a proved scalar width.

    This is the common boundary used to seed symbolic unrolling and finite-run
    step audits.  In particular, an external memory keeps its exact indexed base
    plus sparse writes; it is never materialized merely to enter an encoding.
    A query context owns every scalar, array address and store guard together.
    """

    minimum = validated_width(rng, name)
    width = minimum if width is None else width
    if type(width) is not int or width < minimum:
        raise EncodingError(
            f"the scalar encoding for {name} has {width!r} bits and needs at "
            f"least {minimum}", kind="width")

    def encode(item, item_kind):
        if isinstance(item_kind, value_types.Array):
            from .uniform_array import UniformArray

            if (type(item) is list and item and item_kind.elem == value_types.INTEGER
                    and len(item) == item_kind.cells
                    and all(type(cell) is int and rng.lo <= cell <= rng.hi for cell in item)):
                # Validate every native word, but allocate terms only for the
                # compact base and changed values, not once per repeated zero.
                item = UniformArray.from_values(item)
            if type(item) is UniformArray and not isinstance(
                    item_kind.elem, value_types.Array):
                if len(item) != item_kind.cells:
                    raise EncodingError(
                        f"the initial value of {name} has another recursive shape",
                        kind="shape")
                writes = dict(item.sparse_items())
                if writes and len(writes) == len(item):
                    # The base has no live cells after a complete overwrite.
                    # Its old value need not inhabit this snapshot's carrier;
                    # use a live value as the base, then encode every last write.
                    out = FunctionalArray.uniform(
                        len(item), encode(next(iter(writes.values())), item_kind.elem))
                elif item.is_uniform:
                    out = FunctionalArray.uniform(
                        len(item), encode(item.default, item_kind.elem))
                else:
                    if item.element_width is None or width < item.element_width:
                        raise EncodingError(
                            f"the initial value of {name} needs a wider memory "
                            "carrier", kind="width")
                    _tag, memory_name, data_width = item.base_image
                    out = FunctionalArray.memory(
                        len(item), memory_name, data_width, width)
                    if context is not None:
                        out = out.translate(context)
                index_width = max(1, len(item).bit_length() + 1)
                for index, stored in writes.items():
                    out = out.store(
                        z3.BitVecVal(index, index_width, ctx=context),
                        encode(stored, item_kind.elem),
                        z3.BoolVal(True, ctx=context),
                    )
                return out
            if type(item) is UniformArray:
                item = item.materialize()
            if type(item) is not list or len(item) != item_kind.cells:
                raise EncodingError(
                    f"the initial value of {name} has another recursive shape",
                    kind="shape")
            return [encode(cell, item_kind.elem) for cell in item]
        if item_kind == value_types.BOOLEAN:
            valid = type(item) is bool
            scalar = int(item) if valid else 0
        elif item_kind == value_types.INTEGER:
            valid = type(item) is int
            scalar = item if valid else 0
        else:
            valid = False
            scalar = 0
        if not valid or not rng.lo <= scalar <= rng.hi:
            raise EncodingError(
                f"the initial value of {name} is outside its declared range",
                kind="range")
        return z3.BitVecVal(scalar, width, ctx=context)

    try:
        return encode(value, kind)
    finally:
        # ``encode`` reaches itself through its closure. Break that cycle so a
        # per-query z3 context is released on return, not at a later collection.
        encode = None


class _Encoder:
    """Builds the formula of a procedure body.

    One valuation carries both kinds of variable: a scalar binds a term, an
    array binds the list of its cells. Keeping them apart would mean the
    expression layer and the command layer each holding half the environment,
    and then a read of an array written by an earlier command finds nothing.
    """

    def __init__(self, proc, env, tag=""):
        self.proc = proc
        self.error_codes = (
            proc.semantics.error_codes
            if proc is not None and proc.semantics is not None
            else ERROR_CODES
        )
        # What distinguishes this encoding's names from another's. Two steps of
        # one run are two encodings, and if their bound names coincide the
        # conjunction of the two says their intermediate values are equal,
        # which is a constraint neither step contains.
        self.tag = tag
        # What each variable can hold, carried alongside the terms. A width is
        # a property of the values a subterm takes, not of the widths of the
        # things it was built from: a sixteen bit value shifted left by
        # sixteen needs thirty-three bits, and taking the operand's width
        # would drop exactly the bits the shift moved.
        self.env = dict(env)
        self.widths = {}
        # The step is a conjunction of definitions rather than one term. Every
        # value a command leaves gets a name, and the name is what later
        # commands read, so a term stays the size of the command that built it
        # instead of the size of everything before it.
        self.defs = []
        self.definitions = []
        # Exact common subexpressions share one SSA definition within this
        # encoding. Definitions retain their RHS, so cached Z3 identities
        # cannot be recycled while the encoder is alive. No source-expression,
        # range or cross-step equivalence is assumed.
        self._bound_terms = {}
        from .guarded_reads import GuardedReads

        self._guarded_reads = GuardedReads()
        self._read_guard = None
        self._branch_guard = None
        self._query_prefix = None
        # Scalar dense reads are pure terms. Retain their complete cell/index
        # syntax within this encoding, so an identical read need not rebuild
        # the multiplexer. Keys own the terms, not a mutable array's identity;
        # changed cells, widths or indices necessarily describe a new read.
        self._selected_terms = {}
        # Primitive terms are pure functions of the encoded operands and result
        # carrier. Share construction before bind(), not only the resulting SSA
        # equation afterwards. Entries retain their operands, so changed state or
        # recycled native AST identities cannot reuse another computation.
        self._scalar_terms = {}
        self._expression_terms = None
        self._comparison_rewriter = None
        self._defer_constraint = False
        self.array_selections = []
        self._names = 0
        self._range = {}
        #: Collect failures and keep going, instead of stopping at the first.
        #:
        #: M3 asks that `verify(...) == []` imply `encode(...)` cannot fail for a type,
        #: a shape or a range. A separate predictor cannot give that: it would be a
        #: second reading of the program, and two readings that have to agree are two
        #: readings that can differ -- the first attempt was conservative by 365 models
        #: in one direction and, when made to flow, wrong by three in the other, which
        #: is the direction that must never be wrong.
        #:
        #: So there is no predictor. There is one pass with two modes, and the
        #: implication is true by construction because the thing that answers is the
        #: thing that would fail. The formula built in this mode stands on substituted
        #: values and **is discarded**; `verify_encoding` is the only caller and it
        #: keeps the faults and throws the terms away.
        self.collect = False
        self.faults = []
        # Filled at the public boundary from the validated EncodingPlan.  A
        # semantic loop carries no resource-policy maximum of its own.
        self.loop_bounds = {}
        self.loop_visits = {}
        self.loop_context = []
        self.command_numbers = {}
        # A bounded goal query may encode an audited command cone while still
        # consuming the complete procedure's already-validated position plan.
        # ``None`` is the ordinary complete step.  The private query entry
        # installs command identities and the exact public goals that cone is
        # allowed to answer; no public encoder caller can weaken a step this
        # way.
        self.analysis_keep = None
        self.analysis_goals = None
        # Installed from the validated one-step plan.  A position plan may
        # replace the same semantic ranges with horizon-wide widths, but every
        # planned encoding consumes occurrence facts rather than rediscovering
        # weaker ranges while constructing solver terms.
        self.occurrence_carriers = None
        # A validated position plan is immutable within this encoding. Loop
        # copies revisit the same command positions; reuse their carrier table,
        # not any flow-sensitive range inferred from a previous iteration.
        self._command_carriers = {}
        # Supplied state terms are not constrained by their proposed ranges.
        # Value elision may consume occurrence facts only after the boundary
        # itself establishes those entry ranges (fresh symbols or literals).
        self._value_range_premises = False
        # SolverIR has distinct Boolean and integer semantic types.  The current
        # position backend still assigns both one implementation width for the
        # whole horizon; legacy encodings retain range-minimal widths.  This is
        # an explicit representation limitation, not a type equivalence.
        self.scalar_width = None
        # Number of semantic command copies actually entered after ground-
        # false and query-cone pruning.  The plan performs a contextual
        # preflight; this independent counter is the allocation-side backstop
        # if a future planner and encoder specialization ever diverge.
        self.expanded_commands = 0
        self.expansion_stopped = False
        self._checkpoint = None
        self.compact_arrays = False
        self._array_recipes = None

    def checkpoint(self):
        if self._checkpoint is not None:
            self._checkpoint()

    def stop(self, kind, message, substitute=None):
        """Refuse, or record and carry on with ``substitute``.

        Every failure of the type, shape and range class goes through here, so what
        `verify_encoding` reports and what `encode_step` raises are the same set of
        conditions rather than two lists that have to be kept level.
        """
        if not self.collect:
            raise EncodingError(message, kind)
        self.faults.append(Fault(kind, message))
        return substitute

    def rng(self, e):
        """The range of a subterm, refusing rather than guessing one."""
        got = self._range.get(id(e))
        if got is None:
            if self.occurrence_carriers is not None:
                return self.stop(
                    "range",
                    f"the position plan did not prime a carrier for {_brief(e)}",
                    ranges.Range(0, 0))
            # The range walk already visits every child. Retain those results
            # for subsequent width requests under this same valuation; writes
            # and command/loop boundaries clear this cache as before.
            self._range.update(ranges._ranges_of_tree(e, self.env, self.env))
            got = self._range.get(id(e))
            if got is None:
                # A zero interval while collecting, so the walk reaches the rest of
                # the step and reports every subterm with no range rather than the
                # first. The formula it leaves is wrong and is thrown away.
                got = self.stop("range",
                                f"no range for {_brief(e)}, so no width",
                                ranges.Range(0, 0))
            self._range[id(e)] = got
        return got

    def _prime_expression(self, root, occurrence):
        """Install one root's complete position carrier table before use."""
        if self.occurrence_carriers is None:
            return
        from .plan import ExpressionPosition

        stack = [(occurrence, root)]
        while stack:
            self.checkpoint()
            key, expr = stack.pop()
            carrier = self.occurrence_carriers.get(key)
            if carrier is None:
                self.stop("range", f"the position plan has no carrier for {key}")
                continue
            planned = carrier.range
            previous = self._range.get(id(expr))
            if previous is not None and previous != planned:
                self.stop(
                    "range",
                    f"the shared expression at {key} has conflicting position "
                    f"carriers {previous} and {planned}")
                continue
            self._range[id(expr)] = planned
            children = tuple(ranges._children(expr))
            for index in range(len(children) - 1, -1, -1):
                stack.append((ExpressionPosition(
                    key.command, key.role, key.path + (index,)),
                    children[index]))

    def _prime_command(self, command):
        if self.occurrence_carriers is None:
            return
        from .plan import ExpressionPosition, _expression_roots

        # A shared IR object may occur in two command positions separated by a
        # write.  The carrier belongs to the occurrence, not to ``id(expr)``;
        # clear the implementation cache at the command boundary before this
        # occurrence primes it.
        self._range.clear()
        planned = self._command_carriers.get(id(command))
        if planned is not None:
            self._range.update(planned)
            return
        roots = _expression_roots(command)
        if not roots:
            return
        number = self.command_numbers.get(id(command))
        if number is None:
            self.stop("range", "a command has no stable occurrence number")
            return
        faults_before = len(self.faults)
        for role, root in roots:
            self._prime_expression(root, ExpressionPosition(number, role))
        if len(self.faults) == faults_before:
            self._command_carriers[id(command)] = dict(self._range)

    def width(self, e):
        rng = self.rng(e)
        try:
            minimum = validated_width(rng, _brief(e))
        except EncodingError as exc:
            return self.stop(exc.kind, str(exc), 1)
        if self.scalar_width is None:
            return minimum
        if self.scalar_width < minimum:
            return self.stop(
                "width",
                f"the horizon-wide scalar encoding has {self.scalar_width} bits "
                f"but {_brief(e)} needs {minimum}",
                minimum)
        return self.scalar_width

    def bind(self, term):
        """Name ``term``, so that reading it costs one symbol."""
        self.checkpoint()
        key = (term.ctx, term.get_id())
        previous = self._bound_terms.get(key)
        if previous is not None:
            return previous
        # Reduce constants and aliases in one RHS over boundary/SSA atoms.
        # This exact rewrite has no range/path premises or solver query. Keep
        # every remaining non-atomic RHS in its original representation: array
        # selection evidence and structural carrier proofs refer to that form.
        # The native rewriter replaces a Python DAG walk to find closed terms;
        # it also recognizes identities such as x-x and ite(p,v,v).
        if not z3.is_const(term):
            reduced = z3.simplify(term)
            if z3.is_const(reduced):
                return reduced
        else:
            return term
        self._names += 1
        kind = "b" if z3.is_bool(term) else "e"
        definition = FormulaDefinition.define(
            f"behavior{self.tag}:{self._names}",
            _internal_symbol(kind, self.tag, self._names), term)
        name = definition.lhs
        self.definitions.append(definition)
        # Guard facts may use the equivalent native rewrite (e.g. removing
        # constant casts around a 0/1 flag). Keep the original SSA equation for
        # the formula and its array-selection/carrier evidence.
        self._guarded_reads.define(name, reduced)
        if not self._defer_constraint:
            self.defs.append(definition.assertion)
        self._bound_terms[key] = name
        return name

    def term_rewriter(self):
        if self._comparison_rewriter is None:
            from .term_rewrite import _ComparisonRewriter

            self._comparison_rewriter = _ComparisonRewriter(
                self.definitions, array_selections=self.array_selections,
                checkpoint=self._checkpoint)
        return self._comparison_rewriter

    def bind_value(self, value):
        """Share scalar expressions and costly array histories."""
        if isinstance(value, FunctionalArray):
            if not self.compact_arrays:
                return value
            # Reuse exact template state binding when a persistent history
            # costs more than its finite component domain. Sparse histories
            # remain lazy; no update, guard, bit or range premise is dropped.
            construction = value.bind_state_steps(tag=self.tag, bind=self.bind,
                bounds=self.term_rewriter().array_bounds, recipes=self._array_recipes)
            while True:
                try:
                    next(construction)
                except StopIteration as done:
                    successor, _definitions = done.value
                    return successor
                self.checkpoint()
        if isinstance(value, list):
            return [self.bind_value(item) for item in value]
        return self.bind(value)

    def choose_value(self, cond, new, old, what="array value"):
        """Componentwise ``ite`` over one recursive value shape."""
        if not _same_shape(new, old):
            return self.stop(
                "shape", f"{what} has incompatible recursive shapes", new)
        if isinstance(new, FunctionalArray) \
                and isinstance(old, FunctionalArray):
            return FunctionalArray.choose(cond, new, old)
        new = _materialize_functional(new)
        old = _materialize_functional(old)
        if isinstance(new, list):
            return [self.choose_value(cond, a, b, what)
                    for a, b in zip(new, old)]
        return _choose(cond, new, old)

    # ── expressions ──

    def value(self, e, rho):
        """Encode a scalar or recursively nested array expression."""
        if isinstance(e, ir.Var) and isinstance(
                rho.get(e.name), (list, FunctionalArray)):
            return self._guarded_reads.read(_copy_tree(rho[e.name]), self._read_guard)
        if isinstance(e, ir.Sel):
            return self._guarded_reads.read(self._select(e, rho), self._read_guard)
        if isinstance(e, ir.Op):
            return self._op(e, rho)
        got = self.expr(e, rho)
        return got

    def expr(self, e, rho):
        self.checkpoint()
        if isinstance(e, ir.Lit):
            encoded = int(e.value) if type(e.value) is bool else e.value
            magnitude = scalar_magnitude_bits(encoded)
            if magnitude > SCALAR_BITS:
                width = self.stop(
                    "width",
                    f"literal needs {magnitude} magnitude bits, over the "
                    f"reported scalar capability of {SCALAR_BITS}",
                    1)
                # Collection mode continues only to report the remaining
                # faults; no formula containing this substitute is returned.
                return _const(0, width)
            width = (self.width(e) if self.scalar_width is not None else
                     ranges.encoding_width(ranges.Range(encoded, encoded)))
            return _const(encoded, width)
        if isinstance(e, ir.Var):
            if e.name not in rho:
                return self.stop("shape", f"{e.name} is read before it is written",
                                 _const(0, 1))
            if isinstance(rho[e.name], (list, FunctionalArray)):
                return self.stop("shape", f"array {e.name} used where a value is"
                                          " expected", _const(0, 1))
            return self._guarded_reads.read(rho[e.name], self._read_guard)
        if isinstance(e, ir.Sel):
            got = self._select(e, rho)
            if isinstance(got, (list, FunctionalArray)):
                return self.stop("shape", "an array selection used where a value "
                                          "is expected", _const(0, 1))
            return self._guarded_reads.read(got, self._read_guard)
        if isinstance(e, ir.Op):
            got = self._op(e, rho)
            if isinstance(got, (list, FunctionalArray)):
                return self.stop(
                    "shape", "an array used where a value is expected",
                    _const(0, 1))
            return got
        return self.stop("type", f"expression {type(e).__name__}", _const(0, 1))

    def _select(self, e, rho):
        # A stored array is immutable here. Resolve a closed index before
        # refining its cells: the caller reads the selected value under the
        # same guard, so unrelated cells need no conditional-history walk.
        stored = rho.get(e.array.name) if isinstance(e.array, ir.Var) else None
        cells = (stored if isinstance(stored, (list, FunctionalArray))
                 else self._cells(e.array, rho))
        index = self.bind(self.expr(e.index, rho))
        if isinstance(cells, FunctionalArray):
            selection = _ArraySelection(cells, index)
            self.array_selections.append(selection)
            return selection.term
        # bind reduces closed index arithmetic, including unrolled i-1, before
        # constructing a multiplexer. Negative IR indices are out of range;
        # Python's indexing-from-the-end is normalized by source lowering.
        if isinstance(index, z3.BitVecNumRef):
            at = index.as_signed_long()
            if 0 <= at < len(cells):
                return _copy_tree(cells[at])
            return _zeros_like(cells[0]) if cells else _const(0, DEFAULT_WIDTH)
        if cells is stored:
            cells = self._guarded_reads.read(cells, self._read_guard)
        key = (None if any(isinstance(cell, (list, FunctionalArray))
                           for cell in cells) else (index, *cells))
        if key is not None:
            previous = self._selected_terms.get(key)
            if previous is not None:
                return previous
        # Out of range reads a canonical value, the guard being a command, so
        # the selection is total and needs no case of its own here.
        out = _zeros_like(cells[0]) if cells else _const(0, DEFAULT_WIDTH)
        for j, cell in enumerate(cells):
            out = self.choose_value(
                _at(index, j, len(cells)), cell, out, "array selection")
        if key is not None:
            self._selected_terms[key] = out
        return out

    def _cells(self, array, rho):
        value = self.value(array, rho)
        if isinstance(value, (list, FunctionalArray)):
            return value
        return self.stop("shape", "an array whose cells are not known",
                         [_const(0, 1)])

    def _op(self, e, rho):
        # Source lowering can share a subtree in several operands. Primitive
        # CSE runs after those operands have been encoded, so it does not avoid
        # recursively expanding that DAG as a tree. Within one outer operation
        # the valuation, read guard and carriers are fixed and IR expressions
        # have no events or writes. Share scalar subtrees only in this scope;
        # never carry an IR-identity result into another expression evaluation.
        # Fault collection deliberately visits every occurrence as before.
        if self.collect:
            return self._encode_operation(e, rho)
        outer = self._expression_terms is None
        if outer:
            self._expression_terms = {}
        try:
            previous = self._expression_terms.get(id(e))
            if previous is not None:
                return previous
            value = self._encode_operation(e, rho)
            if z3.is_bv(value):
                self._expression_terms[id(e)] = value
            return value
        finally:
            if outer:
                self._expression_terms = None

    def _encode_operation(self, e, rho):
        try:
            spec = primitives.primitive(e.op)
        except primitives.PrimitiveRegistryError as error:
            raise EncodingError(str(error)) from None
        terms = spec.signature.terms_for(len(e.args))
        if terms is None:
            raise EncodingError(f"operation {e.op} of {len(e.args)} arguments")
        if (self.occurrence_carriers is not None and self._value_range_premises
                and spec.signature.result is primitives.BOOLEAN):
            # The checked plan describes this command when active. A total
            # Boolean expression forced by those carriers needs no operand
            # formula, just as a forced branch guard does. Source events and
            # partial-operation failures are separate commands; guarded writes
            # still retain their old value on inactive/failing paths.
            truth = ranges._constant_truth(e, self.rng)
            if truth is not None:
                return _const(int(truth), self.width(e))
        args = []
        for term, argument in zip(terms, e.args):
            if term in (
                    primitives.WIDTH, primitives.SHIFT,
                    primitives.ARRAY_LENGTH):
                literal = isinstance(argument, ir.Lit)
                bounds = (None if literal or term in (
                    primitives.WIDTH, primitives.ARRAY_LENGTH)
                          else self.rng(argument))
                domain_error = primitives.argument_domain_error(
                    term,
                    literal=literal,
                    value=argument.value if literal else None,
                    bounds=bounds,
                )
                if domain_error is not None:
                    kind = ("width" if term is primitives.WIDTH
                            and "capability" in domain_error else
                            "type" if literal or term in (
                                primitives.WIDTH, primitives.ARRAY_LENGTH)
                            else "range")
                    return self.stop(
                        kind, f"operation {e.op} {domain_error}",
                        _const(0, self.scalar_width or 1))
                if term in (primitives.WIDTH, primitives.ARRAY_LENGTH):
                    # These are checked compile-time parameters, not values
                    # in the symbolic valuation. Encoding and then decoding a
                    # numeral adds no equation or semantic information.
                    args.append(argument.value)
                    continue
            if term in (
                    primitives.BOOLEAN, primitives.INTEGER,
                    primitives.SCALAR, primitives.SAME_SCALAR,
                    primitives.WIDTH, primitives.SHIFT,
                    primitives.ARRAY_LENGTH):
                args.append(self.expr(argument, rho))
            elif term is primitives.ARRAY:
                args.append(self._cells(argument, rho))
            else:
                args.append(self.value(argument, rho))

        scalar = (spec.signature.result not in (
                    primitives.ARRAY, primitives.ARRAY_OF_SAME)
                  and all(not isinstance(value, (list, FunctionalArray))
                          for value in args))
        result_width = None
        if scalar:
            # Mathematical scalar operations require a common carrier. The
            # explicit WIDTH family instead owns its operand conversion: wrap,
            # saturation, comparison and shift callbacks extract/extend to the
            # declared carrier themselves. Pre-extending those operands (and
            # the width literal) only builds terms they immediately undo.
            result_width = self.width(e)
            if primitives.WIDTH not in terms:
                operation_width = max(
                    [result_width] + [value.size() for value in args])
                args = [_extend(value, operation_width) for value in args]
        # Z3's overloaded equality constructs another formula. Dictionary
        # lookup needs exact AST identity instead, as bind() does. Keep the
        # operands in the entry so native identities cannot be recycled after
        # a primitive simplifies away an operand. Literal parameters remain
        # typed Python values, separate from context-qualified AST identities.
        key = (e.op, result_width, *(
            (value.ctx, value.get_id()) if isinstance(value, z3.AstRef)
            else (type(value), value) for value in args)) if scalar else None
        cached = self._scalar_terms.get(key) if scalar else None
        if cached is None:
            operation_args = args
            if e.op in {"fdiv", "mul"}:
                rewrite = self.term_rewriter()
                width = (rewrite.division_width(*args) if e.op == "fdiv"
                         else rewrite.multiplication_width(*args))
                if width < args[0].size():
                    operation_args = [z3.Extract(width - 1, 0, arg) for arg in args]
            value = spec.symbolic(self, operation_args, result_width)
            if operation_args is not args:
                value = _extend(value, args[0].size())
            if self.compact_arrays and isinstance(value, list) and value and all(z3.is_bv(item) for item in value):
                value = FunctionalArray.from_cells(value)
            if scalar:
                self._scalar_terms[key] = (tuple(args), value)
        else:
            _, value = cached
        # The optional proof sees the same append-only definition history even
        # when primitive construction was shared. Do not memoize that proof's
        # resource state or any flow-sensitive source range.
        if scalar and z3.is_app_of(value, z3.Z3_OP_ITE):
            value = self.term_rewriter().rewrite(value)
        return value

    # The registry's symbolic callbacks use only this small exact interface.
    # It keeps Z3 and recursive-array mechanics here while keeping primitive
    # selection and meaning in the registry.
    @staticmethod
    def bitwise_and(left, right, result_width):
        """A literal nonnegative mask bounds the result on the full carrier.

        This is an algebraic identity, independent of inferred state ranges:
        0 <= x & mask <= mask, including negative x. Keep the zero sign bit
        when shrinking, so mathematical integer consumers see the same value.
        """
        value = left & right
        width = value.size()
        for operand in (left, right):
            if z3.is_bv_value(operand):
                mask = operand.as_signed_long()
                if mask >= 0:
                    width = min(width, mask.bit_length() + 1)
        # Position plans may deliberately use one common width for the whole
        # horizon. Compact only redundant operand bits above that carrier.
        width = max(width, result_width)
        return z3.Extract(width - 1, 0, value) if width < value.size() else value

    @staticmethod
    def truth(condition, width):
        return z3.If(condition, _const(1, width), _const(0, width))

    @staticmethod
    def truth_value(value, width):
        """The explicit scalar-to-Boolean conversion emitted by lowering."""
        return z3.If(value != _const(0, value.size()), _const(1, width),
                     _const(0, width))

    @staticmethod
    def logical_and(left, right, width):
        """The exact 0/1 encoding of Boolean conjunction."""

        return _Encoder.truth(
            z3.And(
                left != _const(0, left.size()),
                right != _const(0, right.size()),
            ),
            width,
        )

    @staticmethod
    def logical_or(left, right, width):
        """The exact 0/1 encoding of Boolean disjunction."""

        return _Encoder.truth(
            z3.Or(
                left != _const(0, left.size()),
                right != _const(0, right.size()),
            ),
            width,
        )

    @staticmethod
    def integer_value(value, _width):
        """The explicit Boolean/integer-to-integer conversion.

        Scalar arguments have already been extended to the operation width by
        ``_op``.  The conversion changes semantic type, not the finite numeric
        image used by this exact encoder.
        """
        return value

    @staticmethod
    def floor_div(a, b):
        zero = _const(0, a.size())
        return z3.If(b == zero, zero, _floor_div(a, b))

    @staticmethod
    def floor_mod(a, b):
        zero = _const(0, a.size())
        return z3.If(b == zero, zero, _floor_mod(a, b))

    @staticmethod
    def total_shift(function, value, amount):
        """Total IR shift; source lowering guards Python's negative case."""
        zero = _const(0, value.size())
        return z3.If(amount < _const(0, amount.size()), zero,
                     function(value, amount))

    @staticmethod
    def _fixed_bits(value, width):
        """The low ``width`` bits of one mathematical integer encoding."""
        current = value.size()
        if current < width:
            return z3.SignExt(width - current, value)
        if current == width:
            return value
        return z3.Extract(width - 1, 0, value)

    @staticmethod
    def _fixed_result(bits, result_width, *, signed):
        current = bits.size()
        target = max(result_width, current + (0 if signed else 1))
        if target == current:
            return bits
        extension = target - current
        return (z3.SignExt(extension, bits) if signed
                else z3.ZeroExt(extension, bits))

    def fixed_wrap(self, value, width, result_width, *, signed):
        bits = self._fixed_bits(value, width)
        return self._fixed_result(bits, result_width, signed=signed)

    def fixed_saturate(self, value, width, result_width, *, signed):
        """Clamp one mathematical integer to a fixed-width carrier."""
        if signed:
            bound = 1 << (width - 1)
            lower, upper = -bound, bound - 1
        else:
            lower, upper = 0, (1 << width) - 1
        comparison_width = max(value.size(), width + (0 if signed else 1))
        compared = _extend(value, comparison_width)
        low = _const(lower, comparison_width)
        high = _const(upper, comparison_width)
        clamped = z3.If(compared < low, low,
                        z3.If(compared > high, high, compared))
        bits = self._fixed_bits(clamped, width)
        return self._fixed_result(bits, result_width, signed=signed)

    def fixed_compare(self, function, left, right, width, result_width,
                      *, signed):
        left = self._fixed_bits(left, width)
        right = self._fixed_bits(right, width)
        if signed:
            condition = function(left, right)
        else:
            unsigned = {
                operator.lt: z3.ULT,
                operator.le: z3.ULE,
                operator.gt: z3.UGT,
                operator.ge: z3.UGE,
            }
            try:
                condition = unsigned[function](left, right)
            except KeyError:
                raise EncodingError(
                    "a fixed-width comparison has no registered relation",
                    kind="type") from None
        return self.truth(condition, result_width)

    def fixed_shift(self, value, amount, width, result_width, *,
                    signed, direction):
        bits = self._fixed_bits(value, width)

        bound_width = ranges.encoding_width(ranges.Range(width, width))
        comparable, bound = widen(amount, _const(width, bound_width))
        oversized = comparable >= bound
        amount_width = amount.size()
        if amount_width < width:
            shift = z3.SignExt(width - amount_width, amount)
        elif amount_width > width:
            shift = z3.Extract(width - 1, 0, amount)
        else:
            shift = amount

        zero = _const(0, width)
        if direction == "left":
            shifted = bits << shift
            fill = zero
        elif direction == "logical-right":
            shifted = z3.LShR(bits, shift)
            fill = zero
        elif direction == "arithmetic-right":
            shifted = bits >> shift
            fill = z3.If(bits < zero, _const(-1, width), zero)
        else:
            raise EncodingError(
                f"unknown fixed-width shift direction {direction!r}",
                kind="type")
        result = z3.If(oversized, fill, shifted)
        return self._fixed_result(result, result_width, signed=signed)

    @staticmethod
    def logical_not(a, _width):
        return z3.If(a == _const(0, a.size()), _const(1, a.size()),
                     _const(0, a.size()))

    @staticmethod
    def _magnitude(a):
        wide = z3.SignExt(1, a)
        zero = _const(0, wide.size())
        return wide, z3.If(wide < zero, -wide, wide)

    def popcount(self, a, _width):
        wide, magnitude = self._magnitude(a)
        # Each subtree counts only its own leaves. Its sum needs log2(leaves)
        # bits, not the full input carrier. Balanced, unsigned additions are
        # exact because the declared maximum includes every contributing bit.
        # The old serial chain used one input-width adder per bit.
        terms = [(z3.Extract(index, index, magnitude), 1)
                 for index in range(wide.size())]
        while len(terms) > 1:
            level = []
            for index in range(0, len(terms) - 1, 2):
                left, left_max = terms[index]
                right, right_max = terms[index + 1]
                maximum = left_max + right_max
                width = maximum.bit_length()
                level.append((z3.ZeroExt(width - left.size(), left)
                              + z3.ZeroExt(width - right.size(), right), maximum))
            if len(terms) & 1:
                level.append(terms[-1])
            terms = level
        out = terms[0][0]
        if out.size() < a.size():
            return z3.ZeroExt(a.size() - out.size(), out)
        if out.size() > a.size():
            return z3.Extract(a.size() - 1, 0, out)
        return out

    def bit_length(self, a, _width):
        wide, magnitude = self._magnitude(a)
        out = _const(0, a.size())
        for index in range(wide.size()):
            bit = z3.Extract(index, index, magnitude)
            out = z3.If(
                bit == _const(1, 1), _const(index + 1, a.size()), out)
        return out

    def conditional(self, condition, when_true, when_false, _width):
        taken = condition != _const(0, condition.size())
        if isinstance(when_true, (list, FunctionalArray)) \
                or isinstance(when_false, (list, FunctionalArray)):
            return self.choose_value(
                taken, when_true, when_false, "conditional value")
        return z3.If(taken, when_true, when_false)

    @staticmethod
    def array_literal(args):
        # A repeated scalar literal/expression is the same finite value as
        # arrayfill. Preserve that representation so later guarded stores and
        # selection proofs do not expand one update into every array cell.
        # Syntactic equality needs no range assumption or solver query.
        if args and all(z3.is_bv(value) and z3.eq(value, args[0])
                        for value in args):
            return FunctionalArray.uniform(len(args), args[0])
        return [_copy_tree(value) for value in args]

    @staticmethod
    def array_fill(value, cells):
        if cells == 0:
            return []
        if isinstance(value, (list, FunctionalArray)):
            return [_copy_tree(value) for _ in range(cells)]
        return FunctionalArray.uniform(cells, value)

    def array_store(self, cells, index, value):
        # Match command stores and reads: reduce closed address arithmetic once
        # and name symbolic addresses before retaining them in array history.
        index = self.bind(index)
        if isinstance(cells, FunctionalArray):
            if isinstance(value, (list, FunctionalArray)):
                return self.stop(
                    "shape", "a functional scalar array received an array cell",
                    cells)
            return self.bind_value(cells.store(index, value, z3.BoolVal(True)))
        return [self.choose_value(_at(index, position, len(cells)), value,
                                  old, "array store")
                for position, old in enumerate(cells)]

    def merge_bits(self, current, data, mask, width, *, signed):
        if all(isinstance(value, FunctionalArray) for value in (current, data, mask)):
            return FunctionalArray.merge_bits(current, data, mask, width, signed=signed)
        if isinstance(current, (list, FunctionalArray)):
            def cells(value):
                if isinstance(value, FunctionalArray):
                    index_width = max(1, value.length.bit_length() + 1)
                    return (value.select(_const(i, index_width)) for i in range(value.length))
                return value
            return [self.merge_bits(a, b, m, width, signed=signed)
                    for a, b, m in zip(*(cells(v) for v in (current, data, mask)), strict=True)]
        a, b, m = (self._fixed_bits(v, width) for v in (current, data, mask))
        return self._fixed_result((a & ~m) | (b & m), width + int(not signed), signed=signed)


class _State:
    """One valuation with branch-local write journals, plus global observations.

    A branch saves only fields it changes, not a copy of the whole valuation.
    Its values are valid under its entry guard; the conditional joins the two
    deltas before returning. Hits, failures and prefix offers keep global guards.
    """

    __slots__ = ("_original", "error", "hit_occurrences", "hits", "ok", "rho")

    def __init__(self, rho, hits, ok, error, hit_occurrences=None):
        self.rho = dict(rho)
        self.hits = dict(hits)
        self.hit_occurrences = {
            goal: list(values) for goal, values in
            ({} if hit_occurrences is None else hit_occurrences).items()
        }
        self.ok = ok
        self.error = error
        self._original = None

    def write(self, name, value):
        if self._original is not None and name not in self._original:
            self._original[name] = self.rho.get(name)
        self.rho[name] = value

    @contextmanager
    def branch(self):
        """Collect a delta and restore its entry valuation, including on cancel."""
        parent, self._original = self._original, {}
        delta = {}
        try:
            yield delta
        finally:
            for name, old in self._original.items():
                delta[name] = self.rho[name]
                if old is None:
                    self.rho.pop(name)
                else:
                    self.rho[name] = old
            self._original = parent


def _conj(a, b):
    """``a`` and ``b``, without writing down a conjunction with the truth."""
    if z3.is_false(a) or z3.is_false(b):
        return z3.BoolVal(False)
    if z3.is_true(a):
        return b
    if z3.is_true(b):
        return a
    return z3.And(a, b)


def _neg(value):
    """Boolean negation without retaining a ground wrapper."""

    if z3.is_true(value):
        return z3.BoolVal(False)
    if z3.is_false(value):
        return z3.BoolVal(True)
    return z3.Not(value)


def _disj(a, b):
    """Boolean disjunction without retaining literal identity elements."""
    if z3.is_true(a) or z3.is_true(b):
        return z3.BoolVal(True)
    if z3.is_false(a):
        return b
    if z3.is_false(b):
        return a
    return z3.Or(a, b)


def _zeros_like(value):
    """The value a variable holds where the command that writes it did not."""
    if isinstance(value, FunctionalArray):
        return FunctionalArray.uniform(
            value.length, _const(0, value.element_width))
    if isinstance(value, list):
        return [_zeros_like(cell) for cell in value]
    return _const(0, value.size())


class _EncodingPaused(TimeoutError):
    """The private query owner yielded its current construction quantum."""


def _retry_encoding(function, *args, **kwargs):
    """Retry one uncommitted operation, retaining its already-bound SSA terms.

    Only the private pause signal is resumable. Capability errors and external
    cancellation still unwind. Callers must commit semantic state only after
    the operation's final checkpoint; partial total definitions are harmless.
    """
    while True:
        try:
            return function(*args, **kwargs)
        except _EncodingPaused:
            yield


class _QueryPrefix:
    """Exact hit conditions for an encoded prefix, not a complete transition.

    Only local Search consumes this private product. An UNSAT prefix cannot
    exclude later occurrences; bounded analysis still requires the whole step.
    """

    def __init__(self, encoding):
        self.encoding = encoding


class _StepConstruction:
    """One private local question's suspended encoder, never a solver answer."""

    def __init__(self):
        self.iterator = None
        self.result = None
        self.prefix = None
        self.checkpoint = ConstructionCheckpoint()

    def advance(self, arguments, checkpoint):
        if self.result is not None:
            return self.result
        if self.prefix is not None:
            return self.prefix
        self.checkpoint.callback = checkpoint
        if self.iterator is None:
            self.iterator = _encode_step_iter(**arguments, _checkpoint=self.checkpoint)
        try:
            yielded = next(self.iterator)
            if isinstance(yielded, _QueryPrefix):
                self.prefix = yielded
                return yielded
        except StopIteration as done:
            self.result = done.value
            self.iterator = None
        except BaseException:
            # Only the private pause signal retains a continuation. External
            # cancellation or a real error invalidates the construction.
            self.iterator = None
            raise
        finally:
            self.checkpoint.callback = None
        return self.result


class _Commands(_Encoder):
    """The command rules, each producing the state its command leaves."""

    @staticmethod
    def _read_only_index(loop):
        return not any(
            (isinstance(node, (ir.Assign, ir.Store)) and node.var == loop.index)
            or (isinstance(node, ir.For) and node.index == loop.index)
            for node in ir.walk(loop.body))

    def cmd(self, c, state, reached):
        iterator = self._cmd(c, state, reached)
        try:
            next(iterator)
        except StopIteration as done:
            return done.value
        raise RuntimeError("only an owned query construction may suspend encoding")

    def _cmd(self, c, state, reached):
        # Child commands are continuations, not Python recursion. Each frame
        # retains its normal state/guard/loop scope across a private pause.
        stack = [self._scoped_command(c, state, reached)]
        result = None
        try:
            while stack:
                try:
                    child = stack[-1].send(result)
                except StopIteration as done:
                    stack.pop()
                    result = done.value
                    continue
                result = None
                if child is None:
                    yield  # Only a private encoding checkpoint may pause.
                elif isinstance(child, _QueryPrefix):
                    yield child
                else:
                    stack.append(self._scoped_command(*child))
            return result
        finally:
            # External cancellation unwinds the same nested scopes as an
            # exception in recursive yield-from, deepest command first.
            for frame in reversed(stack):
                frame.close()

    def _scoped_command(self, c, state, reached):
        # Specialize reads only while encoding this command's active path.
        # Writes, visits, failures and final/audit values keep their guards.
        # A suspended construction retains the scope until its continuation
        # finishes; ordinary errors unwind it through the same finally block.
        previous = self._read_guard
        self._read_guard = reached
        try:
            return (yield from self._command(c, state, reached))
        finally:
            self._read_guard = previous

    def _command(self, c, state, reached):
        """Encode ``c`` into ``state``, under the condition ``reached``.

        A command writes into the state it is given rather than into a copy of
        it. Copying belongs where control forks, since that is where two
        valuations really exist; copying at every command instead would cost
        the whole valuation per command, and a step holding thousands of
        variables would spend its time copying rather than encoding.
        """
        # No operation, carrier, observation or constraint is associated with
        # Skip. Avoid the construction machinery as well as its budget charge;
        # the planner counts the same semantic work, not padding in the IR.
        if isinstance(c, ir.Skip):
            return state
        if (isinstance(c, ir.Mark) and self.analysis_goals is not None
                and c.goal not in self.analysis_goals):
            # An unrequested nonterminal observation neither computes nor
            # constrains anything. Fail is deliberately not included here.
            return state
        yield from _retry_encoding(self.checkpoint)
        # Sequence nodes only carry order and are not members of a command
        # cone.  Descend through them; every semantic command, including an
        # enclosing branch or loop, must have been retained explicitly.
        if self.analysis_keep is not None \
                and not isinstance(c, ir.Seq) \
                and id(c) not in self.analysis_keep:
            return state
        # A false path is an identity transition, whether ground-false or
        # contradictory through our Boolean definitions. This includes loop
        # copies beyond a fixed count and commands after an absorbed failure.
        # Goal accounting and structural validation already ran; do not ask
        # the position plan for carriers of a command that cannot execute.
        if z3.is_false(reached) or self._guarded_reads.impossible(reached):
            return state
        # A command with proved-false control has no visits and need not have
        # position carriers. Check control before looking up those carriers;
        # the complete procedure's structural/type validation still ran at
        # the encoding boundary, independently of this execution path.
        yield from _retry_encoding(self._prime_command, c)
        if not isinstance(c, ir.Seq):
            self.expanded_commands += 1
            if self.expanded_commands > TERM_BUDGET:
                if not self.expansion_stopped:
                    self.expansion_stopped = True
                    self.stop(
                        "capability",
                        "the exact encoding entered more than "
                        f"{TERM_BUDGET} semantic command copies")
                return state
        if isinstance(c, ir.Assign):
            return (yield from _retry_encoding(self._assign, c, state, reached))
        if isinstance(c, ir.Store):
            return (yield from _retry_encoding(self._store, c, state, reached))
        if isinstance(c, ir.Mark):
            if self.analysis_goals is not None \
                    and c.goal not in self.analysis_goals:
                return state
            seen = state.hits.get(c.goal)
            hit = reached if seen is None or z3.is_false(seen) else (
                yield from _retry_encoding(self.bind, z3.Or(seen, reached)))
            state.hit_occurrences.setdefault(c.goal, []).append(reached)
            state.hits[c.goal] = hit
            if self._query_prefix is not None:
                prefix = self._query_prefix(c.goal, state, reached)
                if prefix is not None:
                    yield prefix
            return state
        if isinstance(c, ir.Fail):
            # Reaching the failure is reaching its goal, under exactly the same
            # condition. The two used to be separate commands and the encoding
            # of the pair depended on the lowering having put them in the right
            # order.
            record_hit = c.goal and (self.analysis_goals is None
                                    or c.goal in self.analysis_goals)
            if record_hit:
                seen = state.hits.get(c.goal)
                hit = reached if seen is None or z3.is_false(seen) else (
                    yield from _retry_encoding(self.bind, z3.Or(seen, reached)))
            ok = yield from _retry_encoding(self.bind, _conj(state.ok, _neg(reached)))
            if record_hit:
                state.hit_occurrences.setdefault(c.goal, []).append(reached)
                state.hits[c.goal] = hit
            state.ok = ok
            state.error = z3.If(
                reached, _code(c.code, self.error_codes), state.error)
            if record_hit and self._query_prefix is not None:
                prefix = self._query_prefix(c.goal, state, reached)
                if prefix is not None:
                    yield prefix
            return state
        if isinstance(c, ir.Seq):
            # Walk iteratively: sequence depth can be the entire unrolled
            # procedure, independent of source nesting. Sequence nodes carry
            # order; a semantic command outside the audited cone is identity.
            parts, stack = [], [c]
            while stack:
                node = stack.pop()
                if isinstance(node, ir.Seq):
                    stack.append(node.second)
                    stack.append(node.first)
                elif self.analysis_keep is None or id(node) in self.analysis_keep:
                    # Dropped commands already denote identity in _command.
                    # Do not enter a scope or rebuild its unchanged guard.
                    parts.append(node)
            # Each part requires completion of its predecessors. Rebuild that
            # guard only when a child changes `ok`; otherwise keep the same
            # immutable condition, including across a private pause.
            before = state.ok
            under = reached
            for index, part in enumerate(parts):
                previous_ok = state.ok
                state = yield (part, state, under)
                if index + 1 < len(parts) and not z3.eq(state.ok, previous_ok):
                    under = reached if z3.eq(state.ok, before) else (
                        yield from _retry_encoding(self.bind, _conj(reached, state.ok)))
            return state
        if isinstance(c, ir.If):
            return (yield from self._if(c, state, reached))
        if isinstance(c, ir.For):
            from .plan import ExpressionPosition

            command_number = self.command_numbers.get(id(c))
            occurrence = (None if command_number is None else
                          str(ExpressionPosition(command_number, "trip_count")))
            maximum = self.loop_bounds.get(occurrence)
            if maximum is None:
                return self.stop(
                    "range",
                    f"the loop {occurrence or '<unknown>'} has no planned "
                    "finite trip-count maximum", state)
            contextual_table = dict(
                self.loop_visits.get(str(command_number), ()))
            visit_maximum = contextual_table.get(tuple(self.loop_context))
            # A contextual maximum is an optional certified refinement.  If
            # an enclosing context could not be made pointwise, retain the
            # occurrence-wide envelope; absence never narrows the formula.
            if visit_maximum is not None:
                if visit_maximum > maximum:
                    return self.stop(
                        "range",
                        f"the contextual maximum {visit_maximum} for loop "
                        f"{occurrence or '<unknown>'} exceeds its planned "
                        f"envelope {maximum}", state)
                maximum = visit_maximum
            # Capture the term before assigning the loop index or running the
            # body.  Z3 terms are immutable, so later writes to every variable
            # occurring in the expression cannot change this entry value.
            trips = yield from _retry_encoding(
                lambda: self.bind(self.expr(c.trip_count, state.rho)))
            if maximum == 0:
                return state
            exact_trips = (max(0, trips.as_signed_long())
                           if z3.is_bv_value(trips) else None)
            if exact_trips is not None and exact_trips > maximum:
                return self.stop(
                    "range",
                    f"the loop {occurrence or '<unknown>'} evaluates to "
                    f"{exact_trips} trips beyond its planned maximum {maximum}",
                    state)
            copies = maximum if exact_trips is None else exact_trips
            span = ranges.Range(0, maximum - 1)
            width = self.scalar_width or ranges.encoding_width(span)
            entered_ok = state.ok
            read_only_index = self._read_only_index(c)
            for j in range(copies):
                if exact_trips is None:
                    left, right = widen(trips, _const(j, width))
                    due = yield from _retry_encoding(self.bind, left > right)
                else:
                    due = z3.BoolVal(True)
                active = _conj(reached, due)
                if not z3.eq(state.ok, entered_ok):
                    active = yield from _retry_encoding(self.bind, _conj(active, state.ok))
                state = yield from _retry_encoding(self._assign,
                    ir.Assign(c.index, ir.Lit(j)), state, active,
                    exact_range=ranges.Range(j, j))
                self.env[c.index] = span
                self._range.clear()
                guarded_index = state.rho[c.index]
                # Inside an active copy the index is exactly j. Leaving its
                # inactive alternative in every array index/arithmetic operand
                # needlessly couples all unrolled copies. Keep the guarded
                # binding at the boundary, including zero-trip/failing paths.
                # A nested loop may reuse this name, so specialize only when
                # the body cannot write it, implicitly or explicitly.
                if read_only_index:
                    state.write(c.index, _const(j, guarded_index.size()))
                self.loop_context.append((command_number, j))
                try:
                    state = yield (c.body, state, active)
                finally:
                    self.loop_context.pop()
                    if read_only_index:
                        state.write(c.index, guarded_index)
            return state
        raise EncodingError(f"command {type(c).__name__}")

    ARRAY_OPS = ("arraylit", "arrayfill", "store")

    def _assign(self, c, state, reached, *, exact_range=None):
        if (isinstance(c.expr, ir.Op) and c.expr.op == 'store'
                and isinstance(c.expr.args[0], ir.Var)
                and c.expr.args[0].name == c.var):
            # x = store(x, i, v) under g is one store guarded by g, not
            # an unguarded store followed by a whole-array conditional join.
            # Keep the original command/operand carriers already primed by
            # its position plan; no source occurrence or goal is rewritten.
            return self._store(ir.Store(c.var, c.expr.args[1], c.expr.args[2]),
                               state, reached)
        if (exact_range is not None and self.scalar_width is not None
                and isinstance(c.expr, ir.Lit)):
            new = _const(c.expr.value, self.scalar_width)
        else:
            new = self.bind_value(self.value(c.expr, state.rho))
        old = state.rho.get(c.var)
        written = self._write(new, old, self._relative_guard(reached), c.var)
        if self.compact_arrays and isinstance(written, (list, FunctionalArray)):
            written = self.bind_value(written)
        state.write(c.var, written)
        self._rebind(
            c.var, exact_range if exact_range is not None else self.rng(c.expr),
            reached)
        return state

    def _rebind(self, name, rng, reached, *, preserves_cells=False):
        """What a variable can hold after a write that may not have happened.

        A guarded write leaves either the new value or the old one, so the
        variable can hold what either could, and the width of what carries it
        has to cover both.
        """
        old = self.env.get(name)
        # A Store replaces at most one cell and therefore retains the possible
        # values of every untouched cell even when it is unguarded.  Assignment
        # replaces the whole value and only retains the old range when its
        # control guard may be false.
        if old is not None and (preserves_cells or not z3.is_true(reached)):
            rng = rng.join(old)
        self.env[name] = rng
        # What a subterm can hold depends on what the variables hold now, so a
        # range remembered from before a write is no longer about this step.
        self._range.clear()

    def _write(self, new, old, reached, name):
        """What a variable holds after a command that may not have run.

        Within a branch this guard is relative to its entry condition. The
        branch exit, not every assignment, retains the enclosing inactive value.
        """
        if z3.is_true(reached):
            return new
        if old is None:
            old = _zeros_like(new)
        new_array = isinstance(new, (list, FunctionalArray))
        old_array = isinstance(old, (list, FunctionalArray))
        if new_array != old_array:
            return self.stop("shape", f"{name} is an array on one branch and a "
                                      "value on the other", new)
        if isinstance(new, FunctionalArray) or isinstance(old, FunctionalArray):
            if not _same_shape(new, old):
                return self.stop(
                    "shape", f"{name} has incompatible functional array shapes",
                    new)
            if isinstance(new, FunctionalArray) \
                    and isinstance(old, FunctionalArray):
                return FunctionalArray.choose(reached, new, old)
            new = _materialize_functional(new)
            old = _materialize_functional(old)
        if isinstance(new, list):
            if len(new) != len(old):
                return self.stop(
                    "shape", f"{name} is an array of {len(new)} cells where it "
                             f"held {len(old)}", new)
            return [self._write(x, y, reached, name)
                    for x, y in zip(new, old)]
        if z3.eq(new, old):
            return new
        return self.bind(z3.If(reached, *widen(new, old)))

    def _store(self, c, state, reached):
        cells = state.rho.get(c.var)
        if not isinstance(cells, (list, FunctionalArray)):
            self.stop("shape", f"a store into {c.var}, which is not an array")
            return state
        index = self.bind(self.expr(c.index, state.rho))
        value = self.bind_value(self.value(c.expr, state.rho))
        written_guard = self._relative_guard(reached)
        if isinstance(cells, FunctionalArray):
            if isinstance(value, (list, FunctionalArray)):
                self.stop(
                    "shape", f"a store into scalar array {c.var} writes an array")
                return state
            state.write(c.var, self.bind_value(cells.store(index, value, written_guard)))
            self._rebind(c.var, self.rng(c.expr), reached,
                         preserves_cells=True)
            return state
        # A cell the store cannot name is the cell it was, which is neither a
        # definition nor a node. Binding is for terms worth naming, and an
        # unchanged cell is already named.
        at = index.as_signed_long() if isinstance(index, z3.BitVecNumRef) else None
        if at is not None and 0 <= at < len(cells):
            # Closed indices (including unrolled-loop arithmetic) identify one
            # cell exactly. Preserve the immutable remainder without walking
            # its scalar leaves or constructing false guards for every cell.
            written = list(cells)
            written[at] = self.bind_value(self.choose_value(
                written_guard, value, cells[at], f"store into {c.var}"))
        else:
            written = []
            for j, cell in enumerate(cells):
                got = self.choose_value(
                    _conj(written_guard, _at(index, j, len(cells))), value, cell,
                    f"store into {c.var}")
                written.append(self.bind_value(got))
        state.write(c.var, written)
        self._rebind(c.var, self.rng(c.expr), reached,
                     preserves_cells=True)
        return state

    def _if(self, c, state, reached):
        """Encode each arm from the same entry and join only changed fields."""
        taken = yield from _retry_encoding(self._branch_condition, c, state)
        parent = self._branch_guard
        arms = []
        try:
            for body, condition in ((c.then, taken), (c.orelse, _neg(taken))):
                under = yield from _retry_encoding(self.bind, _conj(reached, condition))
                self._branch_guard = under
                with state.branch() as delta:
                    state = yield (body, state, under)
                arms.append(delta)
        finally:
            self._branch_guard = parent
        yes, no = arms
        guard = yield from _retry_encoding(self._relative_guard, reached)
        for name in dict.fromkeys((*yes, *no)):
            old = state.rho.get(name)
            fallback = old if old is not None else _zeros_like(
                yes[name] if name in yes else no[name])
            new = yield from _retry_encoding(
                self.choose_value, taken, yes.get(name, fallback),
                no.get(name, fallback), f"branch join for {name}")
            written = yield from _retry_encoding(self._write, new, old, guard, name)
            state.write(name, written)
        return state

    def _relative_guard(self, reached):
        if self._branch_guard is None:
            return reached
        if reached.eq(self._branch_guard):
            return z3.BoolVal(True, ctx=reached.ctx)
        return self._guarded_reads.condition(reached, self._branch_guard)

    def _branch_condition(self, c, state):
        taken = None
        if self.occurrence_carriers is not None and self._value_range_premises:
            # Position ranges describe executions that reach this command.
            # A preceding guarded assignment can still leave an ITE in rho,
            # although its active arm proves this guard constant.  The plan
            # correctly omits carriers for the impossible branch; preserve
            # that proof here instead of trying to encode its dead terms.
            truth = ranges._constant_truth(c.guard, self.rng)
            if truth is not None:
                taken = z3.BoolVal(truth)
        if taken is None:
            # IR expressions are total and carry no source events. If the
            # existing occurrence proof already selected a constant decision,
            # building (and then discarding) its symbolic expression adds only
            # unused SSA equations. Keep dynamic guards exactly as before.
            guard = self.expr(c.guard, state.rho)
            taken = guard != _const(0, guard.size())
        return self.bind(taken)


def verify_encoding(proc, env=None, tag=""):
    """Every type, shape and range fault in encoding ``proc``, or an empty list.

    M3's acceptance, and the reason it is here rather than in a module that predicts
    this one: **an empty list implies `encode_step` cannot fail for that class, because
    the thing that answers is the thing that would fail.** Two readings of the program
    that have to agree are two readings that can differ, and the attempt to write the
    second one was conservative by 365 models in the direction that costs coverage and,
    when made to flow, wrong by three in the direction that costs soundness.

    What it is not is a claim that encoding succeeds. An operation with no primitive is
    T10's column and a width nothing can build is M4's; neither is a gap in the
    frontend, and neither is reported here.

    The formula this builds stands on substituted values wherever a fault was recorded
    and is discarded. That is the whole reason `collect` is not a public argument to
    `encode_step`: a caller must not be able to ask for a formula built this way.
    """
    enc = _Commands(proc, dict(env or {}), tag)
    enc.collect = True
    try:
        encode_step(proc, env=env, tag=tag, _into=enc)
    except Exception:                              # noqa: BLE001, S110
        # A failure outside the collected class ends the walk, and what was recorded
        # up to there still stands. An empty list plus a raise from `encode_step` is
        # the honest answer that this class had nothing to say about it: an operation
        # with no primitive is T10's column, and a value whose decimal form is past
        # what the solver's bindings will print -- one model reaches that -- is a
        # capability limit, which is M4's. Broad on purpose, because this function
        # must be total: a caller asking whether the frontend left anything unanswered
        # should not be handed the backend's exception instead of an answer.
        pass
    return list(enc.faults)


def encode_step(proc, pre=None, env=None, tag="", plan=None,
                occurrence_carriers=None, scalar_width=None, _into=None):
    """Encode one public step after fully validating any supplied plan."""

    return _encode_step(
        proc, pre=pre, env=env, tag=tag, plan=plan,
        occurrence_carriers=occurrence_carriers, scalar_width=scalar_width,
        _into=_into, _derived_plan=False)


def encode_goal_query(proc, goals, pre=None, env=None, tag="", plan=None,
                      occurrence_carriers=None, scalar_width=None, *,
                      _checkpoint=None):
    """Encode one step, retaining only the audited cone of ``goals`` when possible.

    The complete step remains the fail-closed fallback.  Callers choose goals but
    cannot supply a command subset: the verifier derives the cone and checks it against
    every occurrence before the reduced formula is constructed.
    """

    goals = tuple(goals)
    if not goals or any(type(goal) is not str or not goal for goal in goals):
        raise TypeError("a local exact query names nonempty string goals")
    if len(goals) != len(set(goals)):
        raise ValueError("a local exact query names each goal once")
    from . import slicing

    events = slicing.linearize(proc.body)
    keep = slicing.for_run(proc, goals, steps=1, events=events)
    if keep is None or slicing.audit(proc, goals, keep, events=events):
        return _encode_step(
            proc, pre=pre, env=env, tag=tag, plan=plan,
            occurrence_carriers=occurrence_carriers,
            scalar_width=scalar_width, _checkpoint=_checkpoint)
    if plan is None and occurrence_carriers is None and scalar_width is None:
        # Derive a fresh query plan from the audited dependencies, rather than
        # charging unrelated loops to its expansion/carrier budget. Validate
        # the original contract first; pruning is not a source-admission path.
        # Explicit plans/carriers below still belong to the original procedure.
        proc = _query_procedure(proc, keep, _procedure_validation(proc))
        # The projection also retains CFG controls and their dependencies.
        # Re-index those rebuilt controls for the selected observation cone;
        # their planning presence does not require encoding unrelated work.
        events = slicing.linearize(proc.body)
        keep = slicing.for_run(proc, goals, steps=1, events=events)
        if keep is None or slicing.audit(proc, goals, keep, events=events):
            keep = frozenset(id(command) for command in ir.walk(proc.body))
    return _encode_query_step(
        proc, pre=pre, env=env, tag=tag, plan=plan,
        occurrence_carriers=occurrence_carriers,
        scalar_width=scalar_width, keep=keep, goals=goals,
        _checkpoint=_checkpoint)


def _query_procedure(proc, keep, validation):
    """An admitted query projection retains even dropped locals' inferred types."""
    from . import slicing

    _checked_procedure_validation(validation, proc)
    return slicing._query_projection(proc, keep, validation.types)


def _encode_goal_occurrence_query(
        proc, goal, site, pre=None, env=None, tag="", plan=None,
        occurrence_carriers=None, scalar_width=None, *, _events=None,
        _procedure_validation=None, _plan_prevalidated=False):
    """Encode one lowered occurrence for generation-only witness search.

    One source goal can appear at many IR sites after helper inlining.  A model
    satisfying any one site is a candidate for concrete replay.  This private
    entry point keeps the site-specific operation inside the exact provider: a
    caller may not turn one site's UNSAT result into a public-goal claim without
    checking every other site.
    """

    from . import slicing

    events = slicing.linearize(proc.body) if _events is None else _events
    keep = _goal_occurrence_keep(proc, goal, site, events)
    return _encode_query_step(
        proc, pre=pre, env=env, tag=tag, plan=plan,
        occurrence_carriers=occurrence_carriers,
        scalar_width=scalar_width, keep=keep, goals=(goal,),
        _procedure_validation=_procedure_validation,
        _plan_prevalidated=_plan_prevalidated)


def _goal_occurrence_keep(proc, goal, site, events, *, _index=None):
    """Derive and audit a static occurrence cone before any formula is built.

    Equal returned selections may share an encoding, but every member must
    pass this check and every requested occurrence still needs an answer.
    """
    if type(goal) is not str or not goal:
        raise TypeError("a local exact query names a nonempty string goal")
    if type(site) is not int or site < 0:
        raise TypeError("a goal occurrence is a nonnegative event position")
    from . import slicing

    bodies = parents = reads_cache = branches = None
    if _index is not None:
        indexed = _index.checked_for(proc)
        if events is not indexed.events and tuple(events) != indexed.events:
            raise ValueError("events and slice index describe different bodies")
        events, bodies, parents = indexed.events, indexed.loop_bodies, indexed.parents
        reads_cache = indexed.command_reads
        branches = indexed.branches
    cut = slicing.cone(events, goal, total=len(events), _site=site,
                       _bodies=bodies, _parents=parents, _reads=reads_cache, _branches=branches)
    if cut is None:
        raise EncodingError(
            f"{goal!r} has no lowered occurrence", kind="goal")
    # This selection was just derived here, not supplied by a caller. Calling
    # audit_occurrence would derive the identical cone a second time and compare
    # it with itself. Check the independent binding obligation once; external
    # or stored selections still use the full occurrence audit.
    refused = (indexed.binding_faults(cut.kept) if _index is not None else
               slicing._binding_faults(proc, cut.kept, events, reads_cache))
    if refused:
        raise EncodingError(
            "the occurrence cone is not closed: " + str(refused[0]),
            kind="goal")
    return frozenset(cut.kept)


def _encode_query_step(proc, pre=None, env=None, tag="", plan=None,
                       occurrence_carriers=None, scalar_width=None, *,
                       keep, goals, _procedure_validation=None,
                       _plan_prevalidated=False, _checkpoint=None,
                       _defer_constraint=False, _construction=None,
                       _plan_checkpoint=None, _prefix_goals=False,
                       _state_ranges_established=False):
    """Encode one audited goal cone against the complete procedure plan.

    This is deliberately private.  ``keep`` is produced and audited by
    :mod:`slicing`; accepting it through :func:`encode_step` would let a caller
    silently replace the procedure semantics by an arbitrary subset.
    """
    keep = frozenset(keep)
    goals = frozenset(goals)
    if any(type(identity) is not int for identity in keep):
        raise TypeError("a query command cone contains object identities")
    if any(type(goal) is not str or not goal for goal in goals):
        raise TypeError("a query command cone names nonempty string goals")
    return _encode_step(
        proc, pre=pre, env=env, tag=tag, plan=plan,
        occurrence_carriers=occurrence_carriers, scalar_width=scalar_width,
        _derived_plan=False, _analysis_keep=keep, _analysis_goals=goals,
        _procedure_validation=_procedure_validation,
        _plan_prevalidated=_plan_prevalidated, _checkpoint=_checkpoint,
        _defer_constraint=_defer_constraint, _construction=_construction,
        _plan_checkpoint=_plan_checkpoint, _prefix_goals=_prefix_goals,
        _state_ranges_established=_state_ranges_established)


def _encode_boundary_step(proc, pre=None, env=None, tag="", plan=None,
                          occurrence_carriers=None, scalar_width=None, *,
                          keep, _validation, _checkpoint=None):
    """Encode a verifier-derived, failure-erased boundary over-approximation."""

    keep = frozenset(keep)
    if any(type(identity) is not int for identity in keep):
        raise TypeError("a boundary command cone contains object identities")
    _checked_procedure_validation(_validation, proc)
    return _encode_step(
        proc, pre=pre, env=env, tag=tag, plan=plan,
        occurrence_carriers=occurrence_carriers, scalar_width=scalar_width,
        _derived_plan=True, _analysis_keep=keep, _analysis_goals=frozenset(),
        _procedure_validation=_validation, _boundary_overapprox=True,
        _checkpoint=_checkpoint)


def _encode_derived_step(proc, pre=None, env=None, tag="", plan=None,
                         occurrence_carriers=None, scalar_width=None,
                         _validation=None, *, observations=True, _checkpoint=None):
    """Encode with a plan just derived inside the position-plan builder."""

    return _encode_step(
        proc, pre=pre, env=env, tag=tag, plan=plan,
        occurrence_carriers=occurrence_carriers, scalar_width=scalar_width,
        _derived_plan=True, _procedure_validation=_validation,
        _analysis_goals=None if observations else frozenset(),
        _checkpoint=_checkpoint)


def _encode_step(proc, pre=None, env=None, tag="", plan=None,
                 occurrence_carriers=None, scalar_width=None, _into=None,
                 _derived_plan=False, _analysis_keep=None,
                 _analysis_goals=None, _procedure_validation=None,
                 _boundary_overapprox=False, _plan_prevalidated=False,
                 _checkpoint=None, _defer_constraint=False, _construction=None,
                 _state_ranges_established=False, _plan_checkpoint=None,
                 _prefix_goals=False, _array_recipes=None):
    if _prefix_goals and (_construction is None or _analysis_keep is None):
        raise TypeError("goal prefixes need an owned local query construction")
    arguments = {
        "proc": proc, "pre": pre, "env": env, "tag": tag, "plan": plan,
        "occurrence_carriers": occurrence_carriers, "scalar_width": scalar_width,
        "_into": _into, "_derived_plan": _derived_plan, "_analysis_keep": _analysis_keep,
        "_analysis_goals": _analysis_goals, "_procedure_validation": _procedure_validation,
        "_boundary_overapprox": _boundary_overapprox,
        "_plan_prevalidated": _plan_prevalidated, "_defer_constraint": _defer_constraint,
        "_state_ranges_established": _state_ranges_established,
        "_plan_checkpoint": _plan_checkpoint, "_prefix_goals": _prefix_goals,
        "_array_recipes": _array_recipes}
    if _construction is not None:
        return _construction.advance(arguments, _checkpoint)
    iterator = _encode_step_iter(**arguments, _checkpoint=_checkpoint)
    try:
        next(iterator)
    except StopIteration as done:
        return done.value
    raise RuntimeError("only an owned query construction may suspend encoding")


def _encode_step_iter(proc, pre=None, env=None, tag="", plan=None,
                      occurrence_carriers=None, scalar_width=None, _into=None,
                      _derived_plan=False, _analysis_keep=None,
                      _analysis_goals=None, _procedure_validation=None,
                      _boundary_overapprox=False, _plan_prevalidated=False,
                      _checkpoint=None, _defer_constraint=False,
                      _state_ranges_established=False, _plan_checkpoint=None,
                      _prefix_goals=False, _array_recipes=None):
    """Encode one step of ``proc`` as a formula over its inputs and state.

    ``pre`` gives the terms standing for the state read and the inputs applied,
    so that unrolling a run is chaining the state one step leaves into the state
    the next one reads. Without it fresh symbols are made from the ranges.

    ``tag`` distinguishes the names this encoding makes from another's. Two
    steps of one run are two encodings, and conjoining two that share names
    asserts that their intermediate values agree, which neither step says. The
    symptom is a goal reachable at one step reported unreachable at two.

    ``plan`` is M3's: an `EncodingPlan` whose widths and cell counts were decided
    before any term was built, so the encoder stops discovering them. **It is read
    rather than trusted.** A plan whose width for a name differs from the one this
    step would carry is a plan built against a different environment or a
    different bound, and encoding against it would silently narrow a term -- so
    the disagreement is a refusal through `stop`, in the same column as any other
    thing the frontend had not worked out.

    That check is not hypothetical. Both places computed "the width the encoder
    carries a range at" as their own expression and one of them omitted
    `signed=True`, so every non-negative range in `plan.widths` was **one bit
    narrower** than the encoder's, 20 of 20 across four models. Nothing had broken
    only because nothing read the plan yet. Both now call
    `ranges.encoding_width`.
    """
    env = dict(env or {})
    # Validate before a plan fingerprint, boundary-layout reader, command
    # encoder, or solver value sees the public procedure.  Plan validation is
    # deliberately not allowed to become a bypass around this capability.
    enc = _into if _into is not None else _Commands(proc, env, tag)
    enc._query_prefix = None
    enc._checkpoint = _checkpoint
    enc._defer_constraint = _defer_constraint
    enc.compact_arrays = bool(_procedure_validation is not None and _procedure_validation.compact_arrays)
    enc._array_recipes = _array_recipes
    yield from _retry_encoding(enc.checkpoint)
    if _analysis_keep is not None:
        if (_into is not None
                or (_derived_plan and not _boundary_overapprox)):
            raise TypeError("an analysis cone is only consumed by its query encoder")
        if _boundary_overapprox:
            _checked_procedure_validation(_procedure_validation, proc)
        enc.analysis_keep = _analysis_keep
        enc.analysis_goals = _analysis_goals
    if _procedure_validation is None:
        try:
            validate_procedure(proc)
        except EncodingError as exc:
            if enc.collect:
                enc.stop(exc.kind, str(exc))
            raise
    else:
        _checked_procedure_validation(_procedure_validation, proc)
    enc.error_codes = proc.semantics.error_codes
    for name, declared_bounds in proc.semantics.solver_input_bounds():
        rng = env.get(name)
        if (not isinstance(rng, ranges.Range)
                or (rng.lo, rng.hi) != declared_bounds):
            enc.stop(
                "range",
                f"the range environment for input {name!r} does not equal "
                f"its finite procedure domain {declared_bounds}",
            )
    if plan is None:
        # A chained state may have grown since the caller's initial range
        # estimate. Derive this private plan from its actual signed carriers;
        # never narrow the supplied terms or assume the estimate is invariant.
        # Explicitly supplied plans retain their exact layout checks below.
        for name, kind in proc.semantics.state_types:
            if pre is None or name not in pre:
                continue
            if not _matches_type(pre[name], kind):
                enc.stop("shape", f"{name} does not have its declared recursive shape")
            actual = (env.get(name) if _state_ranges_established else
                      _supplied_state_range(pre[name]))
            if actual is not None:
                env[name] = actual
        enc.env = dict(env)
    graph = proc.graph if _procedure_validation is None else _procedure_validation.graph
    needs_occurrences = graph is not None or any(
        isinstance(cmd, ir.For) for cmd in ir.walk(proc.body))
    implicit_plan = plan is None and needs_occurrences
    if implicit_plan:
        # The query may already fix control/data inputs. Plan only their
        # possible executions, but keep the complete public boundary and bind
        # this specialization to the supplied literals below.
        fixed_inputs = {
            name: pre[name].as_signed_long() for name in proc.inputs
            if pre is not None and name in pre and z3.is_bv_value(pre[name])
            and isinstance(env.get(name), ranges.Range)
            and env[name].lo <= pre[name].as_signed_long() <= env[name].hi
        }
        if _procedure_validation is None:
            from .plan import plan_encoding

            plan = plan_encoding(proc, env, fixed_inputs=fixed_inputs,
                                 _checkpoint=_plan_checkpoint)
            _derived_plan = True
        else:
            # Each cone used to rederive the whole procedure's loop/range
            # plan. The owned validation scope retains that exact plan for
            # identical ranges, including across steps with equal carriers.
            from .plan import reuse_encoding_plan

            plan = reuse_encoding_plan(
                _procedure_validation, env, fixed_inputs=fixed_inputs, _checkpoint=_plan_checkpoint)
            _plan_prevalidated = True
        yield from _retry_encoding(enc.checkpoint)
    if occurrence_carriers is not None and plan is None:
        raise TypeError(
            "position occurrence carriers require a validated EncodingPlan")
    if scalar_width is not None:
        if type(scalar_width) is not int or scalar_width < 1:
            raise ValueError("a horizon-wide scalar width is a positive integer")
        if occurrence_carriers is None:
            raise TypeError(
                "a horizon-wide scalar width requires position occurrence carriers")
    if plan is not None:
        from .plan import EncodingPlan, OccurrenceCarriers

        if type(plan) is not EncodingPlan:
            raise TypeError("plan is not an EncodingPlan")
        for name, value in plan.fixed_inputs.items():
            supplied = (pre or {}).get(name)
            if not z3.is_bv_value(supplied) or supplied.as_signed_long() != value:
                from .plan import PlanMismatch

                raise PlanMismatch(
                    f"the encoding plan requires concrete input {name!r}={value}")
        if _plan_prevalidated:
            if _procedure_validation is None:
                raise TypeError(
                    "a prevalidated plan needs invocation-local procedure evidence")
            _checked_procedure_validation(_procedure_validation, proc)
        elif _derived_plan:
            # ``plan_positions`` has just constructed this plan from the same
            # procedure and environment.  Recheck its immutable binding but do
            # not repeat the loop/range derivation that produced it.  Public
            # ``encode_step`` never enters this path.
            plan.validate_binding(
                proc, env, _procedure_validation=_procedure_validation)
        else:
            plan.validate(proc, env)
        planned_occurrences = plan.occurrence_carriers
        if occurrence_carriers is None:
            occurrence_carriers = planned_occurrences
        else:
            if type(occurrence_carriers) is not OccurrenceCarriers:
                raise TypeError(
                    "position occurrence carriers are an OccurrenceCarriers value")
            same_ranges = (
                set(occurrence_carriers) == set(planned_occurrences)
                and all(
                    occurrence_carriers[key].lo == planned_occurrences[key].lo
                    and occurrence_carriers[key].hi == planned_occurrences[key].hi
                    for key in planned_occurrences)
            )
            if not same_ranges:
                raise EncodingError(
                    "the position occurrence carriers do not belong to the "
                    "validated one-step plan", kind="range")
            if scalar_width is not None and any(
                    carrier.width != scalar_width
                    for carrier in occurrence_carriers.values()):
                raise EncodingError(
                    "the position occurrence carriers do not use the one "
                    "horizon-wide scalar encoding", kind="width")
        from .plan import _materialization_limit

        observing_all = _analysis_keep is None and _analysis_goals is None
        required = [item for item in plan.unresolved
                    if observing_all or not _materialization_limit(item)]
        if required and _analysis_keep is not None and not _boundary_overapprox:
            from .plan import ExpressionPosition, _command_numbers

            # A private, dependency-audited goal cone never evaluates dropped
            # commands. Their expression carriers cannot block this query.
            # Keep all boundary, loop and unlocated obligations; full-step and
            # boundary-proof encoding still require the complete plan.
            retained = {number for identity, number in
                        _command_numbers(proc.body).items()
                        if identity in _analysis_keep}
            required = [item for item in required
                        if not isinstance(item.what, ExpressionPosition)
                        or item.what.role == "trip_count" or item.kind == "resource"
                        or item.what.command in retained]
        # Validation re-derives the expansion cost, so this guard remains
        # authoritative even if an adversarial caller reconstructs a plan
        # with its diagnostic list removed.
        if (observing_all and plan.expansion_cost > TERM_BUDGET
                and not any(item.kind == "capability"
                            and item.what == "encoding-expansion"
                            for item in required)):
            from .plan import Unresolved

            required.append(Unresolved(
                "capability", "encoding-expansion",
                f"the planned loop expansion needs {plan.expansion_cost} "
                f"command copies, over the reported capability of "
                f"{TERM_BUDGET}"))
        if required:
            first = required[0]
            message = (f"the encoding plan is unresolved at {first.what}: "
                       f"{first.detail}")
            if enc.collect:
                for item in required:
                    enc.stop(
                        item.kind,
                        f"the encoding plan is unresolved at {item.what}: "
                        f"{item.detail}")
            raise EncodingError(message, first.kind)
        enc.loop_bounds = dict(plan.loop_bounds)
        enc.loop_visits = dict(plan.loop_visits)
        enc.occurrence_carriers = occurrence_carriers
        enc.scalar_width = scalar_width
    from .plan import _command_numbers

    enc.command_numbers = _command_numbers(proc.body)
    planned = dict(getattr(plan, "widths", None) or {})
    planned_cells = dict(getattr(plan, "cells", None) or {})
    planned_shapes = dict(getattr(plan, "shapes", None) or {})
    widths = {}
    rho = dict(pre or {})
    contract = []
    enc._value_range_premises = True
    boundary_types = {
        **dict(proc.semantics.state_types),
        **dict(proc.semantics.input_types),
    }
    for name in proc.state + proc.inputs:
        yield from _retry_encoding(enc.checkpoint)
        supplied = name in rho
        shape = boundary_types.get(name)
        told_cells = planned_cells.get(name)
        if told_cells is not None \
                and told_cells != getattr(shape, "cells", None):
            enc.stop("shape",
                     f"{name} is planned with {told_cells} cells and the step "
                     f"declares {getattr(shape, 'cells', None)}")
        told_shape = planned_shapes.get(name)
        declared_shape = _type_shape(shape)
        if told_shape is not None and told_shape != declared_shape:
            enc.stop("shape",
                     f"{name} is planned with shape {told_shape} and the "
                     f"step declares {declared_shape}")
        if supplied and shape is not None and not _matches_type(rho[name], shape):
            enc.stop("shape", f"{name} does not have its declared recursive shape")
        rng = env.get(name)
        if rng is None:
            if supplied:
                continue
            # Through the encoder, so `verify_encoding` sees it. It was a plain raise,
            # which its `except` then swallowed into an empty list -- a miss at the
            # very first gate, and misses are the one thing that arrangement must not
            # have. Collecting leaves the name unbound, so a later read of it is
            # reported too, which is the truth about a step with no width for it.
            enc.stop("range", f"{name} has no range, so it has no width")
            continue
        try:
            minimum_width = validated_width(rng, name)
        except EncodingError as exc:
            minimum_width = enc.stop(exc.kind, str(exc), 1)
        width = scalar_width or minimum_width
        if width < minimum_width:
            enc.stop(
                "width",
                f"{name} needs {minimum_width} bits but the horizon-wide "
                f"scalar encoding has {width}")
        if not supplied:
            told = planned.get(name)
            if told is not None and (
                    told != width if scalar_width is None else told > width):
                # A plan that disagrees is not a plan for this step. Reported
                # rather than preferred either way: taking the plan's would narrow
                # the term, and taking the encoder's would leave a reader holding a
                # plan that does not describe the formula.
                enc.stop("range",
                         f"{name} is planned at {told} bits and this step carries "
                         f"it at {width}, so the plan is for another bound or "
                         f"another environment")
            widths[name] = width
            if isinstance(shape, value_types.Array):
                # An array is its cells. One bit-vector for a name declared as a
                # list was a state variable with no cells, so a store into it
                # failed for want of an array and the step could not be encoded
                # at all. Which names are arrays is on the procedure now rather
                # than being inferred from whatever this loop happened to build.
                from .uniform_array import UniformArray

                compact = (rng if type(proc.initial.get(name)) is UniformArray
                           else None)
                rho[name] = _symbolic_value(
                    name, shape, widths[name], tag, compact=compact,
                    leaf_width=minimum_width)
            else:
                rho[name] = _symbolic_value(
                    name, shape, widths[name], tag, leaf_width=minimum_width)
        if supplied:
            expected_width = (scalar_width if scalar_width is not None
                              else planned.get(name))
            if expected_width is not None:
                for term in _flatten_tree(rho[name]):
                    # A freshly derived state plan covers all recursive leaves;
                    # a narrower leaf already denotes a subset of that domain.
                    # A literal remains the same integer at a wider physical
                    # width. Its exact value established this private plan;
                    # arbitrary symbols still require the whole carrier.
                    compatible = ((term.size() <= expected_width or z3.is_bv_value(term)
                                   or _state_ranges_established)
                                  if implicit_plan and name in proc.state
                                  else term.size() == expected_width)
                    if not compatible:
                        enc.stop(
                            "range", f"{name} is planned at {expected_width} bits "
                            "but its supplied value has another leaf width")
                    # A supplied symbolic state establishes the entry range
                    # only if that range covers its entire signed carrier.
                    # Otherwise retain the old literal-only evidence rule.
                    # The complete reset-origin unroller separately owns a
                    # checked position-closure proof for its chained terms;
                    # public steps and arbitrary supplied states do not.
                    if name in proc.state and not _state_ranges_established and not (
                            (z3.is_bv_value(term)
                             and rng.lo <= term.as_signed_long() <= rng.hi)
                            or (rng.lo <= -(1 << (term.size() - 1))
                                and rng.hi >= (1 << (term.size() - 1)) - 1)):
                        enc._value_range_premises = False
            elif name in proc.state and not _state_ranges_established:
                enc._value_range_premises = False
        contract.extend(_semantic_type_constraints(rho[name], shape))
        if isinstance(rho[name], FunctionalArray):
            contract.extend(rho[name].base_restrictions)
        # A width holds the range but does not say it: a sixteen bit port is
        # carried in seventeen signed bits, and without this the solver may
        # answer with a negative input the interface cannot present. The
        # answer would still be sound where it is unsatisfiable, since that
        # searched more stimuli than exist, but a stimulus it produced would
        # be one nothing could drive.
        #
        # An input is bounded whoever supplied its symbol, since what bounds
        # it is the interface. A state the caller supplied is a term the step
        # before it left, and bounding that would be asserting the range
        # analysis rather than using it, so only a state symbol made here is
        # bounded.
        if supplied and name not in proc.inputs:
            continue
        # Every cell of an array is bounded by the range, since the range
        # analysis joins what the cells hold and says nothing about which cell
        # holds what.
        if not isinstance(rho[name], FunctionalArray):
            for term in _flatten_tree(rho[name]):
                # Nonnegative signed carriers are exactly those with sign bit
                # zero. Expose that bit instead of comparing the full payload:
                # physical zero-extension then makes this restriction constant
                # without pulling an unused queue/datapath into the query.
                lower = (z3.Extract(term.size()-1, term.size()-1, term) == 0
                         if rng.lo == 0 else term >= _const(rng.lo, term.size()))
                contract.append(z3.And(
                    lower,
                    term <= _const(rng.hi, term.size())))

    # The supported-goal set is structural, while each hit condition is
    # semantic.  Seed every producer with false so constant-dead paths may be
    # omitted from the formula without turning an unreachable goal into an
    # unsupported one.
    produced_goals = ir.goals_of(proc.body)
    if _analysis_goals is not None:
        produced_goals &= set(_analysis_goals)
    state = _State(
        rho, {goal: z3.BoolVal(False) for goal in sorted(produced_goals)},
        z3.BoolVal(True), _code("", enc.error_codes),
        {goal: [] for goal in sorted(produced_goals)})
    # Only explicit boundary restrictions justify these constants. Fresh state
    # leaves have the same restrictions as inputs; a caller-supplied state does
    # not, even if its proposed environment happens to be a singleton.
    def singleton_boundary(value, literal):
        if isinstance(value, list):
            return [singleton_boundary(item, literal) for item in value]
        if isinstance(value, FunctionalArray):
            # Fresh compact singleton carriers are already constant bases.
            return value
        return z3.BitVecVal(literal, value.size(), ctx=value.ctx)

    restricted = (*proc.inputs, *(name for name in proc.state
                                 if name not in (pre or {})))
    for name in restricted:
        support = env.get(name)
        if isinstance(support, ranges.Range) and support.lo == support.hi:
            state.rho[name] = singleton_boundary(state.rho[name], support.lo)
    if enc.compact_arrays:
        def compact(value, kind):
            if not isinstance(value, list) or not isinstance(kind, value_types.Array):
                return value
            if value and kind.elem == value_types.INTEGER:
                return FunctionalArray.from_cells(value)
            return [compact(item, kind.elem) for item in value]
        # Keep original boundary atoms and all component input constraints in
        # rho. Only body reads/writes use the persistent representation.
        state.rho = {name: compact(value, proc.types.get(name)) for name, value in state.rho.items()}
    if _prefix_goals:
        offered = set()

        def query_prefix(goal, current, reached):
            # Offer the first live symbolic occurrence per goal. Later copies
            # remain in the continuation and its complete disjunction. This
            # avoids a solver invocation on every unrolled loop iteration.
            if goal in offered:
                return None
            offered.add(goal)
            return _QueryPrefix(StepEncoding(
                rho, {}, {goal: reached}, current.ok, current.error, None,
                widths, error_codes=enc.error_codes,
                definitions=enc.definitions, restrictions=contract,
                hit_occurrences={goal: (reached,)},
                array_selections=enc.array_selections))

        enc._query_prefix = query_prefix
    out = yield from enc._cmd(proc.body, state, z3.BoolVal(True))
    # The state keeps its own names and each output is read under its port's
    # name, whatever variable the lowering held it in.
    state_post = {name: out.rho[name]
                  for name in proc.state if name in out.rho}
    post = dict(state_post)

    def public_output(value):
        # ``FunctionalArray`` is an internal exact-encoding optimization.  The
        # public step boundary remains the componentwise finite array used by
        # every existing consumer and by the appendix definition.
        if isinstance(value, FunctionalArray):
            width = max(1, value.length.bit_length() + 1)
            return [value.select(_const(index, width))
                    for index in range(value.length)]
        if isinstance(value, list):
            return [public_output(item) for item in value]
        return value

    if _analysis_keep is None:
        post.update({
            port: public_output(out.rho[var])
            for port, var in proc.output_vars.items() if var in out.rho
        })
    # How many transactions the step produced, under the name the count is
    # reported by rather than the lowering's variable. Without it the formula
    # describes the buffers and not the sequence, so a query about what a step
    # emits could not tell nothing from a single zero, and the two readings had
    # nothing to disagree about even when they disagreed.
    if (_analysis_keep is None and proc.count is not None
            and proc.count in out.rho):
        post[COUNT] = out.rho[proc.count]
    # The definitions are part of the step: without them the names the state
    # and the hits are written in stand for nothing. The contract is part of
    # it too, since a stimulus outside what the interface admits is not one.
    yield from _retry_encoding(enc.checkpoint)
    constraint = None if _defer_constraint else z3.And(*enc.defs, *contract)
    output_checks = ({} if _analysis_keep is not None
                     else _output_contract_checks(proc, out.rho))
    yield from _retry_encoding(enc.checkpoint)
    return StepEncoding(
        rho, post, out.hits, out.ok, out.error,
        constraint, widths, out.rho, output_checks, state_post,
        enc.error_codes, enc.definitions, contract,
        out.hit_occurrences, enc.array_selections)
