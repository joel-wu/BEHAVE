"""Predicated loop-index propagation preserves the complete command result."""

from functools import reduce

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import encoder, interp, ir, ranges
from behave.sim.symbolic.functional_array import FunctionalArray


def seq(*commands):
    return reduce(ir.Seq, commands)


def command(kind):
    i, x = ir.Var("i"), ir.Var("x")
    middle = ir.Store("buf", i, ir.Op("add", x, i))
    if kind == "count-write":
        middle = seq(middle, ir.Assign("n", ir.Lit(-1)))
    elif kind.startswith("nested"):
        index = "i" if kind == "nested-same" else "j"
        middle = seq(ir.For(index, ir.Var("n"),
                           ir.Assign("x", ir.Op("add", x, ir.Var(index)))), middle)
    elif kind == "index-write":
        middle = seq(middle, ir.Assign("i", ir.Lit(1)))
    loop = ir.For("i", ir.Var("n"), seq(
        ir.Mark("visited"),
        ir.Assign("x", ir.Op("add", x, i)),
        ir.If(ir.Op("eq", i, ir.Var("stop")),
              ir.Fail("Stopped", "failed"), ir.Skip()),
        middle))
    return seq(ir.If(ir.Op("eq", ir.Var("enabled"), ir.Lit(1)), loop, ir.Skip()),
               ir.Assign("y", ir.Var("i")), ir.Mark("finished"))


def encode(body, pre, tag):
    env = {name: ranges.Range(-16, 32) for name in pre}
    enc = encoder._Commands(None, env, tag)
    enc.scalar_width = 16
    enc.error_codes = ("", "Stopped")
    for number, node in enumerate(ir.walk(body)):
        enc.command_numbers[id(node)] = number
    # Use the same occurrence spelling as the public planner.
    from behave.sim.symbolic.plan import ExpressionPosition
    enc.loop_bounds = {str(ExpressionPosition(enc.command_numbers[id(node)], "trip_count")): 3
                       for node in ir.walk(body) if isinstance(node, ir.For)}
    state = encoder._State(pre, {}, z3.BoolVal(True), z3.BitVecVal(0, 1))
    return enc, enc.cmd(body, state, z3.BoolVal(True))


def leaves(value):
    if isinstance(value, FunctionalArray):
        return [value.select(z3.BitVecVal(i, 16)) for i in range(value.length)]
    return value if isinstance(value, list) else [value]


@pytest.mark.parametrize("kind", ["basic", "count-write", "nested-distinct",
                                  "nested-same", "index-write"])
def test_specialized_and_guarded_indices_have_identical_results(monkeypatch, kind):
    body = command(kind)
    pre = {name: z3.BitVec(f"index_pre_{name}", 16)
           for name in ("enabled", "n", "stop")}
    pre.update({name: z3.BitVecVal(value, 16)
                for name, value in {"i": 6, "j": 5, "x": 1, "y": 7}.items()})
    pre["buf"] = [z3.BitVecVal(0, 16) for _ in range(3)]
    fast, after = encode(body, pre, "specialized")
    with monkeypatch.context() as patch:
        patch.setattr(encoder._Commands, "_read_only_index", staticmethod(lambda _: False))
        old, before = encode(body, pre, "guarded")
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(*fast.defs, *old.defs,
               pre["enabled"] >= 0, pre["enabled"] <= 1,
               pre["n"] >= -1, pre["n"] <= 3,
               pre["stop"] >= -1, pre["stop"] <= 2)
    differences = [after.ok != before.ok, after.error != before.error]
    for name in pre:
        differences.extend(a != b for a, b in zip(leaves(after.rho[name]),
                                                  leaves(before.rho[name]), strict=True))
    assert after.hits.keys() == before.hits.keys()
    differences.extend(after.hits[g] != before.hits[g] for g in after.hits)
    solver.push()
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat
    solver.pop()

    # Also check against command interpretation, independently of both encoders.
    for enabled, n, stop in [(0, 3, -1), (1, -1, -1), (1, 0, -1),
                              (1, 3, -1), (1, 3, 1)]:
        values = {"enabled": enabled, "n": n, "stop": stop,
                  "i": 6, "j": 5, "x": 1, "y": 7, "buf": [0] * 3}
        native = interp._Interp(values)
        hits, failed = set(), False
        try:
            native.run(body, hits)
        except interp.Halt as halt:
            assert halt.code == "Stopped"
            failed = True
        solver.push()
        solver.add(pre["enabled"] == enabled, pre["n"] == n, pre["stop"] == stop)
        assert solver.check() == z3.sat
        model = solver.model()
        assert z3.is_true(model.eval(after.ok)) == (not failed)
        assert {g for g, hit in after.hits.items() if z3.is_true(model.eval(hit))} == hits
        for name, value in values.items():
            expected = value if isinstance(value, list) else [value]
            assert [model.eval(t).as_signed_long() for t in leaves(after.rho[name])] == expected
        solver.pop()
