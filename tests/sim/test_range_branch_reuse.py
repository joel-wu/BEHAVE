"""Repeated predicated transfers share values, never observations or assumptions."""

import pytest

from behave.sim.symbolic import ir, ranges


def transfer(command, env, posts, **kwargs):
    return ranges._post(command, dict(env),
                        _flows=ranges._range_transfer_cache(loop_posts=posts), **kwargs)


def branch():
    return ir.If(ir.Var("guard"), ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1))),
                 ir.Assign("x", ir.Lit(3)))


def test_complete_transfer_reused_without_unrelated_values_or_mutable_results(monkeypatch):
    command, posts, visited = branch(), {}, []
    calculate = ranges._value_range

    def counted(*args):
        visited.append(1)
        return calculate(*args)

    monkeypatch.setattr(ranges, "_value_range", counted)
    original = dict(guard=ranges.Range(0, 1), x=ranges.Range(0, 2), other=ranges.Range(5, 5))
    first = transfer(command, original, posts)
    count = len(visited)
    second = transfer(command, {**original, "other": ranges.Range(9, 9)}, posts)
    assert len(visited) == count
    assert first["x"] == second["x"] == ranges.Range(1, 3)
    assert first["other"] == ranges.Range(5, 5) and second["other"] == ranges.Range(9, 9)
    second["x"] = ranges.Range(-1, -1)
    assert transfer(command, original, posts) == first


@pytest.mark.parametrize("entry", [{}, {"x": ranges.Range(0, 0)}, {"x": ranges.Range(-2, 4)}])
@pytest.mark.parametrize("guard", [0, 1])
def test_missing_or_changed_inputs_and_failures_preserve_uncached_results(entry, guard):
    command, posts = branch(), {}
    transfer(command, {"guard": ranges.Range(0, 1), "x": ranges.Range(0, 2)}, posts)
    env = {"guard": ranges.Range(guard, guard), **entry}
    assert transfer(command, env, posts) == transfer(command, env, {})
    failure = ir.If(ir.Var("guard"), ir.Fail("Assertion", "bad"), ir.Skip())
    assert transfer(failure, env, posts) == transfer(failure, env, {})


def test_recovered_predicate_and_copy_bindings_are_in_the_key():
    command, posts = branch(), {}
    env = {"guard": ranges.Range(0, 1), "x": ranges.Range(0, 0)}
    for predicate in (ir.Lit(False), ir.Lit(True)):
        assert transfer(command, env, posts, _guard=(predicate, ())) == (
            transfer(command, env, {}, _guard=(predicate, ())))


def test_observed_transfers_still_visit_every_command():
    command, posts = branch(), {}
    env = {"guard": ranges.Range(0, 1), "x": ranges.Range(0, 0)}
    transfer(command, env, posts)
    actual, expected = [], []
    first = transfer(command, env, posts,
        witness=lambda cmd, at: actual.append((cmd, dict(at))))
    second = transfer(command, env, {},
        witness=lambda cmd, at: expected.append((cmd, dict(at))))
    assert first == second and actual == expected
    assert {id(cmd) for cmd, _ in actual} == {id(command), id(command.then), id(command.orelse)}


def test_array_elements_not_only_their_hull_bind_a_branch_summary():
    command = ir.If(ir.Var("guard"), ir.Assign("x", ir.Sel(ir.Var("a"), ir.Lit(0))), ir.Skip())
    posts = {}
    for cells in ([0, 1], [1, 0], [0, 1]):
        env = {"guard": ranges.Range(1, 1),
               "a": ranges._CellRange(tuple(ranges.Range(value, value) for value in cells))}
        assert transfer(command, env, posts) == transfer(command, env, {})
        assert transfer(command, env, posts)["x"] == ranges.Range(cells[0], cells[0])


def test_interrupted_branch_does_not_publish_its_partial_result(monkeypatch):
    command, posts = branch(), {}
    calculate = ranges._value_range

    def interrupted(*args):
        calculate(*args)
        raise TimeoutError("cancel range transfer")

    with monkeypatch.context() as patch:
        patch.setattr(ranges, "_value_range", interrupted)
        with pytest.raises(TimeoutError):
            transfer(command, {"guard": ranges.Range(1, 1), "x": ranges.Range(0, 0)}, posts)
    assert not posts


def test_planning_syntax_cache_does_not_share_range_dependent_tables():
    syntax = {}
    first = ranges._range_transfer_cache(syntax=syntax)
    second = ranges._range_transfer_cache(syntax=syntax)
    for key in (ranges._FOOTPRINT_CACHE, ranges._GRAPH_FOOTPRINT_CACHE,
                ranges._GRAPH_DECISIONS_CACHE, ranges._EXPRESSION_NAME_CACHE,
                ranges._SEQUENCE_PREDICATES):
        assert first[key] is second[key]
    for key in (ranges._VALUE_RANGE_CACHE, ranges._FINITE_ASSUMPTION_CACHE,
                ranges._RANGE_JOIN_CACHE):
        first[key] = {"transient": object()}
        assert key not in second
