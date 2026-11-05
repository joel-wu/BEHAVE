"""Unit tests for the range analysis.

The property that matters is soundness: the interval reported for an expression
must contain every value it can take, since a width chosen from a too-small
interval would wrap where the source does not.
"""
import pickle

import pytest

from behave.sim.symbolic import cfg, ir, plan, primitives
from behave.sim.symbolic import ranges as range_analysis
from behave.sim.behavior.lowering import lower_procedure, lower_with_state_ranges
from behave.sim.symbolic.ranges import (
    Bounds,
    Range,
    _join_envs,
    assume,
    bounds_of_expression,
    fixpoint,
    graph_post,
    of_expression,
    post,
    procedure_post,
    trip_range,
    unranged_subterms,
    width_for,
)


def _primitive_range(name, *parts):
    got = primitives.primitive(name).range_transform(*parts)
    return None if got is None else Range(got.lo, got.hi)


def test_empty_array_expression_has_the_same_zero_cell_range_as_its_value():
    expression = ir.Op("arraylit")
    got = of_expression(expression, {})
    assert type(got) is range_analysis._CellRange
    assert got.cells == ()
    assert got == range_analysis.range_of_value([])
    assert list(unranged_subterms(expression, {})) == []


def test_a_loop_reaches_what_iterating_accumulates():
    """The body was taken once, so three turns of `x = x + 1` from zero were
    reported as reaching one. A width from that interval is too narrow, and a
    formula that wraps is unsatisfiable for a reason the model has not got."""
    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.x = 0\n'
           '    def process(self, a):\n'
           '        x = 0\n'
           '        for j in range(3):\n'
           '            x = x + 1\n'
           '        self.x = x\n'
           '        return {"y": x}\n')
    proc = lower_with_state_ranges(src)
    env = fixpoint(proc, {"a": Range(0, 255)})
    assert env["x"].hi >= 3, env["x"]


def test_a_shift_spans_its_corners():
    """Pairing only the low end with the high one misses a sign change: minus
    one shifted by nought is in the result and was not in the interval."""
    got = _primitive_range("shl", Range(-2, -1), Range(0, 1))
    assert got.lo <= -4 and got.hi >= -1


def test_multiplication_interval_matches_every_small_integer_product():
    intervals = [(lo, hi) for lo in range(-5, 6) for hi in range(lo, 6)]
    for alo, ahi in intervals:
        for blo, bhi in intervals:
            got = _primitive_range("mul", Range(alo, ahi), Range(blo, bhi))
            products = [a * b for a in range(alo, ahi + 1) for b in range(blo, bhi + 1)]
            assert got == Range(min(products), max(products))


@pytest.mark.parametrize("point", [-2, 0, 3])
@pytest.mark.parametrize("ends", [(None, None), (None, 4), (-3, None)])
def test_multiplication_by_a_point_keeps_open_bounds(point, ends):
    domain = primitives.PrimitiveInterval(*ends)
    scalar = primitives.PrimitiveInterval(point, point)
    expected = (primitives.PrimitiveInterval(0, 0) if point == 0 else
                primitives.PrimitiveInterval(*(None if end is None else point * end
                    for end in (ends if point > 0 else ends[::-1]))))
    multiply = primitives.primitive("mul").range_transform
    assert multiply(domain, scalar) == multiply(scalar, domain) == expected


def test_a_total_ir_shift_includes_the_negative_count_filler():
    """IR expressions are total even where source Python would raise.

    Lowering emits the source-level failure guard.  The primitive still needs
    a finite value off that normal path so an interval product containing an
    infeasible negative count does not erase the carrier of the valid path.
    """
    assert _primitive_range("shr", Range(1, 4), Range(-1, 2)) == Range(0, 4)
    assert _primitive_range("shl", Range(1, 4), Range(-1, 2)) == Range(0, 16)


def test_an_interval_with_its_ends_the_wrong_way_round_is_an_error():
    """Swapping them silently turns a transformer that computed them backwards
    into an interval that looks reasonable."""
    with pytest.raises(ValueError):
        Range(5, 2)


@pytest.mark.parametrize("signed", [None, False, True])
def test_carrier_width_matches_the_original_integer_inequalities(signed):
    for lo in range(-32, 33):
        for hi in range(lo, 33):
            use_sign = lo < 0 if signed is None else signed
            width = 1
            if use_sign:
                while -(1 << (width - 1)) > lo or hi > (1 << (width - 1)) - 1:
                    width += 1
            else:
                while hi > (1 << width) - 1:
                    width += 1
            assert width_for(Range(lo, hi), signed=signed) == width


@pytest.mark.parametrize("bits", [1, 8, 65, 513, 8192])
def test_carrier_width_power_of_two_boundaries(bits):
    assert width_for(Range(-(1 << bits), (1 << bits) - 1), signed=True) == bits + 1
    assert width_for(Range(-(1 << bits) - 1, 0), signed=True) == bits + 2
    assert width_for(Range(0, 1 << bits), signed=True) == bits + 2
    assert width_for(Range(0, (1 << bits) - 1), signed=False) == bits


def test_an_immutable_cell_product_caches_its_structural_hash():
    class CountingRange(Range):
        calls = 0

        def __hash__(self):
            type(self).calls += 1
            return super().__hash__()

    leaf = CountingRange(0, 1)
    product = range_analysis._CellRange((leaf, leaf))
    same = range_analysis._CellRange((Range(0, 1), Range(0, 1)))
    construction_calls = CountingRange.calls

    for _ in range(32):
        hash(product)

    assert product == same
    assert hash(product) == hash(same)
    assert CountingRange.calls == construction_calls


def test_scalar_range_joins_do_not_retain_iteration_history():
    cache = {}
    for index in range(5000):
        left, right = Range(-index, index), Range(index - 1, index + 1)
        assert range_analysis._join_range(left, right, cache) == left.join(right)
    assert cache == {}


def test_product_join_reuses_arrays_without_caching_scalar_leaves():
    cache = {}
    left = range_analysis._CellRange((Range(0, 1), Range(4, 5)))
    right = range_analysis._CellRange((Range(1, 2), Range(3, 4)))
    result = range_analysis._join_range(left, right, cache)
    assert result == range_analysis._CellRange((Range(0, 2), Range(3, 5)))
    assert range_analysis._join_range(left, right, cache) is result
    assert len(cache) == 1
    assert next(iter(cache.values())) == (left, right, result)


def test_a_uniform_cell_product_keeps_sparse_componentwise_ranges():
    length = 10**18
    initialized = ir.Assign(
        "buf", ir.Op("arrayfill", ir.Lit(0), ir.Lit(length)))
    body = ir.Seq(initialized, ir.Store("buf", ir.Lit(7), ir.Lit(9)))

    after = range_analysis._post(body, {})
    product = after["buf"]

    assert type(product) is range_analysis._UniformCellRange
    assert product.length == length
    assert (product.lo, product.hi) == (0, 9)
    assert range_analysis.of_expression(
        ir.Sel(ir.Var("buf"), ir.Lit(7)), after, after) == Range(9, 9)
    assert range_analysis.of_expression(
        ir.Sel(ir.Var("buf"), ir.Lit(8)), after, after) == Range(0, 0)
    assert pickle.loads(pickle.dumps(product)) == product


def test_consecutive_uniform_cell_writes_share_one_range_run():
    product = range_analysis._UniformCellRange(100, Range(0, 0))

    for index in range(75):
        product = product.store(index, Range(1, 1))

    assert product.runs == ((0, 75, Range(1, 1)),)
    assert product.cell(74) == Range(1, 1)
    assert product.cell(75) == Range(0, 0)
    assert pickle.loads(pickle.dumps(product)) == product


