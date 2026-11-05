"""Growing prefixes propose witnesses; only whole requests decide Analysis."""

from types import SimpleNamespace

import pytest
import z3

from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.analysis_query import GoalQueryCache, GrowingGoalQuery
from behave.sim.rtl.source_task import SourceTaskFrame
from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check, z3_check_finite

pytestmark = pytest.mark.solver


def _counter(*, horizon=20, depth=10, private=False, bad_tail=False):
    """Small exact transition system with explicit final environment roots."""
    context = z3.Context() if private else None
    true, false = z3.BoolVal(True, context), z3.BoolVal(False, context)
    inputs = [z3.BitVec(f'growing_input_{i}', 4, context) for i in range(horizon)]
    states = [z3.Int(f'growing_state_{i}', context) for i in range(horizon + 1)]
    initial = (states[0] == 0,)
    frames = [SourceTaskFrame(i, None,
        (states[i + 1] == z3.If(inputs[i] == 1, states[i] + 1, 0),),
        {'deep': states[i + 1] >= depth}, {}, {}, false, ()) for i in range(horizon)]
    request = SimpleNamespace(context=context, constraints=(), atomic=False, settled=False,
        drain_slots=(true,), bind_initial=lambda _: initial,
        rows=lambda model: [{'x': model.eval(term, model_completion=True).as_long()} for term in inputs],
        fix=lambda rows: tuple(term == row['x'] for term, row in zip(inputs, rows, strict=True)))
    tail = z3.BoolVal(not bad_tail, context)
    roots = (*initial, *(root for frame in frames for root in frame.constraints))
    visits = []

    def encoding(extra=()):
        return SimpleNamespace(constraints=(*roots, *extra), alive=true,
            environment_assumptions={'tail': z3.Not(tail)}, unfinished=false, domain_faults={},
            hits={'deep': z3.Or(*(frame.hits['deep'] for frame in frames))},
            source=SimpleNamespace(memory=SimpleNamespace(conflicts=())))

    def steps(bound, *, goals):
        assert bound == horizon and set(goals) == {'deep'}
        visits.append('start')
        try:
            for frame in frames:
                visits.append(frame.index)
                yield frame
            visits.append('complete')
            return None, request, encoding()
        finally:
            visits.append('closed')

    def fixed(bound, *, goals, fixed_rows):
        assert bound == horizon and len(fixed_rows) == horizon
        visits.append('fixed')
        return None, request, encoding(request.fix(fixed_rows))

    source = SimpleNamespace(request_coordinates=lambda _: request, initial_binding={},
        bind_request_steps=steps, bind_request=fixed, release_query=lambda *_: visits.append('released'))
    return source, visits


def _solver(calls):
    def solve(phase, roots, **options):
        result = z3_check_finite(roots, 5000, _owned_context=roots[0].ctx,
                                _preparation=options.get('preparation'))
        calls.append((phase, result.answer))
        return result
    return solve


@pytest.mark.parametrize('private', [False, True])
def test_growing_prefix_unsat_deepens_without_rebuilding_or_classifying(private):
    source, visits = _counter(private=private)
    cache, calls = GoalQueryCache(), []
    query = cache.start(source, 20, ('deep',), (), check=lambda: None)
    try:
        fixed, member, _ = query.propose(('deep',), solve=_solver(calls), check=lambda: None)
        assert fixed is not None and member is not None
        assert z3.is_true(member.eval(fixed.hits['deep']))
        assert all(z3.is_true(member.eval(root, model_completion=True)) for root in fixed.constraints)
        assert visits.count('start') == 1 and 'complete' not in visits
        assert [v for v in visits if type(v) is int] == list(range(16))
        assert [answer for phase, answer in calls if phase == 'query-prefix'] == [
            SolverAnswer.UNSAT] * 4 + [SolverAnswer.SAT]
        assert cache.current is None  # A fixed member is not a universal cache entry.
        full = cache.prepare(source, 20, ('deep',), (), check=lambda: None)
        assert full is cache.current and full.encoding is not fixed.encoding
        assert visits.count('start') == 1
        assert [v for v in visits if type(v) is int] == list(range(20))
    finally:
        cache.close()
    assert visits.count('closed') == 1


