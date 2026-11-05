"""Disjoint compiler-bound write destinations, not guessed process ordering."""

from itertools import permutations

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_scheduler import _accepted, _lower, _names, _settle, _state, _step

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


def _masks(machine, field):
    name = _names(machine)[field]
    return [(blocking, deferred) for process in machine.program.processes
            for target, blocking, deferred in process.write_masks if target == name]


@pytest.mark.parametrize("assignment", ["=", "<="])
def test_disjoint_packed_writers_commute_and_preserve_the_other_bits(tmp_path, assignment):
    source = f"""module M(input clk,input [7:0] a,output logic [7:0] q);
      always @(posedge clk) q[3:0] {assignment} a[3:0];
      always @(posedge clk) q[7:4] {assignment} a[7:4];
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    assert _masks(machine, "q") == ([(15, 0), (240, 0)] if assignment == "=" else [(0, 15), (0, 240)])
    for order in permutations((1, 2)):
        state = _state(machine, {"clk": 0, "a": 0, "q": 0xAC})
        state, _, _, _, _ = _step(machine, state, 0, clk=1, a=0x35)
        for action in order:
            state, outputs, _, _, _ = _step(machine, state, action)
            assert _accepted(outputs)
        if assignment == "<=":
            assert state[_names(machine)["q"]] == 0xAC
        state, _ = _settle(machine, state)
        assert state[_names(machine)["q"]] == 0x35


@pytest.mark.parametrize("layout,first,second", [
    ("[2:3]", "[2]", "[3]"),
    ("[3:2]", "[3]", "[2]"),
    ("[4:4][3:2]", "[4][3]", "[4][2]"),
])
def test_separate_continuous_array_drivers_keep_compiler_orientation(tmp_path, layout, first, second):
    source = f"""module M(input [7:0] a,output wire [7:0] q);
      wire [3:0] cells {layout};
      assign cells{first}=a[3:0];
      assign cells{second}=a[7:4];
      assign q={{cells{second},cells{first}}};
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    assert _masks(machine, "cells") == [(15, 0), (240, 0)]
    initial = [[0, 0]] if layout.startswith("[4:4]") else [0, 0]
    state = _state(machine, {"a": 0, "q": 0, "cells": initial}, startup=True)
    state, _ = _settle(machine, state)
    for a in (0x35, 0xA7, 0, 0xFF):
        state, _, _, _, _ = _step(machine, state, 0, a=a)
        state, _ = _settle(machine, state)
        assert state[_names(machine)["q"]] == a


def test_unknown_inner_selection_keeps_its_whole_parent_not_one_replay_cell(tmp_path):
    source = """module M(input clk,input i,input [3:0] a,output wire [7:0] q);
      logic [3:0] cells[0:1][0:1];
      always @(posedge clk) cells[0][i]=a;
      always @(posedge clk) cells[1][i]=a;
      assign q={cells[1][i],cells[0][i]};
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    assert _masks(machine, "cells") == [(255, 0), (65280, 0)]
    for i in (0, 1):
        state = _state(machine, {"clk": 0, "i": i, "a": 0, "q": 0,
                                "cells": [[0, 0], [0, 0]]}, startup=True)
        state, _ = _settle(machine, state)
        state, _, _, _, _ = _step(machine, state, 0, clk=1, a=9)
        state, _ = _settle(machine, state)
        assert state[_names(machine)["q"]] == 0x99


@pytest.mark.parametrize("writers,storage", [
    ("always @(posedge clk) q[3:0]=a; always @(posedge clk) q[2:0]=a;", ""),
    ("always @(posedge clk) q=a; always @(posedge clk) q[2:0]<=a;", ""),
    ("always @(posedge clk) q[i]=a[0]; always @(posedge clk) q[0]=a[1];", ""),
    ("assign x[0]=a; assign x[0]=0; assign q=x[0];", "wire [3:0] x[0:1];"),
    ("always @(posedge clk) x[i]=a; always @(posedge clk) x[0]=0; assign q=x[0];",
     "logic [3:0] x[0:1];"),
    ("always @(posedge clk) x='{4'd1,4'd2}; always @(posedge clk) x[0]=a; assign q=x[0];",
     "logic [3:0] x[0:1];"),
    ("always @(posedge clk) if(i) q=a; always @(posedge clk) if(!i) q=a;", ""),
])
def test_overlapping_or_unproved_writers_still_refuse(tmp_path, writers, storage):
    source = f"module M(input clk,i,input [3:0] a,output logic [3:0] q); {storage} {writers} endmodule"
    with pytest.raises(Refused, match="overlapping source writers"):
        _lower(tmp_path, source)


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("assignment", ["=", "<="])
def test_disjoint_writers_native_values_and_exact_goal_hits_agree(tmp_path, assignment):
    source = f"""module M(input clk,input [7:0] a,output logic [7:0] q);
      wire [3:0] parts[3:2];
      assign parts[3]=a[3:0];
      assign parts[2]=a[7:4];
      always @(posedge clk) begin
        q[3:0] {assignment} parts[3];
        if(parts[3]==5) q[1:0] {assignment} 2;
      end
      always @(posedge clk) q[7:4] {assignment} parts[2];
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    inputs = (0, 0x35, 0xA7, 0xFF, 5)
    actual, native_hits = replay(inputs)
    assert actual == [0, 0x36, 0xA7, 0xFF, 6]
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "parts": [0, 0]}, startup=True)
    state, _ = _settle(machine, state)
    for a, expected, reached in zip(inputs, actual, native_hits):
        state, _, _, _, _ = _step(machine, state, 0, clk=0, a=a)
        state, hits = _settle(machine, state)
        state, _, _, _, _ = _step(machine, state, 0, clk=1, a=a)
        state, during = _settle(machine, state)
        assert state[_names(machine)["q"]] == expected
        assert hits | during == reached


def test_bit_precise_dependencies_do_not_create_a_spurious_event_cycle(tmp_path):
    source = """module M(input [3:0] a,output wire [7:0] q);
      assign q[3:0]=a;
      assign q[7:4]=q[3:0];
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    assert machine.event_bound == 4  # drive + producer + at most two consumer runs
    state = _state(machine, {"a": 0, "q": 0}, startup=True)
    state, _ = _settle(machine, state)
    state, _, _, _, _ = _step(machine, state, 0, a=10)
    state, _ = _settle(machine, state)
    assert state[_names(machine)["q"]] == 0xAA
