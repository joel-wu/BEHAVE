"""Guard precision must survive joins without retaining facts about old values."""
import itertools
import operator

import pytest

from behave.sim.symbolic import cfg, ir, range_relations, ranges


@pytest.mark.parametrize('operation,width,lo,hi,expected', [
    ('swrap', 8, -128, 127, 'value'),
    ('swrap', 8, -129, 127, None),
    ('swrap', 8, 0, 128, None),
    ('uwrap', 8, 0, 255, 'value'),
    ('uwrap', 8, -1, 255, None),
    ('uwrap', 8, 0, 256, None),
    ('swrap', 1, -1, 0, 'value'),
    ('swrap', 1, 0, 1, None),
    ('uwrap', 1, 0, 1, 'value'),
])
def test_copy_conversion_requires_every_input_value_to_be_preserved(operation, width, lo, hi, expected):
    expression = ir.Op(operation, ir.Var('value'), ir.Lit(width))
    assert range_relations.copied_name(expression, {'value': ranges.Range(lo, hi)}) == expected
    assert range_relations.copied_name(expression, {}) is None


@pytest.mark.parametrize('operation,width', [('swrap', 8), ('uwrap', 8), ('swrap', 4)])
@pytest.mark.parametrize('overwrite', [None, 'value', 'out'])
def test_structured_range_copies_keep_clamps_and_invalidate_changed_values(operation, width, overwrite):
    from behave.sim.symbolic.interp import _Interp

    choose = ir.If(ir.Op('gt', ir.Var('value'), ir.Lit(10)), ir.Assign('out', ir.Lit(10)),
        ir.If(ir.Op('lt', ir.Var('value'), ir.Lit(1)), ir.Assign('out', ir.Lit(1)), ir.Skip()))
    commands = [ir.Assign('out', ir.Op(operation, ir.Var('value'), ir.Lit(width)))]
    if overwrite is not None:
        commands.append(ir.Assign(overwrite, ir.Lit(5)))
    body = ir._sequence([*commands, choose])
    proc = ir.Procedure([], ['value'], ['out'], [], body)
    low, high = (-100, 100) if operation == 'swrap' else (0, 200)
    before = {'value': ranges.Range(low, high)}
    after = ranges.procedure_post(proc, before, source_paths=True)
    values = []
    for value in range(low, high + 1):
        interpreter = _Interp({'value': value})
        interpreter.run(body, set())
        values.append(interpreter.rho['out'])
    assert after['out'].lo <= min(values) <= max(values) <= after['out'].hi
    if width == 8 and overwrite is None:
        assert after['out'] == ranges.Range(1, 10)
    assert before == {'value': ranges.Range(low, high)}


def test_structured_graph_observer_preserves_original_branch_and_terminal_failure():
    failed = ir.Fail('stop', 'failure')
    branch = ir.If(ir.Op('eq', ir.Var('value'), ir.Lit(0)), failed, ir.Skip())
    after = ir.Mark('after')
    proc = ir.Procedure([], ['value'], [], [], ir.Seq(branch, after))
    seen = []
    result = ranges.procedure_post(proc, {'value': ranges.Range(0, 0)},
        witness=lambda command, _at: seen.append(command), source_paths=True)
    assert result is None
    assert seen == [branch, failed]


def test_unrelated_writes_reuse_facts_without_rescanning_affine_keys(monkeypatch):
    facts = range_relations.forget({
        ((f'x{i}', 1), (f'y{i}', -1)): ranges.Bounds(-i, i) for i in range(256)}, ())

    def scanned(*args, **kwargs):
        pytest.fail('unrelated writes must not rescan or copy retained affine facts')

    monkeypatch.setattr(type(facts), 'items', scanned)
    monkeypatch.setattr(type(facts), '__iter__', scanned)
    for i in range(1024):
        assert range_relations.forget(facts, {f'scratch{i}'}) is facts
    assert range_relations.forget(facts, ()) is facts


def test_shared_join_does_not_rescan_unchanged_conditional_facts(monkeypatch):
    facts = range_relations.forget({
        ((f'x{i}', 1), (f'y{i}', -1)): ranges.Bounds(-i, i) for i in range(256)}, ())

    def scanned(*args, **kwargs):
        pytest.fail('joining identical immutable facts must not rebuild their relations')

    monkeypatch.setattr(type(facts), 'items', scanned)
    monkeypatch.setattr(type(facts), '__iter__', scanned)
    for _ in range(1024):
        assert range_relations.join(facts, facts) is facts


