"""Frontend-neutral exact slice/full solver coordination."""

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

import behave.sim.symbolic.exact_solve as exact_solve_mod
from behave.sim.symbolic.exact_formula import ExactFormula, FormulaDefinition
from behave.sim.symbolic.exact_solve import (
    ExactAnswerAuthority,
    ExactCheckResult,
    ExactPhase,
    ExactSliceValidationDisagreement,
    ExactUnknownCause,
    solve_exact,
)
from behave.sim.core.query import SolverAnswer


def _formula():
    request = z3.Int("shared_exact_request")
    hit = z3.Bool("shared_exact_hit")
    dead = z3.Int("shared_exact_dead")
    return (
        ExactFormula(
            definitions=(
                FormulaDefinition("hit", hit, request == 1),
                FormulaDefinition("dead", dead, request + 10),
            ),
            boundary_atoms=(request,),
        ),
        request,
        hit,
        dead,
    )


def _check(assertions, timeout_ms):
    solver = z3.Solver()
    if timeout_ms is not None:
        solver.set("timeout", timeout_ms)
    solver.add(*assertions)
    answer = solver.check()
    if answer == z3.sat:
        return ExactCheckResult(SolverAnswer.SAT, model=solver.model())
    if answer == z3.unsat:
        return ExactCheckResult(SolverAnswer.UNSAT)
    return ExactCheckResult(
        SolverAnswer.UNKNOWN, reason=solver.reason_unknown() or "unknown")


def test_sliced_sat_publishes_only_the_pinned_complete_formula_model():
    formula, request, hit, dead = _formula()

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10_000)

    assert result.answer is SolverAnswer.SAT
    assert result.authority is ExactAnswerAuthority.COMPLETE_FORMULA
    assert result.phases == (
        ExactPhase.SLICE, ExactPhase.FULL_PINNED_SAT_VALIDATION)
    assert result.model.eval(request).as_long() == 1
    assert result.model.eval(dead).as_long() == 11


def test_complete_selection_uses_one_full_formula_phase():
    formula, request, hit, dead = _formula()
    calls = []

    def observed(assertions, timeout_ms):
        calls.append(tuple(assertions))
        return _check(assertions, timeout_ms)

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10_000,
        check=observed, complete=True)

    assert result.answer is SolverAnswer.SAT
    assert result.authority is ExactAnswerAuthority.COMPLETE_FORMULA
    assert result.formula_slice.mode is exact_solve_mod.SliceMode.COMPLETE
    assert result.phases == (ExactPhase.FULL,)
    assert len(calls) == 1
    assert result.model.eval(dead).as_long() == 11


def test_sliced_unsat_is_the_only_answer_owned_by_the_audited_slice():
    formula, request, hit, _dead = _formula()

    result = solve_exact(
        formula, z3.And(hit, request == 0), pin_atoms=(request,),
        timeout_ms=10_000)

    assert result.answer is SolverAnswer.UNSAT
    assert result.authority is ExactAnswerAuthority.AUDITED_SLICE
    assert result.phases == (ExactPhase.SLICE,)
    assert not result.full_checked


def test_both_unknown_reasons_survive_the_full_retry():
    formula, request, hit, _dead = _formula()
    calls = 0

    def unknown(_assertions, _timeout_ms):
        nonlocal calls
        calls += 1
        return ExactCheckResult(
            SolverAnswer.UNKNOWN, reason=f"unknown phase {calls}")

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10_000,
        check=unknown)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.authority is ExactAnswerAuthority.NONE
    assert result.phases == (
        ExactPhase.SLICE, ExactPhase.FULL_AFTER_SLICE_UNKNOWN)
    assert "unknown phase 1" in result.reason
    assert "unknown phase 2" in result.reason


def test_memory_exhaustion_does_not_retry_a_larger_formula():
    formula, request, hit, _dead = _formula()
    seen = []

    def memory_limited(_assertions, _timeout_ms, memory_mb):
        seen.append(memory_mb)
        return ExactCheckResult(
            SolverAnswer.UNKNOWN, reason="max. memory exceeded",
            unknown_cause=ExactUnknownCause.MEMORY_LIMIT)

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10_000,
        memory_mb=37, check=memory_limited)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.phases == (ExactPhase.SLICE,)
    assert result.attempts[0].unknown_cause is \
        ExactUnknownCause.MEMORY_LIMIT
    assert seen == [37]