@pytest.mark.parametrize('failure', ['unknown', 'invalid-tail'])
def test_growing_proposal_failure_finishes_the_same_unrestricted_request(failure):
    source, visits = _counter(depth=1, bad_tail=failure == 'invalid-tail')
    cache, calls = GoalQueryCache(), []
    query = cache.start(source, 20, ('deep',), (), check=lambda: None)
    solve = (_solver(calls) if failure == 'invalid-tail' else
             lambda *_a, **_k: ExactCheckResult(SolverAnswer.UNKNOWN, reason='test interruption'))
    try:
        assert query.propose(('deep',), solve=solve, check=lambda: None)[:2] == (None, None)
        assert 'complete' not in visits and cache.current is None
        full = cache.prepare(source, 20, ('deep',), (), check=lambda: None)
        assert visits.count('start') == 1
        assert [v for v in visits if type(v) is int] == list(range(20))
        expected = SolverAnswer.UNSAT if failure == 'invalid-tail' else SolverAnswer.SAT
        assert z3_check((*full.constraints, full.hits['deep']), 5000).answer is expected
    finally:
        cache.close()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('depth', [10, 21])
def test_spurious_prefix_proposal_does_not_replace_the_original_query(private, depth):
    """An overapproximate proposal still owes a complete original member."""
    source, visits = _counter(private=private, depth=depth)
    cache, calls = GoalQueryCache(), []
    query = cache.start(source, 20, ('deep',), (), check=lambda: None)
    exact_solve = _solver(calls)

    def solve(phase, roots, **options):
        if phase != 'query-prefix':
            return exact_solve(phase, roots, **options)
        # At the first cut the penultimate root defines the next state.
        # Havoc that successor only in the proposal; retain original roots.
        assert len(roots) == 4 and z3.is_eq(roots[-2])
        answer = z3_check((*roots[:-2], roots[-1]), 5000)
        assert answer.answer is SolverAnswer.SAT
        calls.append((phase, answer.answer))
        return answer

    try:
        assert query.propose(('deep',), solve=solve, check=lambda: None)[:2] == (None, None)
        assert visits.count('fixed') == 1 and 'complete' not in visits
        assert calls[0] == ('query-prefix', SolverAnswer.SAT)
        assert any(answer is SolverAnswer.UNSAT for _, answer in calls[1:])
        assert cache.current is None
        full = cache.prepare(source, 20, ('deep',), (), check=lambda: None)
        expected = SolverAnswer.SAT if depth <= 20 else SolverAnswer.UNSAT
        assert z3_check((*full.constraints, full.hits['deep']), 5000).answer is expected
        assert visits.count('start') == visits.count('complete') == 1
    finally:
        cache.close()
    assert visits.count('closed') == 1


@pytest.mark.parametrize('where', ['construction', 'solve', 'fixed-member'])
def test_growing_cancellation_releases_partial_construction(where):
    source, visits = _counter(depth=1)
    cache = GoalQueryCache()
    query = cache.start(source, 20, ('deep',), (), check=lambda: None)

    def check():
        if (where == 'construction' and 0 in visits
                or where == 'fixed-member' and 'fixed' in visits):
            raise InterruptedError('stop growing query')

    def solve(phase, roots, **_options):
        if where == 'solve':
            raise InterruptedError('stop growing query')
        return z3_check(roots, 5000)

    with pytest.raises(InterruptedError):
        try:
            query.propose(('deep',), solve=solve, check=check)
        finally:
            cache.close()
    assert cache.current is cache.growing is None
    assert query.steps is query.preparation is None
    assert visits.count('closed') == 1 and 'complete' not in visits


@pytest.mark.parametrize('interrupted', [None, 'encoding', 'preparation'])
def test_growing_member_diagnostics_identify_interrupted_stage(monkeypatch, interrupted):
    from behave.sim.rtl import analysis_query
    from behave.sim.symbolic.diagnostics import diagnostic_scope

    source, visits = _counter(depth=1)
    query, calls, stages = GrowingGoalQuery(source, 20, ('deep',), ()), [], []

    def stop(*args, **kwargs):
        raise InterruptedError('member construction interrupted')

    if interrupted == 'encoding':
        monkeypatch.setattr(source, 'bind_request', stop)
    elif interrupted == 'preparation':
        monkeypatch.setattr(analysis_query, '_prepare_goal_query', stop)
    try:
        with diagnostic_scope(stages.append):
            if interrupted:
                with pytest.raises(InterruptedError, match='member construction interrupted'):
                    query.propose(('deep',), solve=_solver(calls), check=lambda: None)
            else:
                fixed, member, _ = query.propose(('deep',), solve=_solver(calls), check=lambda: None)
                assert all(z3.is_true(member.eval(root)) for root in fixed.constraints)
    finally:
        query.close()
    spans = [row for row in stages if row['stage'][-1].startswith('query.member.')]
    names = ['encoding'] if interrupted == 'encoding' else ['encoding', 'preparation']
    assert [(row['stage'][-1], row['event']) for row in spans] == [
        (f'query.member.{name}', event) for name in names
        for event in ('started', 'raised' if name == interrupted else 'returned')]
    assert query.steps is query.preparation is None and visits.count('closed') == 1


