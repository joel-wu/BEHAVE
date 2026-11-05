"""Raw records need domain admission; short histories may propose fresh runs."""

from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming
from behave.sim.core.domain import TraceDomain
from behave.sim.core.driver import Driver
from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun
from behave.sim.core.runtime_schedule import ReplayPolicy
from behave.sim.core.stimulus import AuditedTrace, Completion, SlotRecord, trace_domain
from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance, _record_domain_audit

from .test_rtl_atomic_exact import _contract as _atomic_contract, _scheduled, _stimulus

pytestmark = pytest.mark.solver


def _ready_valid_contract():
    bit = api.FiniteType(1)
    return api.TaskContract(
        endpoints=(
            api.Endpoint("in", api.Direction.IN, (api.Field("a", bit, "a"),),
                         api.ready_valid("in", "in_valid", "in_ready"),
                         clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint("out", api.Direction.OUT, (api.Field("y", bit, "y"),),
                         api.ready_valid("out", "out_valid", "out_ready"),
                         clock="clk", kind=api.Kind.TRANSACTION, answers_per_transaction=1),
        ),
        clocks=(api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=1),))


@pytest.fixture(params=("ready-valid", "atomic"))
def domain(request):
    """A two-slot analysis domain and the declarations its record audit reads."""
    if request.param == "ready-valid":
        contract, queue_bound, schedule = _ready_valid_contract(), 1, None
        stimulus = api.Stimulus({"in": ((0,),)}, (
            api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 0}),
            api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
        ))
    else:
        contract, queue_bound = _atomic_contract(), 2
        schedule = _scheduled((
            ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
            ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
        ), 2)
        stimulus = _stimulus(schedule)
    instance = SimpleNamespace(queue_bound=queue_bound, memory_environment=None,
                               contract_record=contract)
    bound = SimpleNamespace(domain=TraceDomain(
        2, artifact="a" * 64, task_contract=contract.digest))
    task = SimpleNamespace(contract=contract)
    return instance, bound, task, stimulus, schedule


def _record(stimulus, bound, *, artifact=None, contract=None, completion=Completion.NORMAL,
            request=None, records=None):
    trace = AuditedTrace(
        stimulus, records=[SlotRecord() for _ in stimulus.slots] if records is None else records,
        artifact=bound.domain.artifact if artifact is None else artifact,
        domain=trace_domain(contract or bound.domain.task_contract, horizon=len(stimulus.slots)),
        completion=completion, request=request)
    return api.RunResult.not_compared(request or stimulus, trace, api.RunFailure.verifier(
        "test-audit-view", api.Refusal("test-uncompared", "comparison is irrelevant")))


@pytest.mark.parametrize("change,expected", (
    ("horizon", "outside"), ("memory", "outside"),
    ("artifact", "inconsistent"), ("contract", "inconsistent"),
    ("incomplete", "refused"), ("inside", "refused"), ("runtime", "refused"),
    ("checkpoint", "outside"), ("padded", "refused"),
    ("checkpoint-artifact", "inconsistent"), ("checkpoint-contract", "inconsistent"),
))
def test_parent_and_worker_exclude_before_replay(monkeypatch, domain, change, expected):
    instance, bound, task, stimulus, schedule = domain
    if change == "horizon":
        stimulus = api.Stimulus(dict(stimulus.queues), stimulus.slots[:1])
    elif change == "memory":
        stimulus = api.Stimulus(
            dict(stimulus.queues), stimulus.slots, clock_schedule=schedule,
            memory_environment=MemoryEnvironment((MemoryRun("foreign"),)))
    request = None
    if change == "runtime":
        request = api.Stimulus(dict(stimulus.queues), replay_policy=ReplayPolicy(
            clock_timings=() if schedule is None else schedule.timings))
    records = None
    if change in {"inside", "runtime"} and schedule is None:
        driver = Driver(task.contract, stimulus)
        for index in range(len(stimulus.slots)):
            driver.observe(index, driver.presentations(index), {"in_ready": 1, "out_valid": 0, "y": 0})
        records = driver.records
    record = _record(stimulus, bound, request=request, records=records,
                     artifact="another-artifact" if change.endswith("artifact") else None,
                     contract="another-contract" if change.endswith("contract") else None,
                     completion=(Completion.CHECKPOINT if change.startswith("checkpoint") else
                                 Completion.PADDED if change == "padded" else
                                 Completion.INCOMPLETE if change == "incomplete" else Completion.NORMAL))
    calls = []

    def unavailable(replayed, **_kwargs):
        calls.append(True)
        raise api.Refused(api.Refusal("native-test-unavailable", "native replay was requested"))

    parent = object.__new__(RTLIsolatedAnalysisInstance)
    parent._domain = bound.domain
    parent._context = SimpleNamespace(
        generation_spec=SimpleNamespace(queue_bound=instance.queue_bound),
        schedule=schedule, memory_environment=instance.memory_environment, source_trace=unavailable)
    worker_calls = []
    def worker(_self, operation, **payload):
        assert operation == 'record-audit'
        assert payload['raw_trace'] == record.trace.as_dict()
        worker_calls.append(True)
        return api.Refusal('test-worker-unavailable', 'record audit was requested')
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', worker)
    inside = change in {'inside', 'runtime'}
    result = parent.audit_record(record, task)
    assert result.as_dict()["disposition"] == expected
    # Only a record inside the domain reaches the worker; nothing is replayed first.
    assert worker_calls == ([True] if inside else [])
    assert calls == []


