"""Behavior policy for the frontend-neutral exact-solve coordinator."""

from __future__ import annotations

import math
import time

import z3

from ..symbolic.exact_formula import ExactFormula
from ..symbolic.exact_solve import (
    ExactSliceValidationDisagreement,
    ExactSolveResult,
    z3_check_qfbv,
)
from ..symbolic.exact_solve import (
    solve_exact as _solve_exact,
)
from ..core.frozen import Frozen
from ..core.query import SolverAnswer
from ..core.query_telemetry import StageTimings
from ..core.refusal import Refusal, Refused

__all__ = ["ExactSolveResult", "solve_exact", "solve_exact_components"]


# Kept as one narrow adversarial seam: tests may force an internal solver
# phase without duplicating the coordinator.  Behavior's encoded semantics is
# quantifier-free Boolean/bit-vector logic, so its production strategy is the
# corresponding bit-vector pipeline rather than Z3's generic solver.
_check = z3_check_qfbv


def solve_exact(formula, target, timeout_ms=None, *, memory_mb=None,
                complete=False):
    """Solve one Behavior query while preserving its external stimulus.

    ``complete`` skips only low-level definition-cone discovery.  It does not
    omit an encoded constraint and is reserved for a command cone already
    checked by the Behavior frontend.
    """

    if type(formula) is not ExactFormula:
        raise TypeError("a Behavior exact solve needs an ExactFormula")
    if not isinstance(target, z3.ExprRef) or not z3.is_bool(target):
        raise TypeError("a Behavior exact target is Boolean")
    if not formula.is_qfbv_with(target):
        raise Refused(Refusal(
            "behavior-exact-fragment-unavailable",
            "the encoded Behavior query is outside quantifier-free Boolean and "
            "bit-vector logic"))
    try:
        return _solve_exact(
            formula, target, pin_atoms=formula.boundary_atoms,
            timeout_ms=timeout_ms, memory_mb=memory_mb, check=_check,
            complete=complete)
    except ExactSliceValidationDisagreement as exc:
        raise Refused(Refusal(
            "behavior-exact-slice-validation-disagrees",
            "an audited sliced SAT boundary has no completion in the full "
            "Behavior formula",
            disagreement=str(exc))) from None