def test_uniform_cell_joins_preserve_piecewise_ranges():
    left = range_analysis._UniformCellRange._from_runs(
        100, Range(0, 0), ((0, 50, Range(1, 1)),))
    right = range_analysis._UniformCellRange._from_runs(
        100, Range(0, 0), ((25, 75, Range(2, 2)),))

    joined = left.join(right)

    assert joined.runs == (
        (0, 25, Range(0, 1)),
        (25, 50, Range(1, 2)),
        (50, 75, Range(0, 2)),
    )
    assert joined.cell(75) == Range(0, 0)


@pytest.mark.parametrize("count", [20, 256, 1000])
def test_a_finite_loop_accelerates_proved_additive_recurrences(count):
    body = ir.Seq(
        ir.Store("buf", ir.Var("count"), ir.Var("i")),
        ir.Assign("count", ir.Op(
            "add", ir.Var("count"), ir.Lit(1))),
    )
    command = ir.Seq(
        ir.Assign("buf", ir.Op("arrayfill", ir.Lit(0), ir.Lit(count + 1))),
        ir.Seq(
            ir.Assign("count", ir.Lit(0)),
            ir.For("i", ir.Lit(count), body),
        ),
    )

    after = range_analysis._post(command, {})

    assert after["count"] == Range(0, count)
    assert (after["buf"].lo, after["buf"].hi) == (0, count - 1)
    assert range_analysis._cell_at(after["buf"], count) == Range(0, 0)


def test_small_loop_does_not_pay_for_a_longer_closure_attempt():
    count = range_analysis.STATE_ROUNDS
    command = ir.For("i", count, ir.Assign(
        "count", ir.Op("add", ir.Var("count"), ir.Lit(1))))
    after = range_analysis._post(
        command, {"count": Range(0, 0)},
        _point_product=range_analysis._POINTWISE_LOOP_COMBINATION_LIMIT + 1)
    assert after["count"] == Range(count, count)


def test_recurrence_proof_does_not_skip_observed_iterations():
    count, visits = 32, []
    increment = ir.Assign("count", ir.Op("add", ir.Var("count"), ir.Lit(1)))
    command = ir.For("i", count, increment)

    def observe(current, at):
        if current is increment:
            visits.append(at["count"])

    before = ir.command_fingerprint(command)
    after = range_analysis._post(command, {"count": Range(0, 0)}, observe)
    assert ir.command_fingerprint(command) == before
    assert after["count"] == Range(count, count)
    assert visits == [Range(index, index) for index in range(count)]


def test_pointwise_loop_precision_is_budgeted_across_a_nest():
    initialise = ir.Assign(
        "buf", ir.Op("arrayfill", ir.Lit(0), ir.Lit(8)))
    one_loop = ir.Seq(
        initialise,
        ir.For("j", ir.Lit(8),
               ir.Store("buf", ir.Var("j"), ir.Lit(1))),
    )
    nested = ir.Seq(
        initialise,
        ir.For(
            "i", ir.Lit(8),
            ir.For("j", ir.Lit(8),
                   ir.Store("buf", ir.Var("j"), ir.Lit(1))),
        ),
    )

    pointwise = range_analysis._post(one_loop, {})["buf"]
    widened = range_analysis._post(nested, {})["buf"]

    assert (pointwise.lo, pointwise.hi) == (1, 1)
    assert (widened.lo, widened.hi) == (0, 1)


def test_a_fully_overwritten_uniform_product_excludes_its_default():
    body = ir.Seq(
        ir.Assign("buf", ir.Op("arrayfill", ir.Lit(0), ir.Lit(1))),
        ir.Store("buf", ir.Lit(0), ir.Lit(9)),
    )

    product = range_analysis._post(body, {})["buf"]

    assert (product.lo, product.hi) == (9, 9)


def test_recursive_fixed_values_seed_one_sound_scalar_leaf_hull():
    assert range_analysis.range_of_value(
        [[0, 3], (-2, 1)]) == Range(-2, 3)
    empty = range_analysis.range_of_value([])
    assert type(empty) is range_analysis._CellRange
    assert empty.cells == ()
    assert range_analysis.range_of_value([0, True]) == Range(0, 1)
    assert range_analysis.range_of_value([0, "unknown"]) is None
    cyclic = []
    cyclic.append(cyclic)
    assert range_analysis.range_of_value(cyclic) is None


def test_retained_loop_range_transfer_preserves_unrelated_environment():
    loop = ir.For(
        "i", ir.Lit(3),
        ir.Assign("acc", ir.Op("add", ir.Var("acc"), ir.Lit(1))),
    )
    untouched = {f"old_{index}": Range(index, index)
                 for index in range(2_000)}
    result = post(loop, {"acc": Range(0, 0), **untouched})

    assert result["acc"] == Range(3, 3)
    assert {name: result[name] for name in untouched} == untouched


def test_conditional_range_transfer_preserves_unrelated_environment():
    branch = ir.If(
        ir.Var("condition"),
        ir.Assign("value", ir.Lit(3)),
        ir.Assign("value", ir.Lit(-2)),
    )
    untouched = {f"old_{index}": Range(index, index)
                 for index in range(2_000)}
    result = post(branch, {
        "condition": Range(0, 1),
        "value": Range(0, 0),
        **untouched,
    })

    assert result["value"] == Range(-2, 3)
    assert {name: result[name] for name in untouched} == untouched


def test_range_footprints_are_recomputed_for_each_transfer():
    branch = ir.If(
        ir.Var("condition"),
        ir.Assign("old", ir.Lit(1)),
        ir.Assign("old", ir.Lit(2)),
    )
    assert post(branch, {"condition": Range(0, 1)})["old"] == Range(1, 2)

    branch.then = ir.Assign("new", ir.Lit(3))
    branch.orelse = ir.Skip()
    result = post(branch, {
        "condition": Range(0, 1),
        "new": Range(7, 7),
    })
    assert result["new"] == Range(3, 7)


def test_nested_footprints_match_the_direct_syntax_walk():
    shared = ir.Op("add", ir.Var("value"), ir.Var("delta"))
    branch = ir.If(ir.Var("condition"),
        ir.Store("cells", ir.Var("i"), shared),
        ir.Assign("value", shared))
    loop = ir.For("i", ir.Var("count"),
        ir.Seq(branch, ir.Assign("value", ir.Sel(ir.Var("cells"), ir.Var("i")))))
    body = ir.Seq(ir.Assign("value", ir.Lit(0)),
                  ir.Seq(loop, ir.If(ir.Var("enabled"), branch, ir.Mark("done"))))
    cache = {}
    # The source DAG reuses a branch and an expression. Each subtree still
    # reports exactly the names in its original, unconditioned syntax.
    for command in ir.walk(body):
        read = {node.name for node in ir.expression_nodes(command) if isinstance(node, ir.Var)}
        written = {node.var for node in ir.walk(command)
                   if isinstance(node, (ir.Assign, ir.Store))}
        written.update(node.index for node in ir.walk(command) if isinstance(node, ir.For))
        assert range_analysis._range_footprint(command, cache) == (read | written, written)


def test_nested_footprints_visit_each_expression_once_and_skip_sequence_suffixes(monkeypatch):
    statements = [ir.Assign(f"v{i}", ir.Var(f"input{i}")) for i in range(300)]
    body = statements[-1]
    for statement in reversed(statements[:-1]):
        body = ir.Seq(statement, body)
    roots = [ir.For("i", ir.Lit(2), body)]
    for index in range(12):
        roots.append(ir.If(ir.Var(f"enable{index}"), roots[-1], ir.Skip()))
    count = 0
    original = range_analysis._expression_names

    def counted(expression):
        nonlocal count
        count += 1
        return original(expression)

    monkeypatch.setattr(range_analysis, "_expression_names", counted)
    cache = {}
    for root in reversed(roots):
        range_analysis._range_footprint(root, cache)
    assert count == len(statements) + len(roots)
    assert not any(isinstance(command, ir.Seq)
                   for command, _facts in cache[range_analysis._FOOTPRINT_CACHE].values())


