"""Retained construction is one bound formula, not a reused query answer."""

import gc
import time
import weakref
from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.template_arrays import TemplateArrays
from behave.sim.core.construction_checkpoint import finish_construction
from behave.sim.engines import probing
from behave.sim.engines.rtl import source_session, source_task_query
from behave.sim.engines.rtl.generation import RTLScheduleSegment
from behave.sim.generation import ProbeRequest, StimulusField
from behave.sim.rtl.query_deadline import QueryDeadline

pytestmark = pytest.mark.solver


def question(field=0, goal='g', stop=8):
    coordinate = StimulusField('in', 0, field, 1)
    request = ProbeRequest((goal,), 'segment', (coordinate,), catalogue_digest='a' * 64)
    segment = RTLScheduleSegment('segment', 'seed', 1, stop, 0, (coordinate,))
    return request, segment


@pytest.mark.parametrize('transaction_pending', [False, True])
def test_terminal_obligation_excludes_sampled_input_queues(monkeypatch, transaction_pending):
    from behave.sim import api
    from behave.sim.core.monitor import INPUT_COMPLETION
    from behave.sim.rtl.source_task import SourceTaskEncoding

    field = api.Field('a', api.FiniteType(2), 'a')
    inputs = [SimpleNamespace(name=name, kind=kind, fields=(field,)) for name, kind in
              [('in', api.Kind.TRANSACTION), ('config', api.Kind.SAMPLED)]]
    seed = api.Stimulus({'in': [(0,)], 'config': [(0,), (0,)]}, [
        api.EventSlot(), api.EventSlot(offers=('in', 'config')),
        api.EventSlot(offers=('config',)), api.EventSlot()])
    request, old = question(goal=INPUT_COMPLETION, stop=len(seed.slots))
    segment = RTLScheduleSegment(old.id, seed.fingerprint, old.start, old.stop,
                                 old.rank, old.mutable_fields)
    query = SimpleNamespace(slot=1, state={}, fields={'a': (request.mutable_fields[0], field)})
    binding = SimpleNamespace(seed=seed, contract=SimpleNamespace(
        inputs=lambda: inputs, outputs=lambda: (), clocks=(), endpoints=inputs,
        observation_goals=(), transaction_monitors=(), latency_goals=()),
        atomic=False, at=lambda *args, **kwargs: query,
        product_boundary=lambda *args, **kwargs: (None, None),
        protocol_monitors=lambda: SimpleNamespace(initial=lambda *args: {}),
        monitor_boundary=lambda *args, **kwargs: {})
    context = z3.Context()
    original = SourceTaskEncoding(SimpleNamespace(continuable=z3.BoolVal(True, ctx=context)),
        SimpleNamespace(pointers=({'in': z3.IntVal(int(not transaction_pending), ctx=context),
                                  'config': z3.IntVal(0, ctx=context)},)), ())

    def encode(*args, **kwargs):
        yield
        return original

    relation = SimpleNamespace(
        source=SimpleNamespace(relation=SimpleNamespace(source=SimpleNamespace(
            state_names=(), environment={}))), protocol=SimpleNamespace(algebra=None),
        encode_steps=encode)
    monkeypatch.setattr(source_task_query, 'SourceTaskRelation', lambda *args, **kwargs: relation)
    machine = SimpleNamespace(occurrences=(), boundary_is_quiet=lambda state: True,
        procedure=SimpleNamespace(semantics=SimpleNamespace(state_types={})))
    # Keep the real environment/drain check; only the constructed final cursors
    # are supplied here. Sampled pins do not acquire a transaction obligation.
    enc, _, _ = finish_construction(source_task_query._construct_source_task(
        machine, None, binding, request, segment, context, lambda: None), lambda: None)
    assert z3.is_true(z3.simplify(enc.terminal_hits[INPUT_COMPLETION])) is transaction_pending