@pytest.mark.parametrize('mode', ['complete', 'cancelled', 'broken-sink'])
def test_growing_diagnostics_cover_early_construction_without_changing_it(mode):
    from behave.sim.symbolic.diagnostics import diagnostic_scope

    source, visits = _counter(horizon=5, depth=3)
    query, stages = GrowingGoalQuery(source, 5, ('deep',), ()), []

    def emit(row):
        stages.append(row)
        if mode == 'broken-sink':
            raise RuntimeError('diagnostic output unavailable')

    def check():
        if mode == 'cancelled' and 1 in visits:
            raise InterruptedError('original construction cancelled')

    try:
        with diagnostic_scope(emit):
            if mode == 'cancelled':
                with pytest.raises(InterruptedError, match='original construction cancelled'):
                    query.finish(check)
            else:
                assert query.finish(check)[1] is source.request_coordinates(5)
        ends = [row for row in stages if row['event'] != 'started']
        assert all(row['stage'] == ['history.advance'] and row['horizon'] == 5 for row in stages)
        assert [row['to_slot'] for row in ends] == ([1, 2, 4] if mode == 'cancelled' else [1, 2, 4, 5, 5])
        assert [row['detail']['frames'] for row in ends] == ([1, 1, 0] if mode == 'cancelled' else [1, 1, 2, 1, 0])
        assert ends[-1]['event'] == ('raised' if mode == 'cancelled' else 'returned')
        assert ends[-1]['detail']['complete'] is (mode != 'cancelled')
        assert all(row['elapsed_ns'] >= 0 for row in ends)
        assert query.construction_ns > 0
        expected = 2 if mode == 'cancelled' else 5
        assert [v for v in visits if type(v) is int] == list(range(expected))
        assert len(query.roots) == expected + 2
        assert len(query.images['deep']) == expected
        assert visits.count('start') == 1
    finally:
        query.close()
    assert visits.count('closed') == 1


@pytest.mark.parametrize('depth', [10, 20, 21])
def test_growing_coordinator_reuses_one_history_for_deep_goals(depth):
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl.analysis_job import _SourceAnalysis
    from behave.sim.rtl.analysis_query import GoalReduction
    from .test_rtl_analysis_session import _query_scope

    source, visits = _counter(depth=depth)
    source.template = SimpleNamespace(direct=object())
    source.goal_names = ('deep',)
    source.machine = SimpleNamespace(domain_checks=())
    exact = object.__new__(_SourceAnalysis)
    exact.source, exact.request = source, source.request_coordinates(20)
    exact.domain, exact._queries = SimpleNamespace(bound=20), GoalQueryCache()
    retained = []
    exact._retain_query_member = lambda rows, model, check, *, encoding: retained.append(encoding)
    reduction = GoalReduction(None, ('deep',), frozenset(), frozenset(), source)
    calls = []
    result = collect_verdicts(exact._query_reduction_steps(reduction, _query_scope(_solver(calls)),
                                                         proof_ns=0, proof_answers=[]))
    assert not result.undecided
    if depth <= 20:
        assert set(result.reachable) == {'deep'} and not result.unreachable
        assert len(retained) == 1 and len(result.reachable['deep']) == 20
        assert 'complete' not in visits
    else:
        assert set(result.unreachable) == {'deep'} and not result.reachable and not retained
        assert visits.count('complete') == 1 and 'fixed' not in visits
    assert visits.count('start') == visits.count('closed') == 1
    assert exact._queries.growing is None
    exact._queries.close()