def test_an_unbounded_loop_drops_only_values_it_may_write():
    loop = ir.For(
        "i", ir.Var("unknown_trip_count"),
        ir.Assign("changed", ir.Lit(1)),
    )
    result = post(loop, {
        "changed": Range(0, 0),
        "unrelated": Range(7, 9),
    })

    assert "changed" not in result
    assert "i" not in result
    assert result["unrelated"] == Range(7, 9)


def test_a_variable_only_one_arm_can_bound_has_no_bound():
    joined = _join_envs({"x": Range(0, 3), "y": Range(0, 1)},
                        {"x": Range(4, 9)})
    assert joined["x"] == Range(0, 9)
    assert "y" not in joined


def _range_diamond(*, define_else=True):
    blocks = {
        "entry": cfg.Block(
            "entry", (), cfg.Branch(ir.Var("guard"), "yes", "no")),
        "yes": cfg.Block(
            "yes", (ir.Assign("chosen", ir.Lit(5)),), cfg.Goto("join")),
        "no": cfg.Block(
            "no",
            ((ir.Assign("chosen", ir.Lit(7)),) if define_else else ()),
            cfg.Goto("join")),
        "join": cfg.Block(
            "join", (ir.Assign("answer", ir.Var("chosen")),), cfg.Return()),
    }
    return cfg.CFG(blocks, "entry")


def test_graph_range_flow_joins_source_arm_values_without_a_filler():
    seen = {}

    def witness(command, at):
        if isinstance(command, ir.Assign) and command.var == "answer":
            seen.update(at)

    after = graph_post(
        _range_diamond(), {"guard": Range(0, 1)}, witness)

    assert seen["chosen"] == Range(5, 7)
    assert after["answer"] == Range(5, 7)
    assert after["answer"].lo != 0


def test_graph_range_flow_does_not_invent_a_missing_arm_definition():
    seen = {}

    def witness(command, at):
        if isinstance(command, ir.Assign) and command.var == "answer":
            seen.update(at)

    after = graph_post(
        _range_diamond(define_else=False), {"guard": Range(0, 1)}, witness)

    assert "chosen" not in seen
    assert "answer" not in after


def test_graph_range_flow_reports_infeasible_blocks_without_replaying_reachable_ones():
    reachable = []
    unreachable = []

    def record(into):
        def witness(command, _at):
            if isinstance(command, ir.Assign):
                into.append((command.var, command.expr))
        return witness

    after = graph_post(
        _range_diamond(), {"guard": Range(1, 1)}, record(reachable),
        _unreachable_witness=record(unreachable))

    assert after["answer"] == Range(5, 5)
    assert [name for name, _expr in reachable] == ["chosen", "answer"]
    assert isinstance(reachable[0][1], ir.Lit)
    assert reachable[0][1].value == 5
    assert [name for name, _expr in unreachable] == ["chosen"]
    assert isinstance(unreachable[0][1], ir.Lit)
    assert unreachable[0][1].value == 7


def test_graph_range_flow_recovers_a_hoisted_predicate():
    masked = ir.Op("and", ir.Var("value"), ir.Lit(1 << 15))
    predicate = ir.Op("truth", masked)
    graph = cfg.CFG({
        "entry": cfg.Block(
            "entry",
            (ir.Assign("condition", predicate),
             ir.Assign("guard", ir.Var("condition"))),
            cfg.Branch(ir.Var("guard"), "yes", "no")),
        "yes": cfg.Block("yes", (), cfg.Return()),
        "no": cfg.Block("no", (), cfg.Return()),
    }, "entry")
    exits = {}

    def at_terminator(label, _terminator, environment):
        if label != "entry":
            exits[label] = environment["value"]

    graph_post(
        graph,
        {"value": Range(-32768, 32767)},
        terminator_witness=at_terminator,
    )

    assert exits == {
        "yes": Range(-32768, -1),
        "no": Range(0, 32767),
    }


def test_graph_range_flow_does_not_reinterpret_an_old_predicate_snapshot():
    predicate = ir.Op("lt", ir.Var("value"), ir.Lit(0))
    graph = cfg.CFG({
        "entry": cfg.Block(
            "entry",
            (ir.Assign("guard", predicate),
             ir.Assign("value", ir.Lit(1))),
            cfg.Branch(ir.Var("guard"), "yes", "no")),
        "yes": cfg.Block("yes", (), cfg.Return()),
        "no": cfg.Block("no", (), cfg.Return()),
    }, "entry")
    exits = {}

    def at_terminator(label, _terminator, environment):
        if label != "entry":
            exits[label] = environment["value"]

    graph_post(
        graph,
        {"value": Range(-1, 1)},
        terminator_witness=at_terminator,
    )

    assert exits == {"yes": Range(1, 1), "no": Range(1, 1)}


@pytest.mark.parametrize('cast,width', [('uwrap', 32), ('swrap', 32)])
@pytest.mark.parametrize('changed', [False, True])
@pytest.mark.parametrize('source_graph', [False, True])
def test_copied_array_guard_keeps_safe_casts_and_invalidates_stale_values(
        cast, width, changed, source_graph):
    cell = ir.Sel(ir.Var('items'), ir.Lit(0))
    body = ir._sequence([
        ir.Assign('saved', ir.Op(cast, cell, ir.Lit(width))),
        ir.Store('items', ir.Lit(0), ir.Lit(2)) if changed else ir.Skip(),
        ir.Mark('observation'),
        ir.If(ir.Op('eq', ir.Var('saved'), ir.Lit(2)), ir.Assign('out', ir.Lit(0)),
              ir.Assign('out', ir.Op('add', cell, ir.Lit(1))))])
    env = {'items': range_analysis._UniformCellRange(2, Range(0, 2))}
    got = graph_post(cfg.of_command(body), env) if source_graph else post(body, env)
    assert got['out'] == Range(0, 3 if changed else 2)


@pytest.mark.parametrize('cast,width', [('uwrap', 1), ('uwrap', 3), ('swrap', 1), ('swrap', 3)])
def test_cast_guard_refinement_contains_all_wrapping_integer_cases(cast, width):
    convert = primitives.primitive(cast).denotation
    cell = ir.Sel(ir.Var('items'), ir.Lit(0))
    for low in range(-4, 5):
        for high in range(low, 8):
            for equal in (-1, 0, 2):
                body = ir._sequence([
                    ir.Assign('saved', ir.Op(cast, cell, ir.Lit(width))),
                    ir.If(ir.Op('eq', ir.Var('saved'), ir.Lit(equal)),
                          ir.Assign('out', ir.Lit(0)), ir.Assign('out', cell))])
                got = post(body, {'items': range_analysis._UniformCellRange(2, Range(low, high))})
                actual = [0 if convert(value, width) == equal else value
                          for value in range(low, high + 1)]
                assert got['out'].lo <= min(actual) <= max(actual) <= got['out'].hi


def test_an_exact_array_store_replaces_the_selected_cell_range():
    environment = {}
    range_analysis._post(
        ir.Assign("items", ir.Op("arraylit", ir.Lit(0), ir.Lit(9))),
        environment,
    )
    range_analysis._post(
        ir.Store("items", ir.Lit(0), ir.Lit(3)), environment)

    assert environment["items"].cells == (Range(3, 3), Range(9, 9))


