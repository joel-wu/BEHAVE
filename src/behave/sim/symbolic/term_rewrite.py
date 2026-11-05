"""Optional equivalent rewrites over an encoder's append-only SSA definitions."""

import z3

from behave.core import runtime_defaults as defaults

from .bitvectors import COMPARISONS
from .term_bounds import _StructuralProofLimit, _StructuralProofWork, _SyntacticClosureProof

_PROOF_WORK = defaults.SIM_BEHAVIOR_REWRITE_PROOF_NODES


class _ComparisonRewriter:
    """Reduce comparisons and arithmetic carriers using proved scalar bounds.

    Only defining equations participate: no input ranges, normal-completion
    premise or proposed carrier is assumed. Guarded values retain the union of
    both arms. Thus selecting an arm preserves the term even on inactive and
    failing paths. The existing closure proof supplies the signed/unsigned and
    no-wrap rules; this adapter only keeps its SSA view current.

    One instance belongs to one encoder. Its definitions must be append-only
    and backward-referencing; the encoder retains both sides of every equation.
    Work exhaustion keeps the original expression, never a verdict or refusal.
    Memoized syntax proofs follow the owning construction's cancellation hook.
    An explicitly supplied diagnostic work limit still declines a rewrite.
    """

    def __init__(self, definitions, *, array_selections=(), work_limit=_PROOF_WORK,
                 checkpoint=None):
        self._definitions = definitions
        self._cursor = 0
        self._array_selections = array_selections
        self._selection_cursor = 0
        self._proof = _SyntacticClosureProof(
            {}, work=_StructuralProofWork(work_limit, float("inf"), checkpoint=checkpoint))
        self._exhausted = False
        self._results = {}
        self._array_bounds = {}

    def _refresh(self):
        while self._cursor < len(self._definitions):
            definition = self._definitions[self._cursor]
            self._proof.definitions[definition.lhs.get_id()] = definition.rhs
            self._proof.terms[definition.lhs.get_id()] = definition.lhs
            self._cursor += 1
        while self._selection_cursor < len(self._array_selections):
            selection = self._array_selections[self._selection_cursor]
            self._proof.array_selections[selection.term.get_id()] = selection
            self._selection_cursor += 1

    def _operand_hulls(self, left, right):
        if self._exhausted:
            return None
        self._refresh()
        try:
            a = self._proof.coarse_signed_hull(left)
            b = self._proof.coarse_signed_hull(right)
        except _StructuralProofLimit:
            self._exhausted = True
            return None
        return None if a is None or b is None else (a, b)

    def array_bounds(self, array):
        """Prove the cell-value union before naming a finite array image.

        Take the union of every source, including inactive/overwritten values.
        Only encoded expressions and defining equations supply bounds, never
        a proposed state range. Unknown facts keep all original signed bits.
        """
        from . import ranges

        if array in self._array_bounds:
            return self._array_bounds[array]
        if self._exhausted:
            return None
        self._refresh()
        hull = None
        try:
            for _guard, term, intrinsic in array.range_cases(guarded=False):
                value = (ranges.Range(*intrinsic) if intrinsic is not None else
                         self._proof.coarse_signed_hull(term))
                if value is None:
                    return None
                hull = value if hull is None else ranges.Range(
                    min(hull.lo, value.lo), max(hull.hi, value.hi))
        except _StructuralProofLimit:
            self._exhausted = True
            return None
        result = None if hull is None else (hull.lo, hull.hi)
        self._array_bounds[array] = result
        return result

    def value_bounds(self, value):
        """A chained value's range from retained equations, not a conjecture.

        Use the same memoized, branch-insensitive syntax proof as arithmetic
        rewriting. Failed/stalled paths are included. No solver, proposed
        state envelope or normal-completion premise participates.
        """
        from . import ranges
        from .functional_array import FunctionalArray

        if isinstance(value, FunctionalArray):
            bounds = self.array_bounds(value)
            return None if bounds is None else ranges.Range(*bounds)
        if isinstance(value, list):
            result = None
            for child in value:
                part = self.value_bounds(child)
                if part is None:
                    return None
                result = part if result is None else result.join(part)
            return result
        if self._exhausted:
            return None
        self._refresh()
        try:
            return self._proof.coarse_signed_hull(value)
        except _StructuralProofLimit:
            self._exhausted = True
            return None

    def multiplication_width(self, left, right):
        """Keep both operands and every mathematical product representable.

        These are syntax/SSA bounds, not the proposed state carrier being
        checked. All four interval corners must fit the signed result before
        it can be sign-extended back to the original carrier. If the wide
        operation itself can wrap, retain it unchanged.
        """
        from . import ranges

        original = left.size()
        hulls = self._operand_hulls(left, right)
        if hulls is None:
            return original
        a, b = hulls
        products = [x * y for x in (a.lo, a.hi) for y in (b.lo, b.hi)]
        product = ranges.Range(min(products), max(products))
        return min(original, max(ranges.encoding_width(value)
                                 for value in (a, b, product)))

    def division_width(self, left, right):
        """A sufficient signed carrier, proved from the actual SSA operands.

        Keep both operands AND the magnitude of the dividend representable.
        For a nonzero integer divisor the floor quotient's magnitude cannot
        exceed that of the dividend. This includes min-signed / -1, which
        must not overflow a narrowed carrier before being sign-extended.
        The original primitive retains its zero-divisor totalization. No
        branch assumption or frontend range annotation supplies these bounds.
        """
        from . import ranges

        original = left.size()
        hulls = self._operand_hulls(left, right)
        if hulls is None:
            return original
        a, b = hulls
        magnitude = ranges.Range(min(a.lo, -a.hi), max(a.hi, -a.lo))
        return min(original, max(ranges.encoding_width(magnitude), ranges.encoding_width(b)))

    def rewrite(self, value):
        # Definitions are append-only and backward-referencing. A completed
        # rewrite of these exact operands cannot change when later SSA names
        # are defined. Retain the input term so its native ID cannot recycle.
        key = value.ctx, value.get_id()
        cached = self._results.get(key)
        if cached is not None:
            return cached[1]
        if self._exhausted or not z3.is_bv(value) \
                or not z3.is_app_of(value, z3.Z3_OP_ITE):
            return value
        condition = value.arg(0)
        if condition.decl().kind() not in COMPARISONS \
                or condition.num_args() != 2 \
                or any(not z3.is_bv(arg) for arg in condition.children()):
            return value
        proof = self._proof
        self._refresh()
        try:
            for arg in condition.children():
                proof.coarse_signed_hull(arg)
            truth = proof._comparison_from_hulls(condition, proof.coarse_hulls)
        except _StructuralProofLimit:
            self._exhausted = True
            return value
        result = value if truth is None else value.arg(1 if truth else 2)
        self._results[key] = value, result
        return result
