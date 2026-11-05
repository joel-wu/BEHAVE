"""Reachability-prefix projection is not permission to ignore source events."""

from types import SimpleNamespace
from dataclasses import replace

import pytest
import z3

from behave.sim.core.audit import AuditCheckStatus
from behave.sim.rtl.source_confluence import compare_histories
from behave.sim.rtl.source_relation import SourceObservation, source_event_images

from . import test_rtl_source_native_audit as native_fixtures
from .test_rtl_source_confluence import _comparison
from .test_rtl_source_native_audit import _audit

pytestmark = pytest.mark.solver
PREFIX = SourceObservation.GOAL_PREFIX
witness = native_fixtures.witness
SOURCE = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
  output [1:0] y,input ordy);
  reg busy;
  wire consumed,accepted;
  assign consumed=ov & ordy;
  assign ir=~busy | consumed;
  assign ov=busy;
  assign accepted=iv & ir;
  assign y=0;
  always @(posedge clk) busy<=1'b1;
endmodule'''


@pytest.mark.eda('slang')
def test_native_cover_state_only_history_preserves_changing_guard_values(tmp_path, monkeypatch):
    from behave.sim.rtl.source_direct import DirectSourceCompiler, NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.source_task import SourceTaskRelation
    from behave.sim.rtl.protocol_relation import SymbolicRequest
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_source_history import _simple_contract
    from .test_rtl_source_memory_relation import _prepare

    source = SOURCE.replace('output [1:0] y', 'output logic [1:0] y').replace(
        'assign y=0;', 'always_comb if(accepted) y=a; else y=0;')
    machine, _, _, original = _prepare(tmp_path, source, initialization=True)
    compiler = NativeCoverSourceCompiler(machine)
    optimized = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges}, direct=compiler).normalized()
    contract = _simple_contract(reset_policy='clears')
    old, new = [SourceTaskRelation(template, machine, contract, (),
        complete_state=True, occurrences=False, observation_scope=PREFIX)
        for template in (original, optimized)]
    initial = old.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, 'a'*64)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 1, 0, initial=layout), 1)
    left = old.encode_request(request, initial, binding, tag='original')
    # Each request binds its own compiler copy, so watch the state-only view being
    # built instead of reading it off the shared template's compiler.
    views = []
    settled = DirectSourceCompiler.for_settled_state

    def state_view(*args, **kwargs):
        views.append(settled(*args, **kwargs))
        return views[-1]

    monkeypatch.setattr(DirectSourceCompiler, 'for_settled_state', staticmethod(state_view))
    right = new.encode_request(request, initial, binding, tag='optimized')
    assert views
    assert all(len(phase.actions) == 1 for slot in right.source.slots for phase in slot.phases)
    pair = compare_histories(left, right, tuple(machine.program.fields), observation_scope=PREFIX)
    solver = z3.Solver()
    solver.set(timeout=30000)
    solver.add(*pair.constraints)
    assert solver.check() == z3.sat
    solver.add(pair.different)
    assert solver.check() == z3.unsat


def _prefix(pair):
    return compare_histories(pair.left, pair.right, tuple(pair.left.state), observation_scope=PREFIX)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.eda('slang')
def test_redundant_event_order_does_not_change_goal_prefix(tmp_path, private):
    pair, solver, queues, actions, _, _ = _comparison(
        tmp_path, SOURCE, steps=1, private=private, full_catalogue=True)
    solver.set(timeout=30_000)
    solver.add(queues['in'][0][0] == 0, actions[0].offers['in'], actions[0].controls['ordy'])
    assert solver.check() == z3.sat
    solver.push()
    solver.add(pair.different)
    assert solver.check() == z3.sat  # Strict phase-event policy still notices it.
    solver.pop()
    prefix = _prefix(pair)
    assert prefix.observation_scope is PREFIX
    solver.add(prefix.different)
    assert solver.check() == z3.unsat  # Includes states, protocol and individual occurrences.


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.eda('slang')
def test_goal_prefix_does_not_hide_order_sensitive_branch_hits(tmp_path, private):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg [1:0] saved;
      assign ir=!rst && ordy;
      always @(posedge clk) saved=a;
      always @(posedge clk) begin
        if(saved==1) y=2; else y=2;
        ov=iv;
      end
    endmodule'''
    pair, solver, queues, actions, _, _ = _comparison(
        tmp_path, source, 'saved==1', steps=1, private=private)
    solver.set(timeout=30_000)
    solver.add(queues['in'][0][0] == 1, actions[0].offers['in'], actions[0].controls['ordy'])
    prefix = _prefix(pair)
    solver.add(z3.Or(*(term for name, term in prefix.differences.items() if '.hits.' in name)))
    assert solver.check() == z3.sat