def test_shared_mutable_join_is_detached_and_different_predecessors_still_join():
    key = (('x', 1),)
    source = {key: ranges.Bounds(0, 2)}
    joined = range_relations.join(source, source)
    source.clear()
    assert joined == {key: ranges.Bounds(0, 2)}
    other = range_relations.forget({key: ranges.Bounds(1, 4)}, ())
    assert range_relations.join(joined, other) == {key: ranges.Bounds(0, 4)}
    assert range_relations.join(joined, {}) == {}


@pytest.mark.parametrize('seed', range(12))
def test_indexed_invalidation_matches_plain_fact_filter_and_preserves_predecessors(seed):
    import random

    rng = random.Random(seed)
    names = [f'x{i}' for i in range(12)]
    original = {}
    for _ in range(80):
        selected = sorted(rng.sample(names, rng.randint(1, 4)))
        original[tuple((name, rng.choice((-2, -1, 1, 2))) for name in selected)] = ranges.Bounds(-4, 9)
    initial = range_relations.forget(original, ())
    for _ in range(30):
        written = set(rng.sample(names, rng.randrange(len(names) + 1)))
        expected = {key: bound for key, bound in original.items()
                    if not any(name in written for name, _ in key)}
        assert range_relations.forget(initial, written) == expected
        assert initial == original
    # Branch owners retain detached, read-only snapshots of caller mappings.
    before = dict(initial)
    original.clear()
    assert dict(initial) == before
    key = next(iter(initial))
    with pytest.raises(TypeError):
        initial[key] = ranges.Bounds(0, 0)
    with pytest.raises(AttributeError):
        initial.operands = frozenset()
    with pytest.raises(TypeError):
        initial._values[key] = ranges.Bounds(0, 0)


def test_fact_serialization_preserves_ownership_and_operand_invalidation():
    import pickle

    facts = range_relations.copy({}, 'left', 'right')
    restored = pickle.loads(pickle.dumps(facts))
    assert restored == facts and restored is not facts
    assert range_relations.forget(restored, {'other'}) is restored
    assert range_relations.forget(restored, {'left'}) == {}
    assert restored == facts


def test_shared_facts_do_not_leak_new_assumptions_or_copies_across_branches():
    initial = range_relations.assume({}, ir.Op('le', ir.Var('x'), ir.Var('y')), True)
    before = dict(initial)
    unrelated = range_relations.forget(initial, {'scratch'})
    right = range_relations.assume(unrelated, ir.Op('ge', ir.Var('z'), ir.Lit(7)), True)
    copied = range_relations.copy(initial, 'saved', 'x')
    assert dict(initial) == before
    assert len(right) == len(copied) == len(initial) + 1
    assert range_relations.join(right, initial) == initial
    assert range_relations.forget(copied, {'x'}) == {}
    assert range_relations.forget(right, {'z'}) == initial


@pytest.mark.parametrize("name,predicate", [
    ("lt", operator.lt), ("le", operator.le), ("gt", operator.gt),
    ("ge", operator.ge), ("eq", operator.eq), ("ne", operator.ne),
])
@pytest.mark.parametrize("truth", [False, True])
@pytest.mark.parametrize("offset", [-3, 0, 5])
def test_affine_guard_bounds_contain_every_selected_value(name, predicate, truth, offset):
    x, y = ir.Var("x"), ir.Var("y")
    left = ir.Op("add", x, ir.Lit(offset))
    guard = ir.Op(name, left, y)
    facts = range_relations.assume({}, guard, truth)
    env = {"x": ranges.Range(-5, 5), "y": ranges.Range(-5, 5)}
    for expression, evaluate in (
        (ir.Op("sub", x, y), lambda a, b: a - b),
        (ir.Op("sub", y, x), lambda a, b: b - a),
        (ir.Op("sub", ir.Lit(9), ir.Op("sub", x, y)), lambda a, b: 9 - (a - b)),
    ):
        interval = range_relations.refine(
            expression, ranges.of_expression(expression, env), facts)
        for a, b in itertools.product(range(-5, 6), repeat=2):
            if predicate(a + offset, b) == truth:
                value = evaluate(a, b)
                assert interval.lo <= value <= interval.hi


