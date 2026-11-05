"""Coordinate one audited exact slice and any required complete-formula check.

Definitional slicing is an internal optimisation of one logical query.  It
does not consume another query-budget unit or create another transcript entry.
An ordinary sliced SAT boundary is validated against the complete formula.  A
closed generated formula may instead register the stronger extension theorem:
its ordered total definitions uniquely extend every audited slice model.
"""

from __future__ import annotations

import enum
import math
import threading
import time

import z3

from behave.core import runtime_defaults as defaults

from ..core.frozen import Frozen
from ..core.query import SolverAnswer
from ..core.query_telemetry import FormulaShape, StageTimings
from .exact_formula import (
    ExactFormula,
    FormulaSlice,
    SliceMode,
    measure_formula_shape,
)
from .diagnostics import diagnostic_stage, diagnostics_active

__all__ = [
    "ExactAnswerAuthority",
    "ExactCheckResult",
    "ExactPhase",
    "ExactPhaseAttempt",
    "ExactSliceValidationDisagreement",
    "ExactSolveResult",
    "ExactUnknownCause",
    "solve_exact",
    "z3_check",
    "z3_check_finite",
    "z3_check_qfbv",
]


# Printing a shared-DAG formula as an S-expression can expand it far beyond its
# node count.  Shape telemetry is optional and must not consume the semantic
# query's deadline on large frontend products.
_MAX_MEASURED_SHAPE_ASSERTIONS = defaults.SIM_ANALYSIS_SHAPE_TELEMETRY_ASSERTIONS


class ExactPhase(enum.Enum):
    """An internal phase of one logical exact solver call."""

    FULL = "full"
    SLICE = "slice"
    FULL_AFTER_SLICE_UNKNOWN = "full-after-slice-unknown"
    FULL_PINNED_SAT_VALIDATION = "full-pinned-sat-validation"

    def __bool__(self):
        raise TypeError("an exact-solve phase is not a truth value")


class ExactAnswerAuthority(enum.Enum):
    """The formula that justifies the returned answer."""

    NONE = "none"
    COMPLETE_FORMULA = "complete-formula"
    AUDITED_SLICE = "audited-slice"

    def __bool__(self):
        raise TypeError("an exact-answer authority is not a truth value")


class ExactUnknownCause(enum.Enum):
    """Why one physical solver phase returned ``UNKNOWN``."""

    OTHER = "other"
    MEMORY_LIMIT = "memory-limit"
    TIME_LIMIT = "time-limit"

    def __bool__(self):
        raise TypeError("an exact unknown cause is not a truth value")


class ExactSliceValidationDisagreement(Exception):
    """A sliced SAT boundary has no completion in its exact full formula."""


