"""Held-input write proofs bound event cycles without removing transitions."""

from itertools import product

import pytest

from behave.sim import api
from behave.sim.symbolic import interp, ir, ranges
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.generation import StimulusField
from behave.sim.rtl.source_effects import input_constant_writes
from behave.sim.rtl.source_scheduler import _event_bound

from .test_rtl_source_event_bounds import _freeze, _histories
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _state
from .test_rtl_source_trace import _contract


def test_input_function_requires_single_write_data_and_control_independence():
    a, state = ir.Var('a'), ir.Var('state')
    assign = lambda value: ir.Assign('x', value)
    cases = [
        (assign(a), {'x'}),
        (ir.If(a, assign(a), assign(ir.Lit(0))), {'x'}),
        (ir.If(state, assign(a), assign(ir.Lit(0))), set()),
        (ir.If(a, assign(a), ir.Skip()), set()),
        (ir.Seq(assign(a), assign(ir.Lit(0))), set()),
        (ir.Seq(ir.Assign('tmp', state), assign(ir.Var('tmp'))), set()),
        (ir.Seq(ir.Assign('tmp', a), assign(ir.Var('tmp'))), {'x'}),
        (ir.For('i', ir.Lit(1), assign(a)), set()),
        (ir.Store('x', ir.Lit(0), a), set()),
    ]
    for command, expected in cases:
        assert input_constant_writes(command, {'x'}, {'a'}) == expected


@pytest.mark.eda('slang')
@pytest.mark.solver
@pytest.mark.parametrize('write', [
    'x=a;',
    'if(a[0]) x=a; else x=2;',
    'case(a) 0:x=1; 1:x=2; default:x=a; endcase',
])
def test_capped_cycle_preserves_every_small_input_and_activation_order(tmp_path, write):
    source = f'''module M(input [1:0] a,output reg [1:0] x,y,z);
      always_comb begin {write} if(y==3) z=1; else z=0; end
      always_comb y=x;
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    assert _event_bound(machine.predecessors, (False, False), {0, 1}) is None
    assert machine.notification_caps and machine.event_bound is not None
    # The underlying cycle remains: no subscription or scheduler action is cut.
    assert machine.predecessors == (((1, 1),), ((0, 1),))
    names = _names(machine)
    for before, after, old_x, old_y in product(range(4), repeat=4):
        initial = _state(machine, {'a': before, 'x': old_x, 'y': old_y, 'z': 0})
        pins = {names['a']: after}
        # Independent exhaustive execution checks that *all* enabled orders
        # finish, not just the histories a bounded formula happened to retain.
        state, _, _, _ = interp.run_step(machine.procedure,
            {dict(machine.drive_inputs)[names['a']]: after, 'action': 0}, initial)
        frontier = {_freeze(state): state}
        for _ in range(machine.event_bound - 1):
            following = {}
            for state in frontier.values():
                if machine.boundary_is_quiet(state):
                    continue
                for action in range(1, machine.commit_action + 1):
                    post, output, _, error = interp.run_step(machine.procedure,
                        {dict(machine.drive_inputs)[names['a']]: after, 'action': action}, state)
                    assert not error
                    if output.transactions[0]['accepted']:
                        following[_freeze(post)] = post
            frontier = following
        assert all(machine.boundary_is_quiet(state) for state in frontier.values())
        assert _histories(machine, initial, (pins,), (machine.event_bound,)) == _histories(
            machine, initial, (pins,), (2 * machine.event_bound,))


@pytest.mark.eda('slang')
@pytest.mark.solver
@pytest.mark.parametrize('write', [
    'x=a; x=0;',
    'if(y==1) x=a; else x=0;',
    'if(a[0]) x=a;',
    'x<=a;',
    'x=~y;',
])
def test_intermediate_writes_latches_nba_and_feedback_are_not_constant_proofs(tmp_path, write):
    machine, _ = _lower(tmp_path, f'''module M(input [1:0] a,output reg [1:0] x,y,z);
      always @* begin {write} if(y==3) z=1; else z=0; end
      always @* y=x;
    endmodule''')
    assert machine.event_bound is None


@pytest.mark.cosim
@pytest.mark.solver
def test_cyclic_process_graph_query_has_same_goal_in_native_replay(tmp_path):
    source = '''module M(input [3:0] a,output reg [3:0] y);
      reg [3:0] x,relay;
      always_comb begin x=a; if(relay==9) y=a; else y=a; end
      always_comb relay=x;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    assert machine.event_bound is not None and machine.notification_caps
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': b'class M:\n def reset(self): pass\n def process(self,a): return {"y":a}\n'},
        config={'class': 'M'})
    contract = _contract()
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path/'design.sv', 'M', tmp_path/'build'))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1))
    checked = handle.replay(baseline.stimuli, reference=reference).evaluation
    assert checked.complete and checked.passed
    trace = checked.results[0].trace
    goal = _goal(source, plan, 'relay==9')
    assert not any(hit for row in trace.records for name, hit in row.hits if name == goal)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    query = binding.at((StimulusField('input', 1, 0, binding.releases['input', 1]),))
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
        mutable_inputs=query.mutable_inputs, event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    replay = handle.replay((query.proposal(answer.candidate),), reference=reference).evaluation
    assert replay.complete and replay.passed
    assert any(hit for row in replay.results[0].trace.records for name, hit in row.hits if name == goal)