@pytest.mark.parametrize('reason', ['max. memory exceeded', 'out of memory', "b'out of memory'"])
def test_z3_memory_limit_is_set_before_loading_the_formula(reason):
    events = []

    class Solver:
        def set(self, name, value):
            events.append(("set", name, value))

        def add(self, *_assertions):
            events.append(("add",))

        @staticmethod
        def check():
            return z3.unknown

        @staticmethod
        def reason_unknown():
            return reason

    result = exact_solve_mod._z3_check(
        (z3.BoolVal(True),), 100, Solver(), memory_mb=19)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.reason == reason
    assert result.unknown_cause is ExactUnknownCause.MEMORY_LIMIT
    assert events[:2] == [("set", "timeout", 100),
                          ("set", "max_memory", 19)]
    assert events[2] == ("add",)


def test_qfbv_timeout_wraps_the_complete_tactic_pipeline(monkeypatch):
    # This checks timeout plumbing; setup-cost accounting has its own clock tests.
    monkeypatch.setattr(exact_solve_mod.time, "monotonic", lambda: 1.0)
    events = []
    marker = object()

    class Limited:
        @staticmethod
        def solver():
            events.append(("solver",))
            return marker

    def limited(tactic, timeout_ms):
        events.append(("try-for", tactic, timeout_ms))
        return Limited()

    sentinel = object()

    def checked(assertions, timeout_ms, solver, memory_mb, **options):
        events.append((
            "check", assertions, timeout_ms, solver, memory_mb, options))
        return sentinel

    monkeypatch.setattr(exact_solve_mod.z3, "TryFor", limited)
    monkeypatch.setattr(exact_solve_mod, "_z3_check", checked)

    assertion = z3.BoolVal(True)
    got = exact_solve_mod.z3_check_qfbv(
        (assertion,), 37, memory_mb=19)

    assert got is sentinel
    assert events[0][0] == "try-for"
    assert events[0][2] == 37
    assert events[1] == ("solver",)
    _event, assertions, timeout_ms, solver, memory_mb, options = events[2]
    assert len(assertions) == 1 and z3.is_true(assertions[0])
    assert assertions[0].ctx != assertion.ctx
    assert (timeout_ms, solver, memory_mb) == (37, marker, 19)
    assert options["_result_context"] == assertion.ctx
    assert type(options["_load_started_ns"]) is int
    assert not options["_cancelled"].is_set()


def test_qfbv_watchdog_interrupts_its_private_context(monkeypatch):
    monkeypatch.setattr(exact_solve_mod.time, "monotonic", lambda: 1.0)
    timers = []
    checked = []

    class Timer:
        def __init__(self, seconds, callback):
            self.seconds = seconds
            self.callback = callback
            self.daemon = False
            timers.append(self)

        def start(self):
            self.callback()

        def cancel(self):
            pass

        def join(self):
            pass

    def should_not_check(*_args, **_kwargs):
        checked.append(True)

    monkeypatch.setattr(exact_solve_mod.threading, "Timer", Timer)
    monkeypatch.setattr(exact_solve_mod, "_z3_check", should_not_check)

    got = exact_solve_mod.z3_check_qfbv(
        (z3.Bool("watchdog_source_context"),), timeout_ms=37)

    assert got.answer is SolverAnswer.UNKNOWN
    assert got.unknown_cause is ExactUnknownCause.TIME_LIMIT
    assert got.reason == "timeout"
    assert len(timers) == 1 and timers[0].seconds == 0.037
    assert timers[0].daemon
    assert checked == []


def test_qfbv_watchdog_turns_an_interrupted_native_exception_into_timeout(
        monkeypatch):
    timer = []

    class Timer:
        def __init__(self, _seconds, callback):
            self.callback = callback
            self.daemon = False
            timer.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

        def join(self):
            pass

    def interrupted(*_args, **_kwargs):
        timer[0].callback()
        raise z3.Z3Exception("canceled")

    monkeypatch.setattr(exact_solve_mod.threading, "Timer", Timer)
    monkeypatch.setattr(exact_solve_mod, "_z3_check", interrupted)

    got = exact_solve_mod.z3_check_qfbv(
        (z3.Bool("watchdog_exception_source"),), timeout_ms=37)

    assert got.answer is SolverAnswer.UNKNOWN
    assert got.unknown_cause is ExactUnknownCause.TIME_LIMIT
    assert got.reason == "timeout"