def test_complete_procedure_ranges_include_source_path_joins():
    source = """def mask(value, width):
    return value & ((1 << width) - 1)


class M:
    def reset(self):
        self.mem = [0] * 16

    def process(self, write, addr, side, data):
        addr = addr & 15
        if write:
            self.mem[addr] = mask(data, 32)
            return None
        return {"y": self.mem[addr], "side": mask(side, 1)}
"""
    proc = lower_procedure(source, outputs=["y", "side"])
    entered = {
        "write": Range(0, 1),
        "addr": Range(0, 15),
        "side": Range(0, 1),
        "data": Range(0, 2**32 - 1),
        "mem": Range(0, 0),
    }

    after = procedure_post(proc, entered)["mem"]
    assert (after.lo, after.hi) == (0, 2**32 - 1)
    assert range_analysis._cell_length(after) == 16
    assert all(range_analysis._cell_at(after, i) == Range(0, 2**32 - 1)
               for i in range(16))


def test_retained_loop_uses_its_source_cfg_when_predication_loses_normality():
    """An unused loop target is not a missing input to the sealed body CFG.

    Literal-safe shifts still carry explicit failure branches in SolverIR.  In
    the flattened loop term, losing correlation between their complementary
    reachability flags can erase a later temporary.  The sealed source CFG is
    the authority for the normal body postcondition and must still recover the
    loop-carried values.
    """
    source = """def root(value):
    low = 0
    high = 15
    answer = 0
    for _ in range(4):
        middle = (low + high) >> 1
        square = middle * middle
        if square <= value:
            answer = middle
            low = middle + 1
        else:
            high = middle - 1
    return answer


class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = root(a)
        return {"y": self.x}
"""
    proc = lower_procedure(source, outputs=["y"])
    after = procedure_post(
        proc, {"a": Range(0, 255), "x": Range(0, 0)})

    assert after["x"] == Range(0, 14)
    encoding = plan.plan_encoding(
        proc, {"a": Range(0, 255), "x": Range(0, 0)}, bound=1)
    assert encoding.complete, encoding.unresolved


def test_complete_procedure_ranges_do_not_replay_a_sealed_graph_as_a_term(
        monkeypatch):
    source = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        if a:
            self.x = 3
        else:
            self.x = 5
        return {"y": self.x}
"""
    proc = lower_procedure(source, outputs=["y"])
    original = range_analysis._post
    complete_term_visits = 0

    def observe(command, env, witness=None, _flows=None, _point_product=1):
        nonlocal complete_term_visits
        if command is proc.body:
            complete_term_visits += 1
        return original(command, env, witness, _flows, _point_product)

    monkeypatch.setattr(range_analysis, "_post", observe)
    after = procedure_post(proc, {"a": Range(0, 1), "x": Range(0, 0)})

    assert after["x"] == Range(3, 5)
    assert complete_term_visits == 0


def test_bounded_ranges_validate_the_sealed_procedure_once(monkeypatch):
    source = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = self.x + (a & 1)
        return {"y": self.x}
"""
    proc = lower_procedure(source, outputs=["y"])
    original = ir.command_fingerprint
    body_checks = 0

    def counted(command, *args, **kwargs):
        nonlocal body_checks
        if command is proc.body:
            body_checks += 1
        return original(command, *args, **kwargs)

    monkeypatch.setattr(ir, "command_fingerprint", counted)
    got = range_analysis.bounded(proc, {"a": Range(0, 1)}, 8)

    assert got["x"] == Range(0, 8)
    assert body_checks == 1


def test_bounded_ranges_reuse_a_loop_transfer_at_the_same_inputs(monkeypatch):
    loop = ir.For(
        "i", ir.Lit(4), ir.Assign("temporary", ir.Var("i")))
    body = ir.Seq(
        loop,
        ir.Assign("tick", ir.Op("add", ir.Var("tick"), ir.Lit(1))),
    )
    proc = ir.Procedure(
        ["tick"], [], ["i", "temporary"], [], body,
        initial={"tick": 0},
    )
    original = range_analysis._retained_loop_body_post
    body_transfers = 0

    def counted(command, *args, **kwargs):
        nonlocal body_transfers
        if command is loop:
            body_transfers += 1
        return original(command, *args, **kwargs)

    monkeypatch.setattr(range_analysis, "_retained_loop_body_post", counted)
    got = range_analysis.bounded(proc, {}, 8)

    assert got["tick"] == Range(0, 8)
    assert body_transfers == 4


def test_range_transfers_share_validation_but_not_value_caches():
    validations = {}
    first = range_analysis._range_transfer_cache(validations)
    second = range_analysis._range_transfer_cache(validations)

    assert first is not second
    assert first[range_analysis._SSA_FLOW_CACHE] is validations
    assert second[range_analysis._SSA_FLOW_CACHE] is validations

    first[range_analysis._COMMAND_POST_CACHE] = {"large": object()}
    assert range_analysis._COMMAND_POST_CACHE not in second


def _env(**kw):
    return {k: Range(*v) for k, v in kw.items()}


def test_a_literal_is_its_own_range():
    assert of_expression(ir.Lit(7), {}) == Range(7, 7)


def test_an_unknown_variable_has_no_range():
    assert of_expression(ir.Var("a"), {}) is None


def test_a_sum_widens_by_one_bit():
    e = ir.Op("add", ir.Var("a"), ir.Var("b"))
    r = of_expression(e, _env(a=(0, 65535), b=(0, 65535)))
    assert r == Range(0, 131070)
    assert width_for(r) == 17


def test_a_difference_can_go_negative():
    e = ir.Op("sub", ir.Var("a"), ir.Var("b"))
    assert of_expression(e, _env(a=(0, 255), b=(0, 255))) == Range(-255, 255)


def test_a_product_takes_the_extreme_corner():
    e = ir.Op("mul", ir.Var("a"), ir.Var("b"))
    assert of_expression(e, _env(a=(-3, 2), b=(-4, 5))) == Range(-15, 12)


def test_a_comparison_is_a_bit():
    e = ir.Op("lt", ir.Var("a"), ir.Lit(3))
    assert of_expression(e, _env(a=(0, 255))) == Range(0, 1)


def test_a_shift_left_widens_by_the_shift():
    e = ir.Op("shl", ir.Var("a"), ir.Lit(4))
    assert of_expression(e, _env(a=(0, 255))) == Range(0, 4080)


def test_a_wide_packed_port_may_shift_within_the_scalar_capability():
    e = ir.Op("shl", ir.Var("word"), ir.Var("offset"))
    got = of_expression(e, _env(word=(0, 65535), offset=(0, 1008)))

    assert got == Range(0, 65535 << 1008)


def test_an_array_literal_covers_every_element():
    e = ir.Op("arraylit", ir.Lit(1), ir.Lit(9), ir.Lit(-2))
    assert of_expression(e, {}) == Range(-2, 9)


def test_a_fixed_array_literal_selection_keeps_the_selected_point():
    array = ir.Op("arraylit", ir.Lit(1), ir.Lit(9), ir.Lit(-2))
    selected = ir.Sel(array, ir.Lit(1))
    assert of_expression(selected, {}) == Range(9, 9)
    assert bounds_of_expression(selected, {}) == Bounds(9, 9)


def test_a_fixed_selection_keeps_the_cell_range_after_assignment():
    body = ir.Seq(
        ir.Assign("parts", ir.Op(
            "arraylit", ir.Lit(1), ir.Lit(9), ir.Lit(-2))),
        ir.Assign("selected", ir.Sel(ir.Var("parts"), ir.Lit(1))),
    )

    assert post(body, {})["selected"] == Range(9, 9)


def test_a_fixed_selection_joins_only_corresponding_branch_cells():
    body = ir.Seq(
        ir.If(
            ir.Var("choose"),
            ir.Assign("parts", ir.Op(
                "arraylit", ir.Lit(1), ir.Lit(255), ir.Lit(8388607))),
            ir.Assign("parts", ir.Op(
                "arraylit", ir.Lit(0), ir.Lit(0), ir.Lit(0))),
        ),
        ir.Assign("exponent", ir.Sel(ir.Var("parts"), ir.Lit(1))),
    )

    assert post(body, {"choose": Range(0, 1)})["exponent"] == Range(0, 255)