def test_prefix_preserves_first_hit_boundary_and_each_occurrence():
    def phase(a, b):
        return SimpleNamespace(hits={'goal': z3.BoolVal(a or b)},
            occurrences={'a': z3.BoolVal(a), 'b': z3.BoolVal(b)})
    left = list(source_event_images((phase(True, False), phase(False, True)), PREFIX))
    right = list(source_event_images((phase(True, False), phase(False, False)), PREFIX))
    assert z3.is_true(z3.simplify(left[-1][0]['goal'] == right[-1][0]['goal']))
    assert z3.is_false(z3.simplify(left[-1][1]['b'] == right[-1][1]['b']))
    delayed = list(source_event_images((phase(False, False), phase(True, True)), PREFIX))
    assert z3.is_true(z3.simplify(left[-1][0]['goal'] == delayed[-1][0]['goal']))
    assert z3.is_false(z3.simplify(left[0][0]['goal'] == delayed[0][0]['goal']))
    with pytest.raises(TypeError, match='SourceObservation'):
        list(source_event_images((phase(True, False),), 'goal-prefix/1'))
    with pytest.raises(ValueError, match='catalogue'):
        list(source_event_images((phase(True, False), SimpleNamespace(hits={}, occurrences={})), PREFIX))
    def cancel():
        raise InterruptedError('cancel prefix construction')
    with pytest.raises(InterruptedError, match='prefix'):
        list(source_event_images((phase(True, False),), PREFIX, check=cancel))


@pytest.mark.cosim
def test_native_prefix_keeps_complete_goal_union_and_distinct_proof_scope(witness):
    relation, request, rows, trace, catalogue = witness
    strict = relation.compare_native(request, rows, trace, catalogue)
    prefix = relation.compare_native(request, rows, trace, catalogue, observation_scope=PREFIX)
    audit, _ = _audit(prefix)
    assert audit.status is AuditCheckStatus.AGREED, audit
    image = prefix.native_image
    assert image['initial_hits'] == strict.native_image['initial_hits']
    assert set.union(set(image['initial_hits']), *image['hit_sets']) == set.union(
        set(strict.native_image['initial_hits']), *strict.native_image['hit_sets'])
    seen = set()
    for state in image['states']:
        assert state['source.observation_scope'] == PREFIX.value
        assert 'source.phases' not in state
        for phase in state['source.goal_prefix']:
            assert seen <= set(phase['goals'])
            seen = set(phase['goals'])
    assert not prefix.same_obligation(strict, check=lambda: None)


@pytest.mark.parametrize('change', ['state', 'first-hit'])
@pytest.mark.cosim
def test_prefix_native_audit_still_rejects_incorrect_evidence(witness, change):
    from behave.sim.core.stimulus import AuditedTrace

    relation, request, rows, trace, catalogue = witness
    image = trace.as_dict()
    if change == 'state':
        image['records'][2]['state'] = dict(image['records'][2]['state'])
        image['records'][2]['state']['saved'] ^= 1
    else:
        # Remove startup events, not only a redundant later occurrence.
        from behave.sim.rtl.native_evidence_codec import pack, unpack

        interval = image['source_evidence']['intervals'][0]
        bits = unpack(interval['events'])
        assert any(bits)
        interval['events'] = pack(bytes(len(bits)))
        interval['callbacks'] = 0
    changed = AuditedTrace.from_dict(image)
    prefix = relation.compare_native(request, rows, changed, catalogue, observation_scope=PREFIX)
    audit, _ = _audit(prefix)
    assert audit.status is AuditCheckStatus.INCONSISTENT, audit


@pytest.mark.cosim
@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('stable_mux', [False, True])
def test_native_reconvergent_fanout_admits_prefix_not_phase_events(tmp_path, direct, stable_mux):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.protocol_relation import SymbolicRequest
    from behave.sim.rtl.source_goals import manifest_of
    from behave.sim.rtl.source_task import SourceTaskRelation
    from behave.sim.rtl.task_request import ReadyValidRequestCodec

    from .test_rtl_source_history import _simple_contract
    from .test_rtl_source_memory_relation import _prepare

    source = SOURCE.replace('assign y=0;',
        'assign y=a[0] ? {1\'b0,accepted} : {1\'b0,consumed};') if stable_mux else SOURCE
    machine, plan, _, template = _prepare(tmp_path, source, initialization=True)
    if direct:
        from behave.sim.rtl.source_direct import DirectSourceCompiler
        from behave.sim.rtl.source_relation import SourceIntervalTemplate
        template = SourceIntervalTemplate(machine.procedure,
            {**machine.procedure.state_ranges, **machine.input_ranges},
            direct=DirectSourceCompiler(machine)).normalized()
    contract = _simple_contract()
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
        complete_state=True, occurrences=True,
        observation_scope=PREFIX if direct else SourceObservation.PHASE_EVENTS)
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 1, 0, initial=layout), 1)
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 1, request.queues['in'][0][0] == int(stable_mux),
               request.actions[0].offers['in'], request.actions[0].controls['ordy'],
               z3.Not(request.resets['c'][0]), *(value == 0 for value in binding.values()))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    trace = artifact.exact_trace(contract, request.codec.decode(rows),
        observe_names=('y', 'ov', 'ir'), state_names=tuple(layout.widths),
        state_widths=dict(layout.widths), startup_state=layout.decode(rows))
    if direct:
        with pytest.raises(ValueError, match='phase-event'):
            relation.compare_native(request, rows, trace, catalogue)
    else:
        strict, _ = _audit(relation.compare_native(request, rows, trace, catalogue))
        assert strict.status is AuditCheckStatus.REFUSED
        assert strict.kind == 'rtl-source-native-agreement'
    prefix, _ = _audit(relation.compare_native(request, rows, trace, catalogue, observation_scope=PREFIX))
    assert prefix.status is AuditCheckStatus.AGREED, prefix