def test_qfbv_sat_model_returns_to_the_callers_context():
    value = z3.BitVec("fresh_query_context_value", 8)

    got = exact_solve_mod.z3_check_qfbv(
        (value == 37,), timeout_ms=10_000)

    assert got.answer is SolverAnswer.SAT
    assert got.model.ctx == value.ctx
    assert got.model.eval(value).as_long() == 37


def test_each_qfbv_physical_query_owns_a_distinct_native_context(monkeypatch):
    source = z3.Bool("private_context_source")
    query_terms = []
    sentinel = object()

    def checked(assertions, *_args, **_kwargs):
        query_terms.append(tuple(assertions))
        return sentinel

    monkeypatch.setattr(exact_solve_mod, "_z3_check", checked)

    assert exact_solve_mod.z3_check_qfbv((source,), None) is sentinel
    assert exact_solve_mod.z3_check_qfbv((source,), None) is sentinel

    contexts = [terms[0].ctx for terms in query_terms]
    assert all(context != source.ctx for context in contexts)
    assert contexts[0] != contexts[1]


@pytest.mark.parametrize("count", [1, 16, 128])
def test_qfbv_translates_the_assertion_dag_once_without_changing_roots(monkeypatch, count):
    value = z3.BitVec("batch_translation_input", 16)
    shared = value + 1
    terms = tuple(shared == index for index in range(count))
    # Duplicates and a non-final contradiction must survive the transfer too.
    terms += (terms[0], z3.BoolVal(False), terms[0])
    original = z3.AstVector.translate
    transfers = []

    def translate(vector, context):
        transfers.append(len(vector))
        return original(vector, context)

    def scalar_transfer(*_args, **_kwargs):
        raise AssertionError("a shared formula was translated one root at a time")

    def checked(assertions, *_args, **kwargs):
        restored = z3.AstVector(ctx=assertions[0].ctx)
        for assertion in assertions:
            restored.push(assertion)
        restored = original(restored, value.ctx)
        assert len(restored) == len(terms)
        assert all(left.eq(right) for left, right in zip(restored, terms))
        assert kwargs["_result_context"] == value.ctx
        return "checked"

    monkeypatch.setattr(z3.AstVector, "translate", translate)
    monkeypatch.setattr(z3.AstRef, "translate", scalar_transfer)
    monkeypatch.setattr(exact_solve_mod, "_z3_check", checked)
    assert exact_solve_mod.z3_check_qfbv(terms, None) == "checked"
    assert transfers == [len(terms)]


def test_qfbv_batch_translation_preserves_sat_unsat_and_model_coordinates():
    context = z3.Context()
    x = z3.BitVec("batch_check_x", 3, ctx=context)
    y = z3.BitVec("batch_check_y", 3, ctx=context)
    relation = (y == x + 1, z3.ULT(x, z3.BitVecVal(4, 3, ctx=context)))
    for expected in range(8):
        assertions = relation + (y == expected,)
        oracle = z3.Solver(ctx=context)
        oracle.add(*assertions)
        got = exact_solve_mod.z3_check_qfbv(assertions, 10_000)
        assert got.answer is (SolverAnswer.SAT if oracle.check() == z3.sat
                              else SolverAnswer.UNSAT)
        if got.answer is SolverAnswer.SAT:
            assert got.model.ctx == context
            assert all(z3.is_true(got.model.eval(term)) for term in assertions)


@pytest.mark.parametrize('owned_context', [False, True])
@pytest.mark.parametrize('contradiction', [False, True])
def test_cold_smt_restores_eliminated_scalar_array_and_function_coordinates(
        owned_context, contradiction):
    ctx = z3.Context()
    word = z3.BitVecSort(4, ctx=ctx)
    x, y = z3.Consts('cold_x cold_y', word)
    count, successor = z3.Ints('cold_count cold_successor', ctx=ctx)
    memory = z3.Array('cold_memory', word, word)
    written = z3.Array('cold_written', word, word)
    function = z3.Function('cold_function', word, word)
    roots = (x == 3, y == x + 1, count == 2, successor == count + 1,
             written == z3.Store(memory, x, y), function(x) == y,
             z3.Select(memory, x) == 8,
             z3.Select(written, x) == (5 if contradiction else 4))
    oracle = z3.Solver(ctx=ctx)
    oracle.add(*roots)
    expected = SolverAnswer.SAT if oracle.check() == z3.sat else SolverAnswer.UNSAT
    got = exact_solve_mod.z3_check(roots, 10_000,
        _owned_context=ctx if owned_context else None)
    assert got.answer is expected
    if got.answer is SolverAnswer.SAT:
        assert got.model.ctx == ctx
        # In particular, equations removed by solve-eqs must still have values.
        assert all(z3.is_true(got.model.eval(term)) for term in roots)
        assert got.model.eval(y).as_long() == 4
        assert got.model.eval(successor).as_long() == 3


