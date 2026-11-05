"""Implicit port assignments share typed lowering and event propagation."""

import pytest

from behave.sim.symbolic import interp, ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_goals import manifest_of

from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver,
              pytest.mark.usefixtures("search_lifetime")]


def _paths(machine):
    return {cell.observation: name for name, cell in machine.program.fields.items()}


def _drive(machine, state, **pins):
    paths = _paths(machine)
    updated = {**state, **{paths[name]: value for name, value in pins.items()}}
    inputs = {driven: updated[name] for name, driven in machine.drive_inputs}
    inputs["action"] = 0
    state, _, marks, error = interp.run_step(machine.procedure, inputs, state)
    assert not error
    hits = set(marks)
    for _ in range(80):  # Fixture watchdog only, not a production limit.
        action = next((i + 1 for i, flag in enumerate(machine.pending) if state[flag]), None)
        if action is None and any(state[write.valid] for write, _, _ in machine.queue_edges):
            action = machine.commit_action
        if action is None:
            return state, {goal for mark, _, goal in machine.occurrences if mark in hits}
        inputs["action"] = action
        state, _, marks, error = interp.run_step(machine.procedure, inputs, state)
        assert not error
        hits.update(marks)
    pytest.fail("fixture failed to settle")


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("port,actual,result,target,expected", [
    ("input var [1:0] data", "a", "output logic [3:0] value", "q", [0,1,2,9]),
    ("input [1:0] data", "a+2'd1", "output logic [3:0] value", "q", [1,2,9,0]),
    ("input var signed [1:0] data", "a", "output logic signed [1:0] value", "q", [0,1,14,1]),
    ("input var [1:0] data", "a", "output logic [1:0] value", "q[2:1]", [0,2,4,2]),
])
def test_transfers_query_every_source_goal_and_confirm_native_witnesses(
        tmp_path, port, actual, result, target, expected):
    source = f'''module C(input clk,{port},{result});
      initial value=0;
      always @(posedge clk) if(data==2'b11) value<=9; else value<=data;
    endmodule
    module M(input clk,input [1:0] a,output wire [3:0] q);
      C child(clk,{actual},{target});
      {"assign q[0]=0; assign q[3]=0;" if target != "q" else ""}
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    paths = _paths(machine)
    assert paths["q"] != paths["child", "value"]
    assert all(not process.occurrences for process in machine.program.processes if process.context is None)
    initial = machine.boundary({name: 0 for name in machine.program.fields}, startup=True)
    initial, _ = _drive(machine, initial, clk=0, a=0)
    assert machine.boundary_is_quiet(initial)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    observed, native_hits = replay(range(4))
    assert observed == expected
    state = initial
    for value in range(4):
        state, low = _drive(machine, state, clk=0, a=value)
        state, high = _drive(machine, state, clk=1)
        assert state[paths["q"]] == observed[value]
        assert low | high == native_hits[value]
    # A data/clock phase pair, not a simultaneous change that introduces a
    # legal input-transfer/posedge race. It matches the native Driver phases.
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    native = [replay((value,))[1][0] for value in range(4)]
    for goal in manifest_of(plan.declarations, filename="design.sv"):
        answer = query_source_interval(machine, env, initial,
            {paths["clk"]: 0, paths["a"]: 0}, (goal,),
            mutable_inputs=(paths["a"],), event_bound=8,
            following_inputs=({paths["clk"]: 1, paths["a"]: 0},))
        possible = any(goal in hits for hits in native)
        assert answer.outcome is (probing.Outcome.SAT if possible else probing.Outcome.LOCAL_UNSAT), answer.detail
        if possible:
            assert goal in replay((answer.candidate[paths["a"]],))[1][0]


def test_same_net_read_through_two_symbols_has_one_ir_state_cell(tmp_path):
    machine, _ = _lower(tmp_path, '''module C(input [1:0] a,output [2:0] q);
      assign q=a+M.a;
    endmodule
    module M(input [1:0] a,output [2:0] q); C child(a,q); endmodule''')
    for process in machine.program.processes:
        assert len(process.procedure.state) == len(set(process.procedure.state))


@pytest.mark.parametrize("assignment", ["a", "a[0]"])
def test_variable_output_transfer_keeps_external_input_ownership(tmp_path, assignment):
    source = f'''module C(output logic value); always_comb value=1; endmodule
    module M(input wire [1:0] a); C child({assignment}); endmodule'''
    with pytest.raises(Refused, match="input-written"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("nonblocking", [False, True])
def test_variable_output_propagation_triggers_the_parent_edge(tmp_path, nonblocking):
    operator = "<=" if nonblocking else "="
    source = f'''module C(input clk,input [1:0] data,output logic pulse);
      initial pulse=0;
      always @(posedge clk) pulse {operator} data[0];
    endmodule
    module M(input clk,input [1:0] a,output logic [3:0] q);
      wire trigger;
      initial q=0;
      C child(clk,a,trigger);
      always @(posedge trigger) if(a==3) q<=13; else q<=5;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    paths = _paths(machine)
    initial = machine.boundary({name: 0 for name in machine.program.fields})
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values = (0, 1, 2, 3, 3, 0)
    outputs, native = replay(values)
    state = initial
    for value, expected, observed in zip(values, outputs, native, strict=True):
        state, low = _drive(machine, state, clk=0, a=value)
        state, high = _drive(machine, state, clk=1)
        assert state[paths["q"]] == expected
        assert low | high == observed
    target = _goal(source, plan, "a==3")
    assert all(target not in hits for hits in replay((0,2))[1])
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, initial,
        {paths["clk"]: 0, paths["a"]: 0}, (target,),
        mutable_inputs=(paths["a"],), event_bound=8,
        following_inputs=({paths["clk"]: 1, paths["a"]: 0},))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert target in replay((answer.candidate[paths["a"]],))[1][0]


