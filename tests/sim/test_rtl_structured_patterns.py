"""Compiler-resolved array patterns: native values, events and query witnesses."""

import pytest

from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_initial import declaration_initial_values

from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _settle, _state, _step

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("bounds", ["3:1", "1:3"])
@pytest.mark.parametrize("dynamic_default", [False, True])
def test_keyed_pattern_values_hits_and_query_witness(tmp_path, bounds, dynamic_default):
    pattern = "'{1:-8'sd1, default:a}" if dynamic_default else "'{1:a, default:-8'sd1}"
    condition = "x[2]==9" if dynamic_default else "x[1]==9"
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      logic signed [7:0] x[{bounds}];
      always @(posedge clk) begin
        x={pattern};
        if({condition}) q<=x[1]+x[2]+x[3]; else q<=x[1]+x[2]+x[3];
      end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    inputs = (0, 3, 9, 15, 1)
    actual, native_hits = replay(inputs)
    expected = [((2*a-1) if dynamic_default else (a-2)) & 255 for a in inputs]
    assert actual == expected
    initial = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": [0, 0, 0]})
    state = initial
    for a, output, reached in zip(inputs, actual, native_hits, strict=True):
        state, _, hits, _, _ = _step(machine, state, 0, clk=0, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        state, _, during, _, _ = _step(machine, state, 0, clk=1, a=a)
        hits.update(during)
        state, during = _settle(machine, state)
        hits.update(during)
        assert state[names["q"]] == output
        ascending = [-1, a, a] if dynamic_default else [a, -1, -1]
        assert state[names["x"]] == (ascending if bounds == "1:3" else ascending[::-1])
        assert hits == reached

    target = _goal(source, plan, condition, "true", "control-choice")
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, initial,
        {names["clk"]: 0, names["a"]: 0}, [target], mutable_inputs=(names["a"],),
        following_inputs=({names["clk"]: 1, names["a"]: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names["a"]] == 9
    _, confirmed = replay((answer.candidate[names["a"]],))
    assert target in confirmed[0]


@pytest.mark.parametrize(("declaration", "pattern", "expected"), [
    ("logic signed [7:0] x[3:2][1:0]", "'{3:'{1:a,default:-8'sd2},default:8'sd4}",
     [[7, -2], [4, 4]]),
    ("int x[1:0][2:1]", "'{int:a,default:-1}", [[7, 7], [7, 7]]),
])
def test_nested_defaults_and_type_keys_follow_compiler_conversions(
        tmp_path, declaration, pattern, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration};
      always @(posedge clk) begin x={pattern}; q<=a; end
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": [[0, 0], [0, 0]]})
    state, _, _, _, _ = _step(machine, state, 0, clk=1, a=7)
    state, _ = _settle(machine, state)
    assert state[_names(machine)["x"]] == expected


def test_declaration_pattern_uses_the_same_typed_elements(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      logic signed [7:0] x[3:1]='{2:-8'sd4,default:-8'sd2};
      always @(posedge clk) q<=x[2]+a;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    assert declaration_initial_values(machine.program)[_names(machine)["x"]] == [254, 252, 254]


def test_pattern_does_not_choose_an_order_for_interfering_elements(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      logic [7:0] x[2:0]; integer count;
      always @(posedge clk) begin count=0; x='{default:count++}; q<=x[0]; end
    endmodule"""
    with pytest.raises(Refused, match="order|interfer|independent"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize(("bounds", "expected"), [("1:0", 0x7f), ("0:1", 0xf7)])
def test_packed_pattern_preserves_element_order_and_signed_bits(tmp_path, bounds, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      logic signed [{bounds}][3:0] x;
      always @(posedge clk) begin x='{{1:a,default:-4'sd1}}; q<=x; end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    actual, native_hits = replay((7,))
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": 0})
    state, _, _, _, _ = _step(machine, state, 0, clk=1, a=7)
    state, hits = _settle(machine, state)
    assert actual == [expected] and state[_names(machine)["q"]] == expected
    assert hits == native_hits[0]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize(("used", "other_call"), [(False, False), (False, True), (True, False)])
def test_default_helpers_retain_actual_events_not_setter_declarations(tmp_path, used, other_call):
    pattern = "'{1:a,default:f(a)}" if used else "'{1:a,2:a,3:a,default:f(a)}"
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      logic [7:0] x[3:1];
      function automatic [7:0] f(input [3:0] v);
        if(v==9) f=11; else f=v+1;
      endfunction
      always @(posedge clk) begin {"q=f(a);" if other_call else ""}
        x={pattern}; q<=x[1]+x[2]+x[3]; end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    actual, native_hits = replay((9,))
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": [0, 0, 0]})
    state, _, _, _, _ = _step(machine, state, 0, clk=1, a=9)
    state, hits = _settle(machine, state)
    assert actual == [31 if used else 27]
    assert state[_names(machine)["q"]] == actual[0]
    assert hits == native_hits[0]
    target = _goal(source, plan, "v==9", "true", "control-choice")
    assert (target in hits) is (used or other_call)
    from behave.sim.engines.rtl.source_session import _constant_false_source_goals

    assert (target in _constant_false_source_goals(machine)) is not (used or other_call)
