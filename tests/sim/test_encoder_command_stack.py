"""Deep command control uses heap continuations with unchanged guarded state."""

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import encoder, ir, ranges


def problem(depth):
    body = ir.Seq(ir.Mark("inside"), ir.Assign("y", ir.Lit(7)))
    for index in range(depth):
        guard = ir.Op("eq", ir.Var("a"), ir.Lit(index % 2))
        body = (ir.If(guard, body, ir.Skip()) if index % 2 == 0 else
                ir.If(guard, ir.Skip(), body))
    tail = ir.If(ir.Var("fail"), ir.Fail("DivZero", "bad"), ir.Skip())
    body = ir.Seq(body, ir.Seq(tail, ir.Mark("after")))
    env = {"a": ranges.Range(0, 1), "fail": ranges.Range(0, 1), "y": ranges.Range(0, 7)}
    enc = encoder._Commands(None, env)
    state = encoder._State({"a": z3.BitVec("a", 4), "fail": z3.BitVec("fail", 4),
                            "y": z3.BitVecVal(0, 4)}, {}, z3.BoolVal(True),
                           z3.BitVecVal(0, encoder._code("", enc.error_codes).size()))
    return enc, state, body


@pytest.mark.parametrize("depth", (1, 1200))
@pytest.mark.parametrize("pause", (False, True))
def test_deep_branch_outputs_hits_and_failure_survive_pauses(depth, pause, monkeypatch):
    enc, state, body = problem(depth)
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if pause and calls % 23 == 0:
            raise encoder._EncodingPaused

    monkeypatch.setattr(enc, "checkpoint", checkpoint)
    walk = enc._cmd(body, state, z3.BoolVal(True))
    while True:
        try:
            assert next(walk) is None
        except StopIteration as done:
            out = done.value
            break
    assert enc._read_guard is None and enc.loop_context == []
    assert enc._branch_guard is None and state._original is None
    for a in (0, 1):
        for fail in (0, 1):
            solver = z3.SolverFor("QF_BV")
            solver.add(*enc.defs, state.rho["a"] == a, state.rho["fail"] == fail)
            differs = [out.rho["y"] != (7 if a == 0 else 0),
                       out.hits["inside"] != (a == 0), out.hits["bad"] != bool(fail),
                       out.hits["after"] != (fail == 0), out.ok != (fail == 0)]
            solver.add(z3.Or(*differs))
            assert solver.check() == z3.unsat


@pytest.mark.parametrize("inside_loop", (False, True))
def test_closing_suspended_deep_control_unwinds_read_scopes(monkeypatch, inside_loop):
    enc, state, body = problem(1200)
    if inside_loop:
        from behave.sim.symbolic.plan import ExpressionPosition

        body = ir.For("i", 1, body)
        enc.command_numbers[id(body)] = 0
        enc.loop_bounds[str(ExpressionPosition(0, "trip_count"))] = 1
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 1000:
            raise encoder._EncodingPaused

    monkeypatch.setattr(enc, "checkpoint", checkpoint)
    walk = enc._cmd(body, state, z3.BoolVal(True))
    assert next(walk) is None
    assert enc._read_guard is not None
    walk.close()
    assert enc._read_guard is None and enc.loop_context == []
    assert enc._branch_guard is None and state._original is None


