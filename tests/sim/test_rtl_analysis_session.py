"""Scoped proof reuse must not reuse target answers, native evidence or constraints."""

from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim import job_session
from behave.sim.analysis_budget import analysis_scope
from behave.sim.core.audit import AuditedWitnessLimitError, WitnessInconsistent
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.core.stimulus import canonical_json
from behave.sim.engines.rtl import analysis_session as child
from behave.sim.job_isolation import IsolatedJobFailure
from behave.sim.rtl import analysis_job, analysis_transport
from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
from behave.sim.rtl.analysis_query import GoalQueryCache


def _query_scope(solve, *, check=lambda: None, remaining=lambda: 1000):
    return SimpleNamespace(timeout=1000, budget=SimpleNamespace(remaining_ms=remaining),
                           solve=solve, check=check, provenance=None)


@pytest.mark.parametrize('change', ('none', 'request', 'domain', 'scope', 'inputs', 'record'))
def test_native_evidence_reuse_requires_every_owner_and_complete_record(change):
    from behave.sim.rtl.observation_audit import NativeEvidence

    native = NativeEvidence()
    request, domain, scope = object(), object(), object()
    image = {'output': [7], 'hits': ['g'], 'earlier_state': [0, 1]}
    trace = SimpleNamespace(as_dict=lambda: image)
    built = []
    def build():
        witness = object()
        built.append(witness)
        return witness
    first = native.audit_witness(request, domain, scope, b'inputs', trace,
                                 build=build, check=lambda: None)
    if change == 'record':
        # Identical final output/hits cannot hide a changed intermediate state.
        image['earlier_state'] = [3, 1]
    second = native.audit_witness(
        object() if change == 'request' else request,
        object() if change == 'domain' else domain,
        object() if change == 'scope' else scope,
        b'changed' if change == 'inputs' else b'inputs', trace, build=build, check=lambda: None)
    assert (first is second) == (change == 'none')
    assert len(built) == (1 if change == 'none' else 2)


@pytest.mark.parametrize('stop', ('build', 'post-build', 'reuse'))
def test_native_evidence_never_publishes_failed_or_cancelled_audit(stop):
    from behave.sim.rtl.observation_audit import NativeEvidence

    native = NativeEvidence()
    request, domain, scope = object(), object(), object()
    trace = SimpleNamespace(as_dict=lambda: {'complete': True})
    old = native.audit_witness(request, domain, scope, b'old', trace,
                               build=object, check=lambda: None)
    expired = stop == 'reuse'
    def check():
        if expired:
            raise InterruptedError('audit deadline')
    def build():
        nonlocal expired
        if stop == 'build':
            raise Refused(Refusal('bad-evidence', 'changed native record'))
        expired = True
        return object()
    with pytest.raises((InterruptedError, Refused)):
        native.audit_witness(request, domain, scope, b'old' if stop == 'reuse' else b'new',
                             trace, build=build, check=check)
    if stop == 'reuse':
        assert native.witness[-1] is old  # Valid evidence remains, but was not returned.
    else:
        assert native.witness is None


def test_native_evidence_lifetimes_are_distinct_from_query_preparation():
    from behave.sim.rtl.observation_audit import NativeEvidence

    exact = object.__new__(analysis_job._SourceAnalysis)
    queries = exact._queries = GoalQueryCache()
    assert '_native' not in vars(exact)
    native = exact._native
    assert isinstance(native, NativeEvidence)
    projection, witness = object(), object()
    native.encoding = native.relation = native.trace = native.agreement = projection
    native.member_proof = native.member_miss = native.witness = witness
    native.proofs.entries[b'complete'] = projection
    exact.release_witness()
    assert native.member_proof is native.member_miss is native.witness is None
    assert native.encoding is native.relation is native.trace is native.agreement is projection
    assert native.proofs.entries == {b'complete': projection}
    native.clear()
    assert native.encoding is native.relation is native.trace is native.agreement is None
    assert not native.proofs.entries
    assert exact._queries is queries


@pytest.mark.parametrize('change', ('none', 'compiler', 'request', 'domain', 'scope', 'inputs'))
def test_native_member_proof_cannot_cross_owners(monkeypatch, change):
    from behave.sim.rtl import observation_audit
    from behave.sim.core import analysis, audit

    native = observation_audit.NativeEvidence()
    compiler = SimpleNamespace(covers_native_orders=True, machine=SimpleNamespace(startup=()),
        plan=lambda *a, **k: None)
    request, domain, scope = object(), SimpleNamespace(digest=lambda: 'bound-domain'), object()
    proof = SimpleNamespace(encoding=object(), native_image={}, same_obligation=lambda _, *, check: True)
    native.member_proof = compiler, request, domain, scope, b'inputs', object(), proof
    compared, remembered = [], []
    def compare(*args, **kwargs):
        compared.append(args)
        return proof
    monkeypatch.setattr(observation_audit, 'compare_native_history', compare)
    monkeypatch.setattr(analysis, 'audit_environment_assumptions', lambda *a: None)
    monkeypatch.setattr(audit, 'AuditedWitness', lambda **fields: fields)
    source = SimpleNamespace(template=SimpleNamespace(direct=(
        SimpleNamespace(**vars(compiler)) if change == 'compiler' else compiler)),
        frontend={}, observation=None, contract=object())
    result = native.propose_member(source,
        object() if change == 'request' else request,
        SimpleNamespace(digest=lambda: 'another-domain') if change == 'domain' else domain,
        object() if change == 'scope' else scope, (), SimpleNamespace(stimulus=()),
        key=b'changed' if change == 'inputs' else b'inputs', remember=remembered.append,
        solve=None, check=lambda: None)
    assert bool(compared) == bool(remembered) == (change == 'none')
    assert (result is not None) == (change == 'none')


def _prepared_query_fixture(monkeypatch):
    import z3
    from behave.sim.rtl import source_member

    x = z3.Int('prepared_goal_input')
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    visits = []
    request = object()
    encoded = SimpleNamespace(constraints=(x == 0,), environment_assumptions={'input': x < 0},
        unfinished=false, domain_faults={'fault': x == 1}, alive=true,
        hits={'first': x == 0, 'second': x == 2},
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=(x == 3,))))

    def bind(horizon, *, goals):
        visits.append(('construct', horizon, frozenset(goals)))
        return None, request, encoded

    def literals(roots, encoding, *, context, check, targets):
        visits.append(('literals',))
        assert all(term.ctx is context for term in roots)
        assert targets and all(z3.is_bool(term) and term.ctx is context for term in targets)
        return SimpleNamespace(constant_targets=lambda terms: terms,
                               literal_constraints=lambda: (x == 0,))

    monkeypatch.setattr(source_member, 'source_literal_consequences', literals)
    return SimpleNamespace(bind_request=bind,
        release_query=lambda horizon, encoding: visits.append(('release', horizon))), encoded, visits


def test_prepared_query_reuses_construction_and_literals_not_target_answers(monkeypatch):
    import z3
    cache = GoalQueryCache()
    source, encoded, visits = _prepared_query_fixture(monkeypatch)
    first = cache.prepare(source, 100, ('first', 'second'), (), check=lambda: None)
    checks = []
    again = cache.prepare(source, 100, ('second', 'first'), (),
                          check=lambda: checks.append('checked'))
    assert again is first and checks == ['checked', 'checked']
    assert visits == [('construct', 100, frozenset(('first', 'second'))), ('literals',),
                      ('construct', 100, frozenset(('first', 'second')))]
    assert first.encoding is encoded
    assert all(a.eq(b) for a, b in zip(first.preparation.base, first.constraints, strict=True))
    for goal, expected in (('first', z3.sat), ('second', z3.unsat)):
        solver = z3.Solver()
        solver.add(*first.constraints, first.hits[goal])
        assert solver.check() == expected


def test_prepared_query_owns_readonly_original_and_reduced_images(monkeypatch):
    import z3
    from behave.sim.rtl import source_member

    source, encoded, _ = _prepared_query_fixture(monkeypatch)
    original = encoded.hits['first']
    monkeypatch.setattr(source_member, 'source_literal_consequences',
        lambda *_a, **_k: SimpleNamespace(
            constant_targets=lambda terms: (z3.BoolVal(True), *terms[1:]),
            literal_constraints=lambda: ()))
    query = GoalQueryCache().prepare(source, 100, ('first',), (), check=lambda: None)
    assert query.observations['first'] is original
    assert z3.is_true(query.hits['first'])
    assert encoded.hits['first'] is original
    for image in (query.observations, query.domain_faults, query.hits):
        with pytest.raises(TypeError):
            image['invented'] = z3.BoolVal(True)
    # These are detached query images, not caches installed on mutable inputs.
    encoded.hits['first'] = z3.BoolVal(False)
    encoded.domain_faults.clear()
    assert query.observations['first'] is original
    assert set(query.domain_faults) == {'fault'}


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('open_kind', (None, 'goal', 'unfinished', 'operation', 'memory'))
def test_closed_literal_query_needs_no_whole_history_propagation(monkeypatch, private, open_kind):
    import z3
    from behave.sim.rtl import source_member

    ctx = z3.Context() if private else None
    false, true = z3.BoolVal(False, ctx=ctx), z3.BoolVal(True, ctx=ctx)
    live = z3.Bool('unknown_query_part', ctx=ctx)
    encoded = SimpleNamespace(constraints=(true,), environment_assumptions={}, alive=true,
        hits={'goal': live if open_kind == 'goal' else false},
        unfinished=live if open_kind == 'unfinished' else false,
        domain_faults={'operation': live if open_kind == 'operation' else false},
        source=SimpleNamespace(memory=SimpleNamespace(
            conflicts=(live,) if open_kind == 'memory' else ())))
    source = SimpleNamespace(bind_request=lambda *_a, **_k: (None, object(), encoded))
    visits = []
    monkeypatch.setattr(source_member, 'source_literal_consequences',
                        lambda *_a, **_k: visits.append(True))
    query = GoalQueryCache().prepare(source, 100, ('goal',), (), check=lambda: None)
    assert visits == ([] if open_kind is None else [True])
    assert len(query.constraints) == 1 and query.constraints[0] is true
    solver = z3.Solver(ctx=ctx)
    solver.add(*query.constraints, z3.Or(query.hits['goal'], query.escape))
    assert solver.check() == (z3.unsat if open_kind is None else z3.sat)


@pytest.mark.parametrize('size', (1, 8, 64))
def test_sat_decode_reads_history_images_once_not_once_per_goal(monkeypatch, size):
    import z3
    from collections import Counter
    from behave.sim.core import query
    from behave.sim.rtl import source_domains, source_member
    from behave.sim.symbolic.exact_solve import z3_check

    reads, observed = Counter(), []
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    goals = tuple(f'event-{index}' for index in range(size))
    terms = {goal: z3.Bool(goal) for goal in goals}
    class Encoding(SimpleNamespace):
        @property
        def hits(self):
            reads['hits'] += 1
            return dict(terms)

        @property
        def domain_faults(self):
            reads['faults'] += 1
            return {}

    encoded = Encoding(constraints=tuple(terms.values()), environment_assumptions={},
        alive=true, unfinished=false, startup=None, protocol=SimpleNamespace(hits=()),
        monitor_hits=(), transaction_hits=(), source=SimpleNamespace(memory=SimpleNamespace(conflicts=())))
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.request = SimpleNamespace(atomic=False, settled=False, constraints=(),
        drain_slots=(true,), rows=lambda model: [])
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()), goal_names=goals,
        machine=SimpleNamespace(domain_checks=()),
        bind_request=lambda *_a, **_k: (None, exact.request, encoded), release_query=lambda *_a: None)
    exact.domain, exact._queries = SimpleNamespace(bound=100), GoalQueryCache()
    exact._retain_query_member = lambda *_a, **_k: None
    monkeypatch.setattr(source_domains, 'prove_transition_domains', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_domains, 'prove_transition_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_domains, 'prove_reset_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_member, 'source_literal_consequences', lambda *_a, **_k: None)
    monkeypatch.setattr(source_member, 'source_query_prefixes', lambda *_a, **_k: iter(()))
    def consume(batch, pose):
        for _ in range(2):
            call, hits = pose(batch)
            assert call.answer is query.SolverAnswer.SAT
            observed.append(hits)
        return iter(())
    monkeypatch.setattr(query, 'iter_decide_setwise', consume)
    list(exact._query_batch_steps(goals, _query_scope(
        lambda _phase, roots, **_options: z3_check(roots, 1000))))
    assert observed == [goals, goals]
    assert reads == {'hits': 1, 'faults': 1}


@pytest.mark.parametrize('change', ('source', 'horizon', 'goals', 'exclusions', 'encoding', 'request'))
def test_prepared_query_identity_changes_drop_old_solver(monkeypatch, change):
    import z3
    cache = GoalQueryCache()
    source, _, visits = _prepared_query_fixture(monkeypatch)
    first = cache.prepare(source, 100, ('first', 'second'), (), check=lambda: None)
    cleared = []
    original_clear = first.preparation.clear
    def clear(**options):
        cleared.append(first.preparation)
        return original_clear(**options)
    monkeypatch.setattr(first.preparation, 'clear', clear)
    if change in {'encoding', 'request'}:
        bind = source.bind_request
        def replaced(*args, **kwargs):
            relation, request, encoded = bind(*args, **kwargs)
            return (relation, object() if change == 'request' else request,
                SimpleNamespace(**{**vars(encoded), 'domain_faults': {'fault': z3.BoolVal(True)}})
                if change == 'encoding' else encoded)
        source.bind_request = replaced
    second = cache.prepare(
        SimpleNamespace(**vars(source)) if change == 'source' else source,
        101 if change == 'horizon' else 100,
        ('first',) if change == 'goals' else ('first', 'second'),
        ('fault',) if change == 'exclusions' else (), check=lambda: None)
    assert second is not first and second.preparation is not first.preparation
    assert cleared == [first.preparation]
    assert visits.count(('literals',)) == 2
    if change == 'encoding':
        assert z3.is_true(second.escape)


