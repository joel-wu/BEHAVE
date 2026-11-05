"""Physical child state: source goals, local queries and independent replay."""

import pytest

from behave.sim import api
from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.generation import StimulusField
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_relation import SourceIntervalTemplate

from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _settle, _state, _step

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver,
              pytest.mark.usefixtures("search_lifetime")]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("declaration,path,ports", [
    ("Child child();", "child.value", False),
    ("if(1) begin: nested Child child(); end", "nested.child.value", False),
    ("Child child(clk,a,q);", "child.value", True),
    ("if(1) begin: nested Child child(clk,a,q); end", "nested.child.value", True),
])
def test_child_events_query_every_declared_goal_and_confirm_native_hits(tmp_path, declaration, path, ports):
    header = "(input clock,input [1:0] data,output wire [3:0] result)" if ports else ""
    clock, data = ("clock", "data") if ports else ("M.clk", "M.a")
    source = f'''module Child{header};
      logic [3:0] value=0;
      always @(posedge {clock}) if({data}==3) value<=9; else value<={data};
      {"assign result=value;" if ports else ""}
    endmodule
    module M(input clk,input [1:0] a,output wire [3:0] q);
      {declaration}
      {"" if ports else f"assign q={path};"}
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    assert machine.program.fields[names["value"]].observation == tuple(path.split("."))
    assert {machine.program.fields[name].port for name, _ in machine.drive_inputs} == {"clk", "a"}
    initial = _state(machine, {"clk": 0, "a": 0, "q": 0, "value": 0})
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    observed, native_hits = replay(range(4))
    state = initial
    for value in range(4):
        state, _, _, _, _ = _step(machine, state, 0, clk=0, a=value)
        state, low_hits = _settle(machine, state)
        state, _, _, _, _ = _step(machine, state, 0, clk=1)
        state, high_hits = _settle(machine, state)
        assert state[names["q"]] == observed[value]
        assert low_hits | high_hits == native_hits[value]

    # All catalogue entries, not just a convenient chosen branch. Each query
    # starts at the same quiet native state; each SAT result is replayed fresh.
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    independent_hits = [replay((value,))[1][0] for value in range(4)]
    for goal in manifest_of(plan.declarations, filename="design.sv"):
        answer = query_source_interval(machine, env, initial,
            {names["clk"]: 1, names["a"]: 0}, (goal,),
            mutable_inputs=(names["a"],), event_bound=4)
        reachable = any(goal in hits for hits in independent_hits)
        assert answer.outcome is (probing.Outcome.SAT if reachable else probing.Outcome.LOCAL_UNSAT), answer.detail
        if reachable:
            _, hits = replay((answer.candidate[names["a"]],))
            assert goal in hits[0]
    target = _goal(source, plan, f"{data}==3")
    assert all(target not in hits for hits in replay((0, 1, 2))[1])
    assert target in replay((3,))[1][0]


def test_cross_scope_writers_still_refuse_overlapping_physical_state(tmp_path):
    source = '''module C; logic [3:0] value;
      always @(posedge M.clk) value<=M.a;
    endmodule
    module M(input clk,input [3:0] a); C c();
      always @(posedge clk) c.value<=a+1;
    endmodule'''
    with pytest.raises(Refused, match="competing-writers"):
        _lower(tmp_path, source)


@pytest.mark.parametrize("child,actual", [
    ("input [3:0] data", "a"),
    ("input [1:0] data", "{a[0],a[1]}"),
    ("input [1:0] data", ""),
])
def test_connections_requiring_transfer_are_not_silently_collapsed(tmp_path, child, actual):
    source = f'''module C({child}); logic [3:0] x;
      always_comb x=data;
    endmodule
    module M(input [1:0] a); C c(.data({actual})); endmodule'''
    with pytest.raises(Refused, match="port-propagation-unbound"):
        _lower(tmp_path, source)


def test_multi_level_wire_aliases_keep_top_input_and_independent_instance_state(tmp_path):
    source = '''module C(input clock,input [1:0] data,output wire [3:0] result);
      logic [3:0] value;
      always @(posedge clock) value<=data;
      assign result=value;
    endmodule
    module Wrap(input clk,input [1:0] a,output wire [3:0] q);
      C inner(clk,a,q);
    endmodule
    module M(input clk,input [1:0] a,output wire [3:0] q,r);
      Wrap left(clk,a,q);
      Wrap right(clk,a,r);
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    fields = list(machine.program.fields.values())
    assert {field.observation for field in fields} == {
        "clk", "a", "q", "r", ("left", "inner", "value"), ("right", "inner", "value")}
    assert {field.port for field in fields if field.input} == {"clk", "a"}
    assert len(fields) == 6