@pytest.mark.parametrize("change", ("queue", "schedule"))
def test_queue_and_schedule_are_checked_without_native_evidence(domain, change):
    instance, bound, task, stimulus, schedule = domain
    if change == "queue":
        inputs = task.contract.inputs()
        if not inputs:
            pytest.skip("this atomic fixture has output endpoints only")
        stimulus = api.Stimulus(
            {inputs[0].name: ((0,),) * (instance.queue_bound + 1)}, stimulus.slots)
    else:
        if schedule is None:
            schedule = _scheduled((ClockTiming("clk", 2, clock="clk", reset="rst"),), 2)
        else:
            schedule = _scheduled(schedule.timings, 2, ready_a=0)
        stimulus = _stimulus(schedule)
    declared = getattr(instance, "schedule", None)
    result = _record_domain_audit(
        _record(stimulus, bound), task, bound.domain, queue_bound=instance.queue_bound,
        schedule=declared, memory_environment=instance.memory_environment)
    assert result.as_dict()["disposition"] == "outside"


def test_memory_membership_compares_declared_values_not_object_identity(domain):
    instance, bound, task, stimulus, schedule = domain
    contract = api.TaskContract(
        task.contract.endpoints, task.contract.clocks,
        memories=(MemoryInterface("ram", 2, 4, task.contract.clocks[0].name,
                                  (MemoryPort("rw"),)),))
    task = SimpleNamespace(contract=contract)
    bound = SimpleNamespace(domain=TraceDomain(
        2, task_contract=contract.digest, artifact=bound.domain.artifact))
    first = MemoryEnvironment((MemoryRun("ram", initial={1: 7}, ready={None: (1, 0)}),))
    second = MemoryEnvironment.from_dict(first.as_dict())
    stimulus = api.Stimulus(dict(stimulus.queues), stimulus.slots, clock_schedule=schedule,
                            memory_environment=second)
    record = _record(stimulus, bound)
    assert first is not second
    assert _record_domain_audit(
        record, task, bound.domain, queue_bound=instance.queue_bound,
        schedule=schedule, memory_environment=first) is None
    # Passing this preflight is not admission; the worker still checks the task's
    # memory declarations and the complete independently replayed execution.


def test_wrong_identity_cannot_be_hidden_by_an_outside_horizon(domain):
    instance, bound, task, stimulus, schedule = domain
    stimulus = api.Stimulus(dict(stimulus.queues), stimulus.slots[:1])
    result = _record_domain_audit(
        _record(stimulus, bound, artifact="wrong"), task, bound.domain,
        queue_bound=instance.queue_bound, schedule=schedule,
        memory_environment=instance.memory_environment)
    assert result.as_dict()["disposition"] == "inconsistent"


def _long_observed_record(domain):
    """Header/selection fixture only; the native audit is tested separately."""
    from behave.sim.rtl.source_boundary_capture import HISTORY_VERSION, VERSION

    instance, bound, task, short, schedule = domain
    if schedule is None:
        slots = (*short.slots, *(api.EventSlot(controls=dict(short.slots[-1].controls))
                                  for _ in range(2)))
        stimulus = api.Stimulus(dict(short.queues), slots)
    else:
        stimulus = _stimulus(_scheduled(schedule.timings, 4))
    slots = len(stimulus.slots)
    native = dict(version='rtl-native-observation/3', plan={}, execution={},
                  stimulus=stimulus.fingerprint, completed_steps=slots,
                  intervals=[dict(phase=i) for i in range(1 + 3 * slots)])
    boundaries = dict(version=VERSION, fields=[], samples=[[i, []] for i in range(slots)],
        history=dict(version=HISTORY_VERSION, startup=None, refusal=None, widths={'q': 8},
                     initial={'q': 0}, sampled=[{'q': i} for i in range(slots)],
                     states=[{'q': i + 1} for i in range(slots)]))
    trace = AuditedTrace(stimulus,
        records=[SlotRecord(hits={'early': i == 0, 'late': i == slots - 1}) for i in range(slots)],
        artifact=bound.domain.artifact,
        domain=trace_domain(task.contract.digest, horizon=slots),
        source_evidence=native, query_boundaries=boundaries)
    return api.RunResult.not_compared(stimulus, trace, api.RunFailure.verifier(
        'fixture', api.Refusal('uncompared', 'no score is supplied by this fixture')))


