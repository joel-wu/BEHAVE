"""Identical event subscriptions share computation, not their pending state."""

from itertools import product

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_notifications import notify_value
from behave.sim.rtl.source_scheduler import Subscription
from behave.sim.rtl.source_values import ArrayType


def _procedure(kind, subscriptions, *, offset=ir.Lit(0), restore=False):
    pending = tuple(f"pending{i}" for i in range(1 + max(s.process for s in subscriptions)))
    kinds = {"old": kind.ir_type, "new": kind.ir_type, "offset": types.INTEGER,
             **dict.fromkeys(pending, types.BOOLEAN)}
    temporaries = []

    def temp(*, kind=types.INTEGER):
        name = f"temp{len(temporaries)}"
        temporaries.append(name)
        kinds[name] = kind
        return name

    commands = notify_value(kind, ir.Var("old"), ir.Var("new"),
                            subscriptions, pending, temp, offset=offset)
    if restore:
        commands += notify_value(kind, ir.Var("new"), ir.Var("old"),
                                 subscriptions, pending, temp, offset=offset)
    return ir.Procedure(pending, ("old", "new", "offset"), temporaries, (),
                        ir._sequence(commands), types=kinds), pending


def test_array_fanout_scans_each_distinct_projection_once():
    kind = ArrayType(0, 7, IntegralType(4, False))
    subscriptions = tuple(Subscription(i, "cells", 0xffffffff, "change") for i in range(12))
    subscriptions += (Subscription(12, "cells", 0xffff, "rise"),)
    proc, _ = _procedure(kind, subscriptions)
    loops = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    assert len(loops) == 2  # Full change and half-array rise, not one per consumer.


@pytest.mark.parametrize("edge", ("change", "rise", "fall"))
@pytest.mark.parametrize("restore", (False, True))
def test_fanout_retains_independent_pending_bits_and_transient_events(edge, restore):
    kind = ArrayType(5, 4, IntegralType(2, False))
    subscriptions = (Subscription(0, "cells", 15, edge),
                     Subscription(1, "cells", 15, edge),
                     Subscription(2, "cells", 3, "change"),
                     Subscription(0, "cells", 0, "rise"))
    proc, pending = _procedure(kind, subscriptions, restore=restore)
    for before, after, flags in product(range(16), range(16), product((False, True), repeat=3)):
        initial = dict(zip(pending, flags))
        post, _, _, error = interp.run_step(proc,
            {"old": [before & 3, before >> 2], "new": [after & 3, after >> 2], "offset": 0}, initial)
        assert not error
        expected = initial.copy()
        for old, new in ((before, after), (after, before)) if restore else ((before, after),):
            for sub in subscriptions:
                changed = old ^ new
                if sub.edge != "change":
                    changed &= new if sub.edge == "rise" else old
                expected[pending[sub.process]] |= bool(changed & sub.mask)
        assert {name: post[name] for name in pending} == expected


@pytest.mark.solver
def test_dynamic_fanout_matches_independent_finite_bit_equations():
    import z3

    kind = IntegralType(4, True)
    subscriptions = tuple(Subscription(i, "cells", mask, edge)
                          for i, (mask, edge) in enumerate(
                              ((0x1357, "change"), (0x1357, "change"),
                               (0xffff, "rise"), (0xffff, "rise"), (0xf0, "fall"))))
    proc, pending = _procedure(kind, subscriptions, offset=ir.Var("offset"))
    env = ranges.fixpoint(proc, {"old": kind.domain, "new": kind.domain,
                                "offset": ranges.Range(0, 12),
                                **dict.fromkeys(pending, ranges.Range(0, 1))})
    encoded = encoder.encode_step(proc, env=env)
    old, new = (z3.Extract(3, 0, encoded.pre[name]) for name in ("old", "new"))
    errors = []
    for sub in subscriptions:
        changed = old ^ new
        if sub.edge != "change":
            changed &= new if sub.edge == "rise" else old
        hit = z3.Or(*(z3.And(encoded.pre['offset'] == position,
                            changed & ((sub.mask >> position) & 15) != 0)
                      for position in range(13)))
        name = pending[sub.process]
        expected = z3.Or(encoded.pre[name] != 0, hit)
        errors.append((encoded.post[name] != 0) != expected)
    solver = z3.Solver()
    solver.add(encoded.constraint, z3.Or(*errors))
    assert solver.check() == z3.unsat


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
def test_shared_array_notification_query_replays_the_original_source_goal(tmp_path):
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_interval
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower, _names, _state

    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [3:0] cells[0:2];
      wire [3:0] taps[0:2];
      for(genvar n=0;n<3;n=n+1) begin: fanout
        assign taps[n]=cells[n];
      end
      always @(posedge clk) begin
        cells[0]<=a; cells[1]<=a; cells[2]<=a;
      end
      always_comb if(taps[2]==9) q=123; else q=0;
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    shared = [sub for sub in machine.subscriptions
              if sub.storage == names['cells'] and sub.mask == 0xfff]
    assert len(shared) == 3  # Actual compiler-owned identical subscriptions.
    goal = _goal(source, plan, "taps[2]==9")
    state = _state(machine, {"clk": 0, "a": 0, "q": 0,
                             "cells": [0, 0, 0], "taps": [0, 0, 0]})
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    template = SourceIntervalTemplate(machine.procedure, env)
    answer = query_source_interval(machine, env, state,
        {names['clk']: 1, names['a']: 0}, (goal,), mutable_inputs=(names['a'],),
        event_bound=machine.event_bound, timeout_ms=30_000,
        _relation=template.relation(machine, (goal,)))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names['a']] == 9
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    outputs, hits = replay((0, answer.candidate[names['a']]))
    assert outputs == [0, 123]
    assert goal not in hits[0] and goal in hits[1]