def test_collapsed_net_does_not_hide_competing_child_drivers(tmp_path):
    source = '''module C(input a,output q); assign q=a; endmodule
    module M(input a,b,output q); C left(a,q); C right(b,q); endmodule'''
    with pytest.raises(Refused, match="competing-writers"):
        _lower(tmp_path, source)


@pytest.mark.cosim
@pytest.mark.parametrize("port_kind,sliced", [("wire", False), ("logic", False), ("wire", True)])
def test_public_native_child_history_drives_query_and_replay(tmp_path, port_kind, sliced):
    source = '''module Child(input clock,reset,valid,input [3:0] data,
      output wire [3:0] result,output wire ready);
      logic [3:0] held,value;
      logic valid_q;
      always @(posedge clock) begin
        if(reset) begin held<=0; value<=0; valid_q<=0; end
        else begin
          valid_q<=valid;
          if(valid) begin
            if(data==9 && held==5) value<=data; else value<=data;
            held<=data;
          end
        end
      end
      assign result=value;
      assign ready=valid_q;
    endmodule
    module M(input clk,rst,in_valid,input [3:0] a,output wire out_valid,
             output wire [3:0] y);
      Child child(clk,rst,in_valid,a,y,out_valid);
    endmodule'''
    source = source.replace("output wire", f"output {port_kind}", 2)
    if sliced:
        source = source.replace("input [3:0] a,output", "input [7:0] a,output")
        source = source.replace("child(clk,rst,in_valid,a,y,out_valid)",
                                "child(clk,rst,in_valid,a[5:2],y,out_valid)")
    kind = api.FiniteType(4)
    contract = api.TaskContract([
        api.Endpoint("input", "in", [api.Field("payload", api.FiniteType(8) if sliced else kind, "a")],
            api.sampled_edge("in", valid="in_valid"), "c", kind=api.Kind.TRANSACTION),
        api.Endpoint("output", "out", [api.Field("result", kind, "y")],
            api.sampled_edge("out", valid="out_valid"), "c", kind=api.Kind.TRANSACTION,
            answers_per_transaction=1)],
        [api.ClockDomain("c", "clk", "rst", reset_restores=True)],
        protocol_instances=[api.ProtocolInstance("compute", ("input", "output"))])
    machine, plan = _lower(tmp_path, source)
    target = _goal(source, plan, "data==9 && held==5")
    result = '(a >> 2) & 15' if sliced else 'a'
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": f'class M:\n def reset(self): pass\n def process(self,a): return {{"y":{result}}}\n'.encode()},
        config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path/"design.sv", "M", tmp_path/"build"))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1))
    seed, = baseline.stimuli
    # Keep the 100-input schedule, but deliberately leave nonzero child history
    # at the selected boundary. A fabricated zero would change this query.
    seed = api.Stimulus({"input": [(20 if sliced else 5,), *dict(seed.queues)["input"][1:]]}, seed.slots,
                        replay_policy=seed.replay_policy)
    evaluated = handle.replay((seed,), reference=reference).evaluation
    assert evaluated.complete and evaluated.passed, evaluated.as_dict()
    trace = evaluated.results[0].trace
    assert not any(hit for row in trace.records for goal, hit in row.hits if goal == target)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    relation = SourceIntervalTemplate(machine.procedure, env).relation(machine, (target,))
    held = next(name for name, cell in machine.program.fields.items()
                if cell.observation == ("child", "held"))
    assert held in relation.source.state_names
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    query = binding.at((StimulusField("input", 1, 0, binding.releases["input", 1]),),
                       state_names=relation.source.state_names)
    assert query.state[held] == 5  # Must come from the actual native child handle.
    answer = query_source_interval(machine, env, query.state, query.phases[0], (target,),
        mutable_inputs=query.mutable_inputs, following_inputs=query.phases[1:],
        event_bound=machine.event_bound, _relation=relation)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    proposal = query.proposal(answer.candidate)
    checked = handle.replay((proposal,), reference=reference).evaluation
    assert checked.complete and checked.passed, checked.as_dict()
    assert any(hit for row in checked.results[0].trace.records
               for goal, hit in row.hits if goal == target)
