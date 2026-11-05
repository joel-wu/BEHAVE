"""Free public request coordinates drive source solving and native replay."""

from dataclasses import replace

import pytest
import z3

from behave.sim import api
from behave.sim.core.monitor import INPUT_COMPLETION
from behave.sim.core.record_value import thaw_record_value
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _names

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.cosim
@pytest.mark.parametrize('direct', [False, True])
def test_clockless_request_matches_native_phases_and_all_sampled_outputs(tmp_path, direct):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.native_observation import _read_intervals
    from behave.sim.rtl.settled_request import SettledRequestCodec
    from behave.sim.rtl.source_direct import DirectSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    source = '''module M(input [1:0] a, output reg [1:0] y);
      always_comb begin if (a==2) y=3; else y=a; end
    endmodule'''
    machine, plan, env, template = _prepare(tmp_path, source)
    if direct:
        template = SourceIntervalTemplate(machine.procedure, env, direct=DirectSourceCompiler(machine))
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
                     api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(2), 'y')],
                     api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(SettledRequestCodec(contract, 2, initial=layout), 2)
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver()
    solver.set(timeout=30000)
    goal = _goal(source, plan, 'a==2')
    # Repeated equal inputs are still distinct transactions, not clock edges.
    solver.add(*encoded.constraints, encoded.source.hits[goal],
               request.queues['in'][0][0] == 2, request.queues['in'][1][0] == 2)
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = request.codec.decode(rows)
    startup = request.codec.initial.decode(rows)
    widths = dict(layout.widths)
    trace = artifact.exact_trace(contract, stimulus, observe_names=tuple(widths),
        state_names=tuple(widths), state_widths=widths, startup_state=startup)
    startup.check_trace(trace)
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                  catalogue, expected_plan=None, prefix=False))
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    differences = [encoded.unfinished]
    for phase, (_, observed) in zip(phases, native, strict=True):
        differences.extend(term != (key in observed.goals) for key, term in phase.hits.items())
        differences.extend(term != (key in observed.occurrences) for key, term in phase.occurrences.items())
    names = _names(machine)
    assert len(trace.records) == 2
    for i, record in enumerate(trace.records):
        assert dict(record.outputs)['y'] == 3
        differences.append(encoded.source.slots[i].phases[0].state[names['y']] != 3)
        differences.append(encoded.protocol.accepted[i]['in'] != True)
        differences.append(encoded.protocol.emitted[i]['out'] != True)
        differences.extend(encoded.source.slots[i].state[names[name]] != dict(record.state)[name]
                           for name in widths)
    solver.add(*request.fix(rows), z3.Or(*differences))
    assert solver.check() == z3.unsat


def _request(relation, widths, artifact='a'*64, steps=4, memory_environment=None):
    machine = relation.machine
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact)
    assert dict(layout.widths) == widths
    request = SymbolicRequest(ReadyValidRequestCodec(relation.contract, 2, 1,
                              memory_environment=memory_environment, initial=layout),
                              steps, context=relation.context)
    return request, initial, binding