def test_source_window_keeps_identity_observations_and_queues_without_later_hits(domain):
    from behave.sim.rtl.source_boundary_capture import source_history_prefix

    record = _long_observed_record(domain)
    original = record.trace
    before, fingerprint = original.as_dict(), original.fingerprint
    prefix = source_history_prefix(original, 2)
    assert prefix is not original and original.as_dict() == before
    assert original.fingerprint == fingerprint and prefix.fingerprint != fingerprint
    assert prefix.records == original.records[:2]
    assert prefix.records[0] is original.records[0]
    assert prefix.goal_summary[1] == {'early'} and original.goal_summary[1] == {'early', 'late'}
    assert prefix.request is None and prefix.stimulus.replay_policy is None
    assert prefix.stimulus.queues == original.stimulus.queues
    assert prefix.source_evidence['stimulus'] == prefix.stimulus.fingerprint
    assert prefix.source_evidence['completed_steps'] == 2
    assert prefix.source_evidence['intervals'] == original.source_evidence['intervals'][:7]
    assert prefix.source_evidence['intervals'][0] is original.source_evidence['intervals'][0]
    assert prefix.query_boundaries['history']['states'] == original.query_boundaries['history']['states'][:2]
    assert prefix.query_boundaries['samples'] == original.query_boundaries['samples'][:2]
    assert original.stimulus.fingerprint in prefix.stimulus.source
    if original.stimulus.clock_schedule is not None:
        assert prefix.stimulus.clock_schedule.slots == original.stimulus.clock_schedule.slots[:2]
        assert prefix.stimulus.clock_schedule.timings == original.stimulus.clock_schedule.timings
    assert source_history_prefix(original, 4) is original
    assert source_history_prefix(original, 5) is original


@pytest.mark.parametrize('change', ['native-binding', 'phases', 'history', 'sample', 'incomplete'])
def test_source_window_does_not_relabel_missing_or_foreign_evidence(domain, change):
    from behave.sim.rtl.source_boundary_capture import source_history_prefix

    image = _long_observed_record(domain).trace.as_dict()
    if change == 'native-binding':
        image['source_evidence']['stimulus'] = 'foreign'
    elif change == 'phases':
        image['source_evidence']['intervals'].pop()
    elif change == 'history':
        image['query_boundaries']['history']['states'].pop()
    elif change == 'sample':
        image['query_boundaries']['samples'][-1][0] = -1
    else:
        image['completion'] = 'incomplete'
    trace = AuditedTrace.from_dict(image)
    if change == 'incomplete':
        assert source_history_prefix(trace, 2) is trace
    else:
        with pytest.raises(api.Refused):
            source_history_prefix(trace, 2)


@pytest.mark.parametrize('foreign', [False, True])
def test_parent_sends_only_the_prefix_to_the_ordinary_worker_audit(monkeypatch, domain, foreign):
    instance, bound, task, stimulus, schedule = domain
    record = _long_observed_record(domain)
    if foreign:
        image = record.trace.as_dict()
        image['artifact'] = 'foreign'
        record = api.RunResult.not_compared(record.stimulus, AuditedTrace.from_dict(image),
                                           record.comparison_failure)
    parent = object.__new__(RTLIsolatedAnalysisInstance)
    parent._domain = bound.domain
    parent._context = SimpleNamespace(generation_spec=SimpleNamespace(queue_bound=instance.queue_bound),
                                      schedule=schedule, memory_environment=instance.memory_environment)
    calls = []
    def audit(_self, operation, **payload):
        assert operation == 'record-audit'
        trace = AuditedTrace.from_dict(payload['raw_trace'])
        assert len(trace.records) == 2 and trace.goal_summary[1] == {'early'}
        assert api.Stimulus.from_dict(payload['stimulus']) == trace.stimulus
        calls.append(trace)
        return {'disposition': 'outside', 'why': 'worker fixture declines admission'}
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', audit)
    result = parent.audit_record(record, task)
    assert result.as_dict()['disposition'] == ('inconsistent' if foreign else 'outside')
    assert len(calls) == (0 if foreign else 1)