def _guarded_graph(then, otherwise=(), *, join=()):
    return cfg.CFG({
        "entry": cfg.Block("entry", (), cfg.Branch(
            ir.Op("le", ir.Var("used"), ir.Var("available")), "yes", "no")),
        "yes": cfg.Block("yes", then, cfg.Goto("join")),
        "no": cfg.Block("no", otherwise, cfg.Goto("join")),
        "join": cfg.Block("join", join, cfg.Return()),
    }, "entry")


def _capacity_environment():
    return {"available": ranges.Range(0, 64), "used": ranges.Range(0, 16)}


def test_guarded_subtraction_and_opposite_branch_preserve_capacity():
    graph = _guarded_graph(
        [ir.Assign("available", ir.Op("sub", ir.Var("available"), ir.Var("used")))],
        [ir.Assign("available", ir.Op("add", ir.Lit(64),
                                     ir.Op("sub", ir.Var("available"), ir.Var("used"))))])
    after = ranges.graph_post(graph, _capacity_environment())
    assert after["available"] == ranges.Range(0, 64)


def test_guard_fact_is_dropped_after_an_operand_write():
    graph = _guarded_graph([
        ir.Assign("used", ir.Lit(100)),
        ir.Assign("available", ir.Op("sub", ir.Var("available"), ir.Var("used"))),
    ])
    assert ranges.graph_post(graph, _capacity_environment())["available"].lo == -100


def test_guard_fact_is_dropped_after_a_compound_operand_write():
    graph = _guarded_graph([
        ir.Seq(ir.Assign("used", ir.Lit(100)), ir.Assign("other", ir.Lit(0))),
        ir.Assign("available", ir.Op("sub", ir.Var("available"), ir.Var("used"))),
    ])
    assert ranges.graph_post(graph, _capacity_environment())["available"].lo == -100


def test_join_does_not_inherit_one_predecessors_guard():
    graph = _guarded_graph([], join=[
        ir.Assign("difference", ir.Op("sub", ir.Var("available"), ir.Var("used"))),
    ])
    assert ranges.graph_post(graph, _capacity_environment())["difference"].lo == -16


def test_irrelevant_assignment_preserves_relation_across_a_goto():
    graph = _guarded_graph([ir.Assign("other", ir.Lit(999))])
    graph.blocks["no"].term = cfg.Fail("not-selected")
    graph.blocks["join"].instrs.append(
        ir.Assign("difference", ir.Op("sub", ir.Var("available"), ir.Var("used"))))
    assert ranges.graph_post(graph, _capacity_environment())["difference"].lo == 0


def test_representation_only_impossible_edges_do_not_acquire_false_guards():
    graph = _guarded_graph([
        ir.Assign("difference", ir.Op("sub", ir.Var("available"), ir.Var("used"))),
    ], [ir.Assign("difference", ir.Lit(-1))])
    seen = []
    ranges.graph_post(graph, {"available": ranges.Range(0, 0), "used": ranges.Range(5, 5)},
                      _include_infeasible_edges=True,
                      terminator_witness=lambda label, _term, env: seen.append((label, dict(env))))
    assert dict(seen)["yes"]["difference"] == ranges.Range(-5, -5)


def test_disjunction_does_not_invent_conjunctive_facts():
    guard = ir.Op("lor", ir.Op("le", ir.Var("x"), ir.Var("y")), ir.Var("flag"))
    assert range_relations.assume({}, guard, True) == {}