class ExactCheckResult(Frozen):
    """The checked result of one solver phase before query coordination."""

    __slots__ = (
        "answer", "model", "reason", "solve_ns", "solver_load_ns",
        "unknown_cause",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ExactCheckResult is a closed solver-phase result")

    def __init__(self, answer, *, model=None, reason="", solver_load_ns=None,
                 solve_ns=None, unknown_cause=None):
        _validate_answer(answer, model, reason)
        if answer is SolverAnswer.UNKNOWN:
            if unknown_cause is None:
                unknown_cause = ExactUnknownCause.OTHER
            elif type(unknown_cause) is not ExactUnknownCause:
                raise TypeError("an exact UNKNOWN has a typed cause")
        elif unknown_cause is not None:
            raise ValueError("only exact UNKNOWN carries an unknown cause")
        if (solver_load_ns is None) != (solve_ns is None):
            raise ValueError("exact checker timings are both measured or both absent")
        for value in (solver_load_ns, solve_ns):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("exact checker timings are natural nanoseconds")
        self.answer = answer
        self.model = model
        self.reason = reason
        self.unknown_cause = unknown_cause
        self.solver_load_ns = solver_load_ns
        self.solve_ns = solve_ns
        self._freeze()


class ExactPhaseAttempt(Frozen):
    """One entered phase, including a phase skipped by the shared deadline."""

    __slots__ = (
        "answer", "launched", "phase", "reason", "timeout_ms",
        "unknown_cause",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ExactPhaseAttempt is a closed exact-solve record")

    def __init__(self, phase, *, launched, timeout_ms, answer, reason="",
                 unknown_cause=None):
        if type(phase) is not ExactPhase:
            raise TypeError("an exact phase attempt needs the exact phase enum")
        if type(launched) is not bool:
            raise TypeError("an exact phase launch marker is Boolean")
        if launched:
            if timeout_ms is not None \
                    and (type(timeout_ms) is not int or timeout_ms < 1):
                raise ValueError("a launched exact phase has a positive timeout")
        elif timeout_ms != 0:
            raise ValueError("a deadline-skipped exact phase has zero time")
        _validate_answer(answer, None, reason, phase_record=True)
        if answer is SolverAnswer.UNKNOWN:
            if unknown_cause is None:
                unknown_cause = ExactUnknownCause.OTHER
            elif type(unknown_cause) is not ExactUnknownCause:
                raise TypeError("an exact UNKNOWN phase has a typed cause")
        elif unknown_cause is not None:
            raise ValueError("only an UNKNOWN phase carries an unknown cause")
        if not launched and answer is not SolverAnswer.UNKNOWN:
            raise ValueError("a deadline-skipped exact phase is unresolved")
        self.phase = phase
        self.launched = launched
        self.timeout_ms = timeout_ms
        self.answer = answer
        self.reason = reason
        self.unknown_cause = unknown_cause
        self._freeze()


class ExactSolveResult(Frozen):
    """The authoritative result of one logical exact solver call."""

    __slots__ = (
        "answer", "attempts", "authority", "formula_slice", "model",
        "reason", "shape", "timings",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ExactSolveResult is a closed exact solver result")

    def __init__(self, answer, *, model=None, reason="", authority, attempts,
                 formula_slice, shape, timings):
        _validate_answer(answer, model, reason)
        if type(authority) is not ExactAnswerAuthority:
            raise TypeError("an exact solve needs its exact answer authority")
        attempts = tuple(attempts)
        if not attempts or any(type(item) is not ExactPhaseAttempt
                               for item in attempts):
            raise TypeError("an exact solve retains its internal phase attempts")
        phases = tuple(item.phase for item in attempts)
        allowed = {
            (ExactPhase.FULL,),
            (ExactPhase.SLICE,),
            (ExactPhase.SLICE, ExactPhase.FULL_AFTER_SLICE_UNKNOWN),
            (ExactPhase.SLICE, ExactPhase.FULL_PINNED_SAT_VALIDATION),
        }
        if phases not in allowed:
            raise ValueError("an exact solve has a registered phase path")
        if attempts[-1].answer is not answer:
            raise ValueError("the exact result is the answer of its final phase")
        if type(formula_slice) is not FormulaSlice:
            raise TypeError("an exact solve retains its audited FormulaSlice")
        if formula_slice.mode in {SliceMode.COMPLETE, SliceMode.FULL} \
                and phases != (ExactPhase.FULL,):
            raise ValueError("a complete formula has one full phase")
        if formula_slice.mode is SliceMode.SLICED \
                and phases[0] is not ExactPhase.SLICE:
            raise ValueError("an audited slice begins with its sliced phase")
        expected_authority = (
            ExactAnswerAuthority.NONE
            if answer is SolverAnswer.UNKNOWN
            else ExactAnswerAuthority.AUDITED_SLICE
            if phases == (ExactPhase.SLICE,)
            else ExactAnswerAuthority.COMPLETE_FORMULA
        )
        if authority is not expected_authority:
            raise ValueError("the exact answer names the formula that proves it")
        if answer is SolverAnswer.SAT and not attempts[-1].launched:
            raise ValueError("a SAT exact result comes from a launched phase")
        if shape is not None and type(shape) is not FormulaShape:
            raise TypeError("an exact solve formula shape is measured or absent")
        if type(timings) is not StageTimings:
            raise TypeError("an exact solve retains its measured solver stages")
        self.answer = answer
        self.model = model
        self.reason = reason
        self.authority = authority
        self.attempts = attempts
        self.formula_slice = formula_slice
        self.shape = shape
        self.timings = timings
        self._freeze()

    @property
    def phases(self):
        return tuple(attempt.phase for attempt in self.attempts)

    @property
    def slice(self):
        """Compatibility spelling for the retained formula slice."""

        return self.formula_slice

    @property
    def full_checked(self):
        """Whether a complete-formula solver phase actually launched."""

        return any(
            attempt.launched and attempt.phase is not ExactPhase.SLICE
            for attempt in self.attempts)


def _validate_answer(answer, model, reason, *, phase_record=False):
    if type(answer) is not SolverAnswer:
        raise TypeError("an exact solver result needs a SolverAnswer")
    if type(reason) is not str:
        raise TypeError("an exact solver reason is a string")
    if phase_record:
        if model is not None:
            raise ValueError("a phase record does not retain a solver model")
    elif answer is SolverAnswer.SAT:
        if not isinstance(model, z3.ModelRef):
            raise TypeError("a SAT exact solver result carries its model")
    elif model is not None:
        raise ValueError("only a SAT exact solver result carries a model")
    if answer is SolverAnswer.UNKNOWN and not reason:
        raise ValueError("an UNKNOWN exact solver result carries a reason")
    if answer is not SolverAnswer.UNKNOWN and reason:
        raise ValueError("only UNKNOWN carries an exact solver reason")


class _Budget:
    __slots__ = ("_deadline",)

    def __init__(self, timeout_ms):
        if timeout_ms is not None and type(timeout_ms) is not int:
            raise TypeError("an exact-solve timeout is an integer or None")
        if timeout_ms is not None and timeout_ms < 1:
            raise ValueError("an exact-solve timeout is positive")
        self._deadline = (
            None if timeout_ms is None
            else time.monotonic() + timeout_ms / 1000.0)

    def remaining_ms(self):
        if self._deadline is None:
            return None
        return max(0, math.ceil((self._deadline - time.monotonic()) * 1000))


def _configure_solver(solver, timeout_ms, memory_mb):
    """Apply resource ceilings before loading or preprocessing any roots."""
    solver.set("timeout", 0 if timeout_ms is None else timeout_ms)
    if memory_mb is not None:
        if type(memory_mb) is not int or memory_mb < 1:
            raise ValueError("an exact-solve memory ceiling is positive or absent")
        solver.set("max_memory", memory_mb)


def _memory_exhausted(reason):
    return any(marker in reason.lower() for marker in ('out of memory', 'max. memory exceeded'))


def _solver_capacity_exhausted(reason):
    return any(marker in reason.lower() for marker in (
        'overflow encountered when expanding vector', 'table overflow'))


def _z3_check(assertions, timeout_ms, solver, memory_mb=None, *,
              _result_context=None, _load_started_ns=None,
              _cancelled=None):
    started = (time.monotonic_ns()
               if _load_started_ns is None else _load_started_ns)
    _configure_solver(solver, timeout_ms, memory_mb)
    with diagnostic_stage('solver.load', assertions=len(assertions)):
        solver.add(*assertions)
    solver_load_ns = time.monotonic_ns() - started
    if _cancelled is not None and _cancelled.is_set():
        return ExactCheckResult(
            SolverAnswer.UNKNOWN, reason="timeout",
            unknown_cause=ExactUnknownCause.TIME_LIMIT,
            solver_load_ns=solver_load_ns, solve_ns=0)
    started = time.monotonic_ns()
    with diagnostic_stage('solver.check', includes_preprocessing=True) as diagnostic:
        answer = solver.check()
        if diagnostics_active():
            try:
                diagnostic['answer'] = str(answer)
                if answer == z3.unknown:
                    diagnostic['reason'] = str(solver.reason_unknown() or "unknown")
                statistics = solver.statistics()
                diagnostic['statistics'] = {key: statistics.get_key_value(key)
                                            for key in statistics.keys()}
            except Exception:
                pass  # Optional native statistics cannot change the result.
    solve_ns = time.monotonic_ns() - started
    if answer == z3.sat:
        with diagnostic_stage('solver.model'):
            model = solver.model()
            if _result_context is not None:
                model = model.translate(_result_context)
        return ExactCheckResult(
            SolverAnswer.SAT, model=model,
            solver_load_ns=solver_load_ns, solve_ns=solve_ns)
    if answer == z3.unsat:
        return ExactCheckResult(
            SolverAnswer.UNSAT,
            solver_load_ns=solver_load_ns, solve_ns=solve_ns)
    reason = str(solver.reason_unknown() or "unknown")
    normalized = reason.strip().lower()
    if _cancelled is not None and _cancelled.is_set():
        # Z3 reports a context interruption as either "canceled" or
        # "interrupted" depending on the tactic phase.  Only this check's own
        # watchdog authorises the stronger time-limit classification.
        reason = "timeout"
        cause = ExactUnknownCause.TIME_LIMIT
    else:
        cause = (
            ExactUnknownCause.MEMORY_LIMIT
            if _memory_exhausted(normalized)
            else ExactUnknownCause.TIME_LIMIT
            if normalized in {"timeout", "canceled"}
            else ExactUnknownCause.OTHER
        )
    return ExactCheckResult(
        SolverAnswer.UNKNOWN, reason=reason, unknown_cause=cause,
        solver_load_ns=solver_load_ns, solve_ns=solve_ns)


def z3_check(assertions, timeout_ms, memory_mb=None, *, _owned_context=None):
    """General SMT, under the same complete-operation deadline as other strategies."""

    return _z3_check_native(assertions, timeout_ms, memory_mb,
                            owned_context=_owned_context, strategy="smt", allow_empty=True)


def _timed_out_check(load_started_ns, checked=None):
    # A late answer loses authority, not the measured cost of obtaining it.
    # Otherwise a timeout during solving is misleadingly reported as loading.
    return ExactCheckResult(
        SolverAnswer.UNKNOWN, reason="timeout",
        unknown_cause=ExactUnknownCause.TIME_LIMIT,
        solver_load_ns=(time.monotonic_ns() - load_started_ns if checked is None
                        else checked.solver_load_ns),
        solve_ns=0 if checked is None else checked.solve_ns)


def z3_check_qfbv(assertions, timeout_ms, memory_mb=None, *, _owned_context=None):
    """Check a quantifier-free bit-vector formula with its native solver.

    Frontends use this only after their exact-formula boundary has established
    that every term belongs to the quantifier-free bit-vector fragment.  It is
    a solver strategy, not a weaker formula or a different answer authority.

    A frontend that builds the whole physical query in an exclusively owned
    context can supply it, avoiding a second copy. It must decode the answer
    before releasing that context and retain no query terms between calls.
    Other callers continue to get a fresh context and a translated SAT model.
    """

    return _z3_check_native(assertions, timeout_ms, memory_mb,
                            owned_context=_owned_context, strategy="qfbv")


class SolverPreparation:
    """An exact base in a serialized, exclusively owned solver context.

    Only solver preparation survives. Each call checks its complete original
    roots, with a fresh push/pop for the varying suffix; no answer or model is
    cached. A different prefix runs cold, and uncertainty or cancellation
    discards the solver. The owning worker bounds this object's lifetime.
    Internal capacity failure retires preparation for that lifetime; future
    calls copy their complete roots into a fresh context. Replacing only the
    solver can retain the exhausted context's native tables.
    """

    def __init__(self, base):
        self.base = tuple(base)
        if not self.base or any(not z3.is_bool(term) for term in self.base):
            raise ValueError("a prepared solver needs a nonempty Boolean base")
        self.context = self.base[0].ctx
        if any(term.ctx != self.context for term in self.base):
            raise ValueError("a prepared solver base uses one context")
        self._interrupted = False
        self._cold_only = False
        self.clear()

    def clear(self, *, interrupted=False):
        self._solver = None
        self._configuration = None
        self._pending = ()
        self._interrupted |= interrupted

    def extend(self, base):
        """Retain preparation only if every old root remains in the new base.

        The next check must still present that complete ordered base. No
        result or varying query suffix becomes a permanent assertion. Root
        IDs are compared only in this owned context while both DAGs are held;
        changed/removed roots retire the solver. Loading stays inside check's
        existing deadline, including when several extensions precede a check.
        """
        base = tuple(base)
        if not base or any(not z3.is_bool(term) or term.ctx != self.context for term in base):
            raise ValueError("an extended solver base needs Boolean roots in its owned context")
        old = {term.get_id() for term in self.base}
        new = {term.get_id() for term in base}
        if not old <= new:
            self.clear()
        elif self._solver is not None:
            self._pending += tuple(term for term in base if term.get_id() not in old)
        self.base = base

    def check(self, assertions, timeout_ms, memory_mb, *, strategy, context, options):
        budget = _Budget(timeout_ms)
        solver = None

        def cold(remaining):
            # Reuse the normal private-context translation/model handoff and
            # watchdog. No old solver state or answer crosses this boundary.
            return _z3_check_native(assertions, remaining, memory_mb,
                owned_context=None, strategy=strategy, allow_empty=True)

        def recover(reason):
            nonlocal solver
            self._cold_only = True
            with diagnostic_stage('solver.capacity-retire'):
                self.clear()
                solver = None
            left = budget.remaining_ms()
            if left == 0 or options['_cancelled'].is_set():
                return _timed_out_check(options['_load_started_ns'])
            with diagnostic_stage('solver.cold-retry', reason=reason, remaining_ms=left):
                return cold(left)

        try:
            if context != self.context:
                raise ValueError("solver preparation belongs to its owned context")
            if self._cold_only:
                return cold(timeout_ms)
            create = lambda: (z3.SolverFor("QF_FD", ctx=context)
                              if strategy == "finite" else _smt_solver(context,
                                  preserve_equations=strategy == "smt-shared",
                                  incremental=True))
            if self._interrupted:
                # A context interruption can persist until the next check;
                # even push() on a new solver may then raise 'canceled'. Use
                # the ordinary unscoped full check before preparing again.
                answer = _z3_check(assertions, timeout_ms, create(), memory_mb, **options)
                if answer.answer is not SolverAnswer.UNKNOWN:
                    self._interrupted = False
                return answer
            # Identity is structural, ordered and complete, never a digest or
            # a heuristic division of constraints into 'static' and 'dynamic'.
            if len(assertions) < len(self.base) or any(
                    not old.eq(new) for old, new in zip(self.base, assertions)):
                self.clear()
                return _z3_check(assertions, timeout_ms, create(), memory_mb, **options)
            configuration = (strategy, memory_mb)
            if self._solver is None or self._configuration != configuration:
                self.clear()
                solver = create()
                _configure_solver(solver, timeout_ms, memory_mb)
                with diagnostic_stage('solver.base-load', assertions=len(self.base)):
                    solver.add(*self.base)
                self._solver, self._configuration = solver, configuration
            solver = self._solver
            # Z3 can preprocess its entire base at push(), before _z3_check
            # runs. This work must use this call's limits, not a previous
            # call's timeout or an unconfigured memory ceiling.
            _configure_solver(solver, timeout_ms, memory_mb)
            if self._pending:
                with diagnostic_stage('solver.base-extend', assertions=len(self._pending)):
                    solver.add(*self._pending)
                self._pending = ()
            with diagnostic_stage('solver.push', includes_preprocessing=True):
                solver.push()
            try:
                answer = _z3_check(assertions[len(self.base):], timeout_ms,
                                   solver, memory_mb, **options)
            finally:
                solver.pop()
            if answer.answer is SolverAnswer.UNKNOWN:
                if _solver_capacity_exhausted(answer.reason):
                    return recover(answer.reason)
                self.clear()
            return answer
        except z3.Z3Exception as error:
            self.clear()
            if self._cold_only or not _solver_capacity_exhausted(str(error)):
                raise
            # Retaining internal SMT state is an optimization, not a semantic
            # requirement. Retire an exhausted arena and retry the COMPLETE
            # original roots cold, using only this call's remaining allowance.
            # Drop exception frames too: they can otherwise keep it alive.
            error.__traceback__ = None
            error.__context__ = None
            error.__cause__ = None
            return recover(str(error))
        except BaseException:
            self.clear()
            raise


def z3_check_finite(assertions, timeout_ms, memory_mb=None, *, _owned_context=None,
                    _prefer_finite=True, _preparation=None, _preserve_equations=False):
    """Prefer finite-domain solving, retaining the complete original formula.

    RTL request histories mix bit-vectors with bounded integer protocol state.
    QF_FD can exploit those bounds without the frontend inventing bit widths.
    An unsupported formula falls back to SMT within the same deadline; memory
    or time exhaustion never starts a new attempt. No extra bounds are added.
    A frontend can retain SMT for array products that would otherwise expand
    into a large SAT circuit; they keep the same context/deadline protections.
    History owners can additionally retain named arithmetic boundaries. This
    changes preprocessing only, not the asserted roots or model coordinates.
    """

    return _z3_check_native(assertions, timeout_ms, memory_mb,
                            owned_context=_owned_context,
                            strategy=("finite" if _prefer_finite else
                                      "smt-shared" if _preserve_equations else "smt"),
                            preparation=_preparation)


def _finite_check(assertions, timeout_ms, memory_mb, *, context, options, preparation=None):
    budget = _Budget(timeout_ms)
    first = (preparation.check(assertions, timeout_ms, memory_mb,
                              strategy="finite", context=context, options=options)
             if preparation is not None else
             _z3_check(assertions, timeout_ms, z3.SolverFor("QF_FD", ctx=context),
                       memory_mb, **options))
    if first.answer is not SolverAnswer.UNKNOWN \
            or first.unknown_cause is not ExactUnknownCause.OTHER:
        return first
    left = budget.remaining_ms()
    if left == 0 or options["_cancelled"].is_set():
        return _timed_out_check(options["_load_started_ns"], first)
    # Unsupported theories are not refusals of the source language. Retry the
    # same roots, not a projection, and retain the physical solver costs.
    fallback_options = {key: value for key, value in options.items()
                        if key != "_load_started_ns"}
    second = _z3_check(assertions, left, z3.Solver(ctx=context), memory_mb,
                       **fallback_options)
    return ExactCheckResult(
        second.answer, model=second.model, reason=second.reason,
        unknown_cause=second.unknown_cause,
        solver_load_ns=first.solver_load_ns + second.solver_load_ns,
        solve_ns=first.solve_ns + second.solve_ns)


def _smt_solver(context, *, preserve_equations=False, incremental=False):
    """Normalize local products, preserving named boundaries of long histories.

    Source histories name successors to share their arithmetic. Unrestricted
    solve-eqs substitutes those datapaths through later transitions; subsequent
    rewriting can specialize and duplicate them instead of retaining the DAG.
    History owners therefore fold constants and remove unconstrained terms,
    but keep shared equalities for SMT, disabling its internal equation-
    elimination pass too. Unconstrained elimination reconstructs its removed
    coordinates without substituting shared datapaths through later steps.
    Ordinary local products retain equation elimination: banning it globally leaves
    short protocol proofs stuck on otherwise removable intermediate names.
    Both paths retain the complete roots and reconstruct their SAT models;
    neither a domain restriction nor a resource cap is added.
    Loading, preprocessing, solving and model extraction remain inside the
    existing native-context watchdog; no tactic gets its own work allowance.
    A tactic-backed solver reruns its pipeline on every check, even when the
    Python solver object survives push/pop. Prepared callers instead use the
    native incremental solver, retaining the base's internalized terms and
    learned clauses across query scopes and monotone extensions. Z3 owns scope
    retirement; no previous target or model becomes a permanent assertion.
    """
    if incremental:
        solver = z3.Solver(ctx=context)
        if preserve_equations:
            solver.set(solve_eqs=False)
        return solver
    if preserve_equations:
        return z3.Then(z3.Tactic("simplify", ctx=context),
                       z3.Tactic("propagate-values", ctx=context),
                       z3.Tactic("elim-uncnstr", ctx=context),
                       z3.Tactic("simplify", ctx=context),
                       z3.With(z3.Tactic("smt", ctx=context), solve_eqs=False)).solver()
    return z3.Then(*(z3.Tactic(name, ctx=context) for name in (
        "simplify", "propagate-values", "solve-eqs", "simplify", "smt",
    ))).solver()


def _native_strategy_check(assertions, budget, memory_mb, *, strategy, context,
                           options, preparation):
    """Select an existing backend inside the caller-owned native lifetime."""
    if strategy == "qfbv":
        # Eliminate definitional names before bit blasting, so harmless SSA
        # renaming does not change the circuit presented to the SAT backend.
        tactic = z3.Then(*(z3.Tactic(name, ctx=context) for name in (
            "simplify", "propagate-values", "solve-eqs", "elim-uncnstr",
            "max-bv-sharing", "bit-blast", "sat",
        )))
    remaining = budget.remaining_ms()
    if remaining == 0:
        return _timed_out_check(options['_load_started_ns'])
    if strategy == "finite":
        return _finite_check(assertions, remaining, memory_mb, context=context,
                             options=options, preparation=preparation)
    if strategy == "qfbv":
        # The native timeout supplements, but never replaces, the enclosing
        # watchdog over construction, preprocessing and model extraction.
        if remaining is not None:
            tactic = z3.TryFor(tactic, remaining)
        return _z3_check(assertions, remaining, tactic.solver(), memory_mb, **options)
    if preparation is not None:
        return preparation.check(assertions, remaining, memory_mb,
                                 strategy=strategy, context=context, options=options)
    return _z3_check(assertions, remaining, _smt_solver(context,
                     preserve_equations=strategy == "smt-shared"), memory_mb, **options)


def _z3_check_native(assertions, timeout_ms, memory_mb, *, owned_context, strategy,
                     allow_empty=False, preparation=None):
    """One private context and watchdog own translation and all solver work."""

    if timeout_ms is not None and (
            type(timeout_ms) is not int or timeout_ms < 1):
        raise ValueError("an exact native timeout is positive or absent")
    # Materializing the roots and constructing a private context are work too.
    # Neither may renew the caller's deadline before the watchdog is installed.
    load_started_ns, budget = time.monotonic_ns(), _Budget(timeout_ms)
    assertions = tuple(assertions)
    if not assertions:
        if not allow_empty:
            raise ValueError("an exact native check needs a nonempty assertion set")
        assertions = (z3.BoolVal(True, ctx=owned_context),)
    source_context = assertions[0].ctx
    if any(term.ctx != source_context for term in assertions[1:]):
        raise ValueError("one exact native check uses one Z3 context")
    if owned_context is not None and owned_context != source_context:
        raise ValueError("the owned query context must contain all assertions")
    if preparation is not None and owned_context != preparation.context:
        preparation.clear()
        raise ValueError("solver preparation requires its exclusively owned context")

    # Give every physical tactic query its own native context. Z3's tactic
    # arenas can retain bit-blasted products after a solver object is released;
    # a sequence of individually bounded queries could therefore cross the
    # artifact's aggregate memory ceiling.  The temporary context makes the
    # physical query the memory-lifetime boundary.  A SAT model is translated
    # back before the context is released, so callers continue to evaluate the
    # original boundary atoms.
    if budget.remaining_ms() == 0:
        if preparation is not None:
            preparation.clear()
        return _timed_out_check(load_started_ns)
    query_context = z3.Context() if owned_context is None else owned_context
    remaining = budget.remaining_ms()
    if remaining == 0:
        if preparation is not None:
            preparation.clear()
        return _timed_out_check(load_started_ns)
    cancelled = threading.Event()
    timer = None
    if remaining is not None:
        def interrupt():
            cancelled.set()
            query_context.interrupt()

        timer = threading.Timer(remaining / 1000.0, interrupt)
        timer.daemon = True
        timer.start()

    try:
        # One native translation shares its memo across all assertion roots.
        # Translating roots separately revisits common transition prefixes once
        # per assertion, even though the source and destination are both DAGs.
        # AstVector preserves order and duplicates; no assertion is simplified
        # or dropped, and the physical query still owns its private context.
        if owned_context is None:
            source_assertions = z3.AstVector(ctx=source_context)
            for term in assertions:
                source_assertions.push(term)
            query_assertions = tuple(source_assertions.translate(query_context))
        else:
            query_assertions = assertions
        if cancelled.is_set():
            return _timed_out_check(load_started_ns)

        answer = _native_strategy_check(query_assertions, budget, memory_mb,
            strategy=strategy, context=query_context, preparation=preparation, options={
                "_result_context": source_context if owned_context is None else None,
                "_load_started_ns": load_started_ns,
                "_cancelled": cancelled,
            })
    except z3.Z3Exception as error:
        if preparation is not None:
            preparation.clear()
        if cancelled.is_set():
            return _timed_out_check(load_started_ns)
        reason = str(error)
        if _memory_exhausted(reason):
            # add/push/translation can fail before check() supplies a reason.
            # This is resource UNKNOWN, never a language rejection or proof.
            answer = ExactCheckResult(SolverAnswer.UNKNOWN, reason=reason,
                unknown_cause=ExactUnknownCause.MEMORY_LIMIT)
        elif _solver_capacity_exhausted(reason):
            answer = ExactCheckResult(SolverAnswer.UNKNOWN, reason=reason,
                unknown_cause=ExactUnknownCause.OTHER)
        else:
            raise
    except BaseException:
        if preparation is not None:
            preparation.clear()
        raise
    finally:
        # One join for every path, before publishing any result. A racing
        # interrupt may invalidate both the answer and retained preparation.
        if timer is not None:
            timer.cancel()
            timer.join()
        if preparation is not None and (cancelled.is_set() or budget.remaining_ms() == 0):
            preparation.clear(interrupted=cancelled.is_set())
    if cancelled.is_set() or budget.remaining_ms() == 0:
        return _timed_out_check(load_started_ns, answer)
    if strategy != "qfbv" and preparation is not None and answer.answer is SolverAnswer.UNKNOWN:
        preparation.clear()
    return answer


def _checked(check, assertions, budget, phase, attempts, timing, memory_mb):
    remaining = budget.remaining_ms()
    if remaining == 0:
        reason = f"the exact query deadline was exhausted before {phase.value}"
        attempts.append(ExactPhaseAttempt(
            phase, launched=False, timeout_ms=0,
            answer=SolverAnswer.UNKNOWN, reason=reason,
            unknown_cause=ExactUnknownCause.TIME_LIMIT))
        return ExactCheckResult(
            SolverAnswer.UNKNOWN, reason=reason,
            unknown_cause=ExactUnknownCause.TIME_LIMIT), False
    checked = (
        check(tuple(assertions), remaining)
        if memory_mb is None else
        check(tuple(assertions), remaining, memory_mb)
    )
    if type(checked) is not ExactCheckResult:
        raise TypeError("an exact checker returns an ExactCheckResult")
    if checked.solver_load_ns is not None:
        timing["solver_load_ns"] += checked.solver_load_ns
        timing["solve_ns"] += checked.solve_ns
        timing["measured"] = True
    # A native solver timeout is cooperative and formula loading happens
    # outside its search clock.  An answer returned after the logical deadline
    # is still a theorem of the formula, but it is not an answer produced by
    # the declared analysis policy.  Do not let scheduler latency silently
    # enlarge that policy.
    if budget.remaining_ms() == 0 \
            and checked.answer is not SolverAnswer.UNKNOWN:
        checked = ExactCheckResult(
            SolverAnswer.UNKNOWN,
            reason=f"the exact query deadline expired during {phase.value}",
            unknown_cause=ExactUnknownCause.TIME_LIMIT,
            solver_load_ns=checked.solver_load_ns,
            solve_ns=checked.solve_ns,
        )
    attempts.append(ExactPhaseAttempt(
        phase, launched=True, timeout_ms=remaining,
        answer=checked.answer, reason=checked.reason,
        unknown_cause=checked.unknown_cause))
    return checked, True


def _pin_atoms(formula, pin_atoms):
    pin_atoms = tuple(pin_atoms)
    for atom in pin_atoms:
        if not isinstance(atom, z3.ExprRef) or not z3.is_const(atom) \
                or atom.decl().kind() != z3.Z3_OP_UNINTERPRETED:
            raise TypeError(
                "an exact witness coordinate is a zero-arity Z3 constant")
    if len({atom.get_id() for atom in pin_atoms}) != len(pin_atoms):
        raise ValueError("exact witness coordinates are distinct")
    outside = [
        atom for atom in pin_atoms
        if not formula.contains_boundary_atom(atom)
    ]
    if outside:
        raise ValueError(
            "exact witness coordinates are declared formula boundary atoms")
    return pin_atoms


def solve_exact(formula, target, *, pin_atoms, timeout_ms=None,
                memory_mb=None, check=z3_check, complete=False):
    """Solve one target with a sound slice/full state machine.

    Sliced UNSAT stands.  Sliced UNKNOWN retries the complete formula within
    the same deadline.  A generic sliced SAT model contributes only boundary
    values used to validate the complete formula.  A generated formula whose
    registration proved every dropped definition has a unique ordered
    extension may publish the sliced model directly.  Pinned complete-formula
    UNKNOWN remains UNKNOWN and is never replaced by an unpinned retry.
    ``memory_mb`` applies to every physical solver phase; exhausting it is an
    UNKNOWN answer, not a process failure or an UNSAT conclusion.  ``complete``
    selects a command cone already reduced at a stronger frontend boundary.
    """

    if type(formula) is not ExactFormula:
        raise TypeError("an exact solve consumes an ExactFormula")
    if not isinstance(target, z3.ExprRef) or not z3.is_bool(target):
        raise TypeError("an exact-solve target is Boolean")
    if not callable(check):
        raise TypeError("an exact formula checker is callable")
    if memory_mb is not None and (
            type(memory_mb) is not int or memory_mb < 1):
        raise ValueError("an exact-solve memory ceiling is positive or absent")
    if type(complete) is not bool:
        raise TypeError("complete-formula selection is Boolean")
    pin_atoms = _pin_atoms(formula, pin_atoms)

    # Slicing is part of this logical request, not free work performed before it.
    # Starting the one deadline here prevents an expensive dependency walk or shape
    # measurement from granting every subsequent solver phase a fresh budget.
    budget = _Budget(timeout_ms)
    slice_started = time.monotonic_ns()
    selected = formula.complete(target) if complete else formula.slice(target)
    slice_ns = time.monotonic_ns() - slice_started
    shape = None
    if len(formula.definitions) + len(formula.restrictions) \
            <= _MAX_MEASURED_SHAPE_ASSERTIONS:
        try:
            shape = measure_formula_shape(
                formula.full_constraints + (selected.target,),
                selected.query_assertions)
        except Exception:  # noqa: BLE001 - telemetry cannot own solver semantics
            shape = None

    attempts = []
    timing = {"solver_load_ns": 0, "solve_ns": 0, "measured": False}

    def finish(checked, authority, *, reason=None):
        values = {"slice_ns": slice_ns}
        if timing["measured"]:
            values.update({
                "solver_load_ns": timing["solver_load_ns"],
                "solve_ns": timing["solve_ns"],
            })
        return ExactSolveResult(
            checked.answer, model=checked.model,
            reason=checked.reason if reason is None else reason,
            authority=authority, attempts=attempts,
            formula_slice=selected, shape=shape,
            timings=StageTimings(values))

    if selected.mode in {SliceMode.COMPLETE, SliceMode.FULL}:
        checked, _launched = _checked(
            check, selected.query_assertions, budget, ExactPhase.FULL,
            attempts, timing, memory_mb)
        authority = (
            ExactAnswerAuthority.NONE
            if checked.answer is SolverAnswer.UNKNOWN
            else ExactAnswerAuthority.COMPLETE_FORMULA)
        return finish(checked, authority)

    checked, launched = _checked(
        check, selected.query_assertions, budget, ExactPhase.SLICE,
        attempts, timing, memory_mb)
    if checked.answer is SolverAnswer.UNSAT:
        return finish(checked, ExactAnswerAuthority.AUDITED_SLICE)

    if checked.answer is SolverAnswer.UNKNOWN:
        if not launched:
            return finish(checked, ExactAnswerAuthority.NONE)
        # A complete formula contains every assertion in the audited slice and
        # more definitions. Retrying it cannot make a query that already hit
        # the solver's memory ceiling fit inside that same ceiling. Preserve
        # the typed UNKNOWN instead of paying for a larger guaranteed-overflow
        # phase and risking the enclosing process-tree limit.
        if checked.unknown_cause is ExactUnknownCause.MEMORY_LIMIT:
            return finish(checked, ExactAnswerAuthority.NONE)
        first_reason = checked.reason
        checked, _launched = _checked(
            check, formula.full_constraints + (selected.target,), budget,
            ExactPhase.FULL_AFTER_SLICE_UNKNOWN, attempts, timing, memory_mb)
        authority = (
            ExactAnswerAuthority.NONE
            if checked.answer is SolverAnswer.UNKNOWN
            else ExactAnswerAuthority.COMPLETE_FORMULA)
        reason = None
        if checked.answer is SolverAnswer.UNKNOWN:
            reason = (
                f"sliced query unresolved ({first_reason}); complete query "
                f"unresolved ({checked.reason})")
        return finish(checked, authority, reason=reason)

    if formula.sliced_sat_extends:
        return finish(checked, ExactAnswerAuthority.AUDITED_SLICE)

    pins = tuple(
        atom == checked.model.eval(atom, model_completion=True)
        for atom in pin_atoms)
    checked, _launched = _checked(
        check, formula.full_constraints + pins + (selected.target,), budget,
        ExactPhase.FULL_PINNED_SAT_VALIDATION, attempts, timing, memory_mb)
    if checked.answer is SolverAnswer.UNSAT:
        raise ExactSliceValidationDisagreement(
            "the audited slice found witness coordinates whose complete "
            "formula is UNSAT")
    authority = (
        ExactAnswerAuthority.NONE
        if checked.answer is SolverAnswer.UNKNOWN
        else ExactAnswerAuthority.COMPLETE_FORMULA)
    return finish(checked, authority)