def test_task_relations_share_native_template_preparation(tmp_path, monkeypatch):
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.symbolic.step_template import StepTemplate

    machine, _, _, _ = _prepare(tmp_path, HISTORY_SOURCE, initialization=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    prepared = object.__new__(PreparedSourceExact)
    prepared.machine, prepared._native_views = machine, None
    first = SourceTaskRelation(template, machine, _simple_contract(), (), complete_state=True)
    normalizations = []
    normalize = StepTemplate.normalized
    def counted(value):
        normalizations.append(value)
        return normalize(value)
    monkeypatch.setattr(StepTemplate, 'normalized', counted)
    native = prepared._native_interval(first.source.relation, member=True)
    for _ in range(4):
        another = SourceTaskRelation(template, machine, _simple_contract(), (), complete_state=True)
        assert another.source.relation is first.source.relation
        assert prepared._native_interval(another.source.relation, member=True) is native
    assert len(normalizations) == 2  # Source and drive, once for all requests.


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_request_construction_resumes_or_closes_one_compiler_scope(tmp_path, monkeypatch, cancel, private):
    from behave.sim.core.construction_checkpoint import finish_construction
    from behave.sim.rtl.source_confluence import compare_histories
    from behave.sim.rtl.source_direct import DirectSourceCompiler, NativeCoverSourceCompiler
    from behave.sim.rtl.source_task import SourceTaskFrame
    from behave.sim.rtl.source_relation import SourceIntervalTemplate, SourceObservation

    machine, plan, _, original = _prepare(tmp_path, HISTORY_SOURCE, initialization=True)
    compiler = NativeCoverSourceCompiler(machine)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges}, direct=compiler).normalized()
    relation = SourceTaskRelation(template, machine, _simple_contract(), (), complete_state=True,
        context=z3.Context() if private else None, observation_scope=SourceObservation.GOAL_PREFIX)
    template_compiler = relation.source.relation.direct
    compilers = []
    for_request = DirectSourceCompiler.for_request
    def own(compiler):
        scoped = for_request(compiler)
        compilers.append(scoped)
        return scoped
    monkeypatch.setattr(DirectSourceCompiler, 'for_request', own)
    request, initial, binding = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    steps = relation.encode_request_steps(request, initial, binding, tag='resumed')
    assert not template_compiler._prefix_scope
    while True:
        first = next(steps)
        if type(first) is SourceTaskFrame:
            break
    compiler = compilers[-1]
    assert first.index == 0 and compiler._prefix_scope
    assert compiler is not template_compiler and not template_compiler._prefix_scope
    # Even two requests through the SAME relation own independent scopes.
    sibling = relation.encode_request_steps(request, initial, binding, tag='sibling')
    try:
        while not isinstance(next(sibling), SourceTaskFrame):
            pass
        assert compilers[-1] is not compiler
        assert compilers[-1]._prefix_scope and compiler._prefix_scope
    finally:
        sibling.close()
    assert not compilers[-1]._prefix_scope and compiler._prefix_scope
    if cancel:
        steps.close()
        assert not compiler._prefix_scope and not compiler._empty_queue_scope
        # A retired partial request cannot leak compiler permissions or state.
        steps = relation.encode_request_steps(request, initial, binding, tag='restarted')
    result = finish_construction(steps)
    assert not compiler._prefix_scope and not compiler._empty_queue_scope
    assert len(result.source.slots) == len(request.slots)
    if not cancel:
        assert result.source.slots[0] is first.source  # Kept, not reconstructed.
    assert INPUT_COMPLETION in result.terminal_hits
    assert result.terminal_assumptions and result.terminal_unresolved
    eager = relation.encode_request(request, initial, binding, tag='eager')
    pair = compare_histories(eager, result, tuple(machine.program.fields),
                             observation_scope=SourceObservation.GOAL_PREFIX)
    solver = z3.Solver(ctx=relation.context)
    solver.set(timeout=30_000)
    solver.add(*pair.constraints)
    assert solver.check() == z3.sat
    solver.add(pair.different)
    assert solver.check() == z3.unsat

    def cancel_synchronous_call():
        if any(item._prefix_scope for item in compilers):
            raise InterruptedError('stop at an owned construction checkpoint')

    with pytest.raises(InterruptedError):
        relation.encode_request(request, initial, binding, check=cancel_synchronous_call)
    assert all(not item._prefix_scope and not item._empty_queue_scope for item in compilers)
    assert relation.source.relation.direct is template_compiler and not template_compiler._prefix_scope