@pytest.mark.parametrize("is_and", [False, True])
@pytest.mark.parametrize("negated", [False, True])
@pytest.mark.parametrize("mutation", [None, 100])
@pytest.mark.parametrize("copied", [False, True])
@pytest.mark.parametrize("encoding", ['boolean', 'integer', 'unsigned', 'signed'])
def test_short_circuit_decisions_retain_only_live_guard_facts(is_and, negated, mutation, copied, encoding):
    def encode(value):
        if encoding == 'boolean':
            return value
        value = ir.Op('toint', ir.Op('truth', value))
        return (ir.Op('uwrap' if encoding == 'unsigned' else 'swrap', value, ir.Lit(1))
                if encoding in {'unsigned', 'signed'} else value)

    x = ir.Var("x")
    compared = ir.Op('swrap', x, ir.Lit(8)) if encoding != 'boolean' else x
    first = ir.Op("ge" if is_and else "lt", compared, ir.Lit(0))
    last = ir.Op("le" if is_and else "gt", compared, ir.Lit(3))
    guard = ir.Op("not", ir.Var("decision")) if negated else ir.Var("decision")
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (
            *((ir.Assign("x", ir.Var("source")),) if copied else ()),
            ir.Assign("held", encode(first))), cfg.Branch(
            first, "last" if is_and else "short", "short" if is_and else "last")),
        "short": cfg.Block("short", (ir.Assign("answer", ir.Var("held")),), cfg.Goto("join")),
        "last": cfg.Block("last", (ir.Assign("answer", encode(last)),), cfg.Goto("join")),
        "join": cfg.Block("join", (
            ir.Assign("decision", encode(ir.Var("answer"))),
            *((ir.Assign("x", ir.Lit(mutation)),) if mutation is not None else ())),
            cfg.Branch(guard, "yes", "no")),
        "yes": cfg.Block("yes", (), cfg.Return()),
        "no": cfg.Block("no", (), cfg.Return()),
    }, "entry")
    exits = {}
    ranges.graph_post(graph, {"source" if copied else "x": ranges.Range(-8, 8)},
        terminator_witness=lambda label, _term, env: exits.update(
            {label: env["x"]} if label in {"yes", "no"} else {}))
    if mutation is None:
        bounded = "yes" if is_and != negated else "no"
        assert exits[bounded] == ranges.Range(0, 3)
    for original in range(-8, 9):
        outcome = ((original >= 0 and original <= 3) if is_and else
                   (original < 0 or original > 3))
        label = "yes" if outcome != negated else "no"
        value = original if mutation is None else mutation
        assert exits[label].lo <= value <= exits[label].hi


@pytest.mark.parametrize('operation', ['uwrap', 'swrap'])
@pytest.mark.parametrize('width', [1, 3, 8])
def test_truth_projection_does_not_erase_real_integer_truncation(operation, width):
    from behave.sim.symbolic.interp import _Interp

    value = ir.Var('value')
    expression = ir.Op(operation, ir.Op('toint', value), ir.Lit(width))
    env = {'value': ranges.Range(-(2**width), 2**width)}
    assert ranges.truth_preserving_operand(expression, env) is expression
    assert ranges._truth_binding(expression) is None
    assert not range_relations.assume({}, ir.Op('lt', expression, ir.Lit(1)), True, env=env)
    for number in (-(2**width), -1, 0, 1, 2**width):
        actual = bool(_Interp({'value': number}).expr(expression))
        inferred = ranges.assume(ir.Op('truth', expression), actual, env)
        assert inferred is not None and inferred['value'].lo <= number <= inferred['value'].hi


def test_dead_decisions_do_not_accumulate_in_later_joins(monkeypatch):
    blocks = {}
    for index in range(30):
        entry, yes, no = (f"{kind}{index}" for kind in ("test", "yes", "no"))
        after = f"test{index+1}" if index < 29 else "exit"
        name = f"decision{index}"
        blocks[entry] = cfg.Block(entry, (ir.Assign(
            name, ir.Op("lt", ir.Var("x"), ir.Lit(0))),),
            cfg.Branch(ir.Var(name), yes, no))
        blocks[yes] = cfg.Block(yes, (), cfg.Goto(after))
        blocks[no] = cfg.Block(no, (), cfg.Goto(after))
    blocks["exit"] = cfg.Block("exit", (), cfg.Return())
    joins = []
    original = ranges._join_guard_facts

    def observe(left, right):
        joins.append((len(left), len(right)))
        return original(left, right)

    monkeypatch.setattr(ranges, "_join_guard_facts", observe)
    env = {"x": ranges.Range(-8, 8)}
    assert ranges.graph_post(cfg.CFG(blocks, "test0"), env)["x"] == env["x"]
    assert joins and set(joins) == {(0, 0)}


