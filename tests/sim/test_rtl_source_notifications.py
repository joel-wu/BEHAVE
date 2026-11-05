"""Word-local notifications versus independent finite bit/event semantics."""

from itertools import pairwise, product

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_ir import _sequence
from behave.sim.rtl.source_notifications import mask_spans, notify_value
from behave.sim.rtl.source_scheduler import Subscription
from behave.sim.rtl.source_values import ArrayType


def _procedure(kind, mask, edge, *, offset):
    kinds = {"old": kind.ir_type, "new": kind.ir_type, "offset": types.INTEGER, "pending": types.BOOLEAN}

    def temp():
        name = f"t{len(kinds)}"
        kinds[name] = types.INTEGER
        return name

    body = notify_value(kind, ir.Var("old"), ir.Var("new"),
        (Subscription(0, "unused", mask, edge),), ("pending",), temp, offset=offset)
    return ir.Procedure(["pending"], ["old", "new", "offset"],
        [name for name in kinds if name.startswith("t")], [], _sequence(body), types=kinds)


def test_mask_spans_reconstruct_sparse_and_ram_sized_subscriptions():
    for mask in (0, 1, 2, 0b110110001, (1 << 2097152) - 1, 0xff << 1600000):
        spans = tuple(mask_spans(mask))
        assert sum(((1 << (high-low)) - 1) << low for low, high in spans) == mask
        assert all(a[1] < b[0] for a, b in pairwise(spans))


@pytest.mark.solver
@pytest.mark.parametrize("edge", ["change", "rise", "fall"])
@pytest.mark.parametrize("mask", [0, 1, 0x1529, 0xffff, 0xf000])
@pytest.mark.parametrize("signed", [False, True])
def test_dynamic_word_notifications_equal_finite_projection_for_all_values(edge, mask, signed):
    import z3

    kind = IntegralType(4, signed)
    proc = _procedure(kind, mask, edge, offset=ir.Var("offset"))
    env = ranges.fixpoint(proc, {"old": kind.domain, "new": kind.domain,
        "offset": ranges.Range(0, 12), "pending": ranges.Range(0, 1)})
    enc = encoder.encode_step(proc, env=env)
    old, new = (enc.pre[name] for name in ("old", "new"))
    old, new = (z3.Extract(3, 0, value) if value.size() > 4 else value for value in (old, new))
    changes = old ^ new
    if edge != "change":
        changes &= new if edge == "rise" else old
    # Independent enumeration of the finite position domain, not the generated
    # clamp/shift implementation. Solver quantifies every old/new/pending value.
    hit = z3.Or(*(z3.And(enc.pre["offset"] == position,
        changes & ((mask >> position) & 15) != 0) for position in range(13)))
    expected = z3.Or(enc.pre["pending"] != 0, hit)
    solver = z3.Solver()
    solver.add(enc.constraint, (enc.post["pending"] != 0) != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("edge", ["change", "rise", "fall"])
def test_recursive_whole_array_notifications_preserve_partial_cell_masks(edge):
    kind = ArrayType(7, 6, ArrayType(1, 0, IntegralType(4, True)))
    mask = 0x8163
    proc = _procedure(kind, mask, edge, offset=ir.Lit(0))
    for before, after, pending in product((0, 0x1234, 0xffff), (0, 0xaabb, 0xffff), (False, True)):
        def value(bits):
            flat = [(bits >> (4*i)) & 15 for i in range(4)]
            return kind.decode([flat[:2], flat[2:]])
        changed = before ^ after
        if edge != "change":
            changed &= after if edge == "rise" else before
        post, _, _, error = interp.run_step(proc,
            {"old": value(before), "new": value(after), "offset": 0}, {"pending": pending})
        assert not error
        assert post["pending"] == (pending or bool(changed & mask))


def test_whole_array_notification_does_not_visit_unsubscribed_gaps():
    kind = ArrayType(0, 65535, IntegralType(32, False))
    proc = _procedure(kind, (0xf << 7) | (0xf << (65535*32+7)), "change", offset=ir.Lit(0))
    assert not any(isinstance(command, ir.For) for command in ir.walk(proc.body))
    before = [0]*65536
    after = before.copy()
    after[32768] = 0xffffffff
    post, _, _, error = interp.run_step(proc,
        {"old": before, "new": after, "offset": 0}, {"pending": False})
    assert not error and not post["pending"]
    after[-1] = 1 << 7
    post, _, _, error = interp.run_step(proc,
        {"old": before, "new": after, "offset": 0}, {"pending": False})
    assert not error and post["pending"]


@pytest.mark.solver
def test_large_ram_selected_word_uses_only_word_width_symbolic_operations():
    import z3

    kind = IntegralType(32, False)
    proc = _procedure(kind, 0xf << (50000 * 32 + 7), "change",
                      offset=ir.Op("mul", ir.Var("offset"), ir.Lit(32)))
    env = ranges.fixpoint(proc, {"old": kind.domain, "new": kind.domain,
        "offset": ranges.Range(0, 65535), "pending": ranges.Range(0, 1)})
    enc = encoder.encode_step(proc, env=env)
    expected = z3.Or(enc.pre["pending"] != 0,
        z3.And(enc.pre["offset"] == 50000, (enc.pre["old"] ^ enc.pre["new"]) & 0x780 != 0))
    solver = z3.Solver()
    solver.add(enc.constraint, (enc.post["pending"] != 0) != expected)
    assert solver.check() == z3.unsat


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
@pytest.mark.parametrize(("assignment", "cells"), [("=", 4096), ("<=", 512), ("<=", 65536)])
def test_large_array_write_search_and_native_confirmation(tmp_path, assignment, cells):
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_interval
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower, _names, _state

    base = cells // 2
    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [31:0] x[{cells}];
      always @(posedge clk) x[{base}+a]{assignment}a+4;
      always_comb if(x[{base+5}]==9) q=123; else q=0;
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    goal = _goal(source, plan, f"x[{base+5}]==9")
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": [0]*cells})
    # One immutable source template per artifact, as in the public provider;
    # every goal still gets its own projection/query and native confirmation.
    template = SourceIntervalTemplate(machine.procedure, env)
    answer = query_source_interval(machine, env, state, {names["clk"]: 1, names["a"]: 0},
        (goal,), mutable_inputs=(names["a"],), event_bound=machine.event_bound, timeout_ms=30_000,
        _relation=template.relation(machine, (goal,)))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names["a"]] == 5
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    outputs, hits = replay((0, answer.candidate[names["a"]]))
    assert goal not in hits[0] and goal in hits[1]
    assert outputs == [0, 123]
    # Audit the whole declared source catalogue at this boundary, not just the
    # attractive SAT example. Each value starts a fresh original/native run.
    oracle = [replay((value,))[1][0] for value in range(16)]
    for target in {target for _mark, _key, target in machine.occurrences}:
        answer = query_source_interval(machine, env, state, {names["clk"]: 1, names["a"]: 0},
            (target,), mutable_inputs=(names["a"],), event_bound=machine.event_bound, timeout_ms=30_000,
            _relation=template.relation(machine, (target,)))
        reached = any(target in observed for observed in oracle)
        assert answer.outcome is (probing.Outcome.SAT if reached else probing.Outcome.LOCAL_UNSAT), (
            target, answer.detail)
        if reached:
            assert target in oracle[answer.candidate[names["a"]]]
