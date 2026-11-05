"""Progress is useful after cancellation, never an additional proof premise."""

import json
import threading

import pytest

from behave.sim.symbolic.diagnostics import diagnostic_scope, diagnostic_stage, diagnostics_active


def test_nested_spans_preserve_phase_and_exception_without_reclassifying():
    rows = []
    error = KeyboardInterrupt('cancelled')
    with diagnostic_scope(rows.append), pytest.raises(KeyboardInterrupt) as caught:
        with diagnostic_stage('query'):
            with diagnostic_stage('proof', phase='boundary:establishment'):
                raise error
    assert caught.value is error
    assert [(r['stage'], r['event']) for r in rows] == [
        (['query'], 'started'), (['query', 'proof'], 'started'),
        (['query', 'proof'], 'raised'), (['query'], 'raised')]
    assert rows[2]['phase'] == 'boundary:establishment'
    assert rows[2]['detail']['exception'] == 'KeyboardInterrupt'
    assert rows[2]['elapsed_ns'] >= 0
    json.dumps(rows)
    assert not diagnostics_active()


def test_unmeasured_spans_are_silent_and_nested_sinks_restore():
    first, second = [], []
    with diagnostic_stage('unmeasured'):
        assert not diagnostics_active()
    with diagnostic_scope(first.append):
        with diagnostic_scope(second.append), diagnostic_stage('inner'):
            pass
        with diagnostic_stage('outer'):
            pass
    assert first[0]['stage'] == ['outer']
    assert second[0]['stage'] == ['inner']


def test_refused_span_retains_typed_reason_without_replacing_exception():
    from behave.sim.core.refusal import Refusal, Refused

    rows = []
    error = Refused(Refusal('unsupported-boundary', 'The source needs the scheduler.',
                            required=8, supplied=4))
    with diagnostic_scope(rows.append), pytest.raises(Refused) as caught:
        with diagnostic_stage('boundary'):
            raise error
    assert caught.value is error
    assert rows[-1]['detail'] == {
        'exception': 'Refused', 'refusal': error.refusal.as_dict()}
    json.dumps(rows)


def test_broken_diagnostic_sink_does_not_replace_result_or_cancellation():
    def broken(_row):
        raise OSError('diagnostic stream closed')

    with diagnostic_scope(broken):
        with diagnostic_stage('success'):
            value = 7
        assert value == 7
        with pytest.raises(TimeoutError, match='owner deadline'), diagnostic_stage('cancelled'):
            raise TimeoutError('owner deadline')


def test_independent_context_does_not_claim_another_threads_measurements():
    rows = []

    def background():
        with diagnostic_stage('unmeasured-thread'):
            assert not diagnostics_active()

    with diagnostic_scope(rows.append):
        worker = threading.Thread(target=background)
        worker.start()
        worker.join()
    assert rows == []


@pytest.mark.parametrize('satisfiable', [False, True])
def test_native_solver_spans_preserve_answer_model_and_statistics(satisfiable):
    z3 = pytest.importorskip('z3')
    from behave.sim.symbolic.exact_solve import z3_check
    from behave.sim.core.query import SolverAnswer

    x = z3.BitVec('diagnostic_x', 8)
    roots = (x == 4, x == (4 if satisfiable else 5))
    rows = []
    with diagnostic_scope(rows.append), diagnostic_stage('proof', phase='fixture'):
        measured = z3_check(roots, 1000)
    ordinary = z3_check(roots, 1000)
    assert measured.answer is ordinary.answer
    assert measured.answer is (SolverAnswer.SAT if satisfiable else SolverAnswer.UNSAT)
    if satisfiable:
        assert measured.model.eval(x).as_long() == ordinary.model.eval(x).as_long() == 4
    checked, = [r for r in rows if r['stage'][-1] == 'solver.check' and r['event'] == 'returned']
    assert checked['includes_preprocessing'] is True
    assert checked['detail']['answer'] == ('sat' if satisfiable else 'unsat')
    assert checked['detail']['statistics']
    assert all(isinstance(value, (int, float)) for value in checked['detail']['statistics'].values())


@pytest.mark.parametrize('reason', ['timeout', 'controlled incomplete theory'])
def test_native_unknown_reason_is_reported_without_changing_the_result(monkeypatch, reason):
    z3 = pytest.importorskip('z3')
    from behave.sim.symbolic.exact_solve import _z3_check
    from behave.sim.core.query import SolverAnswer

    solver = z3.Solver()
    monkeypatch.setattr(solver, 'check', lambda: z3.unknown)
    monkeypatch.setattr(solver, 'reason_unknown', lambda: reason)
    roots = (z3.BoolVal(True),)
    rows = []
    with diagnostic_scope(rows.append):
        measured = _z3_check(roots, 1000, solver)
    ordinary = _z3_check(roots, 1000, solver)
    assert measured.answer is ordinary.answer is SolverAnswer.UNKNOWN
    assert measured.reason == ordinary.reason == reason
    assert measured.unknown_cause is ordinary.unknown_cause
    checked, = [row for row in rows
                if row['stage'] == ['solver.check'] and row['event'] == 'returned']
    assert checked['detail']['reason'] == reason
