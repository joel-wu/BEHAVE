"""Branch precision follows only still-current, exact scalar copies."""

import itertools

import pytest

from behave.sim.symbolic import cfg, interp, ir, ranges, typecheck as types


def _sized_boolean(condition, wrappers):
    value = ir.Op("toint", condition)
    for op, width in wrappers:
        value = ir.Op(op, value, ir.Lit(width))
    return ir.Op("truth", value)


@pytest.mark.parametrize("wrappers", [(), (("uwrap", 1),), (("swrap", 1),),
    (("swrap", 1), ("uwrap", 8)), (("uwrap", 32), ("swrap", 1))])
@pytest.mark.parametrize("wanted", [False, True])
def test_boolean_integer_sizing_preserves_truth_refinement(wrappers, wanted):
    guard = _sized_boolean(ir.Op("lt", ir.Var("x"), ir.Lit(3)), wrappers)
    entry = {"x": ranges.Range(-5, 8)}
    got = ranges.assume(guard, wanted, entry)
    assert got["x"] == (ranges.Bounds(-5, 2) if wanted else ranges.Bounds(3, 8))
    assert entry["x"] == ranges.Range(-5, 8)


def test_wrapping_an_arbitrary_integer_is_not_a_boolean_copy():
    guard = ir.Op("truth", ir.Op("uwrap", ir.Var("x"), ir.Lit(8)))
    assert ranges.assume(guard, False, {"x": ranges.Range(256, 256)}) is not None
    assert ranges.assume(guard, True, {"x": ranges.Range(256, 256)}) is None


def _clamp(*, intervening=(), copied=True):
    x, saved = ir.Var("x"), ir.Var("saved")
    value = saved if copied else x
    predicate = _sized_boolean(ir.Op("gt", value, ir.Lit(3)), (("uwrap", 1),))
    prefix = [ir.Assign("saved", x), ir.Assign("decision", predicate), *intervening]
    branch = ir.If(ir.Var("decision"), ir.Assign("x", ir.Lit(3)), ir.Skip())
    return prefix, branch


@pytest.mark.parametrize("graph", [False, True])
def test_copied_operand_and_predicate_refine_the_same_clamp(graph):
    prefix, branch = _clamp()
    entry = {"x": ranges.Range(0, 15)}
    if graph:
        body = cfg.CFG({
            "start": cfg.Block("start", prefix, cfg.Branch(branch.guard, "yes", "no")),
            "yes": cfg.Block("yes", (branch.then,), cfg.Goto("exit")),
            "no": cfg.Block("no", (), cfg.Goto("exit")),
            "exit": cfg.Block("exit", (), cfg.Return()),
        }, "start")
        after = ranges.graph_post(body, entry)
    else:
        after = ranges.post(ir._sequence([*prefix, branch]), entry)
    assert after["x"] == ranges.Range(0, 3)


@pytest.mark.parametrize("mutation", [
    ir.Assign("x", ir.Lit(12)),
    ir.If(ir.Var("flag"), ir.Assign("x", ir.Lit(12)), ir.Skip()),
    ir.For("i", 1, ir.Assign("x", ir.Lit(12))),
])
def test_old_snapshots_do_not_refine_a_replaced_operand(mutation):
    prefix, branch = _clamp(intervening=(mutation,))
    after = ranges.post(ir._sequence([*prefix, branch]),
                        {"x": ranges.Range(0, 15), "flag": ranges.Range(0, 1)})
    assert after["x"].hi >= 12


def test_writing_the_saved_operand_invalidates_the_saved_predicate():
    prefix, branch = _clamp(intervening=(ir.Assign("saved", ir.Lit(12)),))
    after = ranges.post(ir._sequence([*prefix, branch]), {"x": ranges.Range(0, 15)})
    # The old decision is still valid for x, but this limited copy domain must
    # not substitute the *new* saved=12 into the original comparison.
    assert after["x"].hi == 15


def test_a_loop_counter_write_invalidates_an_old_copy():
    prefix, branch = _clamp(intervening=(ir.For("x", 8, ir.Skip()),))
    after = ranges.post(ir._sequence([*prefix, branch]), {"x": ranges.Range(0, 15)})
    assert after["x"].hi >= 7


def test_long_copy_chain_needs_neither_recursion_nor_arithmetic_expansion():
    copies = ranges._PredicateCopies()
    previous = ir.Var("x")
    for index in range(2000):
        command = ir.Assign(f"copy{index}", previous)
        copies.append(command)
        previous = ir.Var(command.var)
    predicate = ir.Op("le", previous, ir.Lit(3))
    recovered, _aliases = copies.predicate(predicate)
    assert repr(recovered) == "le(x, 3)"
    copies.append(ir.Assign("sum", ir.Op("add", previous, ir.Lit(1))))
    assert repr(copies.predicate(ir.Op("lt", ir.Var("sum"), ir.Lit(5)))[0]) == "lt(sum, 5)"


def test_reused_branch_object_does_not_borrow_another_sequences_copy_facts():
    branch = ir.If(ir.Var("decision"), ir.Assign("x", ir.Lit(3)), ir.Skip())
    first = ir.Seq(ir.Assign("decision", ir.Op("gt", ir.Var("x"), ir.Lit(3))), branch)
    second = ir.Seq(ir.Assign("decision", ir.Lit(False)), branch)
    cache = {}
    assert ranges._post(first, {"x": ranges.Range(0, 15)}, _flows=cache)["x"].hi == 3
    assert ranges._post(second, {"x": ranges.Range(0, 15)}, _flows=cache)["x"].hi == 15


@pytest.mark.parametrize("first,second", tuple(itertools.product((None, "x", "saved"), repeat=2)))
def test_range_post_contains_all_executions_across_copy_overwrites(first, second):
    commands = [ir.Assign("x", ir.Var("a")), ir.Assign("saved", ir.Var("x")),
                ir.Assign("decision", _sized_boolean(
                    ir.Op("gt", ir.Var("saved"), ir.Lit(3)), (("swrap", 1),)))]
    for name, value in ((first, 12), (second, -2)):
        if name is not None:
            commands.append(ir.Assign(name, ir.Lit(value)))
    commands += [ir.If(ir.Var("decision"), ir.Assign("x", ir.Lit(3)), ir.Skip()),
                 ir.Assign("y", ir.Var("x"))]
    body = ir._sequence(commands)
    after = ranges.post(body, {"a": ranges.Range(0, 7)})
    proc = ir.Procedure([], ["a"], ["x", "saved", "decision"], ["y"], body,
                        types={name: (types.BOOLEAN if name == "decision" else types.INTEGER)
                               for name in ("a", "x", "saved", "decision", "y")})
    for value in range(8):
        _state, outputs, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        result, = outputs.buffers["y"]
        assert after["y"].lo <= result <= after["y"].hi