@pytest.mark.parametrize('change', ('none', 'no-startup', 'boundary-only', 'capture-unavailable',
    'capture-invalid', 'settled', 'queue', 'incomplete',
    'foreign', 'replay-failed', 'wrong-stimulus', 'wrong-startup', 'fresh-incomplete'))
def test_short_record_extension_executes_and_audits_new_evidence(monkeypatch, domain, change):
    from behave.sim.rtl.initial_state import RTLInitialState
    from behave.sim.rtl.startup_state import RTLStartupState
    from behave.sim.rtl.source_boundary_capture import VERSION, HISTORY_VERSION

    instance, bound, task, stimulus, schedule = domain
    startup = RTLStartupState(RTLInitialState.from_values(bound.domain.artifact, {'q': 8}, {'q': 3}), ())
    short = api.Stimulus(dict(stimulus.queues), stimulus.slots[:1])
    if change == 'queue' and task.contract.inputs():
        short = api.Stimulus({task.contract.inputs()[0].name: ((0,),) * (instance.queue_bound + 1)}, short.slots)
    image = _record(short, bound,
        artifact='foreign' if change == 'foreign' else None,
        completion=Completion.INCOMPLETE if change == 'incomplete' else Completion.NORMAL).trace.as_dict()
    passive = change in {'boundary-only', 'capture-unavailable', 'capture-invalid'}
    image['initialization'] = (None if change == 'no-startup' or passive else
        startup.registers.as_dict() if change == 'settled' else startup.as_dict())
    if passive:
        image['query_boundaries'] = dict(version=VERSION, fields=[], samples=[])
        if change != 'boundary-only':
            image['query_boundaries']['history'] = dict(version=HISTORY_VERSION,
                startup=None, refusal=api.Refusal('capture-unavailable', 'no native layout').as_dict(),
                widths={}, initial={}, sampled=[], states=[])
        if change == 'capture-invalid':
            del image['query_boundaries']['history']['sampled']
    original = AuditedTrace.from_dict(image)
    record = api.RunResult.not_compared(short, original, api.RunFailure.verifier(
        'fixture', api.Refusal('uncompared', 'no original score')))
    before = original.as_dict()
    native_calls, worker_calls = [], []

    def execute(proposal, *, startup_state, reuse):
        assert proposal.queues == short.queues and proposal.slots[:1] == short.slots
        assert len(proposal.slots) == bound.domain.bound
        assert proposal.slots[-1].offers == () and proposal.slots[-1].controls == short.slots[-1].controls
        assert startup_state.as_dict() == startup.as_dict() and reuse is False
        assert short.fingerprint in proposal.source
        native_calls.append(proposal)
        if change == 'replay-failed':
            raise api.Refused(api.Refusal('native-unavailable', 'source execution failed'))
        if change == 'wrong-stimulus':
            proposal = short
        trace = _record(proposal, bound,
            records=[SlotRecord(hits={'fresh-only': True}) for _ in proposal.slots],
            completion=Completion.INCOMPLETE if change == 'fresh-incomplete' else Completion.NORMAL).trace.as_dict()
        trace['initialization'] = None if change == 'wrong-startup' else startup.as_dict()
        return AuditedTrace.from_dict(trace)

    parent = object.__new__(RTLIsolatedAnalysisInstance)
    parent._domain = bound.domain
    parent._context = SimpleNamespace(
        generation_spec=SimpleNamespace(queue_bound=instance.queue_bound), schedule=schedule,
        memory_environment=None, prepare_source=lambda: SimpleNamespace(state_names=('q',)), source_trace=execute)

    def audit(_self, operation, **payload):
        assert operation == 'record-audit'
        fresh = AuditedTrace.from_dict(payload['raw_trace'])
        assert fresh.stimulus == native_calls[0]
        assert fresh.goal_summary[1] == {'fresh-only'} and len(fresh.records) == bound.domain.bound
        worker_calls.append(payload)
        return {'disposition': 'outside', 'why': 'only the worker can establish admission'}

    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', audit)
    outcome = parent.audit_record(record, task).as_dict()
    assert original.as_dict() == before and record.trace is original
    attempted = schedule is None and not passive and change not in {
        'no-startup', 'settled', 'queue', 'incomplete', 'foreign'}
    assert len(native_calls) == int(attempted)
    assert len(worker_calls) == int(attempted and change == 'none')
    if change == 'foreign' or (attempted and change == 'wrong-stimulus'):
        assert outcome['disposition'] == 'inconsistent'
    elif change == 'incomplete' or (schedule is None and change == 'capture-invalid') \
            or (attempted and change in {'replay-failed', 'wrong-startup', 'fresh-incomplete'}):
        assert outcome['disposition'] == 'refused'
    else:
        assert outcome['disposition'] == 'outside'