@pytest.mark.parametrize('private', [False, True])
def test_request_frames_include_all_slot_obligations_without_building_later_source(tmp_path, monkeypatch, private):
    from collections import Counter
    from behave.sim.core.observation_product import TransactionProduct
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate, _union_images
    from behave.sim.rtl.source_task import SourceTaskFrame
    from .test_rtl_source_monitors import _monitors
    from .test_rtl_source_transactions import _contract

    machine, _, _, _ = _prepare(tmp_path, HISTORY_SOURCE, initialization=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    contract = _contract(safety=True)
    relation = SourceTaskRelation(template, machine, contract, (), complete_state=True,
        context=z3.Context() if private else None, monitors=_monitors(contract),
        transactions=TransactionProduct(contract))
    request, initial, binding = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    source_type = type(relation.source.relation)
    original, constructed = source_type.encode_phases_steps, []

    def observe(owner, *args, **kwargs):
        if (owner.source is relation.source.relation.source
                and owner.drive is relation.source.relation.drive):
            constructed.append(kwargs['tag'])
        return (yield from original(owner, *args, **kwargs))

    monkeypatch.setattr(source_type, 'encode_phases_steps', observe)
    steps = relation.encode_request_steps(request, initial, binding)
    frames = []
    try:
        while True:
            try:
                frame = next(steps)
            except StopIteration as done:
                result = done.value
                break
            if isinstance(frame, SourceTaskFrame):
                assert len(constructed) == frame.index + 1
                assert frame.index == len(frames)
                assert 'monitor.sink-stability' in frame.hits
                assert 'observation:pair' in frame.hits
                assert INPUT_COMPLETION not in frame.hits
                frames.append(frame)
    finally:
        steps.close()
    assert len(frames) == len(request.slots)
    assert all(frame.source is result.source.slots[i] for i, frame in enumerate(frames))
    roots = (*request.constraints, *request.bind_initial(binding),
             *(term for frame in frames for term in frame.constraints))
    # Every original root occurs exactly once in its owning frame, including
    # monitor definitions and initial protocol/source obligations. No growing
    # copy of preceding roots is smuggled into a later frame.
    assert Counter(term.get_id() for term in roots) == Counter(term.get_id() for term in result.constraints)
    hits = _union_images((*[frame.hits for frame in frames], result.terminal_hits))
    faults = _union_images(frame.domain_faults for frame in frames)
    assumptions = _union_images((*[frame.environment_assumptions for frame in frames], result.terminal_assumptions))
    differences = [z3.Or(*(frame.unfinished for frame in frames)) != result.unfinished]
    for left, right in ((hits, result.hits), (faults, result.domain_faults),
                        (assumptions, result.environment_assumptions)):
        assert left.keys() == right.keys()
        differences.extend(left[key] != right[key] for key in left)
    solver = z3.Solver(ctx=relation.context)
    solver.set(timeout=30_000)
    solver.add(*roots)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.cosim
@pytest.mark.parametrize('active_low,edge', [(False, 'rising'), (True, 'falling')])
def test_free_source_request_finds_state_sequence_and_replays_every_phase(tmp_path, active_low, edge):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.native_observation import _read_intervals

    source = HISTORY_SOURCE.replace('posedge clk', 'negedge clk' if edge == 'falling' else 'posedge clk')
    if active_low:
        source = source.replace('!rst &&', 'rst &&').replace('if(rst)', 'if(!rst)')
    machine, plan, _, template = _prepare(tmp_path, source)
    base = _simple_contract(edge)
    contract = api.TaskContract(base.endpoints, [api.ClockDomain.from_dict(
        {**base.clocks[0].as_dict(), 'reset_active_low': active_low})])
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    widths = {'saved': 2, 'ov': 1, 'y': 2}
    request, initial, binding = _request(relation, widths, artifact.identity)
    encoded = relation.encode_request(request, initial, binding)
    goal = _goal(source, plan, 'saved==1 && a==2')
    solver = z3.Solver()
    solver.set(timeout=30_000)
    # No seeded payload or observed DUT state: ask for a post-startup source
    # goal, then decode the complete request and construction choice.
    solver.add(*encoded.constraints, encoded.source.hits[goal])
    assert solver.check() == z3.sat
    model = solver.model()
    rows = request.rows(model)
    stimulus = request.codec.decode(rows)
    assert dict(stimulus.queues)['in'] == ((1,), (2,))
    startup = request.codec.initial.decode(rows)
    trace = artifact.exact_trace(contract, stimulus, observe_names=(*widths, 'ir'),
                                 state_names=tuple(widths), state_widths=widths, startup_state=startup)
    startup.check_trace(trace)
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                  catalogue, expected_plan=None, prefix=False))
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    differences = {'unfinished': encoded.unfinished}
    for i, (phase, (_, observed)) in enumerate(zip(phases, native, strict=True)):
        differences.update({f'phase[{i}].hit:{key}': term != (key in observed.goals)
                            for key, term in phase.hits.items()})
        differences.update({f'phase[{i}].occurrence:{key}': term != (key in observed.occurrences)
                            for key, term in phase.occurrences.items()})
    names = _names(machine)
    differences.update({f'startup.state:{name}': encoded.startup.state[names[name]] != dict(trace.initial_state)[name]
                        for name in widths})
    assert len(trace.records) == len(rows)
    for i, record in enumerate(trace.records):
        differences[f'slot[{i}].output'] = encoded.source.slots[i].phases[0].state[names['y']] != dict(record.outputs)['y']
        differences.update({f'slot[{i}].state:{name}': encoded.source.slots[i].state[names[name]] != dict(record.state)[name]
                            for name in widths})
        differences[f'slot[{i}].accepted'] = encoded.protocol.accepted[i]['in'] != (dict(record.accepted).get('in') is not None)
        differences[f'slot[{i}].emitted'] = encoded.protocol.emitted[i]['out'] != (dict(record.accepted).get('out') is not None)
    solver.add(*request.fix(rows), z3.Or(*differences.values()))
    result = solver.check()
    assert result == z3.unsat, [key for key, term in differences.items()
                              if result == z3.sat and z3.is_true(solver.model().eval(term))]