def test_the_length_of_an_array_literal_is_known():
    e = ir.Op("len", ir.Op("arraylit", ir.Lit(1), ir.Lit(2)))
    assert of_expression(e, {}) == Range(2, 2)


def test_a_nested_expression_composes_the_transformers():
    e = ir.Op("mul", ir.Op("add", ir.Var("a"), ir.Lit(1)), ir.Lit(3))
    assert of_expression(e, _env(a=(0, 10))) == Range(3, 33)


def test_if_converted_assignment_uses_its_guard_to_bound_the_value():
    clipped = ir.Op(
        "ite",
        ir.Op("le", ir.Var("x"), ir.Lit(4)),
        ir.Var("x"),
        ir.Lit(4),
    )

    assert post(
        ir.Assign("clipped", clipped), {"x": Range(0, 2**16 - 1)}
    )["clipped"] == Range(0, 4)


def test_a_deep_if_converted_expression_does_not_use_the_python_stack():
    expression = ir.Lit(0)
    for value in range(2000):
        expression = ir.Op("ite", ir.Var("guard"),
                           ir.Lit(value), expression)

    assert of_expression(expression, _env(guard=(0, 1))) == Range(0, 1999)


def test_unranged_walk_is_iterative_and_reports_the_deep_leaf():
    missing = ir.Var("missing")
    expression = missing
    for value in range(2000):
        expression = ir.Op("ite", ir.Var("guard"),
                           ir.Lit(value), expression)

    unranged = unranged_subterms(expression, _env(guard=(0, 1)))
    assert expression in unranged
    assert missing in unranged


def test_an_expression_over_an_unknown_variable_has_no_range():
    e = ir.Op("add", ir.Var("a"), ir.Var("unknown"))
    assert of_expression(e, _env(a=(0, 1))) is None


@pytest.mark.parametrize("literal", [0, False, True, -17, 1 << 1024])
def test_atomic_range_reads_do_not_build_a_syntax_worklist(monkeypatch, literal):
    cells = range_analysis._CellRange((Range(1, 2), Range(7, 9)))
    env = {"scalar": Range(-2, 3), "cells": cells}

    def no_walk(*args, **kwargs):
        pytest.fail("an atomic read has no expression children")

    monkeypatch.setattr(range_analysis, "_children", no_walk)
    monkeypatch.setattr(range_analysis, "_ranges_of_tree", no_walk)
    monkeypatch.setattr(range_analysis, "_expression_names_cached", no_walk)
    assert of_expression(ir.Lit(literal), env) == Range(literal, literal)
    assert bounds_of_expression(ir.Lit(literal), env) == Bounds(literal, literal)
    assert range_analysis._value_range(ir.Lit(literal), env, {}) == Range(literal, literal)
    for name, value in env.items():
        # A separate array selection environment must not replace a Var read.
        assert of_expression(ir.Var(name), env, {name: Range(0, 0)}) is value
        assert bounds_of_expression(ir.Var(name), env) == Bounds(value.lo, value.hi)
        assert range_analysis._value_range(ir.Var(name), env, {}) is value
    assert of_expression(ir.Var("missing"), env) is None
    assert bounds_of_expression(ir.Var("missing"), env) is None
    assert range_analysis._value_range(ir.Var("missing"), env, {}) is None


def test_atomic_reads_do_not_reuse_a_previous_input_binding():
    value, cache = ir.Var("x"), {}
    for current in (Range(1, 1), Range(-3, 9), None, Range(1, 1)):
        env = {} if current is None else {"x": current}
        assert of_expression(value, env) is current
        assert range_analysis._value_range(value, env, cache) is current
        assert bounds_of_expression(value, env, _cache=cache) == (
            None if current is None else Bounds(current.lo, current.hi))


def test_one_sided_bounds_do_not_claim_an_encoding_range():
    value = bounds_of_expression(ir.Var("x"), {"x": Bounds(None, 8)})
    assert value == Bounds(None, 8)
    assert value.finite() is None
    assert trip_range(ir.Var("x"), {"x": value}) == Range(0, 8)


def test_division_by_a_fixed_integer_preserves_the_finite_endpoint():
    positive = ir.Op("fdiv", ir.Var("x"), ir.Lit(4))
    negative = ir.Op("fdiv", ir.Var("x"), ir.Lit(-2))

    assert bounds_of_expression(
        positive, {"x": Bounds(None, 17)},
    ) == Bounds(None, 4)
    assert bounds_of_expression(
        negative, {"x": Bounds(-3, None)},
    ) == Bounds(None, 1)


def test_division_by_a_one_sided_nonzero_denominator_is_bounded():
    expression = ir.Op("fdiv", ir.Var("x"), ir.Var("d"))

    assert bounds_of_expression(expression, {
        "x": Bounds(-9, 10), "d": Bounds(2, None),
    }) == Bounds(-5, 5)
    assert bounds_of_expression(expression, {
        "x": Bounds(-9, 10), "d": Bounds(None, -2),
    }) == Bounds(-5, 4)


def test_assume_refines_conjoined_normal_bounds():
    outside = ir.Op(
        "lor", ir.Op("lt", ir.Var("x"), ir.Lit(-4)),
        ir.Op("ge", ir.Var("x"), ir.Lit(4)))
    assert assume(outside, False, {}) == {"x": Bounds(-4, 3)}


@pytest.mark.parametrize('operator', ['lt', 'le', 'gt', 'ge', 'eq', 'ne'])
@pytest.mark.parametrize('truth', [False, True])
@pytest.mark.parametrize('swapped', [False, True])
def test_integer_conversion_preserves_comparison_refinement(operator, truth, swapped):
    variable, constant = ir.Var('x'), ir.Lit(2)
    plain = (constant, variable) if swapped else (variable, constant)
    converted = tuple(ir.Op('toint', ir.Op('toint', value)) for value in plain)
    environment = {'x': Bounds(-3, 5)}
    assert assume(ir.Op(operator, *converted), truth, environment) == assume(
        ir.Op(operator, *plain), truth, environment)


@pytest.mark.parametrize('wrap', ['uwrap', 'swrap'])
@pytest.mark.parametrize('truth', [False, True])
@pytest.mark.parametrize('value', [0, 1, 255, 256, 257, -256])
def test_truth_does_not_erase_wrapping_of_converted_integers(wrap, truth, value):
    condition = ir.Op('truth', ir.Op(wrap, ir.Op('toint', ir.Var('x')), ir.Lit(8)))
    result = assume(condition, truth, {'x': Bounds(value, value)})
    assert (result is not None) == (bool(value % 256) is truth)


def test_assume_normalizes_one_environment_once_for_a_boolean_tree(
        monkeypatch):
    clauses = [ir.Op("ge", ir.Var(f"x{i}"), ir.Lit(0))
               for i in range(48)]
    condition = clauses[0]
    for clause in clauses[1:]:
        condition = ir.Op("land", condition, clause)
    entered = {f"x{i}": Range(0, 1) for i in range(64)}
    calls = 0
    original = range_analysis._bound

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(range_analysis, "_bound", counted)
    assert range_analysis.assume(condition, True, entered) == {
        name: Bounds(0, 1) for name in entered
    }
    # A nested compiler guard may be much deeper than this one.  Its depth
    # must not multiply the cost of converting every live range.
    assert calls < 500


def test_branch_post_does_not_reconvert_unrelated_live_ranges(monkeypatch):
    guard = ir.Op("ge", ir.Var("x0"), ir.Lit(0))
    parts = [ir.If(guard, ir.Skip(), ir.Skip()) for _ in range(48)]
    body = parts[0]
    for part in parts[1:]:
        body = ir.Seq(body, part)
    entered = {f"x{i}": Range(0, 1) for i in range(64)}
    calls = 0
    original = range_analysis._bound

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(range_analysis, "_bound", counted)
    assert range_analysis.post(body, dict(entered)) == entered
    # Refining x0 must not convert all 63 unrelated entries twice per branch.
    assert calls < 500


