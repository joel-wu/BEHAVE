"""Complete source-worker goals use native ownership and real shared monitors."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
import z3

from behave.sim import api
from behave.sim.analysis_budget import analysis_scope
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.core.reset_monitor import ResetRestorationProvider
from behave.sim.goal_policy import evaluation_goal_policy
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.exact_job_context import RTLExactJobContext
from behave.sim.rtl.exact_job_instance import PreparedSourceExact
from behave.sim.stimulus_set import GenerationSpec

from .test_rtl_source_history import HISTORY_SOURCE
from .test_rtl_source_native_audit import _audit
from .test_rtl_source_transactions import _contract

pytestmark = [pytest.mark.cosim, pytest.mark.solver]


def _worker(root, source, contract, steps, *, schedule=None, memory=None):
    rtl = root/'design.sv'
    rtl.write_text(source)
    artifact = RTLArtifact(rtl, 'M', root/'native', timeout_s=120)
    artifact.bind_observation(contract)
    frontend = artifact.frontend_catalogue()
    complete = evaluation_goal_policy(contract, frontend, artifact_kind='rtl').manifest
    context = RTLExactJobContext(artifact, contract, GenerationSpec(steps, 2, 1, 0),
        frontend, complete, workdir=root/'context', clock_schedule=schedule)
    if memory is not None:
        context.bind_memory_environment(memory)
    with analysis_scope(120):
        layout = context.prepare_source()
    assert not isinstance(layout, Refusal), layout.as_dict()
    prepared = PreparedSourceExact(context.source_payload())
    relation, request, encoded = prepared.bind_request(steps)
    assert set(encoded.hits) == set(complete)
    return context, prepared, relation, request, encoded


@pytest.mark.parametrize('direct', [False, True])
def test_clockless_worker_queries_decode_and_native_audit(tmp_path, direct):
    from behave.sim.core.instance import Verdict
    from behave.sim.core.stimulus import Stimulus
    from behave.sim.rtl import analysis_job
    from behave.sim.rtl.startup_state import RTLStartupState

    source = '''module M(input [1:0] a, output reg [1:0] y);
      always_comb begin if (a==2) y=3; else y=a; end
    endmodule'''
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
                     api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(2), 'y')],
                     api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])

    def value(reply, operation):
        result = analysis_job.decode_analysis_reply(reply, operation)
        assert not isinstance(result, Refusal), result.as_dict()
        return result

    with analysis_scope(120):
        context, *_ = _worker(tmp_path, source, contract, 2)
        admitted = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract))
        worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 2,
            'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}, direct_source=direct)
        value(worker.binding(), 'bind')
        assert worker.exact.domain.initial['reset_hold_cycles'] is None
        assert worker.exact.domain.initial['reset_prefix_inside_horizon'] is False
        source_goal = next(name for name, goal in context.frontend_catalogue.items() if goal.cls == 'coverage')
        goals = [source_goal, 'monitor.input-completion', 'monitor.output-without-transaction']
        verdict = Verdict.from_record(value(worker.call('query', {'goals': goals, 'timeout_ms': 30000}), 'query'))
        assert not verdict.undecided
        assert set(verdict.reachable) == {source_goal}
        assert set(verdict.unreachable) == {'monitor.input-completion', 'monitor.output-without-transaction'}
        checked = 0
        for query in verdict.queries:
            if query.witness is None:
                continue
            decoded = value(worker.call('decode', {'rows': query.witness}), 'decode')
            stimulus = Stimulus.from_dict(decoded['stimulus'])
            assert len(stimulus.slots) == 2 and all(not slot.ticks for slot in stimulus.slots)
            trace = context.source_trace(stimulus, startup_state=RTLStartupState.from_dict(decoded['initialization']))
            value(worker.call('audit-request', {'rows': query.witness, 'trace': trace.as_dict(),
                'domain': worker.exact.domain.digest()}), 'audit-request')
            checked += 1
        assert checked > 0


@pytest.fixture(scope='module', params=['scalar', 'array', 'broken-reset', 'falling-low-reset'])
def task(tmp_path_factory, request):
    root = tmp_path_factory.mktemp('source-task-worker')
    mode = request.param
    source = HISTORY_SOURCE
    if mode == 'array':
        source = source.replace('reg [1:0] saved;', 'reg [1:0] saved[2:1];')
        source = source.replace('saved<=0;', 'saved[2]<=0; saved[1]<=0;')
        source = source.replace('saved==1', '(saved[2]^saved[1])==1')
        source = source.replace('saved<=a;', 'saved[2]<=a; saved[1]<=saved[2];')
    elif mode == 'broken-reset':
        source = source.replace('saved<=0;', 'saved<=saved;')
    elif mode == 'falling-low-reset':
        source = source.replace('posedge clk', 'negedge clk').replace('!rst &&', 'rst &&').replace(
            'if(rst)', 'if(!rst)')
    image = _contract().as_dict()
    # This RTL clears its response register on reset. Declare the matching
    # request semantics; unspecified in-flight reset has a separate refusal test.
    image['endpoints'][0]['reset_policy'] = 'clears'
    image['clocks'][0]['reset_restores'] = True
    if mode == 'falling-low-reset':
        image['clocks'][0].update(edge='falling', reset_active_low=True)
    contract = api.TaskContract.from_dict(image)
    schedule = None
    if mode == 'falling-low-reset':
        from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

        schedule = GlobalSchedule([ClockTiming('c', 2, clock='clk', reset='rst',
                                               edge='falling', reset_active_low=True)],
            [GlobalEventSlot(2*i, event=api.EventSlot(ticks=('c',), resets=('c',) if i in (0, 4) else (),
                offers=('in',) if i == 1 else (), controls={'rst':int(i not in (0, 4)), 'ordy':1}))
             for i in range(6)])
    return mode, *_worker(root, source, contract, 6, schedule=schedule)


def test_complete_source_worker_binds_protocol_reset_transaction_and_terminal(task):
    mode, context, prepared, relation, request, encoded = task
    assert set(encoded.hits) == set(context.complete_catalogue)
    assert prepared.ownership == context.artifact.state_ownership(context.contract)
    assert set(relation.monitor_fields) == set(prepared.ownership.names)
    assert relation.transactions.goals
    assert 'monitor.input-completion' in encoded.hits
    scenario = ResetRestorationProvider.scenario_goal('c')
    violation = ResetRestorationProvider.violation_goal('c')
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 1,
        request.queues['in'][0][0] == 1, *(term == 0 for term in prepared.initial_binding.values()))
    for i in range(6):
        solver.add(request.resets['c'][i] == (i in (0, 4)),
                   request.actions[i].controls['ordy'], request.actions[i].offers['in'] == (i == 1))
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_true(model.eval(encoded.hits[scenario]))
    assert z3.is_true(model.eval(encoded.hits[violation])) == (mode == 'broken-reset')
    rows = request.rows(model)
    trace = context.source_trace(request.codec.decode(rows), startup_state=prepared.initial_layout.decode(rows))
    result, _ = _audit(relation.compare_native(request, rows, trace, context.frontend_catalogue,
                                               observation=prepared.observation))
    assert result.status.value == 'agreed', result.as_dict()


def test_fixed_calendar_inactive_reset_keeps_goals_without_state_partition(tmp_path, monkeypatch):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

    image = _contract().as_dict()
    image['endpoints'][0]['reset_policy'] = 'clears'
    image['clocks'][0]['reset_restores'] = True
    contract = api.TaskContract.from_dict(image)
    schedule = GlobalSchedule([ClockTiming('c', 1, clock='clk', reset='rst')], [
        GlobalEventSlot(i, event=api.EventSlot(ticks=('c',), resets=('c',) if i == 0 else (),
            offers=('in',) if i == 1 else (), controls={'rst':int(i == 0), 'ordy':1}))
        for i in range(6)])

    def unexpected_ownership(*args, **kwargs):
        pytest.fail('inactive fixed-schedule reset cannot require a state partition')

    monkeypatch.setattr(RTLArtifact, 'state_ownership', unexpected_ownership)
    context, prepared, relation, request, encoded = _worker(
        tmp_path, HISTORY_SOURCE, contract, 6, schedule=schedule)
    assert prepared.ownership is None
    assert not relation.monitor_fields
    scenario = ResetRestorationProvider.scenario_goal('c')
    violation = ResetRestorationProvider.violation_goal('c')
    assert {scenario, violation} <= set(encoded.hits) == set(context.complete_catalogue)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat  # Not an empty domain masquerading as UNSAT.
    solver.push()
    solver.add(z3.Or(encoded.hits[scenario], encoded.hits[violation]))
    assert solver.check() == z3.unsat
    solver.pop()
    solver.add(request.lengths['in'] == 1, request.queues['in'][0][0] == 1,
               *(term == 0 for term in prepared.initial_binding.values()))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    trace = context.source_trace(request.codec.decode(rows),
                                 startup_state=prepared.initial_layout.decode(rows))
    result, _ = _audit(relation.compare_native(request, rows, trace, context.frontend_catalogue,
                                               observation=prepared.observation))
    assert result.status.value == 'agreed', result.as_dict()


@pytest.mark.parametrize('mutation', ['missing', 'top', 'width', 'unmapped'])
def test_source_reset_partition_cannot_be_missing_or_relabelled(task, tmp_path, mutation):
    _, context, _, _, _, _ = task
    payload = copy.deepcopy(context.source_payload())
    image = json.loads(Path(payload['relation_path']).read_bytes())
    if mutation == 'missing':
        image['state_ownership'] = None
    elif mutation == 'top':
        image['state_ownership']['top'] = 'Other'
    elif mutation == 'width':
        image['state_ownership']['fields'][0]['width'] += 1
    else:
        for field in image['state_ownership']['fields']:
            field['name'] = 'missing.' + field['name']
    body = json.dumps(image).encode()
    path = tmp_path/'source.json'
    path.write_bytes(body)
    payload.update(relation_path=str(path), source_image_digest=hashlib.sha256(body).hexdigest())
    with pytest.raises(Refused):
        PreparedSourceExact(payload).bind_request(6)


def test_internal_reset_state_does_not_require_legacy_top_port_observability(task):
    _, _, prepared, relation, _, _ = task
    internal = {field.name for field in prepared.ownership.fields if not field.observable}
    assert internal
    assert internal <= relation.monitor_native_names.keys()


def test_complete_worker_whole_domain_audit_keeps_reset_and_transaction_obligations(task):
    from .test_rtl_source_confluence import _all_phases
    from .test_rtl_source_confluence import _audit as domain_audit

    _, _, _, relation, request, _ = task
    pair = relation.compare_request(request)
    audit, calls = domain_audit(pair)
    assert audit.status.value == 'agreed', (audit.as_dict(), calls)
    assert calls == _all_phases(pair)
    assert pair.left.monitor_states and pair.right.transaction_states
    assert pair.left.hits.keys() == pair.right.hits.keys()
    # Agreement is a capability check, not a claim that the DUT has no bug:
    # the broken-reset fixture is intentionally still admitted for queries.


def test_owned_comparison_reuses_only_the_complete_left_history(task, monkeypatch):
    from .test_rtl_source_confluence import _all_phases
    from .test_rtl_source_confluence import _audit as domain_audit

    _, _, prepared, relation, request, encoded = task
    original, calls = relation.encode_request, []

    def encode(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(relation, 'encode_request', encode)
    pair = prepared.compare_request(request, check=lambda: None)
    assert pair.left is encoded and calls == [pair.right]
    assert pair.left is not pair.right
    audit, phases = domain_audit(pair)
    assert audit.status.value == 'agreed', (audit.as_dict(), phases)
    assert phases == _all_phases(pair)
    assert pair.left.hits.keys() == pair.right.hits.keys()
    # The original physical-coordinate binding is not renamed or discarded.
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*pair.constraints, *(term == 0 for term in prepared.initial_binding.values()))
    assert solver.check() == z3.sat


def test_owned_comparison_rejects_another_request_before_building(task, monkeypatch):
    _, _, prepared, relation, request, _ = task
    monkeypatch.setattr(relation, 'bind_native_request',
        lambda *_args, **_kwargs: pytest.fail('foreign request cannot build a comparison'))
    for foreign in (None, copy.copy(request)):
        with pytest.raises(ValueError, match="instance's bound request"):
            prepared.compare_request(foreign, check=lambda: None)


def test_owned_comparison_cancellation_keeps_the_original_history(task, monkeypatch):
    _, _, prepared, relation, request, encoded = task
    calls = []

    def interrupted(*_args, **_kwargs):
        calls.append(None)
        return object()

    def check():
        if calls:
            raise InterruptedError('right history cancelled')

    monkeypatch.setattr(relation, 'bind_native_request', interrupted)
    with pytest.raises(InterruptedError, match='right history cancelled'):
        prepared.compare_request(request, check=check)
    assert prepared.bind_request(6) == (relation, request, encoded)


def test_owned_comparison_keeps_private_startup_independent(tmp_path):
    from .test_rtl_source_confluence import _audit as domain_audit
    from .test_rtl_source_request_audit import _pair

    source = HISTORY_SOURCE.replace('reg [1:0] saved;',
        'reg [1:0] saved; initial saved=ir ? 1 : 2;')
    # This deliberately ambiguous initial process is a source-level rejection
    # oracle; the synthesis ownership frontend rejects it before native build.
    relation, request = _pair(tmp_path, source)
    initial = relation.initial_state(dict.fromkeys(relation.machine.program.fields))
    _, binding = initial.request_layout(relation.machine.program, request.codec.initial.artifact)
    encoded = relation.encode_request(request, initial, binding)
    prepared = PreparedSourceExact.__new__(PreparedSourceExact)
    prepared.machine = relation.machine
    prepared._requests = {3: (relation, request, encoded)}
    pair = prepared.compare_request(request, check=lambda: None)
    assert pair.left is encoded
    audit, phases = domain_audit(pair)
    assert audit.status.value == 'refused'
    assert phases[-1] == 'confluence:startup'
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*pair.constraints, pair.different)
    assert solver.check() == z3.sat


def test_complete_source_binding_refuses_an_omitted_task_goal(task):
    _, context, _, _, _, _ = task
    payload = copy.deepcopy(context.source_payload())
    payload['complete_catalogue'] = context.frontend_catalogue.as_dict()
    with pytest.raises(Refused, match='complete frozen catalogue'):
        PreparedSourceExact(payload).bind_request(6)


@pytest.mark.parametrize('horizon', [0, -1, True, 2.5])
def test_source_task_horizon_is_exact_and_positive(task, horizon):
    with pytest.raises(ValueError, match='positive exact horizon'):
        task[2].bind_request(horizon)


def test_source_task_binding_obeys_preparation_cancellation_even_when_cached(task):
    from unittest.mock import patch

    from behave.isolation.preparation_control import PreparationCancelled

    with patch('behave.isolation.preparation_control.check_preparation',
               side_effect=PreparationCancelled('test cancellation')), \
            pytest.raises(PreparationCancelled, match='test cancellation'):
        task[2].bind_request(6)


@pytest.mark.parametrize('latency', [1, 3])
@pytest.mark.parametrize('scheduled', [False, True])
@pytest.mark.parametrize('order_sensitive', [False, True])
def test_complete_source_worker_carries_delayed_external_memory(tmp_path, latency, scheduled, order_sensitive):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.core.memory import MemoryEnvironment, MemoryRun

    from .test_rtl_source_task_relation import SOURCE
    from .test_rtl_source_task_relation import _contract as memory_contract

    contract = memory_contract()
    source = SOURCE if order_sensitive else '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [7:0] y,input ordy,
      output ram_req_valid,output [1:0] ram_req_addr,
      input ram_req_ready,ram_resp_valid,input [7:0] ram_resp_rdata);
      assign ir=!rst;
      assign ram_req_valid=iv && !rst;
      assign ram_req_addr=a;
      always @(posedge clk) begin
        if(rst) begin ov<=0; y<=0; end
        else begin ov<=ram_resp_valid; if(ram_resp_valid) y<=ram_resp_rdata; end
      end
    endmodule'''
    steps = 7
    slots = [api.EventSlot(ticks=('c',), resets=('c',) if i == 0 else (),
        offers=('in',) if i == 1 else (), controls={'rst':int(i == 0), 'ordy':1})
        for i in range(steps)]
    schedule = (GlobalSchedule([ClockTiming('c', 2, clock='clk', reset='rst')],
        [GlobalEventSlot(2*i, event=slot) for i, slot in enumerate(slots)]) if scheduled else None)
    memory = MemoryEnvironment([MemoryRun('ram', initial={1:201}, read_latency=latency,
                                        ready={None:[1]*steps})])
    context, prepared, relation, request, encoded = _worker(tmp_path, source, contract,
        steps, schedule=schedule, memory=memory)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 1,
               request.queues['in'][0][0] == 1, *(term == 0 for term in prepared.initial_binding.values()))
    for i in range(steps):
        solver.add(request.resets['c'][i] == (i == 0), request.actions[i].controls['ordy'],
                   request.actions[i].offers['in'] == (i == 1))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    trace = context.source_trace(request.codec.decode(rows), startup_state=prepared.initial_layout.decode(rows))
    assert [payload for record in trace.records for name, payload in record.accepted
            if name == 'out' and payload is not None] == [(201,)]
    comparison = relation.compare_native(request, rows, trace, context.frontend_catalogue,
                                        observation=prepared.observation)
    audit, calls = _audit(comparison)
    assert calls == ['feasibility', 'completeness', 'definedness', 'memory', 'membership', 'agreement']
    if order_sensitive:
        # The original fixture has transient source events sensitive to legal
        # internal order. Preserve that refusal, and prove it is *only* events:
        # the complete state, protocol, reset monitors, payloads and RAM agree.
        assert audit.status.value == 'refused', audit.as_dict()
        assert audit.as_dict()['refusal']['kind'] == 'rtl-source-native-agreement'
        solver = z3.Solver()
        solver.set(timeout=30_000)
        solver.add(*comparison.constraints, z3.Or(*(term for name, term in comparison.differences.items()
                    if '.hit:' not in name and '.occurrence:' not in name)))
        assert solver.check() == z3.unsat
    else:
        assert audit.status.value == 'agreed', audit.as_dict()


@pytest.mark.parametrize('independent', [False, True])
def test_complete_source_worker_binds_each_clock_reset_partition(tmp_path, independent):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

    clocks = [api.ClockDomain('A', 'clk_A', 'rst_A', reset_restores=True)]
    if independent:
        clocks.append(api.ClockDomain('B', 'clk_B', 'rst_B', edge='falling', reset_restores=True))
    endpoints, ports, bodies = [], [f'input {c.clock},{c.reset}' for c in clocks], []
    for index, name in enumerate(('a', 'b')):
        clock = clocks[index] if independent else clocks[0]
        endpoints.extend((
            api.Endpoint('in_'+name, 'in', [api.Field('x', api.FiniteType(2), 'x_'+name)],
                         api.ready_valid('in', 'iv_'+name, 'ir_'+name), clock.name, kind='transaction'),
            api.Endpoint('out_'+name, 'out', [api.Field('y', api.FiniteType(2), 'y_'+name)],
                         api.ready_valid('out', 'ov_'+name, 'r_'+name), clock.name, kind='transaction')))
        ports.append(f'input iv_{name},r_{name},input [1:0] x_{name},output ir_{name},'
                     f'output reg ov_{name},output reg [1:0] y_{name}')
        edge = 'negedge' if clock.edge == 'falling' else 'posedge'
        bodies.append(f'''assign ir_{name}=!{clock.reset} && (!ov_{name} || r_{name});
          always @({edge} {clock.clock}) begin
            if({clock.reset}) begin ov_{name}<=0; y_{name}<=0; end
            else if(iv_{name} && ir_{name}) begin ov_{name}<=1; y_{name}<=x_{name}; end
            else if(r_{name}) ov_{name}<=0;
          end''')
    source = 'module M('+','.join(ports)+');\n'+'\n'.join(bodies)+'\nendmodule'
    contract = api.TaskContract(endpoints, clocks)
    names = tuple(c.name for c in clocks)
    ticks = (names, ('A',), ('B',), ('A',), names) if independent else (names,)*5
    slots = [api.EventSlot(ticks=active, resets=names if i in (0, 4) else (),
        offers=tuple(e.name for e in contract.inputs() if e.clock in active) if i in (1, 2) else (),
        controls={**{c.reset:int(i in (0, 4)) for c in clocks}, 'r_a':1, 'r_b':1})
        for i, active in enumerate(ticks)]
    times = (0, 4, 6, 8, 12) if independent else (0, 2, 4, 6, 8)
    schedule = GlobalSchedule([ClockTiming(c.name, (4 if c.name == 'A' else 6) if independent else 2,
        clock=c.clock, reset=c.reset, edge=c.edge) for c in clocks],
        [GlobalEventSlot(time, event=slot) for time, slot in zip(times, slots, strict=True)])
    context, prepared, relation, request, encoded = _worker(tmp_path, source, contract, 5, schedule=schedule)
    assert {name:config.state_fields for name, config in relation.monitors.resets} == dict(prepared.ownership.by_domain())
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, *(length == 1 for length in request.lengths.values()),
               *(term == 0 for term in prepared.initial_binding.values()))
    assert solver.check() == z3.sat
    model = solver.model()
    for clock in clocks:
        assert z3.is_true(model.eval(encoded.hits[ResetRestorationProvider.scenario_goal(clock.name)]))
        assert z3.is_false(model.eval(encoded.hits[ResetRestorationProvider.violation_goal(clock.name)]))
    rows = request.rows(model)
    trace = context.source_trace(request.codec.decode(rows), startup_state=prepared.initial_layout.decode(rows))
    audit, _ = _audit(relation.compare_native(request, rows, trace, context.frontend_catalogue,
                                               observation=prepared.observation))
    assert audit.status.value == 'agreed', audit.as_dict()