@pytest.mark.eda('slang')
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('stable_mux', [False, True])
def test_direct_complete_request_preserves_prefix_and_physical_state(tmp_path, private, stable_mux):
    from behave.sim.rtl.source_direct import DirectSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.source_task import SourceTaskRelation
    from behave.sim.rtl.protocol_relation import SymbolicRequest
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from behave.sim.core.refusal import Refused
    from .test_rtl_source_history import _simple_contract
    from .test_rtl_source_memory_relation import _prepare

    source = SOURCE.replace('assign y=0;',
        'assign y=a[0] ? {1\'b0,accepted} : {1\'b0,consumed};') if stable_mux else SOURCE
    machine, plan, _, template = _prepare(tmp_path, source, initialization=True)
    context = z3.Context() if private else None
    contract = _simple_contract(reset_policy='clears')
    compiler = DirectSourceCompiler(machine)
    direct = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges}, direct=compiler).normalized()
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    old = SourceTaskRelation(template, machine, contract, goals, context=context,
                             complete_state=True, occurrences=True)
    new = SourceTaskRelation(direct, machine, contract, goals, context=context,
        complete_state=True, occurrences=True, observation_scope=PREFIX)
    initial = old.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, 'a' * 64)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 1, 1, initial=layout),
                              3, context=context)
    left = old.encode_request(request, initial, binding, tag='ordinary')
    right = new.encode_request(request, initial, binding, tag='direct')
    pair = compare_histories(left, right, tuple(machine.program.fields), observation_scope=PREFIX)
    solver = z3.Solver(ctx=context)
    solver.set(timeout=30_000)
    solver.add(*pair.constraints)
    assert solver.check() == z3.sat
    # Membership and equivalence are separate full-formula obligations.
    # Avoid carrying the membership model's incremental solver state into
    # the second check; do not narrow inputs or increase its time budget.
    solver = z3.Solver(ctx=context)
    solver.set(timeout=30_000)
    solver.add(*pair.constraints)
    solver.add(pair.different)
    assert solver.check() == z3.unsat
    assert compiler._regions() and not compiler._prefix_scope
    # A previously cached whole-prefix plan cannot be reused by a strict
    # arbitrary-boundary caller, nor claim stronger phase-event evidence.
    with pytest.raises(Refused, match='coactive-dependency'):
        compiler.plan(frozenset(compiler._regions()))
    with pytest.raises(ValueError, match='phase-event'):
        new.require_observation_scope(SourceObservation.PHASE_EVENTS)
    with pytest.raises(RuntimeError, match='cancel scope'):
        with compiler.request_scope(initial, PREFIX):
            raise RuntimeError('cancel scope')
    assert not compiler._prefix_scope
    index = next(iter(compiler._regions()))
    pending = machine.pending[index]
    zero = (z3.BoolVal(False, ctx=context) if z3.is_bool(initial.state[pending])
            else z3.BitVecVal(0, initial.state[pending].size(), ctx=context))
    incomplete = replace(initial, state={**initial.state, pending: zero})
    with pytest.raises(Refused, match='prefix-startup-unbound'):
        with compiler.request_scope(incomplete, PREFIX):
            pytest.fail('an incomplete startup cannot authorize prefix settling')


@pytest.mark.eda('slang')
@pytest.mark.parametrize('consumer', [
    'always @(posedge x) q<=a;',
    'always_comb begin if(x) q=a; else q=0; end',
])
def test_prefix_regions_do_not_swallow_glitch_or_branch_consumers(tmp_path, consumer):
    from behave.sim.rtl.source_direct import DirectSourceCompiler
    from .test_rtl_source_scheduler import _lower, _names

    machine, _ = _lower(tmp_path, f'''module M(input a,b,output reg q);
      wire x,y; assign y=a; assign x=y^b; {consumer}
    endmodule''', initialization=True)
    compiler = DirectSourceCompiler(machine)
    names = _names(machine)
    # Neither producer may be put in a pure closed region: a transient on x
    # is observable by a consumer whose behavior depends on its activation.
    writes = {name for i in compiler._regions() for name in compiler.effects[i][1]}
    assert not writes.intersection({names['x'], names['y']})


@pytest.mark.eda('slang')
def test_prefix_regions_require_exclusive_storage_ownership(tmp_path):
    from behave.sim.core.refusal import Refused
    from .test_rtl_source_scheduler import _lower

    # The shared scheduler checks ownership BEFORE either constructor exists.
    # Do not duplicate that admission rule in the prefix optimizer.
    with pytest.raises(Refused, match='scheduler-competing-writers'):
        _lower(tmp_path, '''module M(input clk,a,b,output reg x,q);
          always @* x=a;
          always @(posedge clk) x<=b;
          always @* q=x^b;
        endmodule''', initialization=True)
