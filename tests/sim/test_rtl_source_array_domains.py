"""Source array domains are proved at the access, not from variable names/types."""

import pytest

from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval

from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _settle, _state, _step

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize("left,right", [(0, 3), (3, 0), (4, 7), (-2, 1)])
@pytest.mark.eda("verilator", "make")
def test_assigned_array_index_matches_native_values_hits_and_query(tmp_path, left, right):
    lo, hi = sorted((left, right))
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      logic [7:0] cells[{left}:{right}];
      always @(posedge clk) begin
        int index;
        for(int i={lo};i<={hi};i++) cells[i]=0;
        index={lo}+int'(a&3);
        cells[index]=a+5;
        if(cells[index]==14) q<=cells[index]+1; else q<=cells[index];
      end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values = tuple(range(16))
    actual, native_hits = replay(values)
    assert actual == [a + 5 + (a == 9) for a in values]
    # The process-local static index is real storage too. Its previous value
    # must not matter: each access follows the newly assigned masked index.
    initial = _state(machine, {"clk": 0, "a": 0, "q": 0, "cells": [0] * 4, "index": 17})
    state = initial
    for a, expected, reached in zip(values, actual, native_hits):
        state, _, hits, _, _ = _step(machine, state, 0, clk=0, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        state, _, during, _, _ = _step(machine, state, 0, clk=1, a=a)
        hits.update(during)
        state, during = _settle(machine, state)
        hits.update(during)
        assert state[names["q"]] == expected
        assert hits == reached

    goal = _goal(source, plan, "cells[index]==14", "true", "control-choice")
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, initial, {names["clk"]: 0, names["a"]: 0}, [goal],
        mutable_inputs=(names["a"],), following_inputs=({names["clk"]: 1, names["a"]: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer
    assert answer.candidate[names["a"]] == 9
    _, confirmed = replay((9,))
    assert goal in confirmed[0]


@pytest.mark.parametrize("body", [
    "index=a; q=cells[index]; index=a&3;",
    "index=a&3; if(a[0]) index=4; q=cells[index];",
    "index=0; for(int i=0;i<5;i++) begin cells[index]=a; index=index+1; end",
])
def test_array_domain_checks_every_actual_access(tmp_path, body):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      logic [7:0] cells[0:3];
      always @(posedge clk) begin int index; {body} end
    endmodule"""
    with pytest.raises(Refused, match="proved in-range"):
        _lower(tmp_path, source)