@pytest.mark.parametrize('complete', [False, True])
def test_progress_is_not_replayed_and_context_releases_without_cyclic_gc(monkeypatch, complete):
    visits = []
    def build(machine, template, binding, request, segment, context, checkpoint):
        for i in range(9):
            yield
            visits.append(i)
        return z3.BitVecVal(3, 8, ctx=context)
    monkeypatch.setattr(source_task_query, '_construct_source_task', build)
    request, segment = question()
    owner = source_task_query.SourceTaskQuery(object(), object(), object(), request, segment)
    arena = weakref.ref(owner.context)
    n = 0
    def check():
        nonlocal n
        n += 1
        if n % 4 == 0:
            raise TimeoutError('pause')
    while owner.product is None:
        try:
            owner.advance(check)
        except TimeoutError:
            assert not owner.broken
            assert owner.checkpoint.callback is None
            if not complete:
                break
    assert visits == list(range(9 if complete else 2))
    if complete:
        assert owner.product.as_long() == 3
    enabled = gc.isenabled()
    gc.disable()
    try:
        del owner
        assert arena() is None
    finally:
        if enabled:
            gc.enable()


def test_expired_watchdog_does_not_poison_a_suspended_context(monkeypatch):
    def build(machine, template, binding, request, segment, context, checkpoint):
        x = z3.BitVec('resume_x', 8, ctx=context)
        yield
        return x + 1 == 4
    monkeypatch.setattr(source_task_query, '_construct_source_task', build)
    owner = source_task_query.SourceTaskQuery(object(), object(), object(), *question())
    next(owner.steps)
    with pytest.raises(TimeoutError):
        with QueryDeadline(owner.context, 0, started=time.monotonic()) as deadline:
            owner.advance(deadline.remaining_ms)
    assert not owner.broken and owner.product is None
    with QueryDeadline(owner.context, 5000, started=time.monotonic()) as deadline:
        term = owner.advance(deadline.remaining_ms)
        solver = z3.Solver(ctx=owner.context)
        solver.add(term)
        assert solver.check() == z3.sat
        assert solver.model().eval(z3.BitVec('resume_x', 8, ctx=owner.context)).as_long() == 3


def test_internal_failure_cannot_become_a_completed_product(monkeypatch):
    def build(*args):
        yield
        raise ValueError('bad carrier')
    monkeypatch.setattr(source_task_query, '_construct_source_task', build)
    owner = source_task_query.SourceTaskQuery(object(), object(), object(), *question())
    with pytest.raises(ValueError, match='bad carrier'):
        owner.advance(lambda: None)
    assert owner.broken and owner.steps is None and owner.product is None