@pytest.mark.parametrize('excluded', ((), ('fault',)))
def test_prepared_query_preserves_roots_environment_and_unproved_exits(monkeypatch, excluded):
    import z3
    source, encoded, _ = _prepared_query_fixture(monkeypatch)
    query = GoalQueryCache().prepare(source, 100, ('first',), excluded, check=lambda: None)
    assert query.constraints[0].eq(encoded.constraints[0])
    assert query.constraints[1].eq(z3.Not(encoded.environment_assumptions['input']))
    solver = z3.Solver()
    expected = z3.Or(encoded.unfinished, *encoded.source.memory.conflicts,
                    *(term for name, term in encoded.domain_faults.items() if name not in excluded))
    solver.add(query.escape != expected)
    assert solver.check() == z3.unsat
    assert query.encoding.hits == encoded.hits  # No catalogue mutation.


def test_prepared_query_cancelled_preprocessing_is_not_reused(monkeypatch):
    from behave.sim.rtl import source_member
    cache = GoalQueryCache()
    source, _, visits = _prepared_query_fixture(monkeypatch)
    original = source_member.source_literal_consequences
    expired = False
    def interrupted(*args, **kwargs):
        nonlocal expired
        value = original(*args, **kwargs)
        expired = True
        return value
    def check():
        if expired:
            raise Refused(Refusal('test-expired', 'no partial prepared query'))
    monkeypatch.setattr(source_member, 'source_literal_consequences', interrupted)
    with pytest.raises(Refused, match='no partial prepared query'):
        cache.prepare(source, 100, ('first',), (), check=check)
    assert cache.current is None
    assert visits[-1] == ('release', 100)
    monkeypatch.setattr(source_member, 'source_literal_consequences', original)
    query = cache.prepare(source, 100, ('first',), (), check=lambda: None)
    assert query.goals == frozenset(('first',))
    assert visits.count(('literals',)) == 2


def test_cached_query_cancellation_releases_solver_and_compiler_entry(monkeypatch):
    from behave.sim.symbolic.exact_solve import z3_check_finite

    cache = GoalQueryCache()
    source, _, visits = _prepared_query_fixture(monkeypatch)
    model = cache.prepare(source, 100, ('first',), (), check=lambda: None)
    z3_check_finite(model.constraints, 1000, _preparation=model.preparation,
                    _owned_context=model.preparation.context)
    assert model.preparation._solver is not None

    def cancel():
        raise InterruptedError('cancel before reuse')

    with pytest.raises(InterruptedError, match='cancel before reuse'):
        cache.prepare(source, 100, ('first',), (), check=cancel)
    assert cache.current is None and model.preparation._solver is None
    assert visits[-1] == ('release', 100)
    before = list(visits)
    cache.close()
    assert visits == before  # Closing twice cannot touch another owner.


@pytest.mark.parametrize('closing_error', [False, True])
def test_cursor_close_releases_query_after_generator_but_keeps_native_evidence(monkeypatch, closing_error):
    source, _, visits = _prepared_query_fixture(monkeypatch)
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact._queries = GoalQueryCache()
    exact._queries.prepare(source, 100, ('first',), (), check=lambda: None)
    exact._pending_query_member = object()
    members = exact._request_members = {b'admitted': None}
    native = exact._native
    evidence = native.encoding = native.relation = native.trace = object()
    native.proofs.entries[b'audited'] = evidence
    prepared = object.__new__(analysis_job.PreparedAnalysis)
    prepared.exact = exact

    def cursor():
        try:
            yield None
        finally:
            assert exact._queries.current is not None
            visits.append(('cursor-closed',))
            if closing_error:
                raise RuntimeError('cursor cleanup failed')

    prepared._query_cursor = cursor()
    next(prepared._query_cursor)
    if closing_error:
        with pytest.raises(RuntimeError, match='cursor cleanup failed'):
            prepared._close_query()
    else:
        prepared._close_query()
    assert visits[-2:] == [('cursor-closed',), ('release', 100)]
    assert exact._queries.current is exact._pending_query_member is prepared._query_cursor is None
    assert native.encoding is native.relation is native.trace is evidence
    assert native.proofs.entries == {b'audited': evidence}
    assert exact._request_members is members and members == {b'admitted': None}


def test_retiring_query_image_never_releases_other_images_or_native_records():
    source = object.__new__(analysis_job.PreparedSourceExact)
    first, later, native = object(), object(), object()
    source._query_requests = {10: (('a',), (None, None, first)),
                              20: (('b',), (None, None, later))}
    source._requests = {10: native}
    source.release_query(10, later)  # A stale owner cannot retire its successor.
    assert len(source._query_requests) == 2
    source.release_query(10, first)
    source.release_query(10, first)  # Retirement is idempotent.
    assert source._query_requests == {20: (('b',), (None, None, later))}
    assert source._requests == {10: native}


def _deferred_goal_batches(monkeypatch, *, local=True, escape=False):
    import z3
    from behave.sim.symbolic.exact_solve import z3_check
    from behave.sim.core import reset_monitor
    from behave.sim.rtl import source_domains, source_member

    control, event = 'local-control', 'source-event'
    false, true = z3.BoolVal(False), z3.BoolVal(True)
    pending, exit_ = z3.Bools('batch_pending batch_escape')
    visits = []
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.request = SimpleNamespace(constraints=(), drain_slots=(true,), atomic=False,
                                    settled=False)
    exact.domain, exact._queries = SimpleNamespace(bound=100), GoalQueryCache()

    def construct(_bound, *, goals):
        visits.append(('construct', tuple(goals)))
        encoded = SimpleNamespace(constraints=(z3.Not(pending),),
            environment_assumptions={}, unfinished=exit_ if escape and not goals else false,
            domain_faults={}, alive=true, hits={control: false, event: pending}, startup=None,
            source=SimpleNamespace(memory=SimpleNamespace(conflicts=())),
            protocol=SimpleNamespace(hits=()), monitor_hits=(), transaction_hits=())
        return None, exact.request, encoded

    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()),
        goal_names=(event,), machine=SimpleNamespace(occurrences=(), domain_checks=()),
        contract=SimpleNamespace(clocks=()), bind_request=construct,
        release_query=lambda *_a: None)

    def domains(*_a, **_k):
        visits.append(('domains',))
        return frozenset()

    def prove(_source, goals, **_options):
        visits.append(('local', tuple(goals)))
        return set(goals).intersection((control,)) if local else frozenset()

    monkeypatch.setattr(reset_monitor, 'reset_state_domains', lambda *_a, **_k: ())
    monkeypatch.setattr(source_domains, 'prove_transition_domains', domains)
    monkeypatch.setattr(source_domains, 'prove_transition_goals', prove)
    monkeypatch.setattr(source_domains, 'prove_reset_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_member, 'source_literal_consequences', lambda *_a, **_k: None)
    monkeypatch.setattr(source_member, 'source_query_prefixes', lambda *_a, **_k: iter(()))

    def solve(phase, roots, **_options):
        visits.append(('solve', phase))
        return z3_check(roots, 1000)

    return exact, (control, event), visits, solve


@pytest.mark.parametrize('answer', ('proved', 'unknown', 'refused'))
@pytest.mark.parametrize('escape', (False, True))
def test_source_control_exclusions_still_require_complete_history(monkeypatch, answer, escape):
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl import source_domains

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch, local=False, escape=escape)
    calls = []

    def control(source, request, selected, **options):
        assert source is exact.source and request is exact.request
        calls.append(tuple(selected))
        if answer == 'refused':
            raise Refused(Refusal('optional-control-unavailable', 'retain original goals'))
        return frozenset(selected) if answer == 'proved' else frozenset()

    monkeypatch.setattr(source_domains, 'prove_reset_goals', control)
    result = collect_verdicts(exact._query_steps(goals, _query_scope(solve)))
    assert calls == [(goals[1],)]  # Only residual source events, not protocol/control goals.
    assert not result.reachable
    assert set(result.undecided) == ({goals[1]} if answer == 'proved' and escape else set())
    assert set(result.unreachable) == set(goals) - set(result.undecided)
    assert [entry[1] for entry in visits if entry[0] == 'construct'] == (
        [(goals[0],), ()] if answer == 'proved' else [(goals[0],), (goals[1],)])


@pytest.mark.parametrize('calendar', ['no-drain', 'drain', 'symbolic'])
@pytest.mark.parametrize('private', [False, True])
def test_terminal_completion_literal_is_resolved_before_source_history(monkeypatch, calendar, private):
    import z3
    from behave.sim import api
    from behave.sim.core.monitor import INPUT_COMPLETION
    from behave.sim.rtl.analysis_query import LocalGoalProofs
    from behave.sim.rtl.atomic_request import AtomicRequestCodec
    from behave.sim.rtl.protocol_relation import SymbolicRequest
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_symbolic_request import _contract, _schedule

    contract = _contract()
    if calendar == 'symbolic':
        codec = ReadyValidRequestCodec(contract, 2, 1)
    else:
        slots = [api.EventSlot(ticks=('c',), resets=('c',) if i == 0 else (),
            offers=('in',) if i > 0 and (calendar == 'no-drain' or i < 3) else (),
            controls={'rst': int(i == 0), 'ordy': int(i > 0)}) for i in range(4)]
        codec = AtomicRequestCodec(contract, 2, _schedule(contract, slots))
    request = SymbolicRequest(codec, 4, context=z3.Context() if private else None)
    original = request.constraints
    exact, _, visits, solve = _deferred_goal_batches(monkeypatch, local=False)
    exact.request = request
    reduction = LocalGoalProofs(exact.source, request).prepare(
        (INPUT_COMPLETION,), solve=solve, check=lambda: None)
    assert reduction.proved == ({INPUT_COMPLETION} if calendar == 'no-drain' else set())
    assert reduction.domains == set() and reduction.source is exact.source
    assert request.constraints is original
    assert not any(row[0] in {'construct', 'solve'} for row in visits)
    # Independently check the monitor identity for arbitrary source completion
    # and pending input; no successful-source premise or fixed queue is used.
    completed, pending = z3.Bools('terminal_complete terminal_pending', ctx=request.context)
    solver = z3.Solver(ctx=request.context)
    solver.add(*original, completed, pending, z3.Or(*request.drain_slots))
    assert solver.check() == (z3.unsat if calendar == 'no-drain' else z3.sat)


@pytest.mark.parametrize('escape', [False, True])
def test_schedule_literal_cannot_hide_an_unfinished_source_history(monkeypatch, escape):
    from behave.sim import api
    from behave.sim.core.monitor import INPUT_COMPLETION
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl.atomic_request import AtomicRequestCodec
    from behave.sim.rtl.protocol_relation import SymbolicRequest
    from .test_rtl_symbolic_request import _contract, _schedule

    contract = _contract()
    slots = [api.EventSlot(ticks=('c',), offers=('in',), controls={'rst': 0, 'ordy': 1})]
    exact, _, visits, solve = _deferred_goal_batches(monkeypatch, local=False, escape=escape)
    exact.request = SymbolicRequest(AtomicRequestCodec(contract, 1, _schedule(contract, slots)), 1)
    result = collect_verdicts(exact._query_batch_steps((INPUT_COMPLETION,), _query_scope(solve)))
    assert [row[1] for row in visits if row[0] == 'construct'] == [()]
    assert set(result.undecided) == ({INPUT_COMPLETION} if escape else set())
    assert set(result.unreachable) == (set() if escape else {INPUT_COMPLETION})
    assert not result.reachable


def test_source_control_budget_exhaustion_cannot_be_swallowed_as_optional_refusal(monkeypatch):
    from behave.sim.rtl import source_domains
    from behave.sim.rtl.analysis_query import LocalGoalProofs

    exact, goals, _, solve = _deferred_goal_batches(monkeypatch)
    expired = False

    def check():
        if expired:
            raise Refused(Refusal('test-budget-exhausted', 'stop the request'))

    def control(*_args, **_kwargs):
        nonlocal expired
        expired = True
        raise Refused(Refusal('test-budget-exhausted', 'stop the request'))

    monkeypatch.setattr(source_domains, 'prove_reset_goals', control)
    with pytest.raises(Refused, match='test-budget-exhausted'):
        LocalGoalProofs(exact.source, exact.request).prepare(goals, solve=solve, check=check)


@pytest.mark.parametrize('cancel', (False, True))
def test_next_goal_batch_retires_old_model_before_construction(monkeypatch, cancel):
    import weakref

    exact, goals, _, solve = _deferred_goal_batches(monkeypatch)
    scope = _query_scope(solve)
    list(exact._query_batch_steps(goals[:1], scope))
    previous = weakref.ref(exact._queries.current.preparation)
    retired = []
    exact.source.release_query = lambda horizon, encoding: retired.append(horizon)
    construct = exact.source.bind_request

    def replacement(*args, **kwargs):
        assert previous() is None, 'old literals and solver roots must not overlap the new build'
        assert retired == [100]
        if cancel:
            raise InterruptedError('new history cancelled')
        return construct(*args, **kwargs)

    exact.source.bind_request = replacement
    parts = exact._query_batch_steps(goals[1:], scope)
    if cancel:
        with pytest.raises(InterruptedError, match='new history cancelled'):
            list(parts)
        assert exact._queries.current is None
    else:
        list(parts)
        assert exact._queries.current.goals == frozenset(goals[1:])


@pytest.mark.parametrize('change', ('none', 'source', 'request'))
def test_local_goal_proofs_belong_to_one_source_request(monkeypatch, change):
    from behave.sim.rtl.analysis_query import LocalGoalProofs

    exact, _, _, _ = _deferred_goal_batches(monkeypatch)
    proofs = LocalGoalProofs(exact.source, exact.request)
    source = object() if change == 'source' else exact.source
    request = object() if change == 'request' else exact.request
    if change == 'none':
        proofs.require_binding(source, request)
    else:
        with pytest.raises(ValueError, match='another source/request'):
            proofs.require_binding(source, request)