@pytest.mark.parametrize('private', [False, True])
def test_source_request_does_not_assume_acceptance_or_success(tmp_path, private):
    from .test_rtl_source_monitors import _monitors

    source = HISTORY_SOURCE.replace('assign ir=!rst && (!ov || ordy);', 'assign ir=0;')
    machine, plan, _, template = _prepare(tmp_path, source)
    context = z3.Context() if private else None
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract,
        tuple(manifest_of(plan.declarations, filename='design.sv')), context=context,
        complete_state=True, monitors=_monitors(contract))
    request, initial, binding = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints, request.lengths['in'] == 1)
    solver.add(encoded.hits[INPUT_COMPLETION])
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    assert request.codec.decode(rows).depth('in') == 1
    assert solver.model().eval(encoded.protocol.pointers[-1]['in']).as_long() == 0
    assert z3.is_true(solver.model().eval(encoded.terminal_hits[INPUT_COMPLETION]))
    assert not z3.is_true(solver.model().eval(encoded.terminal_assumptions['environment.drain-missing']))
    assert not z3.is_true(solver.model().eval(encoded.terminal_unresolved[INPUT_COMPLETION]))


def test_request_requires_whole_state_and_construction_binding(tmp_path):
    machine, _, _, template = _prepare(tmp_path, HISTORY_SOURCE)
    relation = SourceTaskRelation(template, machine, _simple_contract(), (), complete_state=True)
    request, initial, binding = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    with pytest.raises(ValueError, match='every compiler'):
        relation.encode_request(request, replace(initial, bits={}), binding)
    with pytest.raises(ValueError, match='every request coordinate'):
        relation.encode_request(request, initial, {})
    relation.complete_state = False
    with pytest.raises(ValueError, match='complete state'):
        relation.encode_request(request, initial, binding)


@pytest.mark.parametrize('failure', ['fatal', 'unfinished'])
def test_request_domain_keeps_failed_and_unfinished_histories(tmp_path, monkeypatch, failure):
    source = HISTORY_SOURCE
    if failure == 'fatal':
        source = source.replace('endmodule', 'always_comb if(a==2) $fatal(1,"failure"); endmodule')
    machine, plan, _, template = _prepare(tmp_path, source)
    if failure == 'unfinished':
        original = type(machine).phase_event_bounds

        def incomplete(self, *args):
            return (1,) * len(original(self, *args))

        monkeypatch.setattr(type(machine), 'phase_event_bounds', incomplete)
    relation = SourceTaskRelation(template, machine, _simple_contract(),
        tuple(manifest_of(plan.declarations, filename='design.sv')), complete_state=True)
    request, initial, binding = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver()
    solver.add(*encoded.constraints, request.lengths['in'] == 2,
               z3.Not(encoded.alive) if failure == 'fatal' else encoded.unfinished)
    assert solver.check() == z3.sat
    assert request.codec.decode(request.rows(solver.model())).depth('in') == 2
    solver.add(encoded.terminal_hits[INPUT_COMPLETION])
    assert solver.check() == z3.unsat  # A cutoff/fatal prefix is not drain-end evidence.


def test_unfinished_hdl_startup_cannot_release_controls_or_start_transactions(tmp_path):
    source = HISTORY_SOURCE.replace('endmodule',
        'wire [1:0] copy; assign copy=saved^a; endmodule')
    machine, plan, _, template = _prepare(tmp_path, source)
    machine = replace(machine, event_bound=1)
    relation = SourceTaskRelation(template, machine, _simple_contract(),
        tuple(manifest_of(plan.declarations, filename='design.sv')), complete_state=True)
    request, initial, binding = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    encoded = relation.encode_request(request, initial, binding)
    construction, release = encoded.startup.phases
    solver = z3.Solver()
    solver.add(*encoded.constraints, construction.unfinished, request.lengths['in'] == 1)
    assert solver.check() == z3.sat  # Keep the unfinished request, not an empty domain.
    solver.add(z3.Or(release.entered, encoded.continuable, z3.Not(encoded.unfinished),
        *(term for row in encoded.protocol.accepted for term in row.values()),
        *encoded.source.hits.values()))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('latency', [1, 3])
def test_free_request_uses_declared_memory_initialization_and_delayed_responses(tmp_path, latency):
    from behave.sim.core.memory import MemoryEnvironment, MemoryRun
    from .test_rtl_source_task_relation import SOURCE, _contract

    machine, plan, _, template = _prepare(tmp_path, SOURCE)
    relation = SourceTaskRelation(template, machine, _contract(),
        tuple(manifest_of(plan.declarations, filename='design.sv')), complete_state=True)
    memory = MemoryEnvironment([MemoryRun('ram', initial={1: 201}, read_latency=latency,
                                        ready={None: [1]*8})])
    request, initial, binding = _request(relation, {'busy': 1, 'delay_count': 2, 'ov': 1, 'y': 8},
                                        steps=8, memory_environment=memory)
    encoded = relation.encode_request(request, initial, binding)
    goal = _goal(SOURCE, plan, 'ram_resp_rdata==201')
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 1, encoded.source.hits[goal])
    assert solver.check() == z3.sat
    stimulus = request.codec.decode(request.rows(solver.model()))
    assert stimulus.memory_environment == memory
    assert dict(stimulus.queues)['in'] == ((1,),)