@pytest.mark.parametrize("name,predicate", [
    ("lt", operator.lt), ("le", operator.le), ("gt", operator.gt),
    ("ge", operator.ge), ("eq", operator.eq), ("ne", operator.ne),
])
@pytest.mark.parametrize("truth", [False, True])
def test_compound_comparison_refutation_preserves_every_concrete_valuation(name, predicate, truth):
    # Neither side is a variable. Refinement must retain every selected pair,
    # while contradictory endpoints still eliminate the empty branch.
    x, y = ir.Var("x"), ir.Var("y")
    guard = ir.Op(name, ir.Op("sub", x, ir.Lit(1)), ir.Op("add", y, ir.Lit(2)))
    intervals = [(lo, hi) for lo in range(-3, 4) for hi in range(lo, 4)]
    for (low_x, high_x), (low_y, high_y) in itertools.product(intervals, repeat=2):
        env = {"x": ranges.Range(low_x, high_x), "y": ranges.Range(low_y, high_y)}
        selected = [(a, b) for a in range(low_x, high_x + 1)
                    for b in range(low_y, high_y + 1) if predicate(a - 1, b + 2) == truth]
        narrowed = ranges.assume(guard, truth, env)
        assert (narrowed is None) == (not selected)
        if selected:
            for a, b in selected:
                assert narrowed["x"].lo <= a <= narrowed["x"].hi
                assert narrowed["y"].lo <= b <= narrowed["y"].hi
            for key, original in env.items():
                assert original.lo <= narrowed[key].lo <= narrowed[key].hi <= original.hi


def test_compound_refutation_preserves_unknown_bounds_and_disjunctive_alternatives():
    x = ir.Op("sub", ir.Var("x"), ir.Lit(1))
    low = ir.Op("lt", x, ir.Lit(0))
    high = ir.Op("ge", x, ir.Lit(7))
    invalid = ir.Op("lor", low, high)
    assert ranges.assume(invalid, True, {"x": ranges.Range(1, 7)}) is None
    assert ranges.assume(invalid, True, {"x": ranges.Range(0, 8)}) is not None
    assert ranges.assume(invalid, True, {}) is not None
    assert ranges.assume(high, True, {"x": ranges.Bounds(None, None)}) is not None
    # A one-sided bound is sufficient only on the appropriate side.
    assert ranges.assume(high, True, {"x": ranges.Bounds(None, 7)}) is None
    assert ranges.assume(low, True, {"x": ranges.Bounds(1, None)}) is None


def test_unknown_endpoints_are_not_equal_singletons():
    x, y = ir.Var("x"), ir.Var("y")
    env = {"x": ranges.Bounds(), "y": ranges.Bounds()}
    assert ranges.assume(ir.Op("ne", x, y), True, env) is not None
    assert ranges.assume(ir.Op("eq", x, y), False, env) is not None
    assert ranges.assume(x, True, env) is not None
    assert ranges.assume(x, False, env) is not None


def test_affine_index_guard_refines_both_operands():
    index, length = ir.Var("index"), ir.Var("length")
    child = ir.Op("add", ir.Op("mul", ir.Lit(2), index), ir.Lit(1))
    guard = ir.Op("lt", child, length)
    env = {"index": ranges.Range(0, 31), "length": ranges.Range(2, 16)}
    assert ranges.assume(guard, True, env) == {
        "index": ranges.Bounds(0, 7), "length": ranges.Bounds(2, 16)}
    env = {"index": ranges.Range(3, 31), "length": ranges.Range(0, 100)}
    assert ranges.assume(guard, True, env)["length"] == ranges.Bounds(8, 100)


def test_affine_guard_preserves_array_regions_in_source_cfg():
    index, length = ir.Var("index"), ir.Var("length")
    child = ir.Op("add", ir.Op("mul", ir.Lit(2), index), ir.Lit(1))
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (), cfg.Branch(
            ir.Op("lt", child, length), "read", "done")),
        "read": cfg.Block("read", (
            ir.Assign("child", child),
            ir.Assign("value", ir.Sel(ir.Var("cells"), ir.Var("child")))), cfg.Return()),
        "done": cfg.Block("done", (), cfg.Return()),
    }, "entry")
    array = ranges._CellRange(tuple(
        ranges.Range(1, 8) if i < 8 else ranges.Range(-8, -1) for i in range(16)))
    seen = {}
    ranges.graph_post(graph, {"index": ranges.Range(0, 7),
                             "length": ranges.Range(2, 8), "cells": array},
                      terminator_witness=lambda label, _term, env: seen.update({label: env}))
    assert seen["read"]["index"] == ranges.Range(0, 3)
    assert seen["read"]["child"] == ranges.Range(1, 7)
    assert seen["read"]["value"] == ranges.Range(1, 8)
    assert seen["read"]["cells"] is array