def test_escape_proof_cannot_consume_live_goals_or_another_owners_facts(monkeypatch):
    from behave.sim.rtl.analysis_query import LocalGoalProofs

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch)
    proofs = LocalGoalProofs(exact.source, exact.request)
    live = proofs.prepare(goals, solve=solve, check=lambda: None)
    assert live.proved == {goals[0]} and live.residual == (goals[1],)
    with pytest.raises(ValueError, match='live goals'):
        proofs.close_escape(live, solve=solve, check=lambda: None)
    closed = proofs.prepare((goals[0],), solve=solve, check=lambda: None)
    with pytest.raises(ValueError, match='another proof invocation'):
        LocalGoalProofs(exact.source, exact.request).close_escape(
            closed, solve=solve, check=lambda: None)
    assert proofs.close_escape(closed, solve=solve, check=lambda: None).proved == {goals[0]}
    assert visits.count(('domains',)) == 1
    assert not any(entry[0] in {'construct', 'solve'} for entry in visits)


def test_refused_projected_view_does_not_publish_domain_exclusions(monkeypatch):
    from behave.sim.rtl import source_domains
    from behave.sim.rtl.analysis_query import LocalGoalProofs

    exact, goals, _, solve = _deferred_goal_batches(monkeypatch)
    attempted = []
    monkeypatch.setattr(source_domains, 'prove_transition_domains', lambda *_a, **_k: {'fault'})
    def refuse(excluded, *, check):
        attempted.append(excluded)
        raise Refused(Refusal('view-unavailable', 'retain the original domain'))
    exact.source._domain_query_view = refuse
    proofs = LocalGoalProofs(exact.source, exact.request)
    for goal in goals:
        reduction = proofs.prepare((goal,), solve=solve, check=lambda: None)
        assert reduction.source is exact.source and not reduction.domains
    assert attempted == [frozenset({'fault'})]


@pytest.mark.parametrize('local', (False, True))
@pytest.mark.parametrize('escape', (False, True))
def test_empty_residual_batch_runs_after_live_goals_without_dropping_escape(monkeypatch, local, escape):
    from behave.sim.core.query import collect_verdicts

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch, local=local, escape=escape)
    result = collect_verdicts(exact._query_steps(goals, _query_scope(solve)))
    construction = [entry[1] for entry in visits if entry[0] == 'construct']
    assert construction == ([(goals[1],), ()] if local else [(goals[0],), (goals[1],)])
    assert visits.count(('domains',)) == 1
    assert all(visits.count(('local', (goal,))) == 1 for goal in goals)
    assert not result.reachable
    if local and escape:
        assert set(result.unreachable) == {goals[1]}
        assert set(result.undecided) == {goals[0]}
    else:
        assert set(result.unreachable) == set(goals) and not result.undecided


@pytest.mark.parametrize('reset_result', ('proved', 'unknown', 'refused'))
@pytest.mark.parametrize('mixed', (False, 'sat'))
def test_escape_only_batch_proves_domains_after_live_goals_and_keeps_unknown_faults(
        monkeypatch, reset_result, mixed):
    import z3
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl import source_domains

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch)
    original = exact.source
    construct = original.bind_request
    faults = tuple((name, Refusal('test-domain', name)) for name in ('read', 'write'))
    original.machine.domain_checks = faults
    if mixed:
        monkeypatch.setattr(analysis_job, 'goal_batches', lambda *_a: ((goals,), (), frozenset()))
        exact.request.rows = lambda _model: [{'input': 1}]
        exact._retain_query_member = lambda *_a, **_k: visits.append(('member',))

    def view(excluded):
        def build(bound, *, goals):
            _, request, encoded = construct(bound, goals=goals)
            if mixed and goals:
                # The live-goal view has an escape that is excluded only by
                # the complete query, not by syntactic constant folding.
                encoded.unfinished = z3.Bool('mixed_escape')
                encoded.constraints = (z3.Not(encoded.unfinished),)
            # A private fault matters even after every public goal has been
            # locally excluded. An ordinary live batch remains independent.
            encoded.domain_faults = ({name: z3.BoolVal(True)
                for name in ('read', 'write') if name not in excluded} if not goals else {})
            return None, request, encoded
        return SimpleNamespace(**{**vars(original),
            'machine': SimpleNamespace(**{**vars(original.machine), 'domain_checks': tuple(
                row for row in original.machine.domain_checks if row[0] not in excluded)}),
            '_domain_query_view': lambda fresh, **_k: view(excluded | fresh),
            'bind_request': build})

    exact.source = view(frozenset())

    def transition(source, *, solve=None, check):
        if solve is None:
            visits.append(('structural',))
            return frozenset()
        assert ('construct', (goals[1],)) in visits
        visits.append(('closure-transition',))
        return frozenset({'read'})

    def reset(source, request, *, solve, check):
        assert request is exact.request
        assert source.machine.domain_checks == faults[1:]
        visits.append(('closure-reset',))
        if reset_result == 'refused':
            raise Refused(Refusal('optional-unsupported', 'keep the original fault'))
        return frozenset({'write'}) if reset_result == 'proved' else frozenset()

    monkeypatch.setattr(source_domains, 'prove_transition_domains', transition)
    monkeypatch.setattr(source_domains, 'prove_reset_domains', reset)
    completed = []
    parts = exact._query_steps(goals, _query_scope(solve))
    if reset_result == 'proved':
        completed.extend(parts)
    else:
        with pytest.raises(Refused, match='rtl-source-operation-domain-unencoded'):
            for part in parts:
                completed.append(part)
    result = collect_verdicts(completed)
    assert visits.count(('closure-transition',)) == visits.count(('closure-reset',)) == 1
    assert original.machine.domain_checks == faults
    expected = set(goals) if reset_result == 'proved' else {goals[1]}
    if mixed == 'sat':
        expected.remove(goals[1])
        assert set(result.reachable) == {goals[1]}
        assert visits.count(('member',)) == 1
    assert set(result.unreachable) == expected
    assert not result.undecided
    assert visits.count(('local', goals)) == (1 if mixed else 0)


@pytest.mark.parametrize('unknown', (False, True))
def test_mixed_batch_reuses_only_full_unsat_without_escape_reconstruction(monkeypatch, unknown):
    import z3
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl.analysis_query import LocalGoalProofs
    from behave.sim.core.query import SolverAnswer
    from behave.sim.symbolic.exact_solve import ExactCheckResult

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch)
    construct = exact.source.bind_request
    def build(bound, *, goals):
        model, request, encoded = construct(bound, goals=goals)
        encoded.unfinished = z3.Bool('excluded_escape')
        encoded.constraints += (z3.Not(encoded.unfinished),)
        return model, request, encoded
    exact.source.bind_request = build
    def forbidden(*_a, **_k):
        pytest.fail('a full-query UNSAT already discharged this escape')
    if not unknown:
        monkeypatch.setattr(LocalGoalProofs, 'close_escape', forbidden)
    def query(phase, roots, **options):
        if unknown and not visits.count(('solve', 'query')):
            visits.append(('solve', 'query'))
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='test-unknown')
        return solve(phase, roots, **options)
    result = collect_verdicts(exact._query_batch_steps(goals, _query_scope(query)))
    assert set(result.unreachable) == ({goals[0]} if unknown else set(goals))
    assert set(result.undecided) == ({goals[1]} if unknown else set())
    assert [entry[1] for entry in visits if entry[0] == 'construct'] == (
        [(goals[1],), ()] if unknown else [(goals[1],)])
    assert visits.count(('solve', 'query')) == (2 if unknown else 1)
    assert sum(call.solver_calls for call in result.queries) == (2 if unknown else 1)


def test_hard_protocol_lemma_does_not_block_other_batches_or_their_escapes(monkeypatch):
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl import source_stability

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch)
    protocols = (source_stability.GOAL, source_stability.COUNT_GOAL)

    def hard_lemma(*_args, **_kwargs):
        visits.append(('protocol',))
        raise Refused(Refusal('rtl-source-analysis-budget-exhausted', 'hard protocol proof'))

    monkeypatch.setattr(source_stability, 'prove_protocol_goals', hard_lemma)
    def check():
        if ('protocol',) in visits:
            raise Refused(Refusal('rtl-source-analysis-budget-exhausted', 'hard protocol proof'))

    completed = []
    parts = exact._query_steps((*protocols, *goals), _query_scope(solve, check=check))
    with pytest.raises(Refused, match='hard protocol proof'):
        for verdict in parts:
            completed.append(verdict)
    result = collect_verdicts(completed)
    assert set(result.unreachable) == set(goals)
    assert not set(protocols).intersection(result.unreachable)
    assert [entry[1] for entry in visits if entry[0] == 'construct'] == [(goals[1],), ()]
    assert visits[-1] == ('protocol',)


@pytest.mark.parametrize('stop', ('close', 'budget'))
@pytest.mark.parametrize('mixed', (False, True))
def test_deferred_batch_cannot_publish_local_proof_after_cancellation(monkeypatch, stop, mixed):
    import inspect
    import z3

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch)
    if mixed:
        monkeypatch.setattr(analysis_job, 'goal_batches', lambda *_a: ((goals,), (), frozenset()))
        construct = exact.source.bind_request
        def build(bound, *, goals):
            model, request, encoded = construct(bound, goals=goals)
            encoded.unfinished = z3.Bool('cancel_escape')
            encoded.constraints += (z3.Not(encoded.unfinished),)
            return model, request, encoded
        exact.source.bind_request = build
    workers, expired = [], False
    batch_steps = exact._query_batch_steps

    def capture(*args, **kwargs):
        parts = batch_steps(*args, **kwargs)
        workers.append(parts)
        return parts

    exact._query_batch_steps = capture

    def check():
        if expired:
            raise Refused(Refusal('rtl-source-analysis-budget-exhausted', 'owner expired'))

    parts = exact._query_steps(goals,
        _query_scope(solve, check=check, remaining=lambda: 0 if expired else 1000))
    first = next(parts)
    assert set(first.unreachable) == {goals[1]} and not first.undecided
    assert [entry[1] for entry in visits if entry[0] == 'construct'] == [(goals[1],)]
    if not mixed:
        # The postponed batch is data, not a suspended execution frame.
        assert inspect.getgeneratorstate(workers[0]) == inspect.GEN_CLOSED
    if stop == 'close':
        parts.close()
    else:
        expired = True
        with pytest.raises(Refused, match='owner expired'):
            next(parts)
    assert all(inspect.getgeneratorstate(worker) == inspect.GEN_CLOSED for worker in workers)
    assert [entry[1] for entry in visits if entry[0] == 'construct'] == [(goals[1],)]


def test_deferred_batch_returns_facts_and_charges_local_proof_once(monkeypatch):
    import inspect
    import z3
    from behave.sim.core.query import SolverAnswer, collect_verdicts
    from behave.sim.rtl import source_domains
    from behave.sim.rtl.analysis_query import GoalReduction, LocalProofCost
    from behave.sim.symbolic.exact_solve import ExactCheckResult

    exact, goals, visits, _ = _deferred_goal_batches(monkeypatch)
    queried = []

    def local(_source, batch, *, solve, check):
        visits.append(('local', tuple(batch)))
        assert solve('local-proof', (z3.BoolVal(False),)).answer is SolverAnswer.UNSAT
        return frozenset(batch)

    def solve(phase, roots, **options):
        queried.append(phase)
        return ExactCheckResult(SolverAnswer.UNSAT, solver_load_ns=11, solve_ns=17)

    monkeypatch.setattr(source_domains, 'prove_transition_goals', local)
    scope = _query_scope(solve)
    parts = exact._query_batch_steps(goals[:1], scope, defer_proved=True)
    with pytest.raises(StopIteration) as stopped:
        next(parts)
    reduction, cost = stopped.value.value
    assert isinstance(reduction, GoalReduction) and isinstance(cost, LocalProofCost)
    assert inspect.getgeneratorstate(parts) == inspect.GEN_CLOSED
    assert not reduction.residual and cost.elapsed_ns >= 0
    assert cost.calls == [{'solver_load_ns': 11, 'solve_ns': 17}]
    assert not any(entry[0] == 'construct' for entry in visits)

    result = collect_verdicts(exact._query_prepared_steps(reduction, cost, scope,
        proofs=reduction.owner))
    assert set(result.unreachable) == set(goals[:1]) and not result.undecided
    assert queried == ['local-proof']  # Resumption does not repeat the proof.
    assert sum(call.solver_calls for call in result.queries) == 1
    assert visits.count(('local', goals[:1])) == 1


def test_single_locally_closed_batch_never_exports_scheduler_marker(monkeypatch):
    from behave.sim.core.query import collect_verdicts

    exact, goals, visits, solve = _deferred_goal_batches(monkeypatch)
    result = collect_verdicts(exact._query_steps(goals[:1], _query_scope(solve)))
    assert set(result.unreachable) == {goals[0]} and not result.undecided
    assert [entry[1] for entry in visits if entry[0] == 'construct'] == [()]


@pytest.mark.parametrize('expiration', ('entry', 'lemma-refusal', 'lemma-success'))
def test_exhausted_goal_batch_never_starts_another_history(monkeypatch, expiration):
    from behave.sim.rtl import source_stability

    expired = expiration == 'entry'
    visited = []
    exact = object.__new__(analysis_job._SourceAnalysis)

    def forbidden(*args, **kwargs):
        pytest.fail('an expired operation must not construct or solve another history')

    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()), bind_request=forbidden)
    exact.request = object()
    exact.domain = SimpleNamespace(bound=337)

    def check():
        if expired:
            raise Refused(Refusal('rtl-source-analysis-budget-exhausted', 'operation expired'))

    def lemma(*args, **kwargs):
        nonlocal expired
        visited.append('lemma')
        expired = True
        if expiration == 'lemma-refusal':
            raise Refused(Refusal('rtl-source-analysis-budget-exhausted', 'lemma expired'))
        return frozenset(('monitor.sink-stability',))

    monkeypatch.setattr(source_stability, 'prove_protocol_goals', lemma)
    parts = exact._query_batch_steps(('monitor.sink-stability',),
                                    _query_scope(forbidden, check=check))
    with pytest.raises(Refused, match='operation expired'):
        next(parts)
    assert visited == ([] if expiration == 'entry' else ['lemma'])


def test_goal_union_does_not_resimplify_shared_goal_dags(monkeypatch):
    import z3

    value = z3.BitVec('goal_union_value', 128)
    target = value / (value + 1) == 5
    def forbidden(*_args, **_kwargs):
        raise AssertionError('the goal scheduler revisited an arithmetic DAG')
    monkeypatch.setattr(z3, 'simplify', forbidden)
    assert analysis_job._goal_union((z3.BoolVal(False), target), context=value.ctx).eq(target)
    assert z3.is_false(analysis_job._goal_union((z3.BoolVal(False),), context=value.ctx))
    assert z3.is_true(analysis_job._goal_union((target, z3.BoolVal(True)), context=value.ctx))