def test_repeated_branch_reuses_syntax_reads_but_visits_current_bindings(monkeypatch):
    guard = ir.Op("lt", ir.Var("x"), ir.Lit(0))
    branch = ir.If(guard, ir.Assign("result", ir.Lit(-7)),
                   ir.Assign("result", ir.Lit(9)))
    commands = []
    for value in (-1, 1) * 50:
        commands.extend((ir.Assign("x", ir.Lit(value)), branch))
    body = commands[-1]
    for command in reversed(commands[:-1]):
        body = ir.Seq(command, body)

    syntax_visits = 0
    original = range_analysis._expression_names

    def counted(expression):
        nonlocal syntax_visits
        if expression is guard:
            syntax_visits += 1
        return original(expression)

    observed = []

    def witness(command, env):
        if command is branch:
            observed.append(env["x"])

    monkeypatch.setattr(range_analysis, "_expression_names", counted)
    result = range_analysis.walk_post(body, {"untouched": Range(2, 3)}, witness)
    assert result == {"x": Range(1, 1), "result": Range(9, 9),
                      "untouched": Range(2, 3)}
    assert observed == [Range(value, value) for value in (-1, 1) * 50]
    assert syntax_visits == 1


def test_assume_joins_disjunctive_alternatives_instead_of_intersecting():
    either = ir.Op(
        "lor", ir.Op("lt", ir.Var("x"), ir.Lit(-4)),
        ir.Op("gt", ir.Var("x"), ir.Lit(4)))
    # The two alternatives cover an interval unbounded at both ends.  An
    # intersection would be empty and would unsoundly delete the branch.
    assert assume(either, True, {}) == {"x": Bounds()}


def test_assume_reports_an_infeasible_path():
    condition = ir.Op("lt", ir.Var("x"), ir.Lit(0))
    assert assume(condition, True, {"x": Bounds(1, 3)}) is None


def test_explicit_truth_preserves_a_boolean_comparisons_range_facts():
    condition = ir.Op("truth", ir.Op("lt", ir.Var("x"), ir.Lit(0)))
    assert assume(condition, True, {"x": Bounds(0, 3)}) is None


def test_assume_refines_a_single_bit_truth_test_at_both_interval_ends():
    high_bit = ir.Op("shl", ir.Lit(1), ir.Lit(15))
    condition = ir.Op("truth", ir.Op("and", ir.Var("x"), high_bit))

    assert assume(condition, True, {"x": Bounds(0, 65535)}) == {
        "x": Bounds(32768, 65535)
    }
    assert assume(condition, False, {"x": Bounds(0, 65535)}) == {
        "x": Bounds(0, 32767)
    }
    assert assume(condition, True, {"x": Bounds(0, 7)}) is None


@pytest.mark.parametrize('form', ['and', 'commuted-and', 'uwrap', 'swrap'])
@pytest.mark.parametrize('shift', [0, 1, 3, 7])
def test_shifted_single_bit_refinement_matches_every_small_interval(form, shift):
    value = ir.Op('shr', ir.Var('x'), ir.Lit(shift))
    selected = (ir.Op('and', ir.Lit(1), value) if form == 'commuted-and'
                else ir.Op(form, value, ir.Lit(1)))
    condition = ir.Op('truth', selected)
    for lo in range(-9, 10):
        for hi in range(lo, 10):
            for truth in (False, True):
                possible = [x for x in range(lo, hi + 1) if bool((x >> shift) & 1) == truth]
                got = assume(condition, truth, {'x': Bounds(lo, hi)})
                assert got == ({'x': Bounds(min(possible), max(possible))} if possible else None)


def test_shifted_bit_refinement_preserves_unknown_shifts_and_wider_wraps():
    env = {'x': Bounds(0, 255), 'shift': Bounds(0, 7)}
    for selected in (ir.Op('uwrap', ir.Op('shr', ir.Var('x'), ir.Var('shift')), ir.Lit(1)),
                     ir.Op('uwrap', ir.Var('x'), ir.Lit(2))):
        assert assume(ir.Op('truth', selected), True, env) == env
    selected = ir.Op('uwrap', ir.Op('shr', ir.Var('x'), ir.Lit(10**9)), ir.Lit(1))
    assert assume(ir.Op('truth', selected), True, {'x': Bounds(-17, 17)}) == {'x': Bounds(-17, -1)}
    assert assume(ir.Op('truth', selected), False, {'x': Bounds(-17, 17)}) == {'x': Bounds(0, 17)}


@pytest.mark.parametrize('storage_bits', [8, 16, 64])
@pytest.mark.parametrize('nonzero', [False, True])
def test_packed_bit_guard_keeps_normalization_loop_bounds(storage_bits, nonzero):
    high_bit = ir.Op('uwrap', ir.Op('shr', ir.Var('x'), ir.Lit(7)), ir.Lit(1))
    shift = ir.Assign('x', ir.Op('uwrap', ir.Op('shl', ir.Var('x'), ir.Lit(1)), ir.Lit(storage_bits)))
    body = ir.For('i', ir.Lit(8), ir.If(ir.Op('not', ir.Op('truth', high_bit)), shift, ir.Skip()))
    assert post(body, {'x': Range(int(nonzero), 255)})['x'] == Range(128 if nonzero else 0, 255)


def test_a_failure_arm_is_not_joined_back_into_the_normal_post():
    invalid = ir.Op(
        "lor", ir.Op("lt", ir.Var("i"), ir.Lit(-4)),
        ir.Op("ge", ir.Var("i"), ir.Lit(4)))
    body = ir.Seq(
        ir.If(invalid, ir.Fail("IndexError", "g"), ir.Skip()),
        ir.Assign("n", ir.Var("i")))
    assert post(body, {})["n"] == Range(-4, 3)


# ── widths ───────────────────────────────────────────────────────────

def test_an_unsigned_width_holds_the_maximum():
    assert width_for(Range(0, 255)) == 8
    assert width_for(Range(0, 256)) == 9


def test_a_signed_width_holds_both_ends():
    assert width_for(Range(-128, 127)) == 8
    assert width_for(Range(-129, 127)) == 9


def test_a_width_is_injective_on_its_range():
    r = Range(0, 1000)
    assert (1 << width_for(r)) > r.hi

# ── state across steps ───────────────────────────────────────────────

def test_a_state_variable_keeps_the_range_it_can_hold():
    """What the step can write into it, and not what is reachable from zero.

    A state variable whose starting value nothing could read used to be given
    `Range(0, 0)` and the fixpoint joined outwards from there, so this answered
    `[0, 256]`: the zero was the guessed starting point and `acc` cannot hold it
    on any run, since the first step writes `a + 1`. The bound is now over what
    the step writes, which is true of every run rather than of one.
    """
    body = ir.Seq(ir.Assign("acc", ir.Op("add", ir.Var("a"), ir.Lit(1))),
                  ir.Assign("y", ir.Var("acc")))
    proc = ir.Procedure(["acc"], ["a"], [], ["y"], body)
    out = fixpoint(proc, _env(a=(0, 255)))
    assert out["acc"] == Range(1, 256)


def test_a_state_that_grows_every_step_has_no_bound():
    body = ir.Assign("acc", ir.Op("add", ir.Var("acc"), ir.Lit(1)))
    proc = ir.Procedure(["acc"], [], [], [], body)
    out = fixpoint(proc, {}, rounds=4, limit=8)
    assert "acc" not in out