def test_output_transfer_sensitivity_excludes_destination_merge_reads(tmp_path):
    machine, _ = _lower(tmp_path, '''module C(input a,output logic v);
      always_comb v=a;
    endmodule
    module M(input a,output wire [1:0] q);
      C child(a,q[0]);
      assign q[1]=0;
    endmodule''')
    paths = _paths(machine)
    transfer, = [process for process in machine.program.processes if process.context is None]
    assert transfer.implicit_reads == (paths["child", "v"],)
    assert paths["q"] not in transfer.implicit_reads


def test_unconnected_variable_output_adds_no_transfer(tmp_path):
    machine, _ = _lower(tmp_path, '''module C(input a,output logic v);
      always_comb v=a;
    endmodule
    module M(input a); C child(.a(a),.v()); endmodule''')
    assert not any(process.context is None for process in machine.program.processes)


@pytest.mark.eda("verilator", "make")
def test_disjoint_variable_ports_preserve_other_bits_and_instance_hits(tmp_path):
    source = '''module C(input clk,input var data,output logic value);
      initial value=0;
      always @(posedge clk) if(data) value<=1; else value<=0;
    endmodule
    module M(input clk,input [1:0] a,output wire [1:0] q);
      C left(clk,a[0],q[0]);
      C right(clk,a[1],q[1]);
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    paths = _paths(machine)
    assert paths["left", "value"] != paths["right", "value"]
    masks = [mask for process in machine.program.processes if process.context is None
             for name, mask, _ in process.write_masks if name == paths["q"]]
    assert sorted(masks) == [1, 2]
    initial = machine.boundary({name: 0 for name in machine.program.fields})
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values = (0, 1, 2, 3, 2, 1)
    observed, hits = replay(values)
    assert observed == list(values)
    state = initial
    for value, native in zip(values, hits, strict=True):
        state, low = _drive(machine, state, clk=0, a=value)
        state, high = _drive(machine, state, clk=1)
        assert state[paths["q"]] == value
        assert low | high == native
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    native = [replay((value,))[1][0] for value in range(4)]
    for goal in manifest_of(plan.declarations, filename="design.sv"):
        answer = query_source_interval(machine, env, initial,
            {paths["clk"]: 0, paths["a"]: 0}, (goal,),
            mutable_inputs=(paths["a"],), event_bound=12,
            following_inputs=({paths["clk"]: 1, paths["a"]: 0},))
        reachable = any(goal in hits for hits in native)
        assert answer.outcome is (probing.Outcome.SAT if reachable else probing.Outcome.LOCAL_UNSAT), answer.detail
        if reachable:
            assert goal in replay((answer.candidate[paths["a"]],))[1][0]