@pytest.mark.parametrize('closed', [False, True])
def test_local_lemma_closes_escape_only_after_live_goals(monkeypatch, closed):
    import z3
    from behave.sim.core import query
    from behave.sim.rtl import source_domains, source_member, source_stability

    proved, pending = 'monitor.sink-stability', 'event'
    false, true = z3.BoolVal(False), z3.BoolVal(True)
    encoded = SimpleNamespace(constraints=(true,), environment_assumptions={},
        unfinished=false if closed else z3.Bool('escape'), domain_faults={}, alive=true,
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=())),
        hits={proved: false, pending: z3.Bool('hit')}, protocol=SimpleNamespace(hits=()),
        monitor_hits=(), transaction_hits=())
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.request = SimpleNamespace()
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()),
        goal_names=(), machine=SimpleNamespace(domain_checks=()),
        bind_request=lambda *_args, **_kwargs: (None, exact.request, encoded),
        release_query=lambda *_args: None)
    exact.domain = SimpleNamespace(bound=100)
    exact._queries = GoalQueryCache()
    monkeypatch.setattr(source_stability, 'prove_protocol_goals', lambda *_a, **_k: {proved})
    monkeypatch.setattr(source_domains, 'prove_transition_domains', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_domains, 'prove_transition_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_member, 'source_literal_consequences', lambda *_a, **_k: None)
    batches = []
    def capture(goals, pose):
        batches.append(tuple(goals))
        return iter(())
    monkeypatch.setattr(query, 'iter_decide_setwise', capture)
    list(exact._query_batch_steps((proved, pending), _query_scope(None)))
    assert batches == ([(proved,), (pending,)] if closed else [(pending,), (proved,)])


def test_prefix_reuses_only_proved_transition_domain_exclusions(monkeypatch):
    import z3
    from behave.sim.core import query
    from behave.sim.rtl import source_domains, source_member

    goal = 'event'
    hit, safe, live = z3.Bools('prefix_hit prefix_proved_domain prefix_live_domain')
    false, true = z3.BoolVal(False), z3.BoolVal(True)
    faults = {'safe': safe, 'live': live}
    encoded = SimpleNamespace(constraints=(true,), environment_assumptions={},
        unfinished=false, domain_faults=faults, alive=true,
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=())), hits={goal: hit},
        protocol=SimpleNamespace(hits=()), monitor_hits=(), transaction_hits=())
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.request = SimpleNamespace(constraints=(), drain_slots=(true,), atomic=False, settled=False)
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()),
        goal_names=(goal,),
        bind_request=lambda *_a, **_k: (None, exact.request, encoded))
    exact.domain = SimpleNamespace(bound=100)
    exact._queries = GoalQueryCache()
    monkeypatch.setattr(source_domains, 'prove_transition_domains', lambda *_a, **_k: {'safe'})
    projected = SimpleNamespace(bind_request=exact.source.bind_request, goal_names=(goal,))
    exact.source._domain_query_view = lambda _proved, **_kwargs: projected
    def original_history(*_a, **_k):
        pytest.fail('a proved query view must be selected before history construction')
    exact.source.bind_request = original_history
    def local_goals(source, *_a, **_k):
        assert source is projected
        return frozenset()
    monkeypatch.setattr(source_domains, 'prove_transition_goals', local_goals)
    monkeypatch.setattr(source_domains, 'prove_reset_goals', local_goals)
    monkeypatch.setattr(source_member, 'source_literal_consequences', lambda *_a, **_k: None)
    monkeypatch.setattr(source_member, 'source_query_prefixes',
        lambda *_a, **_k: iter((((true,), {goal: hit}, faults),
                               ((true, z3.Not(hit)), {goal: hit}, faults))))
    monkeypatch.setattr(query, 'iter_decide_setwise', lambda goals, pose: pose(goals))

    class Captured(Exception):
        pass

    preparations = []
    def solve(phase, roots, *, preparation):
        from behave.sim.symbolic.exact_solve import ExactCheckResult

        assert phase == ('query-prefix' if not preparations else 'query')
        assert roots[-1].eq(z3.Or(hit, live))
        assert len(preparation.base) == len(roots) - 1
        assert all(a.eq(b) for a, b in zip(preparation.base, roots[:-1]))
        preparations.append(preparation)
        if len(preparations) == 1:
            return ExactCheckResult(query.SolverAnswer.UNSAT)
        raise Captured

    with pytest.raises(Captured):
        next(exact._query_batch_steps((goal,), _query_scope(solve)))
    assert faults == {'safe': safe, 'live': live}  # Original observations unchanged.
    assert len(preparations) == 2 and preparations[0] is not preparations[1]
    assert preparations[0]._solver is None  # Also released on exceptions.


@pytest.mark.parametrize('locally_closed,prefix_answer,valid', [
    (True, 'sat', True), (True, 'sat', False),
    (True, 'unsat', False), (True, 'unknown', False), (False, 'sat', True),
])
@pytest.mark.parametrize('private', [False, True])
def test_escape_only_prefix_requires_full_validation(
        monkeypatch, locally_closed, prefix_answer, valid, private):
    import z3
    from behave.sim.core import query
    from behave.sim.rtl import source_member
    from behave.sim.rtl.analysis_query import GoalReduction
    from behave.sim.symbolic.exact_solve import ExactCheckResult

    context = z3.Context() if private else z3.main_ctx()
    false, true = z3.BoolVal(False, context), z3.BoolVal(True, context)
    escape = z3.Bool('early_domain_escape', ctx=context)
    goal, fault = 'locally_closed', 'unsupported'
    full = (true,) if valid else (z3.Not(escape),)
    encoded = SimpleNamespace(constraints=full, environment_assumptions={},
        unfinished=false, domain_faults={fault: escape}, alive=true, hits={goal: false},
        startup=SimpleNamespace(domain_faults={fault: escape}),
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=())),
        protocol=SimpleNamespace(hits=()), monitor_hits=(), transaction_hits=())
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.request = SimpleNamespace(constraints=(), drain_slots=(true,), atomic=False, settled=False)
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()), goal_names=(goal,),
        machine=SimpleNamespace(domain_checks=((fault, Refusal('unencoded', 'test domain')),)),
        bind_request=lambda *_a, **_k: (None, exact.request, encoded))
    exact.domain, exact._queries = SimpleNamespace(bound=100), GoalQueryCache()
    monkeypatch.setattr(source_member, 'source_literal_consequences', lambda *_a, **_k: None)
    monkeypatch.setattr(source_member, 'source_query_prefixes', lambda *_a, **_k: iter((
        ((true,), {goal: false}, {fault: false}),
        ((true,), {goal: false}, {fault: escape}))))
    calls, validations = [], []

    def member(model, histories, assertions, **_options):
        assert histories == (encoded,)
        assert all(any(root.eq(original) for root in assertions) for original in full)
        accepted = all(z3.is_true(model.eval(root, model_completion=False)) for root in assertions)
        validations.append(accepted)
        return model if accepted else None

    monkeypatch.setattr(source_member, 'source_member_hint', member)

    def solve(phase, roots, **_options):
        calls.append(phase)
        assert roots[-1].eq(escape)  # Never query the preceding empty prefix.
        if phase == 'query':
            return ExactCheckResult(query.SolverAnswer.UNKNOWN, reason='full query pending')
        assert phase == 'query-prefix'
        solver = z3.Solver(ctx=context)
        solver.add(*roots)
        assert solver.check() == z3.sat
        answer = query.SolverAnswer(prefix_answer)
        return ExactCheckResult(answer,
            model=solver.model() if answer is query.SolverAnswer.SAT else None,
            reason='prefix pending' if answer is query.SolverAnswer.UNKNOWN else '')

    def decide(goals, pose):
        call, hits = pose(goals)
        assert call.answer is query.SolverAnswer.UNKNOWN and not hits
        return iter(())

    monkeypatch.setattr(query, 'iter_decide_setwise', decide)
    reduction = GoalReduction(None, (goal,), frozenset((goal,)) if locally_closed else frozenset(),
                              frozenset(), exact.source)
    parts = exact._query_reduction_steps(reduction, _query_scope(solve), proof_ns=0, proof_answers=[])
    confirmed = locally_closed and prefix_answer == 'sat' and valid
    if confirmed:
        with pytest.raises(Refused, match='rtl-source-operation-domain-unencoded') as error:
            list(parts)
        assert error.value.refusal.detail['startup_faults'] == (fault,)
    else:
        assert list(parts) == []  # No partial-prefix classification escapes.
    assert calls == (['query-prefix'] if confirmed else
                     ['query-prefix', 'query'] if locally_closed else ['query'])
    assert validations == ([valid] if locally_closed and prefix_answer == 'sat' else [])