def test_paused_query_returns_constructing_but_solver_unknown_does_not(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    def build(*args):
        clock[0] += .2
        yield
        return 'complete'
    def solve(machine, template, binding, request, segment, context, remaining, result, owner):
        assert owner.advance(remaining) == 'complete'
        owner.queried = True
        return result(probing.Outcome.UNKNOWN, detail='solver timeout')
    monkeypatch.setattr(source_task_query, '_construct_source_task', build)
    monkeypatch.setattr(source_task_query, '_query_source_task', solve)
    owner = source_task_query.SourceTaskQuery(object(), object(), object(), *question())
    for expected in (probing.Outcome.CONSTRUCTING, probing.Outcome.UNKNOWN):
        answer = source_task_query.query_source_task(*owner.arguments, timeout_ms=100,
                                                     _construction=owner)
        assert answer.outcome is expected and answer.candidate is None
    assert owner.product == 'complete'


@pytest.mark.parametrize('changed', ['machine', 'template', 'binding', 'goal', 'field', 'interval'])
def test_changed_question_cannot_borrow_the_product(changed):
    machine, template, binding = object(), object(), object()
    request, segment = question()
    owner = source_task_query.SourceTaskQuery(machine, template, binding, request, segment)
    if changed == 'machine':
        machine = object()
    elif changed == 'template':
        template = object()
    elif changed == 'binding':
        binding = object()
    else:
        request, segment = question(field=int(changed == 'field'),
            goal='h' if changed == 'goal' else 'g', stop=9 if changed == 'interval' else 8)
    assert not owner.matches(machine, template, binding, request, segment)
    result = source_task_query.query_source_task(machine, template, binding, request, segment,
        timeout_ms=100, _construction=owner)
    assert result.outcome is probing.Outcome.UNSUPPORTED
    assert result.candidate is None


@pytest.mark.parametrize('quantum', [1, 3, 11])
def test_array_macro_dag_can_suspend_without_reinstantiating_nodes(quantum):
    recipes = TemplateArrays()
    i, x = z3.BitVecs('i x', 8)
    a = FunctionalArray.uniform(16, z3.BitVecVal(0, 8))
    for n in range(8):
        a = a.store(i + n, x + n, x != n)
    term = recipes.term(a)
    j = z3.Const('j', term.sort().domain())
    image = z3.Function('_step_fields', z3.BitVecSort(8), z3.BoolSort())(z3.Select(term, j))
    expected = recipes.instantiate(image, ())
    calls = []
    for key, original in tuple(recipes.constructors.items()):
        def build(*args, key=key, original=original):
            calls.append(key)
            return original(*args)
        recipes.constructors[key] = build
    steps, count = recipes.instantiate_steps(image, ()), 0
    def check():
        nonlocal count
        count += 1
        if count % (quantum + 1) == 0:
            raise TimeoutError('pause')
    while True:
        try:
            actual = finish_construction(steps, check)
            break
        except TimeoutError:
            pass
    assert len(calls) == 9  # uniform + eight distinct stores, not once per resume
    solver = z3.Solver()
    solver.add(actual[0] != expected[0])
    assert solver.check() == z3.unsat


def test_worker_keeps_each_field_scope_and_drops_rebound_or_completed_owners(monkeypatch):
    from behave.sim.core.catalogue import CatalogueManifest, Goal

    seen = []
    outcome = [probing.Outcome.UNKNOWN]
    def probe(*args, _construction, **kwargs):
        seen.append(_construction)
        return probing.ProbeResult(outcome[0], probing.Scope.RTL_LOCAL_HIT)
    monkeypatch.setattr(source_task_query, 'query_source_task', probe)
    worker = object.__new__(source_session.PreparedSourceGeneration)
    worker.machine, worker.template, worker.binding = object(), object(), object()
    worker.contract = SimpleNamespace(clocks=('c',))
    worker.catalogue = CatalogueManifest((Goal('rtl-source:g', 'coverage',
        family='location', provider='rtl-structural'),))
    worker._task_progress = {}
    worker._interval_progress = None
    for field in (0, 1, 0):
        old, segment = question(field, goal='rtl-source:g')
        request = ProbeRequest(old.targets, old.occurrence, old.mutable_fields,
                               catalogue_digest=worker.catalogue.digest())
        worker.probe({'request': request.as_dict(), 'segment': segment.as_dict(), 'timeout_ms': 100})
    assert seen[0] is seen[2] and seen[1] is not seen[0]
    outcome[0] = probing.Outcome.LOCAL_UNSAT
    worker.probe({'request': request.as_dict(), 'segment': segment.as_dict(), 'timeout_ms': 100})
    assert len(worker._task_progress) == 1
    with pytest.raises(KeyError):
        worker.bind({})
    assert worker.binding is None and not worker._task_progress


def test_worker_keeps_protocol_and_source_construction_separate(monkeypatch):
    from behave.sim.core.catalogue import CatalogueManifest, Goal

    worker = object.__new__(source_session.PreparedSourceGeneration)
    worker.machine, worker.env = SimpleNamespace(procedure=object()), {}
    worker.template, worker.binding = SimpleNamespace(), object()
    worker._protocol_template, worker._interval_progress = None, None
    worker._task_progress = {}
    worker.contract = SimpleNamespace(clocks=('c',))
    worker.catalogue = CatalogueManifest((Goal('rtl-source:g', 'coverage',
        family='location', provider='rtl-structural'),
        Goal('monitor.sink-stability', 'safety', provider='monitor')))
    built, seen = [], []

    def construct():
        template = object()
        built.append(template)
        return template

    def probe(machine, template, binding, *args, _construction, **kwargs):
        seen.append((template, _construction))
        return probing.ProbeResult(probing.Outcome.UNKNOWN, probing.Scope.RTL_LOCAL_HIT)

    worker.template.normalized = construct
    monkeypatch.setattr(source_task_query, 'query_source_task', probe)
    for goal in ('rtl-source:g', 'monitor.sink-stability') * 2:
        old, segment = question(goal=goal)
        request = ProbeRequest(old.targets, old.occurrence, old.mutable_fields,
                               catalogue_digest=worker.catalogue.digest())
        worker.probe(dict(request=request.as_dict(), segment=segment.as_dict(), timeout_ms=100))
    assert len(built) == 1 and len(worker._task_progress) == 2
    assert seen[0] == seen[2] and seen[1] == seen[3]
    assert seen[0][0] is worker.template and seen[1][0] is built[0]
    assert seen[0][1] is not seen[1][1]
    with pytest.raises(KeyError):
        worker.bind({})
    assert not worker._task_progress and worker.binding is None
    assert worker._protocol_template is built[0]  # Immutable syntax, no seed.


@pytest.mark.parametrize('domain', ['safe', 'unsafe', 'unknown', 'absent'])
@pytest.mark.parametrize('goal_count', [1, 32])
def test_domain_image_is_built_once_per_probe_and_keeps_its_full_scope(monkeypatch, domain, goal_count):
    from behave.sim.core.query import SolverAnswer

    context = z3.Context()
    value = z3.BitVec('input', 8, ctx=context)
    reads, calls = [], []

    class Source:
        memory = SimpleNamespace(validity_constraints=())

        @property
        def hits(self):
            reads.append('hits')
            return {f'g{i}': value == 3 for i in range(goal_count)}

        @property
        def domain_faults(self):
            reads.append('domain')
            return {} if domain == 'absent' else {'private': value == 7}

    constraints = (z3.ULT(value, 7),) if domain == 'safe' else ()
    from behave.sim.rtl.source_task import SourceTaskEncoding
    enc = SourceTaskEncoding(Source(), SimpleNamespace(hits=()), constraints)
    query = SimpleNamespace(proposal=lambda values: values)
    owner = SimpleNamespace(advance=lambda _remaining: (enc, {'input': value}, query),
                            queried=False)
    solve = source_task_query.z3_check

    def checked(*args, **kwargs):
        calls.append('solve')
        if domain == 'unknown':
            return SimpleNamespace(answer=SolverAnswer.UNKNOWN, reason='controlled timeout')
        return solve(*args, **kwargs)

    monkeypatch.setattr(source_task_query, 'z3_check', checked)
    cause = SimpleNamespace(kind='invalid-operation', why='outside local domain')
    machine = SimpleNamespace(domain_checks=(('private', cause),))
    request, segment = question(goal='g0')
    request = ProbeRequest(tuple(f'g{i}' for i in range(goal_count)), request.occurrence,
                           request.mutable_fields, catalogue_digest=request.catalogue_digest)
    answer = source_task_query._query_source_task(machine, None, None, request, segment,
        context, lambda: 10_000,
        lambda outcome, **fields: probing.ProbeResult(outcome, probing.Scope.RTL_LOCAL_HIT, **fields),
        owner)
    assert reads == (['domain', 'hits'] if domain in ('safe', 'absent') else ['domain'])
    assert len(calls) == (2 if domain == 'safe' else 1)
    assert owner.queried
    if domain in ('safe', 'absent'):
        assert answer.outcome is probing.Outcome.SAT
        assert answer.candidate == {'input': 3}
    elif domain == 'unsafe':
        # The target itself is safe. Another mutable input is not: do not
        # conjoin the target into definedness or quietly narrow the domain.
        assert answer.outcome is probing.Outcome.UNSUPPORTED
        assert answer.candidate is None
        assert 'invalid-operation' in answer.detail
    else:
        assert answer.outcome is probing.Outcome.UNKNOWN
        assert answer.candidate is None
        assert 'source operation-domain proof' in answer.detail