@pytest.mark.parametrize('contradiction', [False, True])
def test_cold_smt_keeps_guarded_failure_and_unconstrained_successors(contradiction):
    ctx = z3.Context()
    live, failure = z3.Bools('cold_live cold_failure', ctx=ctx)
    old, new = z3.BitVecs('cold_old cold_new', 4, ctx=ctx)
    roots = (z3.Not(live), z3.Implies(live, new == old + 1),
             new != old + 1, failure, z3.Not(failure) if contradiction else failure)
    got = exact_solve_mod.z3_check(roots, 10_000)
    assert got.answer is (SolverAnswer.UNSAT if contradiction else SolverAnswer.SAT)
    if got.answer is SolverAnswer.SAT:
        assert all(z3.is_true(got.model.eval(term)) for term in roots)


def test_owned_qfbv_context_does_not_copy_the_complete_formula_or_model(monkeypatch):
    context = z3.Context()
    x = z3.BitVec('owned_query_x', 8, ctx=context)

    def no_copy(*_args, **_kwargs):
        pytest.fail('a query constructed in its private context was copied again')

    monkeypatch.setattr(z3.AstVector, 'translate', no_copy)
    monkeypatch.setattr(z3.ModelRef, 'translate', no_copy)
    for terms, wanted in [((x == 17,), SolverAnswer.SAT),
                          ((x == 17, x == 18), SolverAnswer.UNSAT)]:
        answer = exact_solve_mod.z3_check_qfbv(terms, 10_000, _owned_context=context)
        assert answer.answer is wanted
        if wanted is SolverAnswer.SAT:
            assert answer.model.ctx == context
            assert answer.model.eval(x).as_long() == 17
    with pytest.raises(ValueError, match='owned query context'):
        exact_solve_mod.z3_check_qfbv((x == 17,), 10_000, _owned_context=z3.main_ctx())


@pytest.mark.parametrize('check', [exact_solve_mod.z3_check_qfbv, exact_solve_mod.z3_check])
def test_owned_context_watchdog_cancels_before_checking(monkeypatch, check):
    context = z3.Context()
    interrupted = []
    original = context.interrupt

    def interrupt():
        interrupted.append(True)
        original()

    class Timer:
        daemon = False

        def __init__(self, seconds, callback):
            assert seconds == 0.037
            self.callback = callback

        def start(self):
            self.callback()

        def cancel(self):
            pass

        def join(self):
            pass

    monkeypatch.setattr(context, 'interrupt', interrupt)
    monkeypatch.setattr(exact_solve_mod.threading, 'Timer', Timer)
    monkeypatch.setattr(exact_solve_mod, '_z3_check', lambda *a, **k: pytest.fail('checked after cancellation'))
    answer = check((z3.BoolVal(True, ctx=context),), 37, _owned_context=context)
    assert answer.answer is SolverAnswer.UNKNOWN
    assert answer.unknown_cause is ExactUnknownCause.TIME_LIMIT
    assert interrupted == [True]


def test_a_canceled_tactic_is_a_typed_time_limit():
    class Solver:
        @staticmethod
        def set(*_args):
            pass

        @staticmethod
        def add(*_assertions):
            pass

        @staticmethod
        def check():
            return z3.unknown

        @staticmethod
        def reason_unknown():
            return "canceled"

    result = exact_solve_mod._z3_check(
        (z3.BoolVal(True),), 100, Solver())

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.unknown_cause is ExactUnknownCause.TIME_LIMIT


def test_the_private_context_watchdog_owns_its_interrupted_reason():
    class InterruptedDuringCheck:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    interrupted = InterruptedDuringCheck()

    class Solver:
        @staticmethod
        def set(*_args):
            pass

        @staticmethod
        def add(*_assertions):
            pass

        @staticmethod
        def check():
            return z3.unknown

        @staticmethod
        def reason_unknown():
            return "interrupted"

    result = exact_solve_mod._z3_check(
        (z3.BoolVal(True),), 100, Solver(), _cancelled=interrupted)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.reason == "timeout"
    assert result.unknown_cause is ExactUnknownCause.TIME_LIMIT