@pytest.mark.parametrize('prefix_answer', ['unsat', 'unknown', 'sat'])
def test_failed_prefix_retires_until_a_new_query_without_classifying_goals(monkeypatch, prefix_answer):
    import z3
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core import query
    from behave.sim.rtl import source_domains, source_member

    goals = ('a', 'b', 'c')
    hits = dict(zip(goals, z3.Bools('prefix_a prefix_b prefix_c'), strict=True))
    escape = z3.Bool('prefix_escape')
    false, true = z3.BoolVal(False), z3.BoolVal(True)
    encoded = SimpleNamespace(constraints=(true,), environment_assumptions={},
        unfinished=false, domain_faults={'live': escape}, alive=true,
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=())), hits=hits,
        protocol=SimpleNamespace(hits=()), monitor_hits=(), transaction_hits=())
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.request = SimpleNamespace(constraints=(), drain_slots=(true,), atomic=False, settled=False)
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()), goal_names=goals,
        bind_request=lambda *_a, **_k: (None, exact.request, encoded))
    exact.domain = SimpleNamespace(bound=100)
    exact._queries = GoalQueryCache()
    monkeypatch.setattr(source_domains, 'prove_transition_domains', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_domains, 'prove_transition_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_domains, 'prove_reset_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_member, 'source_literal_consequences', lambda *_a, **_k: None)
    monkeypatch.setattr(source_member, 'source_member_hint', lambda *_a, **_k: None)
    short = z3.Not(z3.Or(*hits.values(), escape))
    longer = z3.Bool('longer_prefix')
    monkeypatch.setattr(source_member, 'source_query_prefixes', lambda *_a, **_k: iter((
        ((short,), hits, {'live': escape}), ((longer,), hits, {'live': escape}))))
    batches = (('a', 'b'), ('b',), ('c',), ('b', 'c'))
    proposals, full_queries = [], []

    def solve(phase, roots, **_options):
        if phase == 'query-prefix':
            index = int(roots[0].eq(longer))
            proposals.append(index)
            answer = query.SolverAnswer(prefix_answer) if index == 0 else query.SolverAnswer.UNKNOWN
            return ExactCheckResult(answer, model=z3.Model() if answer is query.SolverAnswer.SAT else None,
                reason='prefix unfinished' if answer is query.SolverAnswer.UNKNOWN else '')
        assert phase == 'query'
        batch = batches[len(full_queries) % len(batches)]
        # Retiring a failed prefix leaves the full target and its live
        # unencoded exit intact. It never supplies a full-domain UNSAT.
        expected = z3.Or(*(hits[goal] for goal in batch), escape)
        check = z3.Solver()
        check.add(roots[-1] != expected)
        assert check.check() == z3.unsat
        full_queries.append(batch)
        return ExactCheckResult(query.SolverAnswer.UNKNOWN, reason='complete query unfinished')

    def decide(_goals, pose):
        for batch in batches:
            call, reached = pose(batch)
            assert call.answer is query.SolverAnswer.UNKNOWN and not reached
        return iter(())

    monkeypatch.setattr(query, 'iter_decide_setwise', decide)
    for _ in range(2):
        # A new query/history gets a fresh accelerator, not old negative facts.
        list(exact._query_batch_steps(goals, _query_scope(solve)))
    assert proposals == [0, 0]
    assert full_queries == list(batches) * 2


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('separate_queries', [False, True])
def test_residual_queries_reuse_one_solver_but_drop_prefix_only_constraints(monkeypatch, private, separate_queries):
    import z3
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.core.query import collect_verdicts
    from behave.sim.rtl import source_domains, source_member

    context = z3.Context() if private else z3.main_ctx()
    x = z3.Int('shared_query_input', ctx=context)
    true, false = z3.BoolVal(True, context), z3.BoolVal(False, context)
    goals = ('early', 'late', 'never')
    hits = {'early': x == 0, 'late': x == 2, 'never': x == 3}
    roots = (x >= 0, x <= 2)
    encoded = SimpleNamespace(constraints=roots, environment_assumptions={},
        unfinished=false, domain_faults={}, alive=true, hits=hits,
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=())),
        protocol=SimpleNamespace(hits=()), monitor_hits=(), transaction_hits=())
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.request = SimpleNamespace(constraints=(), drain_slots=(true,), atomic=False,
        settled=False, rows=lambda model: [{'x': model.eval(x).as_long()}])
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object()), goal_names=goals,
        bind_request=lambda *_a, **_k: (None, exact.request, encoded))
    exact.domain, exact._queries = SimpleNamespace(bound=3), GoalQueryCache()
    exact._retain_query_member = lambda *_a, **_k: None
    monkeypatch.setattr(source_domains, 'prove_transition_domains', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_domains, 'prove_transition_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_domains, 'prove_reset_goals', lambda *_a, **_k: frozenset())
    monkeypatch.setattr(source_member, 'source_literal_consequences', lambda *_a, **_k: None)
    monkeypatch.setattr(source_member, 'source_query_prefixes',
        lambda *_a, **_k: iter((((*roots, x < 2), hits, {}),)))

    def member(model, _histories, assertions, **_options):
        assert all(z3.is_true(model.eval(root, model_completion=False)) for root in assertions)
        return model

    monkeypatch.setattr(source_member, 'source_member_hint', member)
    if separate_queries:
        from behave.sim.core import query
        decide = query.iter_decide_setwise
        def individual(goals, pose):
            for goal in goals:
                yield from decide((goal,), pose)
        monkeypatch.setattr(query, 'iter_decide_setwise', individual)
    calls = []

    def solve(phase, assertions, *, preparation):
        assert len(preparation.base) == len(assertions) - 1
        assert all(a.eq(b) for a, b in zip(preparation.base, assertions[:-1], strict=True))
        answer = z3_check_finite(assertions, 1000, _owned_context=context,
            _prefer_finite=False, _preserve_equations=True, _preparation=preparation)
        calls.append((phase, preparation, preparation._solver, answer.answer))
        return answer

    result = collect_verdicts(exact._query_batch_steps(goals, _query_scope(solve)))
    assert set(result.reachable) == {'early', 'late'} and set(result.unreachable) == {'never'}
    assert not result.undecided
    assert [call[0] for call in calls] == ['query-prefix', 'query-prefix', 'query', 'query']
    full = [call for call in calls if call[0] == 'query']
    prefix = [call for call in calls if call[0] == 'query-prefix']
    assert all(call[1] is exact._queries.current.preparation for call in full)
    assert all(call[1] is prefix[0][1] and call[1] is not exact._queries.current.preparation for call in prefix)
    assert full[0][2] is full[1][2]  # Shared by the remaining unrestricted queries.
    assert calls[0][2] is calls[1][2]  # Same base, different residual target.
    assert calls[2][2] is not calls[1][2]  # x < 2 must not survive into the full query.
    assert result.reachable['late'] == ({'x': 2},)
    assert all(call[1]._solver is None for call in prefix)
    assert exact._queries.current.preparation._solver is full[-1][2]


@pytest.mark.parametrize('stop', ('complete', 'solve', 'validate'))
@pytest.mark.parametrize('private', (False, True))
def test_prefix_owner_releases_only_its_preparation(monkeypatch, stop, private):
    import z3
    from behave.sim.rtl import source_member
    from behave.sim.rtl.analysis_query import GoalQueryPrefixes
    from behave.sim.symbolic.exact_solve import SolverPreparation, z3_check_finite

    context = z3.Context() if private else z3.main_ctx()
    value = z3.Int('prefix_owner_input', ctx=context)
    true = z3.BoolVal(True, context)
    roots, hits = (value >= 0, value <= 2), {'goal': value == 1}
    history = SimpleNamespace(alive=true, protocol=SimpleNamespace(hits=()),
                              monitor_hits=(), transaction_hits=())
    request = SimpleNamespace(constraints=roots, drain_slots=(true,), atomic=False, settled=False)
    full = SolverPreparation(roots)
    z3_check_finite((*roots, hits['goal']), 1000, _owned_context=context, _preparation=full)
    retained = full._solver
    model = SimpleNamespace(encoding=history, request=request, constraints=roots,
                            facts=None, domain_faults={}, proved_domains=frozenset())
    monkeypatch.setattr(source_member, 'source_query_prefixes',
        lambda *_a, **_k: iter(((roots, hits, {}),)))
    calls = []

    def solve(_phase, assertions, *, preparation):
        answer = z3_check_finite(assertions, 1000, _owned_context=context, _preparation=preparation)
        calls.append(preparation)
        assert preparation._solver is not None
        if stop == 'solve':
            raise InterruptedError('cancelled during proposal')
        return answer

    def validate(candidate, histories, assertions, *, check):
        assert histories == (history,)
        assert all(z3.is_true(candidate.eval(term)) for term in assertions)
        if stop == 'validate':
            raise InterruptedError('cancelled during completion')
        return candidate

    monkeypatch.setattr(source_member, 'source_member_hint', validate)
    proposals = GoalQueryPrefixes(model, ('goal',), frozenset(),
                                  source_goals=('goal',), check=lambda: None)
    if stop == 'complete':
        member, answers = proposals.propose(('goal',), hits['goal'], solve=solve, check=lambda: None)
        assert member is not None and len(answers) == 1
        assert calls[0]._solver is not None  # Reusable until its owner closes.
        proposals.close()
    else:
        with pytest.raises(InterruptedError):
            proposals.propose(('goal',), hits['goal'], solve=solve, check=lambda: None)
    assert len(calls) == 1 and calls[0]._solver is None
    assert full._solver is retained  # Proposal cleanup cannot retire the full query.
    proposals.close()  # Repeated cleanup is harmless and cannot resurrect a trial.
    assert proposals.propose(('goal',), hits['goal'], solve=solve, check=lambda: None) == (None, [])
    assert len(calls) == 1
    full.clear()


def _membership_product():
    import z3

    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check

    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=None))
    names = ('value', 'initial', 'offer', 'reset', 'length', 'unused')
    terms = {name: z3.Int(name) for name in names}
    rows = [{name: 0 for name in names}]
    calls = []

    def fix(request):
        assert len(request) == 1 and set(request[0]) == set(names)
        assert all(type(value) is int for value in request[0].values())
        return [terms[name] == value for name, value in request[0].items()]

    def solve(phase, assertions):
        calls.append((phase, tuple(assertions)))
        return z3_check(assertions, 1000)

    exact.request = SimpleNamespace(fix=fix, constraints=())
    exact.encoding = SimpleNamespace(monitor_constraints=())
    exact.constraints = (terms['value'] >= 0,)
    exact._request_members = {}
    exact._pending_query_member = None
    return exact, rows, calls, solve, ExactCheckResult


@pytest.mark.parametrize('changed', ('value', 'initial', 'offer', 'reset', 'length', 'unused'))
def test_membership_reuse_binds_every_coordinate_but_no_solver_model(changed):
    exact, rows, calls, solve, _ = _membership_product()
    for request in (rows, [dict(reversed(tuple(rows[0].items())))]):
        assert exact._request_failure(request, solve=solve, check=lambda: None) is None
    assert len(calls) == 1
    assert all(value is None for value in exact._request_members.values())
    changed_rows = [{**rows[0], changed: 1}]
    assert exact._request_failure(changed_rows, solve=solve, check=lambda: None) is None
    assert len(calls) == 2
    assert all(phase == 'fixed-request' for phase, _ in calls)
    other, _, fresh_calls, fresh_solve, _ = _membership_product()
    assert other._request_failure(rows, solve=fresh_solve, check=lambda: None) is None
    assert len(fresh_calls) == 1
    with pytest.raises(AssertionError):
        exact._request_failure([{**rows[0], 'value': False}], solve=solve, check=lambda: None)


@pytest.mark.parametrize('outcome', ('unsat', 'unknown'))
def test_membership_failure_is_not_cached(outcome):
    from behave.sim.core.query import SolverAnswer

    exact, rows, calls, solve, result = _membership_product()
    failure = result(SolverAnswer(outcome), reason='budget' if outcome == 'unknown' else '')
    assert exact._request_failure(rows, solve=lambda *_: failure, check=lambda: None) is failure
    assert not exact._request_members
    assert exact._request_failure(rows, solve=solve, check=lambda: None) is None
    assert len(calls) == 1


@pytest.mark.parametrize('after_solve', (False, True))
def test_membership_cancellation_applies_to_cached_and_new_proofs(after_solve):
    exact, rows, calls, solve, _ = _membership_product()
    if not after_solve:
        exact._request_failure(rows, solve=solve, check=lambda: None)
    solved = False

    def cancelled_solve(phase, assertions):
        nonlocal solved
        answer = solve(phase, assertions)
        solved = True
        return answer

    def check():
        if solved or not after_solve:
            raise TimeoutError('cancelled')

    with pytest.raises(TimeoutError, match='cancelled'):
        exact._request_failure(rows, solve=cancelled_solve, check=check)
    assert len(calls) == 1
    assert bool(exact._request_members) is (not after_solve)


def test_membership_storage_is_bounded_and_eviction_only_repeats_the_proof(monkeypatch):
    exact, rows, calls, solve, _ = _membership_product()
    cost = len(analysis_job._membership_key(rows, lambda: None)) + 128
    monkeypatch.setattr(analysis_job, '_MEMBERSHIP_CACHE_BYTES', 2 * cost)
    for value in range(3):
        exact._request_failure([{**rows[0], 'value': value}], solve=solve, check=lambda: None)
    assert len(exact._request_members) == 2
    exact._request_failure(rows, solve=solve, check=lambda: None)
    assert len(calls) == 4
    exact._request_members.clear()
    monkeypatch.setattr(analysis_job, '_MEMBERSHIP_CACHE_KEY_BYTES', 1)
    for _ in range(2):
        assert exact._request_failure(rows, solve=solve, check=lambda: None) is None
    assert len(calls) == 6 and not exact._request_members


def test_membership_pool_avoids_entry_count_thrash_within_the_same_byte_budget():
    exact, rows, calls, solve, _ = _membership_product()
    pool = [[{**rows[0], 'value': value}] for value in range(50)]
    for request in pool:
        assert exact._request_failure(request, solve=solve, check=lambda: None) is None
    assert len(calls) == 50
    for request in pool:
        assert exact._request_failure(request, solve=solve, check=lambda: None) is None
    assert len(calls) == 50
    assert sum(len(key) + 128 for key in exact._request_members) <= analysis_job._MEMBERSHIP_CACHE_BYTES


def test_remembering_an_existing_member_does_not_evict_another_member(monkeypatch):
    exact, rows, calls, solve, _ = _membership_product()
    cost = len(analysis_job._membership_key(rows, lambda: None)) + 128
    monkeypatch.setattr(analysis_job, '_MEMBERSHIP_CACHE_BYTES', 2 * cost)
    for value in (0, 1, 0):
        request = [{**rows[0], 'value': value}]
        assert exact._request_failure(request, solve=solve, check=lambda: None) is None
        exact._remember_member(analysis_job._membership_key(request, lambda: None))
    assert len(calls) == 2 and len(exact._request_members) == 2


def test_long_request_membership_key_keeps_all_repeated_coordinates(monkeypatch):
    from zlib import decompress

    # Public 100-input histories repeat the complete shared queue in each row.
    rows = [{**{f'queue.in.{i}.value': i for i in range(100)},
             'initial.reg': 0, 'action': slot & 1, 'reset': int(slot == 0)}
            for slot in range(103)]
    raw = canonical_json(rows).encode('utf-8')
    assert len(raw) > analysis_job._MEMBERSHIP_CACHE_KEY_BYTES
    key = analysis_job._membership_key(rows, lambda: None)
    assert key[:1] == b'Z' and decompress(key[1:]) == raw
    assert len(key) <= analysis_job._MEMBERSHIP_CACHE_KEY_BYTES
    assert analysis_job._membership_key([dict(reversed(tuple(r.items()))) for r in rows], lambda: None) == key
    for field in ('initial.reg', 'action', 'reset', 'queue.in.99.value'):
        changed = [dict(row) for row in rows]
        changed[-1][field] += 1
        assert analysis_job._membership_key(changed, lambda: None) != key
    assert analysis_job._membership_key(rows[:-1], lambda: None) != key
    exact, _, _, _, _ = _membership_product()
    validated = []
    def fix(request):
        validated.append(True)
        assert request == rows
        return ()
    exact.request.fix = fix
    exact._remember_member(key)  # Stand in for the completed proof, not a model.
    assert exact._request_failure(rows, solve=lambda *_: pytest.fail('duplicate long-request proof'),
                                  check=lambda: None) is None
    assert validated == [True] and tuple(exact._request_members) == (key,)


def test_wide_queue_membership_uses_lossless_deltas_beyond_compressor_window():
    import random
    from zlib import compress, decompress

    from behave.sim.core.audit import encode_valuation_trace

    rng = random.Random(11)
    queue = {f'queue.data.{i:04}.payload': rng.getrandbits(128) for i in range(1000)}
    rows = [{**queue, 'offer': bool(i % 2), 'ready': bool(i % 3)} for i in range(32)]
    raw = canonical_json(rows).encode('utf-8')
    # Repetition farther apart than zlib's window does not fit the old cache.
    assert len(compress(raw, 1)) > analysis_job._MEMBERSHIP_CACHE_KEY_BYTES
    key = analysis_job._membership_key(rows, lambda: None)
    assert key[:1] == b'D'
    assert json.loads(decompress(key[1:])) == encode_valuation_trace(rows, 'request')
    assert len(key) <= analysis_job._MEMBERSHIP_CACHE_KEY_BYTES
    exact, _, _, _, _ = _membership_product()
    exact.request.fix = lambda request: () if request == rows else pytest.fail('changed request')
    exact._remember_member(key)
    assert exact._request_failure(rows, solve=lambda *_: pytest.fail('duplicate member proof'),
                                  check=lambda: None) is None
    assert analysis_job._membership_key([dict(reversed(tuple(r.items()))) for r in rows],
                                        lambda: None) == key
    for field in ('offer', 'ready', 'queue.data.0999.payload'):
        changed = [dict(row) for row in rows]
        changed[-1][field] = 1 if type(changed[-1][field]) is bool else changed[-1][field] + 1
        assert analysis_job._membership_key(changed, lambda: None) != key
    assert analysis_job._membership_key(rows[:-1], lambda: None) != key


def test_delta_membership_key_preserves_types_shapes_and_cancellation(monkeypatch):
    from copy import deepcopy

    monkeypatch.setattr(analysis_job, '_MEMBERSHIP_CACHE_KEY_BYTES', 1)
    rows = [{'queue': [1, 0.0], 'ready': True, 'initial': {'a': 0}} for _ in range(12)]
    key = analysis_job._membership_key(rows, lambda: None)
    for field, value in (('queue', [True, 0.0]), ('queue', [1, -0.0]),
                         ('queue', [1]), ('ready', 1), ('initial', {'b': 0})):
        changed = deepcopy(rows)
        changed[-1][field] = value
        assert analysis_job._membership_key(changed, lambda: None) != key
    count = 0
    def check():
        nonlocal count
        count += 1
        if count == 7:
            raise TimeoutError('cancel delta encoding')
    with pytest.raises(TimeoutError, match='cancel delta encoding'):
        analysis_job._membership_key(rows, check)


@pytest.mark.parametrize('cancel_at', (1, 2))
def test_request_key_checks_cancellation_before_and_after_packing(cancel_at):
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == cancel_at:
            raise TimeoutError('cancel key construction')
    with pytest.raises(TimeoutError, match='cancel key construction'):
        analysis_job._membership_key([{'value': 0}] * 103, check)


