"""Every native strategy rejects late answers, including translated SAT models."""

import pytest
import z3

from behave.sim.symbolic import exact_solve
from behave.sim.core.query import SolverAnswer


def _clock(monkeypatch):
    now, timers = [1.0], []
    class Timer:
        daemon = False
        def __init__(self, seconds, callback):
            self.seconds, self.callback, self.closed = seconds, callback, False
            self.joins = 0
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.closed = True
        def join(self):
            assert self.closed
            self.joins += 1
    monkeypatch.setattr(exact_solve.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(exact_solve.threading, 'Timer', Timer)
    return now, timers


@pytest.mark.parametrize('strategy', ['general', 'qfbv', 'finite', 'smt'])
@pytest.mark.parametrize('answer', [SolverAnswer.SAT, SolverAnswer.UNSAT])
@pytest.mark.parametrize('expiry', ['deadline', 'watchdog'])
def test_no_native_strategy_returns_a_late_answer(monkeypatch, strategy, answer, expiry):
    now, timers = _clock(monkeypatch)
    ctx = z3.Context()

    def late(assertions, timeout_ms, solver, *args, **kwargs):
        solver.add(*assertions)
        assert solver.check() == z3.sat
        result = exact_solve.ExactCheckResult(answer,
            model=solver.model() if answer is SolverAnswer.SAT else None,
            solver_load_ns=2, solve_ns=31)
        if expiry == 'deadline':
            now[0] += 1
        else:
            timers[0].callback()
        return result

    monkeypatch.setattr(exact_solve, '_z3_check', late)
    result = (exact_solve.z3_check((z3.BoolVal(True, ctx),), 1000, _owned_context=ctx)
              if strategy == 'general' else
              exact_solve._z3_check_native((z3.BoolVal(True, ctx),), 1000, None,
                                            owned_context=ctx, strategy=strategy))
    assert result.answer is SolverAnswer.UNKNOWN and result.model is None
    assert result.unknown_cause is exact_solve.ExactUnknownCause.TIME_LIMIT
    assert len(timers) == 1 and timers[0].closed
    assert (result.solver_load_ns, result.solve_ns) == (2, 31)


@pytest.mark.parametrize('strategy', ['general', 'qfbv', 'finite', 'smt'])
def test_model_transfer_is_inside_the_native_query_budget(monkeypatch, strategy):
    now, timers = _clock(monkeypatch)
    original = z3.ModelRef.translate

    def slow_transfer(model, context):
        result = original(model, context)
        now[0] += 1
        return result

    monkeypatch.setattr(z3.ModelRef, 'translate', slow_transfer)
    value = z3.BitVec('late_model_value', 8)
    result = (exact_solve.z3_check((value == 7,), 1000)
              if strategy == 'general' else
              exact_solve._z3_check_native((value == 7,), 1000, None,
                                            owned_context=None, strategy=strategy))
    assert result.answer is SolverAnswer.UNKNOWN and result.model is None
    assert result.unknown_cause is exact_solve.ExactUnknownCause.TIME_LIMIT
    assert timers[0].closed


@pytest.mark.parametrize('timeout', [0, -1, True, 0.5])
def test_general_smt_rejects_an_ambiguous_timeout(timeout):
    with pytest.raises(ValueError, match='timeout'):
        exact_solve.z3_check((z3.BoolVal(True),), timeout)


@pytest.mark.parametrize('owned', [False, True])
def test_general_smt_preserves_private_context_and_exact_values(owned):
    ctx = z3.Context()
    value = z3.Int('general_value', ctx)
    roots = (value > 2 ** 128, value < 2 ** 128 + 2)
    result = exact_solve.z3_check(roots, 10000, _owned_context=ctx if owned else None)
    assert result.answer is SolverAnswer.SAT
    assert result.model.ctx == ctx
    assert result.model.eval(value).as_long() == 2 ** 128 + 1
    contradiction = exact_solve.z3_check((*roots, value == 0), 10000,
                                        _owned_context=ctx if owned else None)
    assert contradiction.answer is SolverAnswer.UNSAT


@pytest.mark.parametrize('owned', [False, True])
def test_general_smt_preserves_empty_conjunction(owned):
    ctx = z3.Context() if owned else None
    result = exact_solve.z3_check(iter(()), 1000, _owned_context=ctx)
    assert result.answer is SolverAnswer.SAT
    assert result.model.ctx == (ctx if owned else z3.main_ctx())


@pytest.mark.parametrize('strategy', ['general', 'qfbv', 'finite', 'smt'])
@pytest.mark.parametrize('stage', ['assertions', 'context'])
def test_input_materialization_and_context_creation_cannot_renew_native_budget(
        monkeypatch, strategy, stage):
    now, timers = _clock(monkeypatch)
    roots = (z3.BoolVal(True),)

    def delayed_roots(values=roots):
        now[0] += 1
        yield from values

    if stage == 'context':
        original_context = z3.Context
        class SlowContext(original_context):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                now[0] += 1
        monkeypatch.setattr(z3, 'Context', SlowContext)
    else:
        roots = delayed_roots()
    result = (exact_solve.z3_check(roots, 1000) if strategy == 'general' else
              exact_solve._z3_check_native(roots, 1000, None,
                                           owned_context=None, strategy=strategy))
    assert result.answer is SolverAnswer.UNKNOWN and result.model is None
    assert result.unknown_cause is exact_solve.ExactUnknownCause.TIME_LIMIT
    assert result.solve_ns == 0
    assert not timers  # No solver work may start after setup spends the budget.


@pytest.mark.parametrize('strategy', ['general', 'qfbv', 'finite', 'smt'])
def test_native_watchdog_and_solver_share_the_remaining_setup_budget(monkeypatch, strategy):
    now, timers = _clock(monkeypatch)
    ctx = z3.Context()

    def roots():
        now[0] += 0.25
        yield z3.BoolVal(True, ctx)

    def check(assertions, timeout_ms, solver, *args, **kwargs):
        assert timeout_ms == 750
        return exact_solve.ExactCheckResult(SolverAnswer.UNSAT, solver_load_ns=2, solve_ns=3)

    monkeypatch.setattr(exact_solve, '_z3_check', check)
    result = (exact_solve.z3_check(roots(), 1000, _owned_context=ctx) if strategy == 'general' else
              exact_solve._z3_check_native(roots(), 1000, None,
                                           owned_context=ctx, strategy=strategy))
    assert result.answer is SolverAnswer.UNSAT
    assert len(timers) == 1 and timers[0].seconds == 0.75 and timers[0].closed


@pytest.mark.parametrize('strategy', ['qfbv', 'finite', 'smt', 'smt-shared'])
@pytest.mark.parametrize('outcome', ['sat', 'unsat', 'unknown', 'memory', 'capacity',
                                    'exception', 'interrupted'])
def test_native_watchdog_has_one_finalizer_for_every_backend(monkeypatch, strategy, outcome):
    _, timers = _clock(monkeypatch)
    ctx = z3.Context()

    def check(assertions, timeout_ms, solver, *args, **kwargs):
        if outcome == 'exception':
            raise RuntimeError('unexpected checker failure')
        if outcome == 'interrupted':
            timers[0].callback()
            raise z3.Z3Exception('canceled')
        if outcome in ('memory', 'capacity'):
            raise z3.Z3Exception('out of memory' if outcome == 'memory' else 'table overflow')
        answer = SolverAnswer(outcome)
        model = None
        if answer is SolverAnswer.SAT:
            solver.add(*assertions)
            assert solver.check() == z3.sat
            model = solver.model()
        return exact_solve.ExactCheckResult(answer, model=model,
            reason='timeout' if answer is SolverAnswer.UNKNOWN else '',
            unknown_cause=(exact_solve.ExactUnknownCause.TIME_LIMIT
                           if answer is SolverAnswer.UNKNOWN else None))

    monkeypatch.setattr(exact_solve, '_z3_check', check)
    def run():
        return exact_solve._z3_check_native((z3.BoolVal(True, ctx),), 1000, None,
                                          owned_context=ctx, strategy=strategy)
    if outcome == 'exception':
        with pytest.raises(RuntimeError, match='unexpected checker failure'):
            run()
    else:
        result = run()
        assert result.answer is (SolverAnswer(outcome) if outcome in ('sat', 'unsat')
                                 else SolverAnswer.UNKNOWN)
        if result.answer is SolverAnswer.UNKNOWN:
            assert result.model is None
            assert result.unknown_cause is {
                'memory': exact_solve.ExactUnknownCause.MEMORY_LIMIT,
                'capacity': exact_solve.ExactUnknownCause.OTHER,
                'unknown': exact_solve.ExactUnknownCause.TIME_LIMIT,
                'interrupted': exact_solve.ExactUnknownCause.TIME_LIMIT,
            }[outcome]
    assert len(timers) == 1 and timers[0].closed and timers[0].joins == 1


@pytest.mark.parametrize('strategy', ['qfbv', 'finite', 'smt', 'smt-shared'])
def test_all_backend_answers_wait_for_watchdog_join(monkeypatch, strategy):
    _, timers = _clock(monkeypatch)
    original = exact_solve._z3_check

    def check(*args, **kwargs):
        answer = original(*args, **kwargs)
        join = timers[0].join
        def racing_join():
            timers[0].callback()
            join()
        timers[0].join = racing_join
        return answer

    monkeypatch.setattr(exact_solve, '_z3_check', check)
    ctx = z3.Context()
    result = exact_solve._z3_check_native((z3.BoolVal(True, ctx),), 1000, None,
                                        owned_context=ctx, strategy=strategy)
    assert result.answer is SolverAnswer.UNKNOWN and result.model is None
    assert result.unknown_cause is exact_solve.ExactUnknownCause.TIME_LIMIT
    assert timers[0].joins == 1


def test_backend_construction_cannot_renew_the_native_budget(monkeypatch):
    now, timers = _clock(monkeypatch)
    original = z3.Then
    def slow_tactic(*args):
        result = original(*args)
        now[0] += 1
        return result
    monkeypatch.setattr(z3, 'Then', slow_tactic)
    monkeypatch.setattr(exact_solve, '_z3_check', lambda *a, **k: pytest.fail('expired before check'))
    result = exact_solve.z3_check_qfbv((z3.BoolVal(True),), 1000)
    assert result.answer is SolverAnswer.UNKNOWN and result.model is None
    assert result.unknown_cause is exact_solve.ExactUnknownCause.TIME_LIMIT
    assert timers[0].joins == 1