def _delay_chain(depth):
    """``s0`` grows every step and each ``s(k)`` copies what ``s(k-1)`` held.

    So ``s(k)`` does not move until step ``k``, and the order the assignments run in
    is the whole construction: they go from the far end towards ``s0``, so each reads
    the value from before this step rather than the one just written. Folded the other
    way this is a broadcast, every name takes ``s0``'s new value in one step, and it
    tests nothing.
    """
    parts = [ir.Assign(f"s{k}", ir.Var(f"s{k - 1}"))
             for k in range(depth - 1, 0, -1)]
    parts.append(ir.Assign("s0", ir.Op("add", ir.Var("s0"), ir.Lit(1))))
    body = parts[0]
    for part in parts[1:]:
        body = ir.Seq(body, part)
    state = [f"s{k}" for k in range(depth)]
    return ir.Procedure(state, [], [], [], body,
                        initial={name: 0 for name in state})


def test_an_interval_that_stopped_moving_is_not_an_interval_that_is_closed():
    """The fixpoint ran `STATE_ROUNDS` rounds and kept whatever had not moved on the
    last one. Not moving for one round is not being closed: a change travelling down a
    chain of copies arrives at the far end long after it left, so the tail sits still
    while the change is still coming.

    Twelve deep against eight rounds, so the last four were reported `[0, 0]`, which
    is not a near miss. It says the variable is constantly zero, so its symbol is one
    bit wide, the formula cannot represent the states the run reaches, and every
    unsatisfiable answer about them says nothing. Ten models in the population were in
    that shape and eight of them claimed `[0, 0]`.
    """
    from behave.sim.symbolic import interp
    from behave.sim.symbolic.ranges import unproved

    depth = 12
    proc = _delay_chain(depth)

    # The chain delays, which the construction has to earn rather than assert.
    at, first = {n: 0 for n in proc.state}, {}
    for step in range(1, depth + 4):
        at = interp.run_step(proc, {}, at)[0]
        for name in proc.state:
            if at[name] and name not in first:
                first[name] = step
    assert first[f"s{depth - 1}"] == depth, first

    got = fixpoint(proc, {})
    assert unproved(proc, got) == [], "nothing survives that a step would leave"
    reached = {}
    at = {n: 0 for n in proc.state}
    for _ in range(depth + 4):
        at = interp.run_step(proc, {}, at)[0]
        for name in proc.state:
            reached[name] = max(reached.get(name, 0), at[name])
    kept = [(n, str(got[n]), reached[n]) for n in proc.state
            if n in got and got[n].hi < reached[n]]
    assert kept == [], kept


@pytest.mark.parametrize('case', ['growing', 'delayed', 'initial-excluded'])
def test_seeded_state_ranges_cannot_reenter_after_closure_rejects_them(monkeypatch, case):
    if case == 'delayed':
        chain = _delay_chain(5)
        state = (*chain.state, 'kept')
        body = chain.body
        initial = {name: 0 for name in state}
        seed = {name: Range(0, 0) for name in state}
    else:
        state = ('x', 'kept')
        body = ir.Assign('x', ir.Op('add', ir.Var('x'), ir.Lit(1)))
        initial = {'x': 0, 'kept': 0}
        seed = {'x': Range(0, 1), 'kept': Range(0, 0)}
        if case == 'initial-excluded':
            body = ir.Assign('x', ir.Lit(5))
            initial['x'] = 10
            seed['x'] = Range(5, 5)
    proc = ir.Procedure(state, (), ('local',), (),
                        ir.Seq(body, ir.Assign('local', ir.Var('kept'))), initial=initial)
    original = range_analysis.unproved
    previous = []

    def closure_step(proc, env, *args, **kwargs):
        claimed = set(env).intersection(proc.state)
        if previous:
            # Structural progress, not a wall-time watchdog. The old code
            # immediately fails this assertion instead of hanging the test.
            assert claimed < previous[-1]
        previous.append(claimed)
        return original(proc, env, *args, **kwargs)

    monkeypatch.setattr(range_analysis, 'unproved', closure_step)
    got = fixpoint(proc, seed, rounds=2)
    assert previous and len(previous) <= len(proc.state)
    assert set(got).intersection(proc.state) == {'kept'}
    assert got['kept'] == got['local'] == Range(0, 0)
    assert original(proc, got) == []


def test_the_gate_requires_the_interval_to_contain_where_the_run_starts():
    """Closure alone is a gate that passes an interval no run is ever inside.

    What makes an interval sound for a width is `Init subset of I` **and**
    `post#(I) subset of I`; induction over the steps then puts every reachable state in
    I. The first version checked only the second, so for a step `x := 5` on a variable
    starting at 0 it accepted `[5, 5]`: closed under one more step, and excluding the
    state the run begins in, so a width taken from it cannot carry the first step.
    """
    from behave.sim.symbolic.ranges import unproved

    body = ir.Assign("x", ir.Lit(5))
    proc = ir.Procedure(["x"], [], [], [], body, initial={"x": 0})
    assert unproved(proc, {"x": Range(5, 5)}) == ["x"], "closed, and 0 is outside"
    assert unproved(proc, {"x": Range(0, 5)}) == []

    # Every cell of an array is a starting value, the interval being the join over
    # the cells.
    array = ir.Procedure(["t"], [], [], [], ir.Store("t", ir.Lit(0), ir.Lit(3)),
                         initial={"t": [0, 9, 0, 0]})
    assert unproved(array, {"t": Range(0, 3)}) == ["t"]
    assert unproved(array, {"t": Range(0, 9)}) == []


def test_the_gate_reads_a_variable_absent_from_the_step_as_unbounded():
    """Which is the whole of writing it the right way round.

    `post#` is evaluated over exactly what is claimed, so a variable reading something
    unclaimed drops out of the result rather than coming back wider, and there it
    compares equal to nothing rather than unequal to its interval. Written the obvious
    way the gate reports zero failures on the very chain that fails.

    Exact rather than conservative, and that is the objection worth answering: a
    variable the step does not write is not reported, because `_post` leaves it alone,
    so absence can only be a write whose value had no range.
    """
    from behave.sim.symbolic.ranges import unproved

    # `kept` is never written and keeps its interval; `lost` is written from something
    # with no range, so the step gives it no bound and the claim is unproved.
    body = ir.Seq(ir.Assign("lost", ir.Op("add", ir.Var("free"), ir.Lit(1))),
                  ir.Assign("also", ir.Lit(3)))
    proc = ir.Procedure(["kept", "lost"], [], ["also"], [], body)
    claimed = {"kept": Range(5, 9), "lost": Range(0, 0)}
    assert unproved(proc, claimed) == ["lost"]


def test_a_state_bounded_by_a_conditional_covers_both_arms():
    body = ir.If(ir.Var("a"),
                 ir.Assign("s", ir.Lit(3)),
                 ir.Assign("s", ir.Lit(-2)))
    proc = ir.Procedure(["s"], ["a"], [], [], body)
    out = fixpoint(proc, _env(a=(0, 1)))
    assert out["s"] == Range(-2, 3)


def test_a_loop_index_finishes_at_its_last_iteration():
    body = ir.For("j", 4, ir.Assign("s", ir.Var("j")))
    proc = ir.Procedure(["s"], [], [], [], body)
    out = fixpoint(proc, {})
    assert out["j"] == Range(3, 3)
    assert out["s"] == Range(3, 3)


def test_a_mask_bounds_its_result_whatever_it_masks():
    e = ir.Op("and", ir.Var("unknown"), ir.Lit(0xFFFF))
    assert of_expression(e, {}) == Range(0, 0xFFFF)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("operand", [None, (-32768, 32767), (-32768, -1)])
def test_a_nonconstant_unsigned_mask_bounds_signed_and_unknown_operands(reverse, operand):
    args = (ir.Var("value"), ir.Var("mask"))
    expr = ir.Op("and", *(reversed(args) if reverse else args))
    env = _env(mask=(0, 65535))
    if operand is not None:
        env["value"] = Range(*operand)
    assert of_expression(expr, env) == Range(0, 65535)


