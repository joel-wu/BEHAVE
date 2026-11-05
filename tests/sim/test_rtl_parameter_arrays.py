"""Elaborated array parameters retain values and do not invent runtime events."""

import pytest

from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval

from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _settle, _state, _step

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("bounds", ["1:3", "3:1"])
@pytest.mark.parametrize("keyed", [False, True])
def test_array_parameter_values_events_and_search(tmp_path, bounds, keyed):
    pattern = "'{1:-7,2:4,default:9}" if keyed else (
        "'{-7,4,9}" if bounds == "1:3" else "'{9,4,-7}")
    condition = "TABLE[(a%3)+1]==-7 && a==9"
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      localparam logic signed [7:0] TABLE[{bounds}]={pattern};
      always @(posedge clk) begin
        if({condition}) q<=TABLE[(a%3)+1]+a; else q<=TABLE[(a%3)+1];
      end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    assert "TABLE" not in names  # Constants are not mutable boundary state.
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    inputs = (0, 1, 2, 3, 9, 15)
    outputs, observed = replay(inputs)
    assert outputs == [249, 4, 9, 249, 2, 249]
    initial = _state(machine, {"clk": 0, "a": 0, "q": 0})
    state = initial
    for a, output, native_hits in zip(inputs, outputs, observed, strict=True):
        state, _, hits, _, _ = _step(machine, state, 0, clk=0, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        state, _, during, _, _ = _step(machine, state, 0, clk=1, a=a)
        hits.update(during)
        state, during = _settle(machine, state)
        hits.update(during)
        assert state[names["q"]] == output
        assert hits == native_hits
    target = _goal(source, plan, condition, "true", "control-choice")
    assert all(target not in hits for hits in observed[:4])
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, initial,
        {names["clk"]: 0, names["a"]: 0}, [target], mutable_inputs=(names["a"],),
        following_inputs=({names["clk"]: 1, names["a"]: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names["a"]] == 9
    _, confirmed = replay((answer.candidate[names["a"]],))
    assert target in confirmed[0]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("construction", ["pattern", "function", "alias"])
def test_nested_parameter_elaboration_not_runtime_initialization(tmp_path, construction):
    declarations = """typedef logic signed [7:0] table_t [3:2][1:2];
      function automatic table_t build();
        table_t t; t='{3:'{1:-3,2:7},2:'{1:11,2:-5}}; return t;
      endfunction
    """
    expression = "'{3:'{1:-3,2:7},2:'{1:11,2:-5}}" if construction == "pattern" else "build()"
    declarations += f"localparam table_t ORIGINAL={expression};\n"
    declarations += "localparam table_t TABLE=ORIGINAL;" if construction == "alias" else ""
    name = "TABLE" if construction == "alias" else "ORIGINAL"
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {declarations}
      always @(posedge clk) q<={name}[a[0]?2:3][a[1]?2:1];
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    actual, native_hits = replay((0, 1, 2, 3))
    assert actual == [253, 11, 7, 251]
    state = _state(machine, {"clk": 0, "a": 0, "q": 0})
    for a, output, reached in zip(range(4), actual, native_hits, strict=True):
        state, _, _, _, _ = _step(machine, state, 0, clk=0, a=a)
        state, _ = _settle(machine, state)
        state, _, _, _, _ = _step(machine, state, 0, clk=1, a=a)
        state, hits = _settle(machine, state)
        assert state[_names(machine)["q"]] == output
        assert hits == reached


def test_array_parameter_x_is_not_a_zero_witness(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      localparam logic [7:0] TABLE[1:0]='{8'bx,8'd7};
      always @(posedge clk) q<=TABLE[a[0]];
    endmodule"""
    with pytest.raises(Refused, match="four-state-domain-unproved"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_wide_parameter_elements_are_not_display_abbreviations(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      localparam logic [256:0] TABLE[0:1]='{
        257'h10000000000000000000000000000000000000000000000000000000000000003,
        257'h08000000000000000000000000000000000000000000000000000000000000007};
      always @(posedge clk) q<=TABLE[a[0]][256:249]^TABLE[a[0]][7:0];
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    outputs, native_hits = replay((0, 1))
    assert outputs == [131, 71]
    for a, output, reached in zip((0, 1), outputs, native_hits, strict=True):
        initial = _state(machine, {"clk": 0, "a": 0, "q": 0})
        state, _, _, _, _ = _step(machine, initial, 0, clk=1, a=a)
        state, hits = _settle(machine, state)
        assert state[_names(machine)["q"]] == output
        assert hits == reached