@pytest.mark.parametrize('violation', (False, True))
def test_fixed_request_monitor_hint_checks_all_roots_before_caching(violation):
    import z3

    from behave.sim.core.query import SolverAnswer

    exact, rows, calls, solve, _ = _membership_product()
    value, successor = z3.Ints('value successor')
    definition = successor == value + 1
    exact.encoding = SimpleNamespace(monitor_constraints=(definition,), monitor_frame_sizes=(1,))
    exact.constraints += (definition, successor <= (0 if violation else 1))
    result = exact._request_failure(rows, solve=solve, check=lambda: None)
    if violation:
        # The source-only candidate can pick successor=0. Its asserted
        # definition actually gives 1; validation must reject that candidate.
        assert result.answer is SolverAnswer.UNSAT
        assert not exact._request_members
        assert [phase for phase, _ in calls] == ['fixed-request-source', 'fixed-request']
    else:
        assert result is None and exact._request_members
        assert [phase for phase, _ in calls] == ['fixed-request-source']
        assert exact._request_failure(rows, solve=solve, check=lambda: None) is None
        assert len(calls) == 1


def _answer(reply, operation):
    value = analysis_job.decode_analysis_reply(reply, operation)
    if operation == "feasible":
        return value["answer"]
    from behave.sim.core.instance import Verdict

    verdict = Verdict.from_record(value)
    return set(verdict.reachable), set(verdict.unreachable), set(verdict.undecided)


@pytest.mark.parametrize('operation,payload', [('query-next', {}), ('query-close', {}),
    ('query-start', {'goals': ['g'], 'timeout_ms': None})])
def test_cursor_requires_an_owned_session(operation, payload):
    with pytest.raises(ValueError, match='bound analysis session'):
        analysis_job.run_analysis_job({'operation': operation, 'horizon': 3, **payload})


@pytest.mark.parametrize('audit', ['missing', 'domain', 'inputs', 'valid', 'discard'])
def test_cursor_proposal_resumes_only_with_its_own_matching_complete_audit(monkeypatch, audit):
    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import Verdict
    from behave.sim.rtl.analysis_query import NativeQueryProposal

    rows, received, closed = [{'x': 1}], [], []
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.domain = TraceDomain(1)

    def cursor():
        try:
            witness = yield NativeQueryProposal(rows)
            received.append(witness)
            yield Verdict({'g': rows}, (), {}, queries=())
        finally:
            closed.append(True)

    exact.call = lambda *args, **kw: cursor()
    exact.release_witness = lambda: None
    exact.close_query = lambda: None
    prepared = object.__new__(analysis_job.PreparedAnalysis)
    prepared.exact, prepared.task, prepared._query_cursor = exact, None, None
    prepared._query_proposal = prepared._query_witness = None
    value = analysis_job.decode_analysis_reply(prepared.call('query-start',
        {'goals': ['g'], 'timeout_ms': 1000}), 'query-start')
    assert value == {'native_proposal': rows} and not received
    witness = AuditedWitness(domain='other' if audit == 'domain' else exact.domain.digest(),
        inputs=[{'x': 2}] if audit == 'inputs' else rows,
        states=[{}, {}], outputs=[()], hit_sets=[('g',)])
    monkeypatch.setattr(analysis_job, '_run_bound_operation',
        lambda op, *args, **kw: analysis_job._reply(op, witness.as_record()))
    if audit not in {'missing', 'discard'}:
        prepared.call('audit-request', {'rows': rows, 'trace': {}, 'domain': exact.domain.digest()})
    if audit in {'valid', 'discard'}:
        operation = 'query-discard' if audit == 'discard' else 'query-next'
        result = analysis_job.decode_analysis_reply(prepared.call(operation, {}), operation)
        assert set(Verdict.from_record(result).reachable) == {'g'}
        assert received == [None if audit == 'discard' else witness]
        assert analysis_job.decode_analysis_reply(prepared.call('query-next', {}), 'query-next') is None
    else:
        with pytest.raises(ValueError, match='must be audited'):
            prepared.call('query-next', {})
        assert not received
    assert closed == [True] and prepared._query_cursor is None
    assert prepared._query_proposal is prepared._query_witness is None


@pytest.mark.parametrize('status', ['refused', 'inconsistent'])
def test_proposal_audit_keeps_typed_adverse_reply(monkeypatch, status):
    from behave.sim.rtl.analysis_query import NativeQueryProposal

    prepared = object.__new__(analysis_job.PreparedAnalysis)
    prepared.exact, prepared.task = object(), None
    prepared._query_proposal, prepared._query_witness = NativeQueryProposal([{'x': 1}]), None
    field = 'refusal' if status == 'refused' else 'inconsistency'
    reply = {'version': analysis_job.VERSION, 'operation': 'audit-request', 'status': status,
             field: {'kind': 'bad-native', 'why': 'no valid native witness'}}
    monkeypatch.setattr(analysis_job, '_run_bound_operation', lambda *a, **k: reply)
    assert prepared.call('audit-request', {'rows': [], 'trace': {}, 'domain': 'x'}) is reply
    assert prepared._query_witness is None
    if status == 'inconsistent':
        with pytest.raises(WitnessInconsistent):
            analysis_job.decode_analysis_reply(reply, 'audit-request')


@pytest.mark.parametrize('entry', ['binding', 'bound', 'cursor'])
@pytest.mark.parametrize('failure', ['refused', 'inconsistent', 'limit', 'unexpected'])
def test_worker_failure_representation_is_shared_without_hiding_errors(monkeypatch, entry, failure):
    error = {
        'refused': Refused(Refusal('unavailable', 'no completed evidence')),
        'inconsistent': WitnessInconsistent('disagreement', 'completed observations differ'),
        'limit': AuditedWitnessLimitError(17),
        'unexpected': RuntimeError('implementation failure'),
    }[failure]
    exact = object.__new__(analysis_job._SourceAnalysis)
    closed, annotations = [], []
    def fail(*args, **kwargs):
        raise error
    def annotate(refusal, operation, owner, *args, **kwargs):
        annotations.append((operation, owner))
        return refusal
    monkeypatch.setattr(analysis_job, 'annotate_refusal', annotate)
    monkeypatch.setattr(analysis_job._SourceAnalysis, 'call', fail)
    monkeypatch.setattr(analysis_job._SourceAnalysis, 'close_query', lambda self: closed.append(self))
    payload = {'goals': ['g'], 'timeout_ms': 1000}
    operation = 'query-start' if entry == 'cursor' else 'query'
    if entry == 'binding':
        monkeypatch.setattr(analysis_job, '_bound', fail)
        run = lambda: analysis_job.run_analysis_job({'operation': operation, 'horizon': 3, **payload})
    elif entry == 'bound':
        run = lambda: analysis_job._run_bound_operation(operation, payload, exact, None)
    else:
        session = object.__new__(analysis_job.PreparedAnalysis)
        session.exact, session.task, session._query_cursor = exact, None, None
        run = lambda: session.call(operation, payload)
    if failure == 'unexpected' or (entry == 'binding' and failure == 'limit'):
        # Preserve the existing binding boundary; do not turn arbitrary
        # exceptions into "unavailable" or hide implementation failures.
        with pytest.raises(type(error)) as caught:
            run()
        assert caught.value is error
    else:
        reply = run()
        assert reply['operation'] == operation and reply['version'] == analysis_job.VERSION
        if failure == 'inconsistent':
            assert reply['status'] == 'inconsistent'
            assert reply['inconsistency'] == error.as_dict()
        else:
            assert reply['status'] == 'refused'
            expected = (error.refusal if failure == 'refused' else Refusal(
                'analysis-audited-witness-evidence-too-large', str(error), node_limit=17))
            assert reply['refusal'] == expected.as_dict()
    assert annotations == ([(operation, None if entry == 'binding' else exact)]
                           if failure == 'refused' else [])
    assert closed == ([exact] if entry == 'cursor' else [])


def _transport(monkeypatch, binding):
    process = SimpleNamespace(stdin=io.BytesIO(), stderr=io.BytesIO(), closed=0)
    process.stdout = io.BytesIO(json.dumps({
        "version": child.VERSION, "id": 0, "ok": True, "wall_ms": 0,
        "result": analysis_job._reply("bind", binding),
    }).encode() + b"\n")
    process.close = lambda: setattr(process, "closed", process.closed + 1)
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda n, p: (p, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *a, **k: None)
    monkeypatch.setattr(job_session, "start", lambda spec: process)
    return process


def test_parent_rechecks_the_entire_binding_and_closes_on_mismatch(monkeypatch):
    process = _transport(monkeypatch, {"domain": "other"})
    with analysis_scope(60), pytest.raises(IsolatedJobFailure, match="different binding"):
        analysis_transport.RTLAnalysisSession({}, canonical_json({"domain": "first"}),
                                              timeout_s=1)
    assert process.closed == 1


def _worker_messages(messages):
    reader = io.BytesIO(b"".join(json.dumps({"version": child.VERSION, "id": i,
                                           "op": op, "payload": payload}).encode() + b"\n"
                                for i, (op, payload) in enumerate(messages)))
    writer = io.BytesIO()
    child.serve(reader, writer)
    return [json.loads(line) for line in writer.getvalue().splitlines()]


@pytest.mark.parametrize("deadline", [None, True, "120", float("inf"), float("nan")])
def test_worker_requires_a_finite_parent_deadline(monkeypatch, deadline):
    monkeypatch.setattr(child, "PreparedAnalysis", lambda _: pytest.fail("constructed invalid frame"))
    replies = _worker_messages([("open", {"context": {}, "deadline": deadline})])
    assert len(replies) == 1 and replies[0]["ok"] is False


def test_worker_deadline_covers_construction_and_later_operations_without_reset(monkeypatch):
    from behave.isolation.preparation_control import check_preparation
    from behave.sim.analysis_budget import active_analysis, remaining_query_ms

    now, observed = [100.0], []
    monkeypatch.setattr(child.time, "monotonic", lambda: now[0])

    class Prepared:
        def __init__(self, context):
            assert context == {"horizon": 3}
            assert active_analysis().deadline == 110
            observed.append(remaining_query_ms(None))
            now[0] = 104
            check_preparation()

        def binding(self):
            observed.append(remaining_query_ms(None))
            return {}

        def call(self, operation, payload):
            assert operation == "query"
            observed.append(remaining_query_ms(payload["timeout_ms"]))
            now[0] = 110
            try:
                check_preparation()
            except BaseException as exc:
                exc.add_note("RTL exact Analysis proof phase: confluence")
                raise

    monkeypatch.setattr(child, "PreparedAnalysis", Prepared)
    replies = _worker_messages([
        ("open", {"context": {"horizon": 3}, "deadline": 110}),
        ("query", {"timeout_ms": 2000}),
        ("query", {"timeout_ms": 9000}),
    ])
    assert observed == [10_000, 6000, 2000]
    assert len(replies) == 2 and replies[0]["ok"] is True
    refusal = replies[1]["refusal"]
    assert refusal["kind"] == "analysis-budget-exhausted"
    assert refusal["detail"]["operation"] == "query"
    assert "confluence" in refusal["detail"]["notes"][0]
    assert active_analysis() is None


def test_expired_worker_deadline_never_constructs_a_source(monkeypatch):
    monkeypatch.setattr(child.time, "monotonic", lambda: 100)
    monkeypatch.setattr(child, "PreparedAnalysis", lambda _: pytest.fail("constructed after deadline"))
    replies = _worker_messages([("open", {"context": {}, "deadline": 100})])
    assert replies[0]["refusal"]["kind"] == "analysis-budget-exhausted"


def test_worker_cannot_publish_a_binding_after_construction_uses_the_budget(monkeypatch):
    from behave.sim.analysis_budget import active_analysis

    now = [100.0]
    monkeypatch.setattr(child.time, "monotonic", lambda: now[0])

    class Prepared:
        def __init__(self, context):
            now[0] = 110

        def binding(self):
            pytest.fail("binding published after construction deadline")

    monkeypatch.setattr(child, "PreparedAnalysis", Prepared)
    replies = _worker_messages([("open", {"context": {}, "deadline": 110})])
    assert replies[0]["refusal"]["kind"] == "analysis-budget-exhausted"
    assert active_analysis() is None


@pytest.mark.parametrize("expires", [False, True])
@pytest.mark.parametrize("abstract", [False, True])
def test_source_binding_solver_uses_remaining_analysis_budget(monkeypatch, expires, abstract):
    import z3

    from behave.sim.symbolic import exact_solve
    from behave.sim.analysis_budget import AnalysisStopped

    now, limits = [100.0], []
    monkeypatch.setattr(child.time, "monotonic", lambda: now[0])
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.encoding = SimpleNamespace(alive=z3.BoolVal(True))
    exact.source = SimpleNamespace(template=SimpleNamespace(context=z3.main_ctx()))
    exact._prefer_finite = True

    def checked(assertions, timeout_ms, **kwargs):
        limits.append(timeout_ms)
        assert kwargs['_prefer_finite'] is not abstract
        assert not kwargs['_preserve_equations']
        if expires:
            now[0] = 110
        return exact_solve.ExactCheckResult(exact_solve.SolverAnswer.UNSAT)

    def admit(self, solve, check, *, prove, proof_scope):
        assert callable(proof_scope)
        now[0] = 107
        (prove if abstract else solve)("confluence", (z3.BoolVal(False),))
        raise Refused(Refusal("diagnostic-stopped", "stop before binding assembly"))

    monkeypatch.setattr(exact_solve, "z3_check_finite", checked)
    monkeypatch.setattr(analysis_job._SourceAnalysis, "_admit_domain", admit)
    with analysis_scope(10), pytest.raises(AnalysisStopped if expires else Refused) as error:
        exact.call("bind", {}, started_ns=time.monotonic_ns())
    assert limits == [3000]
    if expires:
        assert "proof phase: confluence" in error.value.__notes__[0]


@pytest.mark.parametrize('operation_ms, allowance, expected', [
    (None, 1000, 1000), (None, 10_000, 3000), (None, 0, None), (1000, 1000, None)])