@pytest.mark.parametrize("seed", range(16))
def test_branch_deltas_match_native_values_hits_and_failed_prefixes(seed):
    from copy import deepcopy
    from itertools import product
    from random import Random

    from behave.sim.symbolic import interp
    from behave.sim.symbolic.plan import ExpressionPosition, _command_numbers

    random = Random(seed)

    def command(depth):
        if depth:
            return ir.If(ir.Op("eq", ir.Var(random.choice(("a", "b", "x"))),
                               ir.Lit(random.randrange(3))),
                ir._sequence((command(depth - 1), command(depth - 1))),
                command(depth - 1))
        choice = random.randrange(5)
        if choice == 0:
            return ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Var("b")))
        if choice == 1:
            return ir.Store("table", ir.Var("a"), ir.Var("x"))
        if choice == 2:
            return ir.Assign("x", ir.Sel(ir.Var("table"), ir.Var("b")))
        if choice == 3:
            return ir.Fail("DivZero", "failure")
        return ir.Mark("visited")

    body = ir._sequence((command(2),
        ir.For("i", seed % 4, command(1)),
        ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)),
            ir.Assign("x", ir.Lit(-7)), ir.Assign("x", ir.Var("b"))),
        ir.Mark("finished")))
    symbols = {name: z3.BitVec(name, 4) for name in ("a", "b", "x")}
    initial = {**symbols, "i": z3.BitVecVal(0, 4),
               "table": [z3.BitVecVal(v, 4) for v in (1, -1, 3)]}
    enc = encoder._Commands(None, {name: ranges.Range(-4096, 4095) for name in initial})
    enc.command_numbers = _command_numbers(body)
    enc.loop_bounds = {str(ExpressionPosition(enc.command_numbers[id(c)], "trip_count")): seed % 4
                       for c in ir.walk(body) if isinstance(c, ir.For)}
    state = encoder._State(initial, {}, z3.BoolVal(True), encoder._code(""))
    out = enc.cmd(body, state, z3.BoolVal(True))
    assert enc._branch_guard is None and state._original is None
    for a, b, x in product(range(3), range(3), (-2, 0, 3)):
        concrete = {"a": a, "b": b, "x": x, "i": 0, "table": [1, -1, 3]}
        native, hits, error = interp._Interp(deepcopy(concrete)), set(), ""
        try:
            native.run(body, hits)
        except interp.Halt as stopped:
            error = stopped.code
        solver = z3.Solver()
        solver.add(*enc.defs, *(symbols[name] == concrete[name] for name in symbols))
        assert solver.check() == z3.sat
        differs = [out.ok != (not error), out.error != encoder._code(error)]
        for name, expected in native.rho.items():
            actual = out.rho[name]
            if isinstance(expected, list):
                differs.extend(got != want for got, want in zip(actual, expected, strict=True))
            else:
                differs.append(actual != expected)
        differs.extend(term != (goal in hits) for goal, term in out.hits.items())
        assert hits <= out.hits.keys()
        solver.add(z3.Or(*differs))
        assert solver.check() == z3.unsat, (seed, concrete, native.rho, hits, error)


def test_branch_journal_never_scans_untouched_state():
    class NoScan(dict):
        def __iter__(self):
            pytest.fail("branch copied the full valuation")

        keys = items = values = copy = __iter__

    enc, state, _ = problem(1)
    state.rho = NoScan({**state.rho, **{f"untouched_{i}": z3.BitVecVal(3, 4) for i in range(10000)}})
    before = state.rho
    body = ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
                 ir.Assign("y", ir.Lit(1)), ir.Assign("y", ir.Lit(2)))
    out = enc.cmd(body, state, z3.BoolVal(True))
    assert out.rho is before and out.rho["untouched_9999"].as_long() == 3


def test_cancelling_inside_branch_restores_changed_entry_fields(monkeypatch):
    enc, state, nested = problem(12)
    original = state.rho["y"]
    body = ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
        ir.Seq(ir.Assign("y", ir.Lit(3)), nested), ir.Skip())

    def checkpoint():
        if state.rho["y"] is not original:
            raise encoder._EncodingPaused

    monkeypatch.setattr(enc, "checkpoint", checkpoint)
    walk = enc._cmd(body, state, z3.BoolVal(True))
    assert next(walk) is None
    assert state.rho["y"] is not original
    walk.close()
    assert state.rho["y"] is original
    assert state._original is None and enc._branch_guard is None and enc._read_guard is None


def test_complete_branch_overwrites_remove_the_entry_dependency():
    enc = encoder._Commands(None, {name: ranges.Range(-8, 7) for name in ("a", "x")})
    entry, a = z3.BitVecs("old_x branch_input", 4)
    state = encoder._State({"a": a, "x": entry}, {}, z3.BoolVal(True), encoder._code(""))
    body = ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
                 ir.Assign("x", ir.Lit(1)), ir.Assign("x", ir.Lit(2)))
    out = enc.cmd(body, state, z3.BoolVal(True))
    definitions = {d.lhs.get_id(): d.rhs for d in enc.definitions}
    pending, seen = [out.rho["x"]], set()
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        assert not term.eq(entry)
        pending.extend((definitions[term.get_id()],) if term.get_id() in definitions else term.children())
    solver = z3.Solver()
    solver.add(*enc.defs, out.rho["x"] != z3.If(a == 0, z3.BitVecVal(1, out.rho["x"].size()),
                                             z3.BitVecVal(2, out.rho["x"].size())))
    assert solver.check() == z3.unsat