@pytest.mark.parametrize("name,predicate", [
    ("lt", operator.lt), ("le", operator.le), ("gt", operator.gt),
    ("ge", operator.ge), ("eq", operator.eq), ("ne", operator.ne),
])
@pytest.mark.parametrize("truth", [False, True])
def test_affine_backward_ranges_preserve_every_selected_valuation(name, predicate, truth):
    x, y = ir.Var("x"), ir.Var("y")
    intervals = [(-3, -1), (-1, 2), (0, 0), (1, 3)]
    for a, b in [(-3, -1), (2, 3), (-2, 2), (0, 1), (5, 0), (2, -2)]:
        for offset in (-2, 0, 3):
            # x occurs on both sides; cancellation must happen before inversion.
            left = ir.Op("add", ir.Op("mul", ir.Lit(a), x), ir.Lit(offset))
            right = ir.Op("add", ir.Op("mul", ir.Lit(b), y), x)
            guard = ir.Op(name, left, right)
            for (lx, hx), (ly, hy) in itertools.product(intervals, repeat=2):
                env = {"x": ranges.Range(lx, hx), "y": ranges.Range(ly, hy)}
                got = ranges.assume(guard, truth, env)
                selected = [(vx, vy) for vx in range(lx, hx + 1)
                            for vy in range(ly, hy + 1)
                            if predicate(a * vx + offset, b * vy + vx) == truth]
                if selected:
                    assert got is not None, (guard, truth, env, selected)
                    for vx, vy in selected:
                        assert got["x"].lo <= vx <= got["x"].hi
                        assert got["y"].lo <= vy <= got["y"].hi
                if got is not None:
                    for key, original in env.items():
                        assert original.lo <= got[key].lo <= got[key].hi <= original.hi


def test_affine_backward_ranges_handle_open_bounds_and_integer_rounding():
    x, n = ir.Var("x"), ir.Var("n")
    guard = ir.Op("lt", ir.Op("add", ir.Op("mul", ir.Lit(2), x), ir.Lit(1)), n)
    assert ranges.assume(guard, True, {"n": ranges.Range(2, 16)})["x"] == ranges.Bounds(None, 7)
    assert ranges.assume(guard, True, {"x": ranges.Bounds(0, None),
                                      "n": ranges.Bounds(None, 16)}) == {
        "x": ranges.Bounds(0, 7), "n": ranges.Bounds(2, 16)}
    assert ranges.assume(ir.Op("eq", ir.Op("mul", ir.Lit(2), x), ir.Lit(1)),
                         True, {"x": ranges.Range(-3, 3)}) is None
    assert ranges.assume(ir.Op("lt", ir.Op("sub", x, x), ir.Lit(0)), True, {}) is None
    assert ranges.assume(guard, True, {}) is not None


def test_backward_ranges_do_not_invert_nonlinear_or_wrapped_terms():
    x, y = ir.Var("x"), ir.Var("y")
    for expression, env in (
        (ir.Op("mul", x, y), {"x": ranges.Range(-3, 3), "y": ranges.Range(-3, 3)}),
        (ir.Op("uwrap", x, ir.Lit(8)), {"x": ranges.Range(0, 511)}),
    ):
        got = ranges.assume(ir.Op("lt", expression, ir.Lit(3)), True, env)
        assert got == {name: ranges.Bounds.of(value) for name, value in env.items()}


def test_affine_projection_cost_is_linear_in_operand_count():
    # Construct the affine form directly: the projection must not re-read the
    # whole environment or rebuild all other contributions for each operand.
    class CountedEnvironment(dict):
        reads = 0

        def get(self, *args):
            self.reads += 1
            return super().get(*args)

    coefficients = {f"x{i}": 1 for i in range(1000)}
    env = CountedEnvironment((name, ranges.Range(0, 10)) for name in coefficients)
    got = ranges._assume_affine((0, coefficients), "eq", env)
    assert env.reads == len(coefficients)
    assert all(value == ranges.Bounds(0, 0) for value in got.values())


def test_affine_projection_handles_multiple_open_operands_without_false_bounds():
    x, y, z = (ir.Var(name) for name in ("x", "y", "z"))
    guard = ir.Op("le", ir.Op("add", x, ir.Op("add", y, z)), ir.Lit(4))
    # Either open operand can compensate for the other; neither is bounded.
    env = {"x": ranges.Bounds(), "y": ranges.Bounds(), "z": ranges.Bounds(0, 2)}
    assert ranges.assume(guard, True, env) == env
    env = {"x": ranges.Bounds(), "y": ranges.Bounds(0, None), "z": ranges.Bounds(0, 2)}
    assert ranges.assume(guard, True, env) == {**env, "x": ranges.Bounds(None, 4)}