def test_pinned_validation_unknown_never_launches_an_unpinned_retry():
    formula, request, hit, _dead = _formula()
    calls = []

    def lose_validation(assertions, timeout_ms):
        calls.append((assertions, timeout_ms))
        if len(calls) == 2:
            return ExactCheckResult(
                SolverAnswer.UNKNOWN, reason="validation unresolved")
        return _check(assertions, timeout_ms)

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10_000,
        check=lose_validation)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.reason == "validation unresolved"
    assert len(calls) == 2
    assert result.phases[-1] is ExactPhase.FULL_PINNED_SAT_VALIDATION


def test_pinned_validation_unsat_is_a_neutral_internal_disagreement():
    formula, request, hit, _dead = _formula()
    calls = 0

    def disagree(assertions, timeout_ms):
        nonlocal calls
        calls += 1
        if calls == 2:
            return ExactCheckResult(SolverAnswer.UNSAT)
        return _check(assertions, timeout_ms)

    with pytest.raises(ExactSliceValidationDisagreement):
        solve_exact(
            formula, hit, pin_atoms=(request,), timeout_ms=10_000,
            check=disagree)

    assert calls == 2


def test_expired_deadline_records_but_does_not_launch_the_first_phase(
        monkeypatch):
    formula, request, hit, _dead = _formula()
    moments = iter((10.0, 10.011))
    monkeypatch.setattr(exact_solve_mod.time, "monotonic", moments.__next__)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the expired phase launched a solver")

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10, check=forbidden)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.phases == (ExactPhase.SLICE,)
    assert not result.attempts[0].launched
    assert result.attempts[0].timeout_ms == 0


def test_an_answer_returned_after_the_logical_deadline_is_not_published(
        monkeypatch):
    formula, request, hit, _dead = _formula()
    clock = [10.0]
    monkeypatch.setattr(exact_solve_mod.time, "monotonic", lambda: clock[0])

    def late(assertions, timeout_ms):
        result = _check(assertions, timeout_ms)
        assert result.answer is SolverAnswer.SAT
        clock[0] += 0.011
        return result

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10, check=late)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.attempts[0].answer is SolverAnswer.UNKNOWN
    assert result.attempts[0].unknown_cause is ExactUnknownCause.TIME_LIMIT
    assert not result.attempts[-1].launched


def test_slicing_is_charged_to_the_one_logical_query_deadline(monkeypatch):
    formula, request, hit, _dead = _formula()
    clock = [10.0]
    original = exact_solve_mod.ExactFormula.slice

    def expensive_slice(self, target):
        clock[0] += 0.011
        return original(self, target)

    monkeypatch.setattr(exact_solve_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(exact_solve_mod.ExactFormula, "slice", expensive_slice)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("slicing exhausted the query before solver launch")

    result = solve_exact(
        formula, hit, pin_atoms=(request,), timeout_ms=10, check=forbidden)

    assert result.answer is SolverAnswer.UNKNOWN
    assert result.attempts[0].phase is ExactPhase.SLICE
    assert not result.attempts[0].launched


def test_large_formula_shape_telemetry_cannot_delay_the_solver(monkeypatch):
    formula, request, hit, _dead = _formula()
    monkeypatch.setattr(
        exact_solve_mod, "_MAX_MEASURED_SHAPE_ASSERTIONS", 0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("optional shape telemetry entered the query path")

    monkeypatch.setattr(exact_solve_mod, "measure_formula_shape", forbidden)
    result = solve_exact(
        formula, z3.And(hit, request == 0), pin_atoms=(request,),
        timeout_ms=10_000)

    assert result.answer is SolverAnswer.UNSAT
    assert result.shape is None


def test_pin_coordinates_must_come_from_the_declared_boundary():
    formula, _request, hit, _dead = _formula()
    undeclared = z3.Int("shared_exact_undeclared")

    with pytest.raises(ValueError, match="declared formula boundary"):
        solve_exact(
            formula, hit, pin_atoms=(undeclared,), timeout_ms=10_000)


def test_the_checker_has_one_closed_result_shape():
    formula, request, hit, _dead = _formula()

    with pytest.raises(TypeError, match="ExactCheckResult"):
        solve_exact(
            formula, hit, pin_atoms=(request,), timeout_ms=10_000,
            check=lambda _assertions, _timeout_ms: (
                SolverAnswer.SAT, None, ""))
