"""Value-dependence bounds preserve whole-array event notifications."""

from itertools import product

import pytest

from behave.sim import api
from behave.sim.symbolic import interp, ir, ranges
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.generation import StimulusField
from behave.sim.rtl.source_effects import single_write_dependencies
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_scheduler import _event_bound
from behave.sim.rtl.source_values import ArrayType

from .test_rtl_source_event_bounds import _freeze, _histories
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _state
from .test_rtl_source_trace import _contract


def test_constant_cells_use_their_value_dependencies_not_the_entire_array():
    word = IntegralType(4, False)
    kinds = {'a': word, 'cells': ArrayType(2, 0, word)}
    body = ir._sequence([
        ir.Assign('index', ir.Op('sub', ir.Lit(2), ir.Lit(1))),
        ir.Assign('value', ir.Sel(ir.Var('cells'), ir.Var('index'))),
        ir.Store('cells', ir.Lit(2), ir.Op('add', ir.Var('value'), ir.Var('a'))),
    ])
    assert single_write_dependencies(body, kinds) == {
        ('cells', 0xf00): {('cells', 0xf0), ('a', 0xf)}}


@pytest.mark.parametrize('body', [
    ir._sequence([ir.Store('cells', ir.Lit(0), ir.Var('a')),
                  ir.Store('cells', ir.Lit(0), ir.Lit(0))]),
    ir.Store('cells', ir.Var('a'), ir.Lit(0)),
    ir.If(ir.Var('a'), ir.Store('cells', ir.Lit(0), ir.Lit(0)), ir.Skip()),
    ir.For('i', ir.Lit(2), ir.Store('cells', ir.Var('i'), ir.Var('a'))),
    ir._sequence([ir.Store('cells', ir.Lit(0), ir.Var('a')),
                  ir.Store('cells', ir.Lit(1), ir.Sel(ir.Var('cells'), ir.Lit(0)))]),
])
def test_unproved_write_history_does_not_receive_a_certificate(body):
    word = IntegralType(4, False)
    assert single_write_dependencies(body, {'a': word, 'cells': ArrayType(0, 1, word)}) == {}


@pytest.mark.eda('slang')
@pytest.mark.solver
@pytest.mark.parametrize('left,right,clocked', [(0, 2, False), (2, 0, False), (0, 2, True)])
def test_every_small_initial_state_and_enabled_order_respects_the_bound(
        tmp_path, left, right, clocked):
    indices = range(left, right + (1 if left < right else -1), 1 if left < right else -1)
    first, _second, third = indices
    direction = 1 if left < right else -1
    head = ('reg head; always @(posedge clk) head<=a;' if clocked else '')
    source = f'''module M(input clk,input a,output reg y);
      {head}
      wire cells[{left}:{right}];
      assign cells[{first}]={'head' if clocked else 'a'};
      for(genvar p=0;p<2;p=p+1) begin: stages
        assign cells[{left}+{direction}*(p+1)]=cells[{left}+{direction}*p];
      end
      always_comb y=cells[{third}];
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    all_pending = set(range(len(machine.pending)))
    deferred = tuple(bool(process.deferred) for process in machine.program.processes)
    assert _event_bound(machine.predecessors, deferred, all_pending) is None
    assert machine.event_bound is not None and machine.notification_caps
    assert any(row.storage == _names(machine)['cells'] and row.mask == 7
               for row in machine.subscriptions)  # Real whole-array wakeups stay.
    names = _names(machine)
    for a, *cells in product(range(2), repeat=4):
        initial = _state(machine, {'clk': 0, 'a': 1-a, 'cells': cells, 'y': 0,
                                   **({'head': 0} if clocked else {})})
        pins = {names['a']: a, names['clk']: 1}
        drive = {driven: pins[name] for name, driven in machine.drive_inputs}
        driven, outputs, _, error = interp.run_step(machine.procedure, {**drive, 'action': 0}, initial)
        assert not error and outputs.transactions[0]['accepted']
        frontier = {_freeze(driven): driven}
        for _ in range(machine.event_bound - 1):
            following = {}
            for state in frontier.values():
                if machine.boundary_is_quiet(state):
                    continue
                for action in range(1, machine.commit_action + 1):
                    post, outputs, _, error = interp.run_step(
                        machine.procedure, {**drive, 'action': action}, state)
                    assert not error
                    if outputs.transactions[0]['accepted']:
                        following[_freeze(post)] = post
            frontier = following
        assert all(machine.boundary_is_quiet(state) for state in frontier.values())
        histories = _histories(machine, initial, (pins,), (machine.event_bound,))
        assert histories
        assert histories == _histories(machine, initial, (pins,), (2 * machine.event_bound,))


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_a_genuine_cell_value_cycle_remains_unproved(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(output reg y);
      reg cells[0:1];
      always_comb cells[0]=~cells[1];
      always_comb cells[1]=cells[0];
      always_comb y=cells[1];
    endmodule''')
    assert machine.event_bound is None


@pytest.mark.cosim
@pytest.mark.solver
@pytest.mark.usefixtures("search_lifetime")
def test_array_notification_cycle_can_query_and_natively_confirm_the_same_goal(tmp_path):
    source = '''module M(input [3:0] a,output reg [3:0] y);
      wire [3:0] cells[0:2];
      assign cells[0]=a;
      for(genvar p=0;p<2;p=p+1) begin: stages
        assign cells[p+1]=cells[p];
      end
      always_comb if(cells[2]==9) y=a; else y=a;
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
    goal = _goal(source, plan, 'cells[2]==9')
    assert not any(hit for row in trace.records for name, hit in row.hits if name == goal)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    query = binding.at((StimulusField('input', 1, 0, binding.releases['input', 1]),))
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
        mutable_inputs=query.mutable_inputs, event_bound=machine.event_bound)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    replay = handle.replay((query.proposal(answer.candidate),), reference=reference).evaluation
    assert replay.complete and replay.passed
    assert any(hit for row in replay.results[0].trace.records for name, hit in row.hits if name == goal)