class _DecomposedSolveResult(Frozen):
    """One logical disjunction answered through exact dependency-cone parts."""

    __slots__ = (
        "answer", "model", "reason", "sat_components", "shape",
        "subqueries", "timings",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("a decomposed exact result is closed")

    def __init__(self, answer, *, model=None, reason="", sat_components=(),
                 shape=None, subqueries, timings):
        if type(answer) is not SolverAnswer:
            raise TypeError("a decomposed exact result needs a SolverAnswer")
        if answer is SolverAnswer.SAT:
            if not isinstance(model, z3.ModelRef) or reason:
                raise ValueError("a decomposed SAT result carries only its model")
        elif model is not None:
            raise ValueError("only decomposed SAT carries a model")
        sat_components = tuple(sat_components)
        if answer is SolverAnswer.SAT:
            if not sat_components or any(
                    not isinstance(item, z3.ExprRef) or not z3.is_bool(item)
                    for item in sat_components):
                raise TypeError(
                    "a decomposed SAT names its checked Boolean components")
        elif sat_components:
            raise ValueError("only decomposed SAT names checked components")
        if answer is SolverAnswer.UNKNOWN and not reason:
            raise ValueError("a decomposed unknown result carries its reason")
        if answer is not SolverAnswer.UNKNOWN and reason:
            raise ValueError("only decomposed unknown carries a reason")
        if type(subqueries) is not int or subqueries < 0:
            raise ValueError("a decomposed result records its launched subqueries")
        if type(timings) is not StageTimings:
            raise TypeError("a decomposed result carries exact stage timings")
        self.answer = answer
        self.model = model
        self.reason = reason
        self.sat_components = sat_components
        self.subqueries = subqueries
        self.timings = timings
        # A one-part result retains its physical slice shape.  A genuinely
        # decomposed query has no single shape and leaves it absent.
        self.shape = shape
        self._freeze()


def _remaining_ms(deadline):
    if deadline is None:
        return None
    return max(0, math.ceil((deadline - time.monotonic()) * 1000))


def _accumulate_timings(total, value):
    """Retain measurements from a completed part, not its symbolic slice."""

    for name, duration in value.timings.as_dict().items():
        total[name] = total.get(name, 0) + duration


def _stage_timings(total):
    return StageTimings(total or {"total_ns": 0})


def _component_partitions(formula, groups, chunk_size, *, count_ceiling,
                          preserve_largest_cone, dependency_cone=None,
                          dependency_cones=None, dependency_masks=None):
    """Pack dependency groups without retaining their full cones while solving."""

    cone_of = formula.dependency_cone \
        if dependency_cone is None else dependency_cone
    targets = tuple(group[0] for group in groups)
    packed_bits = dependency_masks is not None or (
        dependency_cones is None and dependency_cone is None)
    if packed_bits:
        masks_of = formula.dependency_masks if dependency_masks is None else dependency_masks
        cones = masks_of(targets)
    elif dependency_cones is not None:
        cones = dependency_cones(targets)
    else:
        cones = tuple(cone_of(target) for target in targets)
    if any(cone is None for cone in cones):
        # Direct roots were auditable before this helper was called.  Keep a
        # positional fallback for a future formula implementation with a
        # stricter transitive-closure audit.
        return tuple(
            groups[index:index + chunk_size]
            for index in range(0, len(groups), chunk_size)
        )

    # Positional square-root chunks may repeatedly load almost identical
    # loop-prefix cones.  Their actual union can be packed without admitting a
    # larger definition slice than ``chunk_size`` independent maximal cones.
    # The stricter policy retains its literal largest-cone ceiling.
    size = int.bit_count if packed_bits else len
    largest = max(size(cone) for cone in cones)
    capacity = largest * (1 if preserve_largest_cone else chunk_size)
    packed = []
    current = []
    current_cone = 0 if packed_bits else frozenset()
    for group, cone in zip(groups, cones):
        combined = current_cone | cone
        full = count_ceiling and len(current) >= chunk_size
        if current and (full or size(combined) > capacity):
            packed.append(tuple(current))
            current = []
            combined = cone
        current.append(group)
        current_cone = combined
    if current:
        packed.append(tuple(current))
    return tuple(packed)


def solve_exact_components(formula, targets, timeout_ms=None, *,
                           memory_mb=None, max_groups_per_query=None,
                           max_subqueries=None,
                           preserve_largest_cone=False,
                           _solve=None, _dependency_roots=None,
                           _dependency_cone=None, _dependency_cones=None,
                           _dependency_masks=None):
    """Solve an occurrence disjunction in bounded exact batches.

    Marks carrying the same public goal may be duplicated by helper inlining or
    loop expansion.  Their disjunction is the public question.  Components with
    an identical audited dependency cone remain one indivisible group.  Distinct
    groups are packed against a square-root cone budget.  This combines
    overlapping loop-prefix cones without admitting a larger slice than
    square-root positional batches, while independent cones remain split.  A
    caller may instead cap every physical batch at the largest indivisible
    cone.  Only an unresolved batch is split further.
    Any SAT batch proves the public disjunction, whereas UNSAT is returned only
    after every batch is UNSAT.  All batches share one deadline and the same
    optional solver-memory ceiling.
    """

    if type(formula) is not ExactFormula:
        raise TypeError("a component exact solve needs an ExactFormula")
    solve = solve_exact if _solve is None else _solve
    if not callable(solve):
        raise TypeError("a component exact checker is callable")
    if type(targets) not in (tuple, list):
        raise TypeError("exact goal components are a finite sequence")
    targets = tuple(targets)
    if not targets:
        raise ValueError("an exact goal has at least one component")
    if timeout_ms is not None and (
            type(timeout_ms) is not int or timeout_ms < 1):
        raise ValueError("a component exact-solve timeout is positive or absent")
    if memory_mb is not None and (
            type(memory_mb) is not int or memory_mb < 1):
        raise ValueError(
            "a component exact-solve memory ceiling is positive or absent")
    if max_groups_per_query is not None and (
            type(max_groups_per_query) is not int
            or max_groups_per_query < 1):
        raise ValueError(
            "a component batch ceiling is a positive integer or absent")
    if max_subqueries is not None and (
            type(max_subqueries) is not int or max_subqueries < 1):
        raise ValueError(
            "a physical-query ceiling is a positive integer or absent")
    if type(preserve_largest_cone) is not bool:
        raise TypeError("the largest-cone policy is Boolean")
    roots_of = formula.dependency_roots \
        if _dependency_roots is None else _dependency_roots
    cone_of = formula.dependency_cone \
        if _dependency_cone is None else _dependency_cone
    if not callable(roots_of) or not callable(cone_of):
        raise TypeError("dependency scheduling views must be callable")
    if _dependency_cones is not None and not callable(_dependency_cones):
        raise TypeError("a bulk dependency scheduling view must be callable")
    if _dependency_masks is not None and not callable(_dependency_masks):
        raise TypeError("a packed dependency scheduling view must be callable")

    # Dependency discovery and partition construction are part of the logical
    # query, not free setup outside its budget.  Start its deadline before any
    # such work and pass only the clock that remains to a physical solver.
    deadline = (None if timeout_ms is None else
                time.monotonic() + timeout_ms / 1000.0)
    planning_started = time.monotonic_ns()

    def planning_timeout(detail):
        elapsed = max(0, time.monotonic_ns() - planning_started)
        return _DecomposedSolveResult(
            SolverAnswer.UNKNOWN, reason=detail, subqueries=0,
            timings=StageTimings({"plan_ns": elapsed}))

    def allowance_or_timeout(detail):
        allowance = _remaining_ms(deadline)
        if allowance == 0:
            return None, planning_timeout(detail)
        return allowance, None

    def ask(target, allowance):
        return (
            solve(formula, target, allowance)
            if memory_mb is None else
            solve(formula, target, allowance, memory_mb=memory_mb)
        )

    def packaged(outcome, components, subqueries=1):
        options = {
            "shape": getattr(outcome, "shape", None),
            "subqueries": subqueries,
            "timings": outcome.timings,
        }
        if outcome.answer is SolverAnswer.SAT:
            options.update({
                "model": outcome.model,
                "sat_components": tuple(components),
            })
        elif outcome.answer is SolverAnswer.UNKNOWN:
            options["reason"] = outcome.reason
        return _DecomposedSolveResult(outcome.answer, **options)

    distinct = []
    seen = set()
    for target in targets:
        if not isinstance(target, z3.ExprRef) or not z3.is_bool(target):
            raise TypeError("an exact goal component is Boolean")
        if target.get_id() not in seen:
            distinct.append(target)
            seen.add(target.get_id())

    groups = []
    by_roots = {}
    for target in distinct:
        roots = roots_of(target)
        allowance, expired = allowance_or_timeout(
            "the logical query deadline was exhausted while discovering "
            "dependency roots")
        if expired is not None:
            return expired
        if roots is None:
            # An unauditable component would make every part fall back to the
            # complete formula.  Pose the original disjunction once instead.
            outcome = ask(z3.Or(distinct), allowance)
            return packaged(outcome, distinct)
        index = by_roots.get(roots)
        if index is None:
            by_roots[roots] = len(groups)
            groups.append([target])
        else:
            groups[index].append(target)
    if len(groups) == 1:
        allowance, expired = allowance_or_timeout(
            "the logical query deadline was exhausted before its physical "
            "solver call")
        if expired is not None:
            return expired
        outcome = ask(z3.Or(distinct), allowance)
        return packaged(outcome, distinct)

    # No solver phase needs the root keys once the exact groups are fixed.
    groups = tuple(tuple(group) for group in groups)
    del by_roots, distinct, roots

    timings = {}
    subqueries = 0
    unknown = []
    unresolved_groups = 0

    def halves(partition):
        middle = (len(partition) + 1) // 2
        return partition[:middle], partition[middle:]

    def partition_values(partition):
        return tuple(target
                     for group in partition
                     for target in group)

    def partition_target(partition):
        values = partition_values(partition)
        return values[0] if len(values) == 1 else z3.Or(values)

    # A square-root partition balances the two independent costs: loading too
    # many occurrence cones at once, and rebuilding the shared boundary for too
    # many initial queries.  The recursive fallback reaches single cones only
    # for batches the solver could not settle together.
    chunk_size = math.isqrt(len(groups) - 1) + 1
    if max_groups_per_query is not None:
        chunk_size = min(chunk_size, max_groups_per_query)
    initial = _component_partitions(
        formula, groups, chunk_size,
        count_ceiling=max_groups_per_query is not None,
        preserve_largest_cone=preserve_largest_cone,
        dependency_cone=_dependency_cone, dependency_cones=_dependency_cones,
        dependency_masks=_dependency_masks)
    _allowance, expired = allowance_or_timeout(
        "the logical query deadline was exhausted while packing dependency "
        "cones")
    if expired is not None:
        return expired
    # Work breadth-first.  Every partition that actually exists at one level
    # receives a fair share before an UNKNOWN creates smaller optional work for
    # the next level.  Reserving a complete binary tree up front is not fair:
    # a loop-expanded goal with hundreds of occurrences can reduce every real
    # solver call below its setup cost to reserve time for descendants that a
    # SAT or UNSAT parent would never create.
    frontier = list(initial)
    while frontier:
        next_frontier = []
        for index, partition in enumerate(frontier):
            pending = frontier[index:]
            if max_subqueries is not None and subqueries >= max_subqueries:
                unknown.insert(0,
                    "the logical query's physical solver-invocation budget was "
                    "exhausted before all dependency-cone partitions were checked")
                unresolved_groups += sum(len(item) for item in pending)
                unresolved_groups += sum(
                    len(item) for item in next_frontier)
                frontier = []
                break
            remaining = _remaining_ms(deadline)
            if remaining == 0:
                unknown.append(
                    "the logical query deadline was exhausted before all "
                    "dependency-cone partitions were checked")
                unresolved_groups += sum(len(item) for item in pending)
                unresolved_groups += sum(
                    len(item) for item in next_frontier)
                frontier = []
                break
            # Children created by an earlier UNKNOWN are no longer
            # hypothetical: every one of them is required before this logical
            # disjunction can be UNSAT.  Share the remaining clock with those
            # real next-level partitions as well as the current level.  If we
            # ignore them, the remaining parents consume the entire deadline
            # and refinement reaches no singleton at all.
            participants = len(pending) + len(next_frontier)
            allowance = (None if remaining is None else
                         max(1, remaining // participants))
            outcome = ask(partition_target(partition), allowance)
            subqueries += 1
            _accumulate_timings(timings, outcome)
            if outcome.answer is SolverAnswer.SAT:
                return _DecomposedSolveResult(
                    SolverAnswer.SAT, model=outcome.model,
                    sat_components=partition_values(partition),
                    subqueries=subqueries, timings=_stage_timings(timings))
            if outcome.answer is SolverAnswer.UNKNOWN:
                reason = outcome.reason
                if len(partition) == 1:
                    unknown.append(reason)
                    unresolved_groups += 1
                else:
                    next_frontier.extend(halves(partition))
            # ExactSolveResult retains its audited FormulaSlice.  The public
            # disjunction needs only the answer and aggregate telemetry after
            # this point, so release it before constructing the next query.
            del outcome
        else:
            frontier = next_frontier
            continue
        break

    combined_timings = _stage_timings(timings)
    if unknown:
        reason = (
            f"{unresolved_groups} of {len(groups)} dependency-cone groups "
            f"remained unresolved; first: {unknown[0]}")
        return _DecomposedSolveResult(
            SolverAnswer.UNKNOWN, reason=reason,
            subqueries=subqueries, timings=combined_timings)
    return _DecomposedSolveResult(
        SolverAnswer.UNSAT, subqueries=subqueries, timings=combined_timings)