def test_growing_complete_query_drops_proposal_only_constraints():
    source, visits = _counter(depth=20)
    source.request_coordinates(20).drain_slots = (z3.BoolVal(False),)
    cache, calls = GoalQueryCache(), []
    query = cache.start(source, 20, ('deep',), (), check=lambda: None)
    try:
        assert query.propose(('deep',), solve=_solver(calls), check=lambda: None)[:2] == (None, None)
        assert all(answer is SolverAnswer.UNSAT for _, answer in calls)
        full = cache.prepare(source, 20, ('deep',), (), check=lambda: None)
        assert z3_check((*full.constraints, full.hits['deep']), 5000).answer is SolverAnswer.SAT
        assert visits.count('start') == visits.count('complete') == 1
    finally:
        cache.close()


@pytest.mark.parametrize('depth,hit', [(10, True), (10, False), (21, False), (10, None), (21, None)])
def test_streaming_proposal_uses_only_full_native_hits_and_keeps_negative_query(depth, hit):
    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import Verdict
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl.analysis_job import _SourceAnalysis
    from behave.sim.rtl.analysis_query import GoalReduction, NativeQueryProposal
    from .test_rtl_analysis_session import _query_scope

    source, visits = _counter(depth=depth)
    source.template = SimpleNamespace(direct=object())
    source.goal_names = ('deep',)
    source.machine = SimpleNamespace(domain_checks=())
    exact = object.__new__(_SourceAnalysis)
    exact.source, exact.request = source, source.request_coordinates(20)
    exact.domain, exact._queries = TraceDomain(20), GoalQueryCache()
    retained, calls = [], []
    exact._retain_query_member = lambda *args, **kw: retained.append(True)
    reduction = GoalReduction(None, ('deep',), frozenset(), frozenset(), source)
    solver = _solver(calls)

    def solve(phase, roots, **options):
        if not hit and phase == 'query-prefix':
            # Deliberately spurious prefix: its modeled hit is not evidence.
            answer = z3_check((*roots[:-2], roots[-1]), 5000)
            assert answer.answer is SolverAnswer.SAT
            calls.append((phase, answer.answer))
            return answer
        return solver(phase, roots, **options)

    parts = exact._query_reduction_steps(reduction, _query_scope(solve),
        proof_ns=0, proof_answers=[], native_proposals=True)
    try:
        proposal = next(parts)
        assert type(proposal) is NativeQueryProposal
        with pytest.raises((ValueError, TypeError)):
            Verdict.from_record(proposal.as_record())
        assert 'fixed' not in visits and 'complete' not in visits
        witness = AuditedWitness(domain=exact.domain.digest(), inputs=proposal.rows,
            states=[{}] * 21, outputs=[()] * 20,
            hit_sets=[('deep',) if hit else ()] * 20)
        first = parts.send(None if hit is None else witness)
        result = collect_verdicts((first, *parts))
        assert not result.undecided
        assert bool(result.reachable) == (depth <= 20)
        assert bool(result.unreachable) == (depth > 20)
        if hit:
            assert set(result.reachable) == {'deep'}
            assert 'complete' not in visits and not retained
            assert all(phase == 'query-prefix' for phase, _ in calls)
        else:
            assert visits.count('complete') == 1
            assert any(phase == 'query' for phase, _ in calls)
        assert 'fixed' not in visits  # No duplicate fixed-input symbolic history.
        assert visits.count('start') == visits.count('closed') == 1
    finally:
        parts.close()
        exact._queries.close()


from .test_rtl_source_worker_task import task


@pytest.mark.cosim
def test_fixed_full_source_member_does_not_replace_or_close_paused_query(task):
    from behave.sim.analysis_budget import analysis_scope
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact

    _, context, *_ = task
    with analysis_scope(120):
        source = PreparedSourceExact(context.source_payload(), direct=True)
        request = source.request_coordinates(6)
        goal = next(name for name, value in context.frontend_catalogue.items() if value.family == 'location')
        query = GrowingGoalQuery(source, 6, (goal,), ())
        calls = []
        try:
            fixed, member, _ = query.propose((goal,), solve=_solver(calls), check=lambda: None)
            assert fixed is not None and member is not None
            assert fixed.request is request and len(request.rows(member)) == 6
            assert not source._requests and not source._query_requests
            assert query.steps is not None
            _, owned, full = query.finish(lambda: None)
            assert owned is request and full is not fixed.encoding
            assert source._query_requests[6][1][2] is full
            # Fixed-request equalities never contaminate the complete query.
            rows = request.rows(member)
            solver = z3.Solver()
            solver.set(timeout=30_000)
            solver.add(*full.constraints, z3.Not(z3.And(*request.fix(rows))))
            assert solver.check() == z3.sat
        finally:
            query.close()