def test_optional_solver_allowance_never_extends_parent_or_uses_zero_timeout(
        monkeypatch, operation_ms, allowance, expected):
    import z3
    from behave.sim.symbolic import exact_solve

    now, limits = [100.0], []
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.encoding = SimpleNamespace(alive=z3.BoolVal(True))
    exact.source = SimpleNamespace(template=SimpleNamespace(context=z3.main_ctx()))
    exact._prefer_finite = False

    def checked(assertions, timeout_ms, **kwargs):
        limits.append(timeout_ms)
        assert timeout_ms > 0
        assert kwargs['_preserve_equations']
        return exact_solve.ExactCheckResult(exact_solve.SolverAnswer.UNSAT)

    def admit(self, solve, check, **kwargs):
        now[0] = 107
        answer = solve('query-prefix', (z3.BoolVal(False),), allowance_ms=allowance)
        assert answer.answer is (exact_solve.SolverAnswer.UNKNOWN if expected is None
                                 else exact_solve.SolverAnswer.UNSAT)
        raise Refused(Refusal('diagnostic-stopped', 'stop before binding assembly'))

    monkeypatch.setattr(exact_solve, 'z3_check_finite', checked)
    monkeypatch.setattr(analysis_job._SourceAnalysis, '_admit_domain', admit)
    with analysis_scope(10), pytest.raises(Refused, match='stop before binding assembly'):
        exact.call('bind', {'timeout_ms': operation_ms}, started_ns=time.monotonic_ns())
    assert limits == ([] if expected is None else [expected])


@pytest.mark.parametrize('owner', ['source', 'query'])
def test_unknown_nonmember_query_retires_preparation_in_the_same_context(monkeypatch, owner):
    import z3
    from behave.sim.symbolic import exact_solve

    ctx = z3.Context()
    x = z3.BitVec('request', 8, ctx=ctx)
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.encoding = SimpleNamespace(alive=z3.BoolVal(True, ctx))
    exact.source = SimpleNamespace(template=SimpleNamespace(context=ctx))
    exact._prefer_finite = True
    prepared = exact_solve.SolverPreparation((x == 3,))
    exact._source_solver = prepared if owner == 'source' else None
    exact._queries = GoalQueryCache()
    exact._queries.current = SimpleNamespace(preparation=prepared) if owner == 'query' else None
    assert exact_solve.z3_check_finite(prepared.base, 1000, _owned_context=ctx,
        _preparation=prepared).answer is exact_solve.SolverAnswer.SAT

    def interrupted(*args, **kwargs):
        assert '_preparation' not in kwargs  # This is a different proof phase.
        ctx.interrupt()
        return exact_solve.ExactCheckResult(exact_solve.SolverAnswer.UNKNOWN, reason='timeout')

    def admit(self, solve, check, **kwargs):
        solve('native-certificate', (z3.BoolVal(False, ctx),))
        raise Refused(Refusal('diagnostic-stopped', 'stop before binding assembly'))

    with monkeypatch.context() as patch:
        patch.setattr(exact_solve, 'z3_check_finite', interrupted)
        patch.setattr(analysis_job._SourceAnalysis, '_admit_domain', admit)
        with pytest.raises(Refused, match='stop before binding assembly'):
            exact.call('bind', {}, started_ns=time.monotonic_ns())
    assert prepared._solver is None and prepared._interrupted
    answer = exact_solve.z3_check_finite(prepared.base, 1000, _owned_context=ctx,
        _preparation=prepared)
    assert answer.answer is exact_solve.SolverAnswer.SAT


def test_source_operation_keeps_cursor_budget_and_provenance_across_an_audit(monkeypatch):
    now, admitted = [100.0], []
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(time, 'monotonic_ns', lambda: int(now[0] * 1_000_000_000))
    exact = object.__new__(analysis_job._SourceAnalysis)
    exact.source = SimpleNamespace(complete=('g',))
    exact.domain = SimpleNamespace(bound=1)
    exact.request = SimpleNamespace(fix=lambda rows: None)
    exact.release_witness = lambda: None
    exact._use_admitted_history = lambda check: check()
    exact._request_failure = lambda *args, **kwargs: None

    def admit(solve, check, **kwargs):
        check()
        admitted.append(solve.__self__)
        return {'call': len(admitted)}

    def steps(goals, scope, *, native_proposals):
        assert goals == ['g']
        assert native_proposals
        for _ in range(3):
            scope.check()
            yield scope

    exact._admit_domain, exact._query_steps = admit, steps
    with analysis_scope(60):
        parts = exact.call('query-start', {'goals': ['g'], 'timeout_ms': 10_000},
                           started_ns=time.monotonic_ns() - 2_000_000_000)
        query = next(parts)
        assert query.timeout == query.budget.remaining_ms() == 8000
        now[0] = 104
        assert exact.call('step-audit', {'traces': [[{}]]},
                          started_ns=time.monotonic_ns()) == {'status': 'agreed'}
        assert len(admitted) == 2 and admitted[0] is query
        assert admitted[1] is not query and admitted[1].exact is query.exact
        assert admitted[1].provenance(None)['source_domain_admission'] == {'call': 2}
        assert next(parts) is query and query.budget.remaining_ms() == 4000
        assert query.provenance(query.timeout)['source_domain_admission'] == {'call': 1}
        now[0] = 108
        with pytest.raises(Refused, match='operation budget'):
            next(parts)


@pytest.mark.parametrize('outcome', ['sat', 'unsat', 'unknown'])
def test_source_operation_drops_late_answers_and_preserves_measured_cost(monkeypatch, outcome):
    import z3
    from behave.sim.symbolic import exact_solve

    now, retired = [100.0], []
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(time, 'monotonic_ns', lambda: int(now[0] * 1_000_000_000))
    exact = SimpleNamespace(_prefer_finite=False, _source_solver=None,
        source=SimpleNamespace(template=SimpleNamespace(context=z3.main_ctx())),
        _queries=SimpleNamespace(clear_solver=lambda **kw: retired.append('shared')))
    preparation = SimpleNamespace(clear=lambda: retired.append('selected'))
    scope = analysis_job._SourceOperation(exact, 1000, time.monotonic_ns())

    def solve(assertions, timeout_ms, **kwargs):
        assert 0 < timeout_ms <= 1000
        assert kwargs['_preparation'] is preparation
        now[0] = 102
        return exact_solve.ExactCheckResult(exact_solve.SolverAnswer(outcome),
            model=z3.Model() if outcome == 'sat' else None,
            reason='test interruption' if outcome == 'unknown' else '', solver_load_ns=7, solve_ns=11)

    monkeypatch.setattr(exact_solve, 'z3_check_finite', solve)
    with analysis_scope(60):
        answer = scope.solve('query', (z3.BoolVal(True),), preparation=preparation)
    assert answer.answer is exact_solve.SolverAnswer.UNKNOWN and answer.model is None
    assert (answer.solver_load_ns, answer.solve_ns) == (7, 11)
    assert retired == (['shared', 'selected'] if outcome == 'unknown' else ['selected'])


def test_parent_sends_one_original_deadline_after_source_freezing(monkeypatch):
    process = _transport(monkeypatch, {})
    with analysis_scope(60) as budget:
        session = analysis_transport.RTLAnalysisSession({"horizon": 3}, timeout_s=30)
        frame = json.loads(process.stdin.getvalue())
        assert frame["payload"] == {"context": {"horizon": 3}, "deadline": budget.deadline}
        assert frame["version"] == "rtl-analysis-session/2"
        session.close()


def test_session_uses_remaining_analysis_wall_without_exchange_quota(monkeypatch):
    from behave.sim import analysis_budget

    now = [100.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    process = _transport(monkeypatch, {})
    limits = []
    monkeypatch.setattr(job_session, "_job_spec",
                        lambda *args, **kwargs: limits.append(kwargs))
    with analysis_budget.analysis_scope(20):
        now[0] = 115.0
        session = analysis_transport.RTLAnalysisSession(
            {}, canonical_json({}), timeout_s=300)
        assert limits[0]["timeout_s"] == 5
        assert session._exchanges is None
        session.close()
    assert process.closed == 1


def _proxy():
    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._session = None
    proxy._session_active = False
    proxy._session_attempted = False
    proxy._session_owner = None
    proxy._binding = canonical_json({})
    proxy._domain = SimpleNamespace(bound=3, schema={})
    proxy._context = SimpleNamespace(
        analysis_payload=dict, artifact=SimpleNamespace(timeout_s=1),
        unavailable=lambda op, exc: Refusal("test-transport-failed", str(exc)))
    return proxy


def test_streaming_private_proposal_is_audited_once_before_any_public_verdict(monkeypatch):
    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import Verdict
    from behave.sim.core.query import QueryCall, SolverAnswer

    proxy = _proxy()
    proxy._domain = TraceDomain(1, schema={'source_query_semantics': 'native-cover/1'})
    proxy._session = object()
    rows, operations = [{'x': 1}], []
    witness = AuditedWitness(domain=proxy._domain.digest(), inputs=rows,
        states=[{}, {}], outputs=[()], hit_sets=[('g',)])
    # An old parent cache entry must not stand in for this worker's audit.
    proxy._pending_native = canonical_json(rows), witness
    verdict = Verdict({'g': rows}, (), {}, queries=(
        QueryCall(('g',), SolverAnswer.SAT, witness=rows),))
    replies = iter((verdict.as_record(), None))

    def raw(self, operation, **payload):
        operations.append(operation)
        if operation == 'query-start':
            return {'native_proposal': rows}
        if operation == 'audit-request':
            assert payload['rows'] == rows
            return witness.as_record()
        assert operation == 'query-next'
        assert operations.index('audit-request') < len(operations) - 1
        return next(replies)

    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', raw)
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_source_payload',
        lambda *args: (None, SimpleNamespace(as_dict=dict)))
    results = list(proxy.query_steps(('g',), 1))
    assert len(results) == 1 and set(results[0].reachable) == {'g'}
    assert operations == ['query-start', 'audit-request', 'query-next', 'query-next']
    assert proxy.audit_request(rows, proxy._domain.digest()) == witness
    assert len(operations) == 4  # Existing closure consumes the checked witness.


@pytest.mark.parametrize('kind', ['environment', 'monitor-refused', 'framing', 'interrupted', 'transport', 'inconsistent'])
def test_streaming_discard_is_only_for_explicit_input_domain_failure(monkeypatch, kind):
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import Verdict

    proxy = _proxy()
    proxy._domain = TraceDomain(1, schema={'source_query_semantics': 'native-cover/1'})
    proxy._session = object()
    rows, operations = [{'x': 1}], []
    refusal = (Refusal('rtl-source-witness-environment-invalid', 'input check',
        audit={'disposition': 'outside' if kind == 'environment' else 'refused'})
        if kind in {'environment', 'monitor-refused'}
        else Refusal('rtl-native-run-interrupted', 'stopped',
            driver_refusal={'kind': 'input-framing-violated', 'why': 'illegal frame'})
        if kind == 'framing' else Refusal('rtl-native-run-interrupted' if kind == 'interrupted'
                                        else 'unavailable', 'no completed observation'))

    def raw(self, operation, **payload):
        operations.append(operation)
        if operation == 'query-start':
            return {'native_proposal': rows}
        if operation == 'query-discard':
            return Verdict({}, ('g',), {}, queries=()).as_record()
        assert operation == 'query-next'
        return None

    def audit(self, *_args):
        if kind == 'inconsistent':
            raise WitnessInconsistent('bad-native', 'observations disagree')
        raise Refused(refusal)

    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', raw)
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_proposal_witness', audit)
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_close_session', lambda self: None)
    if kind in {'environment', 'framing'}:
        results = list(proxy.query_steps(('g',), 1))
        assert len(results) == 1 and results[0].unreachable == frozenset({'g'})
        assert not results[0].reachable
        assert operations == ['query-start', 'query-discard', 'query-next']
    else:
        with pytest.raises(WitnessInconsistent if kind == 'inconsistent' else Refused):
            list(proxy.query_steps(('g',), 1))
        assert operations == ['query-start']


@pytest.mark.parametrize('hits', [('a', 'b'), ('a',), (), ('outside',)])
def test_source_cover_proposals_need_native_hits_before_public_sat(monkeypatch, hits):
    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import Verdict
    from behave.sim.core.query import QueryCall, SolverAnswer
    from behave.sim.core.query_telemetry import QueryTelemetryScope, StageTimings

    proxy = _proxy()
    proxy._domain = TraceDomain(1, schema={'source_query_semantics': 'native-cover/1'})
    rows = [{'x': 0}]
    witness = AuditedWitness(domain=proxy._domain.digest(), inputs=rows,
        states=({}, {}), outputs=((),), hit_sets=(hits,))
    audited = []
    audit_request = RTLIsolatedAnalysisInstance.audit_request

    def audit(self, inputs, domain):
        assert domain == proxy._domain.digest() and inputs == rows
        audited.append(inputs)
        return witness

    monkeypatch.setattr(RTLIsolatedAnalysisInstance, 'audit_request', audit)
    telemetry = QueryTelemetryScope(backend='rtl-z3', semantic_instance='a'*64, horizon=1).fragment(
        ('a', 'b'), timings=StageTimings({'solve_ns': 1})).finalize(
            outcome='sat', goals_settled=('a', 'b'))
    proposal = Verdict({'a': rows, 'b': rows}, (), {}, queries=(
        QueryCall(('a', 'b'), SolverAnswer.SAT, witness=rows, solver_calls=3, telemetry=telemetry),))
    result = proxy._confirm_proposals(proposal)
    assert set(result.reachable) == set(hits) & {'a', 'b'}
    assert set(result.undecided) == {'a', 'b'} - set(hits)
    assert not result.unreachable
    assert sum(call.solver_calls for call in result.queries) == 3
    assert audited == [rows]
    for goal in result.undecided:
        assert any(call.goals == (goal,) and call.answer is SolverAnswer.UNKNOWN for call in result.queries)
    assert all(set(call.goals) & set(hits) for call in result.queries if call.answer is SolverAnswer.SAT)
    assert QueryCall.from_dict(result.queries[0].as_dict()).answer is result.queries[0].answer
    # Hand off this already checked witness once; do not execute the audit's
    # native run again. The separate closure replay is unchanged.
    assert audit_request(proxy, rows, proxy._domain.digest()) is witness
    assert proxy._pending_native is None