@pytest.mark.parametrize("operation", ["and", "or", "xor"])
def test_bitwise_ranges_cover_every_small_signed_interval_pair(operation):
    expr = ir.Op(operation, ir.Var("a"), ir.Var("b"))
    apply = primitives.primitive(operation).denotation
    intervals = [Range(lo, hi) for lo in range(-4, 5) for hi in range(lo, 5)]
    for a in intervals:
        for b in intervals:
            got = of_expression(expr, {"a": a, "b": b})
            values = [apply(x, y) for x in range(a.lo, a.hi + 1) for y in range(b.lo, b.hi + 1)]
            assert got.lo <= min(values) <= max(values) <= got.hi, (a, b, got)
            assert got == of_expression(expr, {"a": b, "b": a})
            if a.lo == a.hi and b.lo == b.hi:
                assert got == Range(values[0], values[0])


@pytest.mark.parametrize("operation", ["and", "or", "xor"])
@pytest.mark.parametrize("width", [1, 32, 65, 256])
def test_constant_bitwise_masks_keep_exact_signed_and_wide_values(operation, width):
    apply = primitives.primitive(operation).denotation
    expr = ir.Op(operation, ir.Var("a"), ir.Var("b"))
    values = (0, -1, (1 << width) - 1, -(1 << width), 1 << width)
    for a in values:
        for b in values:
            result = apply(a, b)
            env = {"a": Range(a, a), "b": Range(b, b)}
            assert of_expression(expr, env) == Range(result, result)
            assert bounds_of_expression(expr, env) == Bounds(result, result)


@pytest.mark.parametrize("width", [3, 16, 65, 128])
def test_variable_masked_history_has_an_inductive_range(width):
    # Common to source NBA event history and Behavior bit-mask accumulators.
    # No source-specific wrapping or guessed state bound is needed.
    full = (1 << width) - 1
    diff = ir.Op("xor", ir.Var("old"), ir.Var("new"))
    body = ir.Assign("seen", ir.Op("or", ir.Var("seen"), ir.Op("and", diff, ir.Var("mask"))))
    proc = ir.Procedure(["seen"], ["old", "new", "mask"], [], [], body, initial={"seen": 0})
    signed = Range(-(1 << (width - 1)), (1 << (width - 1)) - 1)
    env = {"old": signed, "new": signed, "mask": Range(0, full)}
    inferred = fixpoint(proc, env)
    assert inferred["seen"] == Range(0, full)
    assert range_analysis.unproved(proc, inferred) == []


def test_a_modulus_bounds_its_result_whatever_it_divides():
    e = ir.Op("fmod", ir.Var("unknown"), ir.Lit(10))
    assert of_expression(e, {}) == Range(0, 9)


def test_a_masked_accumulator_converges_at_once():
    body = ir.Assign("acc", ir.Op("and", ir.Op("add", ir.Var("acc"), ir.Lit(1)),
                                  ir.Lit(0xFF)))
    proc = ir.Procedure(["acc"], [], [], [], body)
    out = fixpoint(proc, {})
    assert out["acc"] == Range(0, 0xFF)


def test_a_bounded_result_does_not_make_its_operands_encodable():
    # The mask bounds the result, so a loop over it can be unrolled, but the
    # product inside still has no width and the encoder must refuse it.
    inner = ir.Op("mul", ir.Var("a"), ir.Var("b"))
    e = ir.Op("and", inner, ir.Lit(0xFF))
    assert of_expression(e, {}) == Range(0, 0xFF)
    assert unranged_subterms(e, {}) and inner in unranged_subterms(e, {})


def test_nothing_is_unranged_when_every_variable_is_known():
    e = ir.Op("and", ir.Op("mul", ir.Var("a"), ir.Var("b")), ir.Lit(0xFF))
    assert unranged_subterms(e, _env(a=(0, 15), b=(0, 15))) == []


def test_a_modulus_bounds_the_result_but_not_the_dividend():
    inner = ir.Op("mul", ir.Var("a"), ir.Var("b"))
    e = ir.Op("fmod", inner, ir.Lit(10))
    assert of_expression(e, {}) == Range(0, 9)
    assert inner in unranged_subterms(e, {})


def test_a_shift_by_an_unbounded_amount_has_no_range():
    # Shifting by a 32-bit value would give a number with billions of bits, so
    # the analysis reports no range rather than computing one.
    e = ir.Op("shl", ir.Lit(1), ir.Var("s"))
    assert of_expression(e, _env(s=(0, 2**31 - 1))) is None


def test_a_shift_by_a_small_amount_still_has_one():
    e = ir.Op("shl", ir.Var("a"), ir.Var("s"))
    assert of_expression(e, _env(a=(0, 255), s=(0, 4))) == Range(0, 4080)


def test_repeated_operand_multiplication_keeps_the_square_nonnegative():
    value = ir.Var("x")
    square = ir.Op("mul", value, value)
    env = _env(x=(-7, 5))
    assert of_expression(square, env) == Range(0, 49)
    assert bounds_of_expression(square, env) == Bounds(0, 49)


def test_finite_positive_division_keeps_its_sign_and_tight_extrema():
    got = _primitive_range("fdiv", Range(1 << 20, 1 << 20),
                           Range(2, 1 << 22))
    assert got == Range(0, 1 << 19)


def test_a_step_created_state_is_named_as_entry_dead_not_guessed():
    """A canonical IR filler is justified by definite assignment, not treated
    as a source value or a range seed."""
    from behave.sim.behavior.lowering import lower_with_state_ranges

    read = ('class M:\n'
            '    def reset(self):\n'
            '        self.n = 3\n'
            '    def process(self, a):\n'
            '        self.n = self.n + a\n'
            '        return {"y": self.n}\n')
    unread = ('class M:\n'
              '    def reset(self):\n'
              '        self.n = 3\n'
              '    def process(self, a):\n'
              '        self.m = a\n'
              '        return {"y": self.n + self.m}\n')
    assert lower_with_state_ranges(read, input_widths=[8]).guessed_initial == ()
    proc = lower_with_state_ranges(unread, input_widths=[8])
    assert proc.guessed_initial == ()
    assert proc.entry_dead == ("m",)


def test_an_entry_dead_filler_is_not_published_as_source_initial_state():
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.behavior.lowering import lower_with_state_ranges

    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.n = 3\n'
           '    def process(self, a):\n'
           '        self.m = a\n'
           '        return {"y": self.n + self.m}\n')
    proc = lower_with_state_ranges(src, input_widths=[8])
    env = fixpoint(proc, {"a": Range(0, 255)})
    inst = behavior_instance.BehaviorInstance(proc, env, source=src)
    assert inst.unreadable_initial() == {}
    assert proc.initial["m"] == 0, "the total IR state has a filler"
    assert "m" not in inst.domain(1).initial, \
        "the source object has no m at construction"


def test_an_unproved_interval_is_refused_by_the_shape_check():
    """The fixpoint drops what it cannot prove, so an instance built the ordinary way
    has nothing to report. `well_formed` asks anyway, because a caller may assemble
    `env` itself and this is the one defect on that list which leaves the step
    perfectly readable: the width is taken, the query is answered, and the answer means
    nothing. Silent is what it was.

    The env is assembled by hand for that reason. The lowering will not produce it any
    more, and a check that cannot be made to fail is worse than none.
    """
    from behave.sim.behavior import instance as behavior_instance

    proc = _delay_chain(12)
    honest = fixpoint(proc, {})
    assert behavior_instance.BehaviorInstance(proc, honest).well_formed() == []

    claimed = dict(honest)
    claimed["s11"] = Range(0, 0)          # what the old fixpoint returned
    inst = behavior_instance.BehaviorInstance(proc, claimed)
    assert any("does not survive one more step" in r
               for r in inst.well_formed()), inst.well_formed()