@pytest.mark.parametrize('inconsistent', [False, True])
def test_source_cover_does_not_hide_native_evidence_failures(monkeypatch, inconsistent):
    from behave.sim.core.audit import AuditCheck
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import Verdict
    from behave.sim.core.query import QueryCall, SolverAnswer

    proxy = _proxy()
    proxy._domain = TraceDomain(1, schema={'source_query_semantics': 'native-cover/1'})
    finding = (AuditCheck.inconsistent('wrong source history') if inconsistent else
               AuditCheck.refused('unavailable', 'native observation unavailable',
                                  failure={'origin': 'candidate', 'event': 'assertion'}))
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, 'audit_request', lambda *_: finding)
    verdict = Verdict({'a': [{}]}, (), {}, queries=(QueryCall(('a',), SolverAnswer.SAT, witness=[{}]),))
    with pytest.raises(WitnessInconsistent if inconsistent else Refused) as raised:
        proxy._confirm_proposals(verdict)
    if not inconsistent:
        assert raised.value.refusal is finding.refusal


def test_audit_wire_preserves_details_and_rejects_unknown_shapes():
    from behave.sim.core.audit import AuditCheck
    from behave.sim.rtl.analysis_job import decode_audit_check

    check = AuditCheck.refused('native-failed', 'source stopped',
                              failure={'origin': 'candidate', 'hits': ['assertion']})
    assert decode_audit_check(check.as_dict()).as_dict() == check.as_dict()
    for bad in ([], 'lost', None):
        image = check.as_dict()
        image['refusal']['detail'] = bad
        with pytest.raises((ValueError, TypeError)):
            decode_audit_check(image)
    image = check.as_dict()
    image['refusal']['extra'] = 'not canonical'
    with pytest.raises(ValueError):
        decode_audit_check(image)


@pytest.mark.parametrize('origin', ['candidate', 'verifier', 'infrastructure'])
def test_native_failure_keeps_replay_request_and_attribution(monkeypatch, origin):
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.stimulus import EventSlot, Stimulus
    from behave.sim.rtl import initial_state

    proxy = _proxy()
    proxy._domain = TraceDomain(1, schema={'source_query_semantics': 'native-cover/1'})
    stimulus = Stimulus({}, [EventSlot(controls={'x': 3})])
    initialization = {'test': 'retained initial choice'}
    failure = getattr(RunFailure, origin)(
        'native-run', Refusal('assertion', 'source stopped', event='bad-event'))

    def native(*_args, **_kwargs):
        raise Refused(Refusal(failure.reason.kind, failure.reason.why,
                              failure=failure.as_dict()))

    proxy._context.source_trace = native
    monkeypatch.setattr(initial_state, 'decode_initialization', lambda _: object())
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', lambda *_a, **_k: {
        'stimulus': stimulus.as_dict(), 'initialization': initialization})
    check = proxy.audit_request([{'x': 3}], proxy._domain.digest())
    assert check.status is AuditCheckStatus.REFUSED
    assert check.kind == 'assertion'
    assert check.as_dict()['refusal']['detail'] == {
        'failure': failure.as_dict(), 'stimulus': stimulus.as_dict(),
        'initialization': initialization}


def test_native_terminal_failure_is_forwarded_for_worker_validation(monkeypatch):
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.stimulus import EventSlot, Stimulus
    from behave.sim.rtl import initial_state

    proxy = _proxy()
    stimulus = Stimulus({}, [EventSlot(controls={'x': 3})])
    failure = RunFailure.candidate('rtl-source-execution',
        Refusal('rtl-source-failure', 'native source stopped', replay_prefix={'untrusted': True}))
    def stopped(*_args, **_kwargs):
        raise Refused(Refusal(failure.kind, failure.why, failure=failure.as_dict()))
    proxy._context.source_trace = stopped
    monkeypatch.setattr(initial_state, 'decode_initialization', lambda _: object())
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', lambda *_a, **_k: {
        'stimulus': stimulus.as_dict(), 'initialization': {'selected': 'choice'}})
    request, result = proxy._source_payload([{'x': 3}])
    assert request == stimulus and type(result) is RunFailure
    assert result.as_dict() == failure.as_dict()  # Forwarding grants no witness or goal hit.


@pytest.mark.parametrize('semantics', ['native-cover/2', 'unrecognized/1'])
def test_unknown_query_semantics_cannot_bypass_native_confirmation(semantics):
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import Verdict
    from behave.sim.core.query import QueryCall, SolverAnswer

    proxy = _proxy()
    proxy._domain = TraceDomain(1, schema={'source_query_semantics': semantics})
    verdict = Verdict({'a': [{}]}, (), {}, queries=(
        QueryCall(('a',), SolverAnswer.SAT, witness=[{}]),))
    with pytest.raises(Refused, match='rtl-analysis-query-semantics-unsupported'):
        proxy._confirm_proposals(verdict)


def test_operation_wait_uses_original_analysis_deadline_not_preparation_cap(monkeypatch):
    from behave.sim import analysis_budget

    now = [100.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    process = _transport(monkeypatch, {})
    calls = []
    with analysis_scope(90):
        session = analysis_transport.RTLAnalysisSession({}, canonical_json({}), timeout_s=1)
        monkeypatch.setattr(session, "_exchange", lambda op, payload, seconds:
                            calls.append((op, payload, seconds)))
        now[0] = 117.0
        session.call("decode", {"rows": []})
        session.call("query", {"goals": ["hit"], "timeout_ms": 7})
        assert [call[2] for call in calls] == [73, 73]
        assert calls[1][1]["timeout_ms"] == 7  # Solver cap is not discarded.
        now[0] = 191.0
        with pytest.raises(analysis_budget.AnalysisStopped):
            session.call("decode", {"rows": []})
        assert len(calls) == 2
        session.close()
    assert process.closed == 1


def test_one_shot_operation_wait_also_uses_remaining_analysis_time(monkeypatch):
    from behave.sim import analysis_budget, worker

    now, calls = [100.0], []
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    def run_job(name, payload, **options):
        calls.append(options["timeout_s"])
        return analysis_job._reply(payload["operation"], [])
    monkeypatch.setattr(worker, "run_job_strict", run_job)
    proxy = _proxy()
    with analysis_scope(90):
        now[0] = 117.0
        assert proxy._raw_call("decode", rows=[]) == []
    assert calls == [73]


def test_initial_bind_uses_analysis_owner_not_rtl_tool_timeout(monkeypatch):
    from behave.sim import analysis_budget

    now, limits = [100.0], []
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    proxy = _proxy()
    proxy._binding = None
    _transport(monkeypatch, {})
    exchange = job_session.JobSession._exchange

    def capture(self, operation, payload, timeout_s):
        limits.append((operation, timeout_s))
        return exchange(self, operation, payload, timeout_s)

    monkeypatch.setattr(job_session.JobSession, "_exchange", capture)
    with analysis_scope(90):
        now[0] = 117.0
        assert proxy._raw_call("bind", horizon=3) == {}
    assert proxy._context.artifact.timeout_s == 1  # EDA/replay, not Analysis.
    assert limits == [("open", 73)]


def test_audit_refusal_retains_the_transport_cause(monkeypatch):
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    proxy = _proxy()
    proxy._domain = SimpleNamespace(digest=lambda: "bound-domain")
    refusal = RTLExactJobContext.unavailable("analysis-decode", ValueError("invalid test coordinate"))
    def unavailable(self, _rows):
        raise Refused(refusal)
    monkeypatch.setattr(type(proxy), "_source_payload", unavailable)
    audit = proxy.audit_request([{}], "bound-domain")
    assert audit.status.value == "refused"
    assert audit.kind == "rtl-exact-isolated-analysis-decode-unavailable"
    assert "ValueError: invalid test coordinate" in audit.why


def test_bind_cancellation_retains_operation_context_and_releases_owner(monkeypatch):
    from behave.sim.analysis_budget import AnalysisStopped, active_analysis

    proxy = _proxy()
    proxy._binding = None

    def cancelled(*args, **kwargs):
        raise AnalysisStopped(active_analysis())

    monkeypatch.setattr(analysis_transport, "RTLAnalysisSession", cancelled)
    with analysis_scope(60):
        with pytest.raises(AnalysisStopped) as error:
            proxy._raw_call("bind", horizon=3)
        assert error.value.__notes__ == ["RTL exact Analysis operation: bind"]
    assert proxy._session is None and proxy._session_owner is None


@pytest.mark.parametrize("session", [False, True])
def test_frozen_witness_rows_cross_both_json_transports_without_mutation(monkeypatch, session):
    from behave.sim import worker
    from behave.sim.core.frozen import FrozenMap

    proxy, sent = _proxy(), []
    rows = (FrozenMap({"a": 2**100 + 7}),) * 3
    def answer(operation, payload):
        wire = json.loads(json.dumps(payload, allow_nan=False))
        sent.append(wire)
        return analysis_job._reply(operation, wire["rows"])
    if session:
        proxy._session_active = True
        proxy._session = SimpleNamespace(call=answer)
    else:
        monkeypatch.setattr(worker, "run_job_strict", lambda name, payload, **options:
                            answer(payload["operation"], payload))
    with analysis_scope(60):
        assert proxy._raw_call("decode", rows=rows) == [{"a": 2**100 + 7}] * 3
    assert len(sent) == 1 and type(sent[0]["rows"][0]) is dict
    assert all(type(row) is FrozenMap and row["a"] == 2**100 + 7 for row in rows)


def test_scope_is_lazy_closes_on_exception_and_never_retries_failed_open(monkeypatch):
    proxy = _proxy()
    opens = []

    def fail(*args, **kwargs):
        opens.append(args)
        raise IsolatedJobFailure("timeout", "no answer")

    monkeypatch.setattr(analysis_transport, "RTLAnalysisSession", fail)
    with analysis_scope(60), proxy.execution_scope():
        assert opens == []
        for _ in range(2):
            with pytest.raises(Refused):
                proxy._raw_call("feasible", timeout_ms=1)
        assert len(opens) == 1
        with pytest.raises(RuntimeError, match="nested"), proxy.execution_scope():
            pass
    assert not proxy._session_active


@pytest.mark.parametrize("fail", [False, True])
def test_analysis_scope_borrows_outer_build_without_closing_it(fail):
    from behave.sim.rtl.replay_build import current_replay_build, replay_build_scope

    proxy = _proxy()
    artifact = proxy._context.artifact
    with analysis_scope(60), replay_build_scope(artifact) as outer:
        seen_error = False
        try:
            with proxy.execution_scope():
                assert current_replay_build(artifact) is outer
                if fail:
                    raise ValueError("test interrupted analysis")
        except ValueError as error:
            seen_error = True
            assert fail and str(error) == "test interrupted analysis"
        assert seen_error is fail
        assert current_replay_build(artifact) is outer
        assert not proxy._session_active
    with pytest.raises(RuntimeError, match="no owning build scope"):
        current_replay_build(artifact)
    closed = []
    with analysis_scope(60), pytest.raises(ValueError), proxy.execution_scope():
        proxy._session = SimpleNamespace(close=lambda: closed.append(True))
        raise ValueError("coordinator stopped")
    assert closed == [True]
    assert proxy._session is None


def test_session_cannot_invent_an_analysis_budget():
    with pytest.raises(ValueError, match="owning analysis budget"):
        analysis_transport.RTLAnalysisSession({}, canonical_json({}), timeout_s=1)


def _retained_transport(monkeypatch, binding):
    sessions = []
    class Session:
        def __init__(self, context, expected=None, *, timeout_s):
            self.opened = analysis_job._reply('bind', binding)
            self.expected, self.calls, self.closed = expected, [], 0
            sessions.append(self)

        def call(self, operation, payload):
            self.calls.append((operation, payload))
            return analysis_job._reply(operation, [])

        def close(self):
            self.closed += 1
    monkeypatch.setattr(analysis_transport, 'RTLAnalysisSession', Session)
    return sessions


@pytest.mark.parametrize('coordinator', [False, True])
def test_initial_binding_belongs_to_owner_and_is_reused_by_coordinator(monkeypatch, coordinator):
    proxy = _proxy()
    proxy._binding = None
    sessions = _retained_transport(monkeypatch, {})
    with analysis_scope(60):
        assert proxy._raw_call('bind', horizon=3) == {}
        assert len(sessions) == 1 and sessions[0].expected is None
        assert sessions[0].calls == []  # open already returned the binding.
        proxy._binding = canonical_json({})
        with pytest.raises(ValueError, match='cannot rebind'):
            proxy._raw_call('bind', horizon=4)
        if coordinator:
            with proxy.execution_scope():
                assert proxy._raw_call('decode', rows=[]) == []
                assert proxy._raw_call('decode', rows=[]) == []
            assert sessions[0].closed == 1
        else:
            assert sessions[0].closed == 0
    assert len(sessions) == 1 and sessions[0].closed == 1
    assert proxy._session is None and proxy._session_owner is None
    with analysis_scope(60), proxy.execution_scope():
        proxy._raw_call('decode', rows=[])
        assert sessions[1].expected == proxy._binding
    assert sessions[1].closed == 1  # A new owner needs a freshly checked binding.


@pytest.mark.parametrize('error', [ValueError('post-bind failure'), KeyboardInterrupt()])
def test_failure_before_coordinator_still_closes_the_bound_worker(monkeypatch, error):
    proxy = _proxy()
    proxy._binding = None
    sessions = _retained_transport(monkeypatch, {})
    with pytest.raises(type(error)), analysis_scope(60):
        proxy._raw_call('bind', horizon=3)
        raise error
    assert sessions[0].closed == 1
    assert proxy._session is None and proxy._session_owner is None


def test_unowned_binding_remains_one_shot(monkeypatch):
    from behave.sim import worker
    proxy, calls = _proxy(), []
    proxy._binding = None
    def run_job(name, payload, **options):
        calls.append(payload)
        return analysis_job._reply('bind', {})
    monkeypatch.setattr(worker, 'run_job_strict', run_job)
    assert proxy._raw_call('bind', horizon=3) == {}
    assert len(calls) == 1 and proxy._session is None
