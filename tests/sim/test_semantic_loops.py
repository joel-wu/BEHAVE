"""One semantic loop body, with finite expansion owned by the plan."""

import ast
import pickle

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import while_plan
from behave.sim.symbolic import (
    cfg,
    encoder,
    interp,
    ir,
    plan,
    ranges,
    slicing,
    ssa,
    typecheck as types,
)
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.symbolic.step_template import _leaves
from behave.sim.behavior.lowering import (
    LoweringError,
    _Lowerer,
    lower_with_state_ranges,
)


def _loop_proc(trips):
    body = ir.Seq(
        ir.Assign("s", ir.Lit(0)),
        ir.Seq(
            ir.For(
                "i", trips,
                ir.Assign("s", ir.Op("add", ir.Var("s"), ir.Lit(1)))),
            ir.Assign("y", ir.Var("s"))))
    return ir.Procedure(
        ("s",), ("n",), ("i",), ("y",), body,
        initial={"s": 0}, output_vars={"y": "y"})


def _sealed_loop_proc(*, signature=None):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        z = 0
        for i in range(a):
            if i:
                z += 1
        return {"y": z}
"""
    return lower_with_state_ranges(source, input_widths=[2], signature=signature)


def _one_sealed_loop(proc):
    loops = [command for command in ir.walk(proc.body)
             if isinstance(command, ir.For) and command.flow is not None]
    assert len(loops) == 1
    return loops[0]


def _cutoff(bound, *, coefficient=1, offset=0, inclusive=False):
    return while_plan.WhileCutoff(
        coefficient,
        offset,
        ast.Name(id=bound, ctx=ast.Load()),
        inclusive,
    )


@pytest.mark.parametrize("ranked", [False, True], ids=["exact", "ranked"])
@pytest.mark.parametrize("advances_before", [False, True])
def test_nested_else_continue_cannot_invent_induction_progress(ranked, advances_before):
    condition = "i < a and j < a" if ranked else "i < a"
    before = "i += 1" if advances_before else "pass"
    after = "pass" if advances_before else "i += 1"
    node = ast.parse(f'''while {condition}:
    {before}
    for unused in range(1):
        pass
    else:
        if a & 1:
            continue
    {after}
''').body[0]
    analyse = while_plan.analyse_ranking if ranked else while_plan.analyse
    if advances_before:
        assert analyse(node) is not None
    else:
        # For odd a this back edge makes no progress. Never execute that
        # infinite Python loop as a test; require the proof to refuse it.
        with pytest.raises(while_plan.WhilePlanError):
            analyse(node)


@pytest.mark.parametrize("lowerer", [GraphLowerer, _Lowerer])
@pytest.mark.parametrize("ranked", [False, True], ids=["exact", "ranked"])
def test_lowering_refuses_nested_else_continue_without_progress(lowerer, ranked):
    condition = "i < a and j < a" if ranked else "i < a"
    source = f'''class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        j = 0
        while {condition}:
            for unused in range(1):
                pass
            else:
                if a & 1:
                    continue
            i += 1
        return {{"y": i}}
'''
    with pytest.raises(LoweringError) as captured:
        lower_with_state_ranges(source, input_widths=[4], lowerer=lowerer)
    assert captured.value.code == "while-trip-count-unproved"


def test_an_exit_ranking_bounds_backedges_and_the_final_exit_visit():
    ranking = while_plan.WhileExitRanking(
        "i",
        (while_plan.WhileExitPath(2, 1, (_cutoff(
            "limit", coefficient=2, offset=1),)),),
    )

    visits, entries = while_plan.exit_ranking_domain(
        ranking,
        ranges.Range(0, 1),
        ((ranges.Range(7, 7),),),
    )

    assert visits == ranges.Range(1, 3)
    assert entries == ranges.Range(0, 5)


@pytest.mark.parametrize("start", [-3, 0, 2])
@pytest.mark.parametrize("updates", [((1, 2),), ((2, 1), (2, 2)),
                                     ((2, -1), (3, 1)), ((1, 3), (4, 2))])
def test_affine_exit_bound_covers_every_branch_sequence(start, updates):
    cutoff = 14
    paths = tuple(while_plan.WhileExitPath(
        a, d, (_cutoff("limit", inclusive=True),)) for a, d in updates)
    ranking = while_plan.WhileExitRanking("i", paths)
    bounds = tuple((ranges.Range(cutoff, cutoff),) for _ in paths)
    if any((a - 1) * start + d < 1 for a, d in updates):
        with pytest.raises(while_plan.WhilePlanError, match="strictly increasing"):
            while_plan.exit_ranking_domain(ranking, ranges.Range(start, start), bounds)
        return
    visits, _ = while_plan.exit_ranking_domain(ranking, ranges.Range(start, start), bounds)
    # Enumerate states by depth, not paths, so all branch orders are covered.
    current = {start}
    actual = 1
    while any(value <= cutoff for value in current):
        current = {a * value + d for value in current if value <= cutoff
                   for a, d in updates}
        actual += 1
    assert actual <= visits.hi <= cutoff - start + 2


@pytest.mark.parametrize("base", [2, 3, 17])
def test_affine_exit_bound_is_logarithmic_on_large_domains(base):
    ranking = while_plan.WhileExitRanking("i", (
        while_plan.WhileExitPath(base, 1, (_cutoff("limit"),)),))
    limit = base ** 500
    visits, _ = while_plan.exit_ranking_domain(
        ranking, ranges.Range(0, 0), ((ranges.Range(limit, limit),),))
    assert visits.hi == 502


def test_additive_trip_spans_need_only_the_endpoints_facing_progress():
    increasing = while_plan.WhileProgress(
        ast.Name(id="limit", ctx=ast.Load()), False, "i", "add", 1)
    decreasing = while_plan.WhileProgress(
        ast.Name(id="limit", ctx=ast.Load()), False, "i", "add", -1)

    assert while_plan.trip_span(
        increasing, ranges.Bounds(0, None), ranges.Bounds(None, 12),
    ) == ranges.Range(0, 12)
    assert while_plan.trip_span(
        decreasing, ranges.Bounds(None, 12), ranges.Bounds(0, None),
    ) == ranges.Range(0, 12)

    with pytest.raises(while_plan.WhilePlanError, match="limit upper bound"):
        while_plan.trip_span(
            increasing, ranges.Bounds(0, None), ranges.Bounds(),
        )


def test_right_shift_trip_spans_need_only_the_endpoints_facing_progress():
    decreasing = while_plan.WhileProgress(
        ast.Name(id="limit", ctx=ast.Load()), False,
        "bits", "rshift", 2)

    assert while_plan.trip_span(
        decreasing,
        ranges.Bounds(1 << 62, 1 << 62),
        ranges.Bounds(1, None),
    ) == ranges.Range(0, 31)

    with pytest.raises(while_plan.WhilePlanError,
                       match="finite upper bound"):
        while_plan.trip_span(
            decreasing,
            ranges.Bounds(0, None),
            ranges.Bounds(1, None),
        )


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_caller_path_bound_proves_a_helper_right_shift_loop(lowerer):
    source = """def trunc_zero(n, d):
    q = abs(n) // d
    return -q if n < 0 else q

def root_bit(z):
    bit = 1 << 6
    while bit > z:
        bit >>= 2
    return bit

class M:
    def reset(self):
        pass

    def process(self, a, d):
        n = a - 8
        q = trunc_zero(n, (d & 3) + 1)
        z = n - q * q
        y = 0
        if z > 0:
            y = root_bit(z)
        return {"y": y}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(
        source, input_widths=[4, 4], lowerer=lowerer)

    for a in range(16):
        for d in range(16):
            expected = namespace["M"]().process(a, d)
            _state, output, _hits, error = interp.run_step(
                proc, {"a": a, "d": d})
            assert not error
            assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_source_plan_bootstraps_a_while_over_loop_carried_state(lowerer):
    source = """class M:
    def reset(self):
        self.count = 0

    def process(self, flush):
        for _ in range(3):
            self.count += 1
            if self.count == 4:
                self.count = 0
        if flush and self.count != 0:
            while self.count != 4:
                self.count += 1
        y = self.count
        self.count = 0
        return {"y": y}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(
        source, input_widths=[1], bound=2, lowerer=lowerer)

    loops = [command for command in ir.walk(proc.body)
             if isinstance(command, ir.For)]
    assert len(loops) == 2
    assert any(not isinstance(loop.trip_count, ir.Lit) for loop in loops)
    for flush in (0, 1):
        expected_model = namespace["M"]()
        expected_model.reset()
        expected = expected_model.process(flush)
        state, output, _hits, error = interp.run_step(
            proc, {"flush": flush})
        assert not error
        assert state["count"] == expected_model.count
        assert output.transactions == [expected]


def test_source_closure_starts_at_reset_not_the_horizon_hull():
    source = """class M:
    def __init__(self):
        self.count = 0

    def reset(self):
        self.count = 0

    def process(self, flush):
        for j in range(8):
            for r in range(2):
                self.count += 1
                if self.count == 8:
                    self.count = 0
        if flush:
            for j in range(6):
                for r in range(2):
                    self.count += 1
                    if self.count == 8:
                        self.count = 0
            if self.count != 0:
                while self.count != 8:
                    self.count += 1
            answer = self.count
            self.count = 0
            return {"y": answer}
        return {"y": self.count}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(source, input_widths=[1], bound=64)

    for flush in (0, 1):
        native = namespace["M"]()
        expected = native.process(flush)
        state, output, _hits, error = interp.run_step(
            proc, {"flush": flush})
        assert not error
        assert state["count"] == native.count
        assert output.transactions == [expected]


def test_every_cutoff_on_an_exit_path_restricts_its_domain():
    ranking = while_plan.WhileExitRanking(
        "i",
        (while_plan.WhileExitPath(
            1,
            1,
            (
                _cutoff("wide"),
                _cutoff("narrow", coefficient=2, inclusive=True),
            ),
        ),),
    )

    visits, entries = while_plan.exit_ranking_domain(
        ranking,
        ranges.Range(2, 3),
        ((ranges.Range(10, 10), ranges.Range(8, 8)),),
    )

    assert visits == ranges.Range(1, 4)
    assert entries == ranges.Range(2, 5)


def test_an_exit_ranking_rejects_a_nonprogressing_backedge():
    ranking = while_plan.WhileExitRanking(
        "i",
        (while_plan.WhileExitPath(1, 0, (_cutoff("limit"),)),),
    )

    with pytest.raises(while_plan.WhilePlanError, match="not strictly increasing"):
        while_plan.exit_ranking_domain(
            ranking,
            ranges.Range(0, 0),
            ((ranges.Range(1, 1),),),
        )


def test_an_impossible_backedge_leaves_only_the_exit_visit():
    ranking = while_plan.WhileExitRanking(
        "i",
        (while_plan.WhileExitPath(1, 1, (_cutoff("limit"),)),),
    )

    visits, entries = while_plan.exit_ranking_domain(
        ranking,
        ranges.Range(5, 7),
        ((ranges.Range(3, 3),),),
    )

    assert visits == ranges.Range(1, 1)
    assert entries == ranges.Range(5, 7)


@pytest.mark.parametrize("trips", (0, 1, 7))
def test_zero_one_and_many_semantic_trips(trips):
    proc = _loop_proc(ir.Var("n"))
    _state, output, _hits, error = interp.run_step(proc, {"n": trips})
    assert not error
    assert output.transactions == [{"y": trips}]


def test_the_trip_count_is_evaluated_once_at_loop_entry():
    body = ir.Seq(
        ir.For(
            "i", ir.Var("n"),
            ir.Seq(
                ir.Assign("n", ir.Lit(0)),
                ir.Assign("s", ir.Op("add", ir.Var("s"), ir.Lit(1))))),
        ir.Assign("y", ir.Var("s")))
    proc = ir.Procedure(
        ("n", "s"), (), ("i",), ("y",), body,
        initial={"n": 4, "s": 0}, output_vars={"y": "y"})
    state, output, _hits, error = interp.run_step(proc, {})
    assert not error
    assert state == {"n": 0, "s": 4}
    assert output.transactions == [{"y": 4}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_source_trip_effect_runs_once_before_the_retained_body(lowerer):
    source = """class M:
    def reset(self):
        self.calls = 0

    def count(self, value):
        self.calls += 1
        return value & 3

    def process(self, a):
        z = 0
        for i in range(self.count(a)):
            a = 0
            z += 1
        return {"y": z * 10 + self.calls}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], lowerer=lowerer)
    loops = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    assert len(loops) == 1
    _state, output, _hits, error = interp.run_step(proc, {"a": 3})
    assert not error
    assert output.transactions == [{"y": 31}], \
        "the count saw a=3 once even though the body overwrote a"


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_enumerate_binds_index_and_element_with_a_dynamic_start(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        total = 0
        for i, value in enumerate([2, 3, 5], a & 1):
            total += (i + 1) * value
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[2], lowerer=lowerer)
    for value, answer in ((0, 23), (1, 33), (2, 23), (3, 33)):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": answer}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_enumerate_wraps_a_dynamic_range(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        total = 0
        for i, value in enumerate(range(a & 3), 4):
            total += i * 10 + value
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], lowerer=lowerer)
    expected = (0, 40, 91, 153, 0, 40, 91, 153)
    for value, answer in enumerate(expected):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": answer}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_enumerate_target_contract_reaches_output_capacity(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        out = []
        for i, value in enumerate([2, 3], a & 1):
            out.append({"index": i, "value": value})
        return out
"""
    proc = lower_with_state_ranges(
        source, input_widths=[2], lowerer=lowerer)
    for value in range(4):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        start = value & 1
        assert not error
        assert output.transactions == [
            {"index": start, "value": 2},
            {"index": start + 1, "value": 3},
        ]


def test_graph_lowering_retains_a_comprehension_body_once():
    source = """class M:
    def reset(self):
        pass

    def bump(self, value, offset):
        if value & 1:
            return value + offset
        return value - offset

    def process(self, a):
        values = [self.bump(i, a) for i in range(32)]
        return {"y": values[31]}
"""
    proc = lower_with_state_ranges(
        source,
        input_bounds={"a": (0, 7)},
        lowerer=GraphLowerer,
    )
    loops = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    assert len(loops) == 1
    assert loops[0].flow is not None
    for value in (0, 3, 7):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": 31 + value}]


def test_nested_filtered_comprehensions_are_nested_retained_loops():
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        values = [
            i + j + a
            for i in range(4) if i & 1
            for j in range(3) if j != 1
        ]
        y = (values[0] * 1000 + values[1] * 100
             + values[2] * 10 + values[3])
        return {"y": y}
"""
    proc = lower_with_state_ranges(
        source,
        input_bounds={"a": (0, 3)},
        lowerer=GraphLowerer,
    )
    loops = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    assert len(loops) == 2
    assert all(loop.flow is not None for loop in loops)
    for value in (0, 1, 3):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": 1335 + 1111 * value}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_negative_range_is_an_exact_empty_loop(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        y = 1
        for i in range(-3):
            y = 2
        else:
            return {"y": 7}
        return {"y": 9}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[1], lowerer=lowerer)
    assert not any(isinstance(cmd, ir.For) for cmd in ir.walk(proc.body))
    _state, output, _hits, error = interp.run_step(proc, {"a": 0})
    assert not error
    assert output.transactions == [{"y": 7}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_break_continue_else_and_return_keep_python_control_semantics(lowerer):
    control = """class M:
    def reset(self):
        pass

    def process(self, a):
        z = 0
        for i in range(a):
            if i == 1:
                continue
            if i == 3:
                break
            z += i
        else:
            z += 100
        return {"y": z}
"""
    proc = lower_with_state_ranges(
        control, input_widths=[3], lowerer=lowerer)
    expected = {0: 100, 1: 100, 2: 100, 3: 102, 4: 2, 7: 2}
    for value, wanted in expected.items():
        got = interp.run_step(proc, {"a": value})[1].transactions
        assert got == [{"y": wanted}], (value, got)

    returned = """class M:
    def reset(self):
        pass

    def process(self, a):
        for i in range(5):
            if i == a:
                return {"y": i}
        else:
            return {"y": 9}
        return {"y": 8}
"""
    proc = lower_with_state_ranges(
        returned, input_widths=[3], lowerer=lowerer)
    for value in (0, 2, 4, 7):
        wanted = value if value < 5 else 9
        got = interp.run_step(proc, {"a": value})[1].transactions
        assert got == [{"y": wanted}], (value, got)


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_nested_loops_retain_one_body_at_each_occurrence(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        z = 0
        for i in range(3):
            for j in range(4):
                if j == 1:
                    continue
                if i == 2:
                    break
                z += 1
        return {"y": z}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[1], lowerer=lowerer)
    loops = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    assert len(loops) == 2
    assert interp.run_step(proc, {"a": 0})[1].transactions == [{"y": 6}]


def test_failure_absorbs_later_iterations_and_commands_but_keeps_hits():
    body = ir.Seq(
        ir.For(
            "i", ir.Lit(5),
            ir.Seq(
                ir.Mark("visited"),
                ir.If(
                    ir.Op("eq", ir.Var("i"), ir.Lit(2)),
                    ir.Fail("IndexError", "failed"), ir.Skip()))),
        ir.Mark("after"))
    proc = ir.Procedure((), (), ("i",), (), body)
    _state, _output, hits, error = interp.run_step(proc, {})
    assert error == "IndexError"
    assert hits == {"visited", "failed"}

    planned = plan.plan_encoding(proc, {})
    encoded = encoder.encode_step(proc, env={}, plan=planned)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    assert solver.check() == z3.sat
    model = solver.model()
    assert not z3.is_true(model.eval(encoded.ok, model_completion=True))
    assert z3.is_true(model.eval(encoded.hits["visited"],
                                 model_completion=True))
    assert z3.is_true(model.eval(encoded.hits["failed"],
                                 model_completion=True))
    assert not z3.is_true(model.eval(encoded.hits["after"],
                                     model_completion=True))


def test_plan_owns_finite_loop_maxima_expansion_and_identity():
    proc = _loop_proc(ir.Var("n"))
    env = ranges.fixpoint(
        proc, {"s": ranges.Range(0, 0), "n": ranges.Range(0, 5)})
    planned = plan.plan_encoding(proc, env)
    assert planned.complete, planned.unresolved
    assert tuple(planned.loop_bounds.values()) == (5,)
    assert planned.expansion_cost > ir.cost(proc.body)

    restored = pickle.loads(pickle.dumps(planned))
    assert restored == planned
    assert restored.validate(proc, env) is restored
    assert plan.procedure_identity(proc) == planned.procedure_fingerprint
    assert plan.procedure_identity(_loop_proc(ir.Lit(4))) != \
        plan.procedure_identity(_loop_proc(ir.Lit(5)))
    forged = planned.replaced(
        loop_bounds={key: 4 for key in planned.loop_bounds})
    with pytest.raises(plan.PlanMismatch, match="loop expansion"):
        forged.validate(proc, env)


def test_frontend_trip_interval_bounds_a_predicated_dynamic_count():
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        n = a if a <= 4 else 4
        y = 0
        for _ in range(n):
            y += 1
        return {"y": y}
"""
    proc = lower_with_state_ranges(source, input_widths=[16])
    loop = _one_sealed_loop(proc)
    assert (loop.flow.minimum, loop.flow.maximum) == (0, 4)
    assert ranges.loop_trip_range(loop, {}) == ranges.Range(0, 4)

    env = {**proc.state_ranges, "a": ranges.Range(0, 65535)}
    planned = plan.plan_encoding(proc, env)
    assert planned.complete, planned.unresolved
    assert tuple(planned.loop_bounds.values()) == (4,)
    for value, answer in ((0, 0), (3, 3), (65535, 4)):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": answer}]


def test_semantic_size_is_constant_while_planned_expansion_tracks_the_maximum():
    def made(maximum):
        proc = _loop_proc(ir.Lit(maximum))
        env = ranges.fixpoint(
            proc, {"s": ranges.Range(0, 0), "n": ranges.Range(0, 0)})
        return proc, plan.plan_encoding(proc, env)

    small, small_plan = made(2)
    large, large_plan = made(20)
    assert ir.cost(small.body) == ir.cost(large.body)
    assert small_plan.expansion_cost < large_plan.expansion_cost
    assert tuple(small_plan.loop_bounds.values()) == (2,)
    assert tuple(large_plan.loop_bounds.values()) == (20,)

    nested = ir.For(
        "i", ir.Lit(3),
        ir.For("j", ir.Lit(4), ir.Assign("s", ir.Lit(1))))
    nested_proc = ir.Procedure(
        ("s",), (), ("i", "j"), (), nested, initial={"s": 0})
    nested_plan = plan.plan_encoding(
        nested_proc, {"s": ranges.Range(0, 1)})
    assert sorted(nested_plan.loop_bounds.values()) == [3, 4]
    assert nested_plan.expansion_cost == 1 + 3 * (1 + 4)


def test_expansion_cost_preserves_fixed_outer_iteration_correlations():
    """A fixed outer value must not multiply every inner maximum.

    Each outer turn selects a distinct exact inner count.  The encoder sees
    those constants and emits 1, 2, 3 and 4 body copies, respectively; charging
    four copies on all four turns would account for a path the program has no
    execution of.
    """
    selected = ir.Sel(
        ir.Op("arraylit", *(ir.Lit(value) for value in (1, 2, 3, 4))),
        ir.Var("i"),
    )
    body = ir.For(
        "i", ir.Lit(4),
        ir.For("j", selected, ir.Assign("s", ir.Lit(1))),
    )
    proc = ir.Procedure(
        ("s",), (), ("i", "j"), (), body, initial={"s": 0})
    planned = plan.plan_encoding(proc, {"s": ranges.Range(0, 1)})

    assert planned.complete, planned.unresolved
    assert sorted(planned.loop_bounds.values()) == [4, 4]
    assert planned.expansion_cost == 1 + sum(1 + n for n in (1, 2, 3, 4))
    encoder.encode_step(proc, env={"s": ranges.Range(0, 1)}, plan=planned)


def test_contextual_expansion_obeys_the_certified_occurrence_envelope():
    """A weaker term range cannot overrule the CFG-backed loop maximum."""
    proc = _loop_proc(ir.Lit(8))
    env = {"s": ranges.Range(0, 8), "n": ranges.Range(0, 0)}
    numbers = plan._command_numbers(proc.body)
    loop = next(command for command in ir.walk(proc.body)
                if isinstance(command, ir.For))
    number = numbers[id(loop)]
    occurrence = str(plan.ExpressionPosition(number, "trip_count"))

    full_cost, _ = plan._contextual_expansion(
        proc, env, {occurrence: 8})
    capped_cost, visits = plan._contextual_expansion(
        proc, env, {occurrence: 3})

    assert capped_cost < full_cost
    assert dict(visits)[number] == (((), 3),)


def test_encoder_uses_contextual_loop_bounds_after_an_intermediate_write(
        monkeypatch):
    """A sealed contextual plan must also bound the allocated formula.

    Naming an exact inner count introduces an SSA symbol, so syntactic Z3
    constant folding cannot recover the outer-loop correlation.  The current
    range environment still proves it and must prevent every outer turn from
    paying the occurrence-wide maximum.
    """
    selected = ir.Sel(
        ir.Op("arraylit", *(ir.Lit(value) for value in (1, 2, 3, 4))),
        ir.Var("i"),
    )
    body = ir.For(
        "i", ir.Lit(4),
        ir.Seq(
            ir.Assign("n", selected),
            ir.For("j", ir.Var("n"), ir.Assign("s", ir.Lit(1))),
        ),
    )
    proc = ir.Procedure(
        ("s",), (), ("i", "j", "n"), (), body, initial={"s": 0})
    env = {"s": ranges.Range(0, 1)}
    planned = plan.plan_encoding(proc, env)

    assert planned.complete, planned.unresolved
    monkeypatch.setattr(encoder, "TERM_BUDGET", planned.expansion_cost)
    encoder.encode_step(proc, env=env, plan=planned)


def test_context_keys_survive_projection_of_compiler_loop_indices():
    """Control history is not reconstructed from the live data environment.

    Lowered source loops bind a synthetic iteration value and then consume it
    while computing later trip counts.  Range projection may correctly drop
    that dead synthetic value before reaching a deeper loop; the planner must
    still address the deeper visit by the outer iterations that led to it.
    """
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        y = 0
        for n in (2, 4):
            for b in range(0, 4, n):
                step = n >> 1
                for _ in range(step):
                    y += a
        return {"y": y}
"""
    proc = lower_with_state_ranges(source, input_widths=[1])
    env = {**proc.state_ranges, "a": ranges.Range(0, 1)}
    planned = plan.plan_encoding(proc, env)

    assert planned.complete, planned.unresolved
    context_depths = {
        max((len(context) for context, _maximum in entries), default=0)
        for entries in planned.loop_visits.values()
    }
    assert {0, 1, 2} <= context_depths
    encoder.encode_step(proc, env=env, plan=planned)


def test_encoder_consumes_the_planners_per_visit_loop_bounds(monkeypatch):
    """Symbolic control must not erase an outer-loop correlation.

    The range proof knows ``a == 1`` throughout this domain.  The formula
    nevertheless carries ``a`` as a symbol, so assigning ``n`` below a guard
    joins it with its prior range inside the encoder.  The sealed visit
    schedule is what preserves the exact inner counts 4, 1, 2 and 3.
    """
    selected = ir.Sel(
        ir.Op("arraylit", *(ir.Lit(value) for value in (4, 1, 2, 3))),
        ir.Var("i"),
    )
    guarded_count = ir.If(
        ir.Op("eq", ir.Var("a"), ir.Lit(1)),
        ir.Assign("n", selected),
        ir.Fail("ValueError", "outside-domain"),
    )
    body = ir.For(
        "i", ir.Lit(4),
        ir.Seq(
            guarded_count,
            ir.For("j", ir.Var("n"), ir.Assign("s", ir.Lit(1))),
        ),
    )
    proc = ir.Procedure(
        ("s",), ("a",), ("i", "j", "n"), (), body,
        initial={"s": 0})
    env = {"s": ranges.Range(0, 1), "a": ranges.Range(1, 1)}
    planned = plan.plan_encoding(proc, env)

    assert planned.complete, planned.unresolved
    assert (4, 1, 2, 3) in tuple(
        tuple(maximum for _context, maximum in entries)
        for entries in planned.loop_visits.values())
    # The formula also materializes the four syntactic failure arms before
    # the input-domain constraint proves them dead.  Leave exactly that fixed
    # overhead, but not the six extra inner copies a joined maximum would add.
    monkeypatch.setattr(
        encoder, "TERM_BUDGET", planned.expansion_cost + 4)
    encoder.encode_step(proc, env=env, plan=planned)

    visits = dict(planned.loop_visits)
    inner = next(key for key, values in visits.items()
                 if tuple(maximum for _context, maximum in values)
                 == (4, 1, 2, 3))
    visits[inner] = tuple((context, 4) for context, _maximum in visits[inner])
    forged = planned.replaced(loop_visits=visits)
    assert forged.fingerprint != planned.fingerprint
    with pytest.raises(plan.PlanMismatch):
        encoder.encode_step(proc, env=env, plan=forged)


def test_encoder_enforces_its_command_budget_after_a_larger_preflight(
        monkeypatch):
    """The allocation-side counter is independent of the plan estimate."""
    proc = _loop_proc(ir.Lit(10))
    env = {"s": ranges.Range(0, 10), "n": ranges.Range(0, 0)}
    monkeypatch.setattr(plan, "TERM_BUDGET", 100)
    planned = plan.plan_encoding(proc, env)
    assert planned.complete, planned.unresolved

    # Model a future planner bug that under-reports the preflight.  Validation
    # is deliberately made to reproduce that same answer; the independent
    # allocation-side counter must still refuse before exceeding its limit.
    contextual_visits = dict(planned.loop_visits)
    planned = planned.replaced(expansion_cost=1)
    monkeypatch.setattr(
        plan, "_contextual_expansion",
        lambda _proc, _env, _loop_bounds: (1, contextual_visits))
    monkeypatch.setattr(encoder, "TERM_BUDGET", 5)
    with pytest.raises(encoder.EncodingError) as caught:
        encoder.encode_step(proc, env=env, plan=planned)
    assert caught.value.kind == "capability"
    assert "semantic command copies" in str(caught.value)


def test_an_unproved_loop_maximum_is_a_typed_plan_refusal():
    proc = _loop_proc(ir.Var("n"))
    env = {"s": ranges.Range(0, 0)}
    planned = plan.plan_encoding(proc, env)
    assert any(item.kind == "range"
               and "trip count" in item.detail
               for item in planned.unresolved)
    with pytest.raises(encoder.EncodingError) as caught:
        encoder.encode_step(proc, env=env, plan=planned)
    assert caught.value.kind == "range"
    faults = encoder.verify_encoding(proc, env=env)
    assert any(fault.kind == "range" and "trip count" in fault.message
               for fault in faults)


def test_expansion_capability_is_checked_even_if_a_plan_drops_its_diagnostic(
        monkeypatch):
    proc = _loop_proc(ir.Var("n"))
    env = {"s": ranges.Range(0, 10), "n": ranges.Range(0, 10)}
    monkeypatch.setattr(plan, "TERM_BUDGET", 5)
    monkeypatch.setattr(encoder, "TERM_BUDGET", 5)

    planned = plan.plan_encoding(proc, env)
    assert planned.expansion_cost == 6       # saturated capability sentinel
    assert any(item.what == "encoding-expansion"
               for item in planned.unresolved)
    forged = planned.replaced(unresolved=())
    with pytest.raises(encoder.EncodingError) as caught:
        encoder.encode_step(proc, env=env, plan=forged)
    assert caught.value.kind == "capability"
    assert any(fault.kind == "capability"
               for fault in encoder.verify_encoding(proc, env=env))


def test_the_symbolic_expansion_matches_each_concrete_trip_count():
    proc = _loop_proc(ir.Var("n"))
    env = ranges.fixpoint(
        proc, {"s": ranges.Range(0, 0), "n": ranges.Range(0, 5)})
    planned = plan.plan_encoding(proc, env)
    encoded = encoder.encode_step(proc, env=env, plan=planned)
    for trips in (0, 1, 5):
        solver = z3.Solver()
        solver.add(encoded.constraint)
        solver.add(encoded.pre["s"] == 0, encoded.pre["n"] == trips)
        assert solver.check() == z3.sat
        model = solver.model()
        assert model.eval(encoded.post["y"]).as_long() == trips


def test_a_fixed_trip_count_does_not_turn_the_body_into_guarded_writes():
    """A ground loop count is evaluated, not promoted to a symbolic choice."""

    proc = _loop_proc(ir.Lit(5))
    env = ranges.fixpoint(
        proc, {"s": ranges.Range(0, 0), "n": ranges.Range(0, 0)})
    encoded = encoder.encode_step(
        proc, env=env, plan=plan.plan_encoding(proc, env))

    assert all(
        definition.rhs.decl().kind() != z3.Z3_OP_ITE
        for definition in encoded.definitions
    )
    solver = z3.Solver()
    solver.add(encoded.constraint)
    assert solver.check() == z3.sat
    assert solver.model().eval(encoded.post["y"]).as_long() == 5


def test_a_fixed_trip_count_does_not_encode_an_inactive_planned_tail():
    """A sound loose maximum is not a request to build unreachable copies."""

    proc = _loop_proc(ir.Lit(5))
    env = ranges.fixpoint(
        proc, {"s": ranges.Range(0, 0), "n": ranges.Range(0, 0)})
    planned = plan.plan_encoding(proc, env)
    exact = encoder._encode_derived_step(proc, env=env, plan=planned)
    occurrence, = planned.loop_bounds
    loose = planned.replaced(loop_bounds={occurrence: 100})
    encoded = encoder._encode_derived_step(proc, env=env, plan=loose)

    assert len(encoded.definitions) == len(exact.definitions)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    assert solver.check() == z3.sat
    assert solver.model().eval(encoded.post["y"]).as_long() == 5


def test_symbolic_expansion_retains_the_last_executed_loop_index():
    body = ir.Seq(
        ir.For("i", ir.Lit(4), ir.Assign("s", ir.Var("i"))),
        ir.Assign("y", ir.Var("i")),
    )
    proc = ir.Procedure(
        ("s",), (), ("i",), ("y",), body,
        initial={"s": 0}, output_vars={"y": "y"})
    env = ranges.fixpoint(proc, {"s": ranges.Range(0, 0)})
    encoded = encoder.encode_step(
        proc, env=env, plan=plan.plan_encoding(proc, env))

    solver = z3.Solver()
    solver.add(encoded.constraint)
    assert solver.check() == z3.sat
    assert solver.model().eval(encoded.post["y"]).as_long() == 3


def test_slice_cfg_ssa_and_types_preserve_a_semantic_loop():
    loop = ir.For(
        "i", ir.Var("n"),
        ir.Seq(
            ir.Assign("s", ir.Op("add", ir.Var("s"), ir.Lit(1))),
            ir.Mark("goal")))
    events = slicing.linearize(loop)
    cut = slicing.cone(events, "goal")
    assert id(loop) in cut.kept and "n" in cut.needs
    sliced = slicing.prune(loop, cut.kept)
    assert isinstance(sliced, ir.For)
    assert repr(sliced.trip_count) == repr(loop.trip_count)

    graph = cfg.of_command(loop)
    retained = cfg.if_convert(graph)
    assert len([cmd for cmd in ir.walk(retained)
                if isinstance(cmd, ir.For)]) == 1
    assert ssa.undefined_uses(graph, {"n", "s"}) == []

    proc = ir.Procedure(
        ("s",), ("n",), ("i",), (), loop, initial={"s": 0})
    _held, faults = types.of_procedure(proc, graph)
    assert faults == []


def test_loop_flow_is_frontend_constructed_immutable_and_rechecked():
    with pytest.raises(TypeError, match="frontend CFG"):
        ir.LoopFlow(1, 1, (), ("made-up",))

    proc = _sealed_loop_proc()
    loop = _one_sealed_loop(proc)
    with pytest.raises(AttributeError, match="immutable"):
        loop.flow._defined = ("made-up",)

    # Immutability is the first lock.  Re-derivation is the second: even a
    # caller deliberately bypassing Python's attribute hook cannot make a
    # self-consistent claim out of facts the sealed CFG does not prove.
    object.__setattr__(loop.flow, "_defined", ("made-up",))
    with pytest.raises(ir.RepresentationStructureError) as caught:
        ssa.loop_flow_facts(loop)
    assert caught.value.code == "loop-flow-facts-mismatch"


def test_loop_flow_validation_is_reused_only_inside_one_analysis(monkeypatch):
    loop = _one_sealed_loop(_sealed_loop_proc())
    original = cfg.graph_fingerprint
    calls = 0

    def counted(graph):
        nonlocal calls
        calls += 1
        return original(graph)

    monkeypatch.setattr(cfg, "graph_fingerprint", counted)
    cache = {}
    assert ssa.loop_flow_facts(loop, _cache=cache) is loop.flow
    after_first = calls
    assert after_first > 0
    assert ssa.loop_flow_facts(loop, _cache=cache) is loop.flow
    assert calls == after_first

    # A public call owns a new transaction and therefore rechecks the seal.
    assert ssa.loop_flow_facts(loop) is loop.flow
    assert calls > after_first


@pytest.mark.parametrize("part", ("body", "trip-count"))
def test_loop_flow_cannot_be_reused_after_its_command_changes(part):
    loop = _one_sealed_loop(_sealed_loop_proc())
    if part == "body":
        loop.body = ir.Seq(loop.body, ir.Skip())
    else:
        loop.trip_count = ir.Lit(1)
    with pytest.raises(ir.RepresentationStructureError) as caught:
        ssa.loop_flow_facts(loop)
    assert caught.value.code == "loop-flow-command-mismatch"


def test_local_and_outer_graph_mutation_invalidates_the_bound_identity():
    proc = _sealed_loop_proc()
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    prepared = plan.plan_encoding(proc, env)

    loop = _one_sealed_loop(proc)
    local = loop.flow.graph
    local.blocks[local.entry].instrs.append(ir.Assign("tampered", ir.Lit(1)))
    with pytest.raises(ir.RepresentationStructureError) as caught:
        prepared.validate(proc, dict(prepared.env))
    assert caught.value.code == "loop-flow-graph-changed"

    proc = _sealed_loop_proc()
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    prepared = plan.plan_encoding(proc, env)
    outer = proc.graph
    outer.blocks[outer.entry].instrs.append(ir.Assign("tampered", ir.Lit(1)))
    with pytest.raises(ir.RepresentationStructureError) as caught:
        prepared.validate(proc, dict(prepared.env))
    assert caught.value.code == "control-flow-authority-changed"


def test_procedure_body_and_control_flow_are_a_write_once_pair():
    naked = ir.Procedure((), (), (), (), ir.Skip())
    with pytest.raises(AttributeError, match="bound by its frontend"):
        naked.graph = cfg.of_command(naked.body)
    assert naked.graph is None

    proc = _sealed_loop_proc()
    with pytest.raises(AttributeError, match="bound by its frontend"):
        proc.graph = cfg.of_command(proc.body)
    with pytest.raises(ir.RepresentationStructureError) as caught:
        proc.derived(body=ir.Seq(proc.body, ir.Skip()))
    assert caught.value.code == "derived-body-kept-stale-control-flow"

    proc.body = ir.Seq(proc.body, ir.Skip())
    with pytest.raises(ir.RepresentationStructureError) as caught:
        plan.procedure_identity(proc)
    assert caught.value.code == "control-flow-body-changed"


def test_loop_flow_and_plan_identity_survive_the_worker_pickle_boundary():
    proc = _sealed_loop_proc()
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    prepared = plan.plan_encoding(proc, env)
    restored = pickle.loads(pickle.dumps(proc))

    assert plan.procedure_identity(restored) == plan.procedure_identity(proc)
    assert prepared.validate(restored, dict(prepared.env)) is prepared
    assert ssa.loop_flow_facts(_one_sealed_loop(restored)) is not None


def test_slicing_drops_local_and_outer_control_flow_authorities():
    proc = _sealed_loop_proc()
    inside = next(
        goal for goal in ir.goals_of(proc.body)
        if goal.startswith("location:behavior.py:8:"))
    sliced = slicing.sliced(proc, [inside])

    assert sliced is not proc
    assert sliced.graph is None
    loops = [command for command in ir.walk(sliced.body)
             if isinstance(command, ir.For)]
    assert loops and all(loop.flow is None for loop in loops)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("source_graph", [False, True])
def test_mark_only_loop_projection_keeps_values_failures_and_flow(nested, source_graph):
    source = """class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        for i in range(3):
            for j in range(2):
                if a == i:
                    self.s = (self.s + j + 1) & 7
                if a == 3:
                    self.s = 5 // (a - 3)
        return {"y": self.s}
"""
    proc = (lower_with_state_ranges(source, input_widths=[2])
            if nested else _sealed_loop_proc())
    all_commands = tuple(ir.walk(proc.body))
    loops = [c for c in all_commands if isinstance(c, ir.For)]
    wanted = next(c.goal for c in ir.walk(loops[-1].body)
                  if isinstance(c, ir.Mark))
    keep = {id(c) for c in all_commands
            if not isinstance(c, ir.Mark) or c.goal == wanted}
    original_seal = ir.command_fingerprint(proc.body, include_flow=True)
    projected = (encoder._query_procedure(proc, keep, encoder._procedure_validation(proc))
                 if source_graph else proc.derived(
                     body=slicing.prune(proc.body, keep), graph=None,
                     boundary_complete=False))
    assert (projected.graph is not None) == source_graph
    if source_graph:
        assert projected.graph is not proc.graph
    projected_loops = [c for c in ir.walk(projected.body) if isinstance(c, ir.For)]
    assert len(projected_loops) == len(loops)
    for original, small in zip(loops, projected_loops):
        assert ssa.loop_flow_facts(small) is small.flow
        assert small.flow is not None
        assert (small.flow.minimum, small.flow.maximum,
                small.flow.required, small.flow.defined) == (
                    original.flow.minimum, original.flow.maximum,
                    original.flow.required, original.flow.defined)
    selected = ir.goals_of(projected.body)
    assert wanted in selected
    assert selected < ir.goals_of(proc.body)
    assert {c.goal for c in all_commands if isinstance(c, ir.Fail)} <= selected
    for value in range(4):
        full_state, full_output, full_hits, full_error = interp.run_step(proc, {"a": value})
        state, output, hits, error = interp.run_step(projected, {"a": value})
        assert (state, output.transactions, error) == (
            full_state, full_output.transactions, full_error)
        assert hits == full_hits & selected
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    full = encoder.encode_step(proc, env=env, tag="full")
    small = encoder.encode_step(projected, pre=full.pre, env=env, tag="projected")
    solver = z3.Solver()
    solver.add(full.constraint, small.constraint)
    assert solver.check() == z3.sat
    differences = [full.ok != small.ok, full.error != small.error]
    differences += [full.hits[goal] != small.hits[goal] for goal in selected]
    for field in ("post", "state_post"):
        for name, value in getattr(full, field).items():
            for left, right in zip(_leaves(value), _leaves(getattr(small, field)[name]),
                                   strict=True):
                if z3.is_bv(left):
                    left, right = encoder.widen(left, right)
                differences.append(left != right)
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat
    assert ir.command_fingerprint(proc.body, include_flow=True) == original_seal
    restored = pickle.loads(pickle.dumps(projected))
    assert plan.procedure_identity(restored) == plan.procedure_identity(projected)
    for loop in ir.walk(restored.body):
        if isinstance(loop, ir.For):
            assert ssa.loop_flow_facts(loop) is not None


def test_projected_observation_flow_still_rejects_changed_computation():
    proc = _sealed_loop_proc()
    kept = {id(c) for c in ir.walk(proc.body) if not isinstance(c, ir.Mark)}
    projected = proc.derived(body=slicing.prune(proc.body, kept), graph=None,
                             boundary_complete=False)
    loop = _one_sealed_loop(projected)
    loop.body = ir.Seq(loop.body, ir.Assign("z", ir.Lit(17)))
    with pytest.raises(ir.RepresentationStructureError,
                       match="does not describe its command"):
        ssa.loop_flow_facts(loop)


@pytest.mark.parametrize("part", ["body", "graph"])
def test_projected_source_authority_rejects_mutation(part):
    proc = _sealed_loop_proc()
    projected = encoder._query_procedure(proc, (), encoder._procedure_validation(proc))
    assert projected.graph is not None
    original = plan.procedure_identity(proc)
    if part == "body":
        projected.body = ir.Seq(projected.body, ir.Assign("z", ir.Lit(17)))
    else:
        graph = projected.graph
        graph.blocks[graph.entry].instrs.append(ir.Assign("z", ir.Lit(17)))
    with pytest.raises(ir.RepresentationStructureError):
        plan.procedure_identity(projected)
    assert plan.procedure_identity(proc) == original


def test_public_goal_and_local_queries_preserve_source_flow():
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType
    from behave.sim.engines import local_ir, probing

    proc = _sealed_loop_proc(signature=ProcedureSignature(
        (("a", FiniteType(2)),), (("y", FiniteType(2)),)))
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    wanted = next(goal for goal in ir.goals_of(proc.body)
                  if goal.startswith("location:behavior.py:9:"))
    original = plan.procedure_identity(proc)
    owner = local_ir._LocalQueryPreparation(proc, env)
    selected = owner.for_query((wanted,))
    # Audited query projection may have a smaller body than the source owner.
    # Reuse belongs to the same target set, not to the unprojected procedure.
    assert selected is owner.for_query((wanted,))
    assert owner._proc is proc and selected._env == owner._env
    assert plan.procedure_identity(proc) == original
    assert selected.validation().graph is not None
    prepared = selected.plan(selected.validation())
    assert prepared.complete
    assert selected.plan(selected.validation()) is prepared
    encoded = encoder.encode_goal_query(proc, (wanted,), env=env)
    for value in range(4):
        native = wanted in interp.run_step(proc, {"a": value})[2]
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value, encoded.hits[wanted])
        assert solver.check() == (z3.sat if native else z3.unsat)
        answer = owner.probe({}, (wanted,), fixed_inputs={"a": value}, timeout_ms=60_000)
        assert answer.outcome is (probing.Outcome.SAT if native
                                  else probing.Outcome.LOCAL_UNSAT), answer.detail


def test_unselected_source_observations_do_not_consume_query_expansion(monkeypatch):
    from behave.sim.behavior.instance import BehaviorInstance

    source = ("class M:\n    def reset(self):\n        pass\n"
              "    def process(self, a):\n        for i in range(8):\n"
              + "            pass\n" * 20 + "        return {'y': a}\n")
    proc = lower_with_state_ranges(source, input_widths=[2])
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    loop = _one_sealed_loop(proc)
    wanted = next(c.goal for c in ir.walk(loop.body) if isinstance(c, ir.Mark))
    keep = slicing.for_run(proc, (wanted,), steps=1)
    assert not slicing.audit(proc, (wanted,), keep)
    projected = encoder._query_procedure(proc, keep, encoder._procedure_validation(proc))
    full = plan.plan_encoding(proc, env)
    small = plan.plan_encoding(projected, env)
    assert full.complete and small.complete
    assert small.expansion_cost < full.expansion_cost
    # Exercise the same resource gate at a small size, without changing its
    # production value or omitting any computation or failure obligation.
    budget = (small.expansion_cost + full.expansion_cost) // 2
    monkeypatch.setattr(plan, "TERM_BUDGET", budget)
    monkeypatch.setattr(encoder, "TERM_BUDGET", budget)
    assert not plan.plan_encoding(proc, env).complete
    encoded = encoder.encode_goal_query(proc, (wanted,), env=env)
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.hits[wanted])
    assert solver.check() == z3.sat
    # The formal entry must share the finite layer plan even when observing
    # every source location would exceed the construction allowance.
    exact = BehaviorInstance(proc, env, bound=100).bind(100)
    assert exact.instance._position_plan.plan.complete
    assert not exact.instance._position_plan.plan.steps[0].complete
    result = exact.instance.query((wanted,), steps=100, timeout_ms=60_000)
    assert wanted in result.reachable, result
    trace = result.reachable[wanted]
    assert 0 < len(trace) <= 100
    assert any(wanted in interp.run_step(proc, row)[2] for row in trace)


def test_flow_metadata_is_part_of_the_procedure_identity():
    proc = _sealed_loop_proc()

    def without_flow(command):
        if isinstance(command, ir.Seq):
            return ir.Seq(without_flow(command.first),
                          without_flow(command.second))
        if isinstance(command, ir.If):
            return ir.If(command.guard, without_flow(command.then),
                         without_flow(command.orelse))
        if isinstance(command, ir.For):
            return ir.For(command.index, command.trip_count,
                          without_flow(command.body), flow=None)
        return command

    unproved = proc.derived(body=without_flow(proc.body), graph=None)
    assert ir.command_fingerprint(unproved.body) == \
        ir.command_fingerprint(proc.body)
    assert plan.procedure_identity(unproved) != plan.procedure_identity(proc)


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_source_while_lowers_to_one_semantic_loop_without_unwound(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        n = a
        total = 0
        while n > 0:
            total += n
            n -= 1
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], lowerer=lowerer)
    loops = [command for command in ir.walk(proc.body)
             if isinstance(command, ir.For)]
    assert len(loops) == 1
    assert not any(
        isinstance(command, ir.Fail) and command.code == "Unwound"
        for command in ir.walk(proc.body))
    while_goals = {
        goal for goal in ir.goals_of(proc.body)
        if goal.startswith("branch:behavior.py:8:14:")}
    assert len(while_goals) == 2

    env = {**proc.state_ranges, "a": ranges.Range(0, 15)}
    prepared = plan.plan_encoding(proc, env)
    assert prepared.complete, prepared.unresolved
    assert tuple(prepared.loop_bounds.values()) == (15,)
    encoded = encoder.encode_step(proc, env=env, plan=prepared)
    for value in (0, 1, 4, 15):
        _state, output, hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": value * (value + 1) // 2}]
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value)
        assert solver.check() == z3.sat
        model = solver.model()
        assert model.eval(encoded.post["y"][0]).as_long() == \
            value * (value + 1) // 2
        assert len(while_goals & hits) == (1 if value == 0 else 2)


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_retained_for_body_does_not_prune_later_iteration_paths(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        flag = 0
        total = 0
        for i in range(4):
            if flag == 0:
                flag = 1
            if i < 3:
                total += 1
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[1], lowerer=lowerer)
    _state, output, hits, error = interp.run_step(proc, {"a": 0})
    assert not error
    assert output.transactions == [{"y": 3}]
    final_branch = {
        goal for goal in ir.goals_of(proc.body)
        if goal.startswith("branch:behavior.py:11:15:")}
    assert len(final_branch) == 2
    assert {goal[-1] for goal in final_branch & hits} == {"T", "F"}


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
@pytest.mark.parametrize("builder", (
    "for i in range(0, 48, 4):\n            values.append(a + i)",
    "for i in range(12):\n            if a & 1:\n                values.append(a + i)",
    "for i in range(12):\n            values.append(a)\n            values.append(i)",
    "for i in range(4):\n            for j in range(3):\n                values.append(a + j)",
))
def test_summarized_append_occurrences_cover_every_loop_visit(lowerer, builder):
    source = f"""class M:
    def reset(self):
        pass

    def process(self, a):
        values = []
        {builder}
        selected = [v for v in values if v != 0]
        total = 0
        for i in range(len(selected)):
            total += selected[i]
        return {{"y": total}}
"""
    namespace = {}
    exec(source, namespace)
    model = namespace["M"]()
    proc = lower_with_state_ranges(source, input_widths=[4], lowerer=lowerer)
    env = {**proc.state_ranges, "a": ranges.Range(0, 15)}
    prepared = plan.plan_encoding(proc, env)
    assert prepared.complete
    encoded = encoder.encode_step(proc, env=env, plan=prepared)
    for value in (0, 1, 2, 15):
        expected = model.process(value)["y"]
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": expected}]
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value)
        assert solver.check() == z3.sat
        solver.add(z3.Or(z3.Not(encoded.ok),
                         encoded.post["y"][0] != expected))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_monotone_while_strides_and_control_exits_are_exact(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        i = -5
        total = 0
        while i <= a:
            i += 3
            if i < 1:
                continue
            if i > 10:
                break
            total += i
        j = a
        while j >= 0:
            total += j
            j -= 2
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], lowerer=lowerer)
    assert len([command for command in ir.walk(proc.body)
                if isinstance(command, ir.For)]) == 2

    def native(a):
        i, total = -5, 0
        while i <= a:
            i += 3
            if i < 1:
                continue
            if i > 10:
                break
            total += i
        j = a
        while j >= 0:
            total += j
            j -= 2
        return total

    for value in range(16):
        _state, output, hits, error = interp.run_step(proc, {"a": value})
        assert not error
        got = output.transactions
        assert got == [{"y": native(value)}], value
        if value == 15:
            first_site = {
                goal for goal in ir.goals_of(proc.body)
                if ":8:14:" in goal
            }
            assert any(goal.endswith(":T") for goal in first_site & hits)
            assert not any(goal.endswith(":F") for goal in first_site & hits), \
                "a break does not evaluate the while condition once more"


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_break_path_need_not_advance_the_while_induction(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        total = 0
        while i < a:
            if i == 3:
                break
            total += i
            i += 1
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], lowerer=lowerer)
    for value in range(16):
        _state, output, hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": sum(range(min(value, 3)))}]
        if value > 3:
            while_site = {
                goal for goal in ir.goals_of(proc.body)
                if ":8:14:" in goal
            }
            assert any(goal.endswith(":T") for goal in while_site & hits)
            assert not any(goal.endswith(":F") for goal in while_site & hits)


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_while_in_a_helper_keeps_lexical_state_and_early_return(lowerer):
    source = """class M:
    def reset(self):
        pass

    def find(self, a):
        i = 0
        while i < 8:
            if i == a:
                return i + 1
            i += 1
        return 0

    def process(self, a):
        return {"y": self.find(a)}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], lowerer=lowerer)
    assert len([command for command in ir.walk(proc.body)
                if isinstance(command, ir.For)]) == 1
    for value in (0, 4, 7, 12):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [
            {"y": value + 1 if value < 8 else 0}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_failure_inside_a_while_is_a_source_failure_not_an_unwind(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        total = 0
        while i < a:
            total += 8 // (a - i - 1)
            i += 1
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], lowerer=lowerer)
    _state, _output, hits, error = interp.run_step(proc, {"a": 3})
    assert error == "DivZero"
    assert not any(
        isinstance(command, ir.Fail) and command.code == "Unwound"
        for command in ir.walk(proc.body))
    while_site = {
        goal for goal in ir.goals_of(proc.body) if ":8:14:" in goal}
    assert any(goal.endswith(":T") for goal in while_site & hits)
    assert not any(goal.endswith(":F") for goal in while_site & hits)


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_while_else_runs_only_after_condition_exhaustion(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        total = 0
        while i < a:
            i += 1
            if i == 2:
                break
            if i & 1:
                continue
            total += 10
        else:
            total += 100
        return {"y": total + i}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], lowerer=lowerer)
    expected = [100, 101, 2, 2, 2, 2, 2, 2]
    for value, answer in enumerate(expected):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
    assert output.transactions == [{"y": answer}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_nonnegative_right_shift_whiles_have_exact_geometric_trip_counts(
        lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        bit = 64
        first = 0
        while bit > a:
            bit >>= 2
            first += 1
        second = 0
        while bit != 0:
            bit >>= 2
            second += 1
        return {"y": first * 10 + second}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    proc = lower_with_state_ranges(
        source, input_widths=[7], lowerer=lowerer)
    loops = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    assert len(loops) == 2
    for value in (0, 1, 3, 4, 63, 64, 127):
        expected = native.process(value)
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [expected]


def test_a_right_shift_while_without_a_nonnegative_exit_domain_is_refused():
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        bit = 4
        while bit > -1:
            bit >>= 1
        return {"y": a}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1])
    assert caught.value.code == "while-trip-range-unproved"


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_helper_and_sequence_ranges_prove_a_right_shift_while(lowerer):
    source = """def to_signed(value, width):
    value &= (1 << width) - 1
    if value & (1 << (width - 1)):
        value -= 1 << width
    return value

def trunc_zero(value, divisor):
    magnitude = value if value >= 0 else -value
    quotient = magnitude // divisor
    return quotient if value >= 0 else -quotient

def scale(value):
    bit = 64
    while bit > value:
        bit >>= 2
    return bit

class M:
    def reset(self):
        pass

    def process(self, a):
        cells = [to_signed((a >> (4 * i)) & 15, 4) for i in range(2)]
        energy = cells[0] * cells[0] + cells[1] * cells[1]
        variance = trunc_zero(energy, 2)
        return {"y": scale(variance + 1)}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    proc = lower_with_state_ranges(source, lowerer=lowerer)
    for value in (0x00, 0x01, 0x18, 0x77, 0x8F, 0xFF):
        expected = native.process(value)
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_affine_conditions_and_nested_loops_preserve_while_progress(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        limit = a if a < 12 else 12
        matched = 0
        total = 0
        while matched + 4 <= limit:
            for j in range(2):
                total += matched + j
            matched += 4
        while matched < limit:
            matched += 1
        step = 16
        while step > 0:
            for j in range(2):
                total += step + j
            step >>= 1
        return {"y": total + matched}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    proc = lower_with_state_ranges(
        source, input_widths=[4], lowerer=lowerer)
    for value in range(16):
        expected = native.process(value)
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_clamped_unknown_input_gives_a_finite_affine_trip_count(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, limit):
        stop = limit if limit < 32 else 32
        i = 0
        while i + 4 <= stop:
            i += 4
        return {"y": i}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    proc = lower_with_state_ranges(source, lowerer=lowerer)
    for value in (-10, -1, 0, 1, 7, 31, 32, 100):
        expected = native.process(value)
        _state, output, _hits, error = interp.run_step(
            proc, {"limit": value})
        assert not error
        assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_state_induction_and_unit_not_equal_while_are_exact(lowerer):
    source = """class M:
    def __init__(self):
        self.count = 0

    def reset(self):
        self.count = 0

    def process(self, a):
        self.count = a & 7
        while self.count != 8:
            self.count += 1
        return {"y": self.count}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(
        source, input_widths=[4], lowerer=lowerer)
    for value in range(16):
        native = namespace["M"]()
        native.reset()
        expected = native.process(value)
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [expected]


def test_a_not_equal_while_that_can_step_past_its_bound_is_refused():
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        while i != 3:
            i += 2
        return {"y": i + (a & 0)}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1])
    assert caught.value.code == "while-trip-range-unproved"


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_while_else_and_early_return_keep_distinct_exits(lowerer):
    source = """class M:
    def reset(self):
        pass

    def choose(self, a):
        i = 0
        while i < a:
            if i == 1:
                return 7
            i += 1
        else:
            return 9
        return 11

    def process(self, a):
        return {"y": self.choose(a)}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], lowerer=lowerer)
    for value, answer in enumerate((9, 9, 7, 7, 7, 7, 7, 7)):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": answer}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
@pytest.mark.parametrize("style", ("for", "exact-while", "ranked-while"))
@pytest.mark.parametrize("can_break", (False, True))
def test_loop_else_joins_only_exhaustion_and_normal_helper_exits(
        lowerer, style, can_break):
    loop = "for unused in range(a):" if style == "for" else "while i < a:"
    advance = ("if i & 1:\n                i += 2\n            else:\n                i += 1"
               if style == "ranked-while" else "i += 1")
    stop = "if mode == 1 and i >= 3:\n                break" if can_break else "pass"
    source = f"""class M:
    def reset(self):
        self.completed = 0

    def choose(self, a, mode):
        i = 0
        total = 0
        {loop}
            {advance}
            {stop}
            if mode == 2 and i >= 3:
                return total + 7
            if mode == 3 and i & 1:
                continue
            total += i
        else:
            self.completed = (self.completed + 1) & 15
            if a & 1:
                return total + 100
            total += 200
        return total

    def process(self, a, mode):
        result = self.choose(a, mode)
        return {{"y": result + 256 * self.completed}}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    proc = lower_with_state_ranges(source, input_widths=[3, 2], lowerer=lowerer)
    state = None
    # Includes empty loops, exhaustion, break/continue, body/else returns,
    # and later transactions observing the persistent update made by else.
    for mode in range(4):
        for a in range(8):
            expected = native.process(a, mode)
            state, output, _hits, error = interp.run_step(
                proc, {"a": a, "mode": mode}, state=state)
            assert not error
            assert output.transactions == [expected]
            assert state["completed"] == native.completed


@pytest.mark.parametrize("body", (
    "if a:\n                continue\n            i += 1",
    "a -= 1\n            i += 1",
    "i -= 1",
))
def test_unproved_while_progress_is_a_typed_refusal(body):
    source = f"""class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        while i < a:
            {body}
        return {{"y": i}}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4])
    assert caught.value.code == "while-trip-count-unproved"


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_data_dependent_positive_stride_uses_a_finite_ranking(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a, choose):
        i = 0
        total = 0
        while i < a:
            total += i
            if choose and i + 1 < a:
                i += 2
            else:
                i += 1
        else:
            total += 100
        return {"y": total + i}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(
        source, input_widths=[3, 1], lowerer=lowerer)
    loops = [command for command in ir.walk(proc.body)
             if isinstance(command, ir.For)]
    assert len(loops) == 1
    assert isinstance(loops[0].trip_count, ir.Lit)
    assert loops[0].trip_count.value == 8
    for value in range(8):
        for choose in range(2):
            native = namespace["M"]()
            native.reset()
            expected = native.process(value, choose)
            _state, output, _hits, error = interp.run_step(
                proc, {"a": value, "choose": choose})
            assert not error
            assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_two_counter_merge_uses_one_sum_ranking(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a, b, choose):
        i = 0
        j = 0
        total = 0
        while i < a and j < b:
            total += i * 10 + j
            if i + j == 3:
                break
            if choose:
                i += 1
                continue
            if (i + j) & 1:
                i += 1
            else:
                j += 1
        else:
            total += 100
        return {"y": total + i * 10 + j}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(
        source, input_widths=[3, 3, 1], lowerer=lowerer)
    assert len([command for command in ir.walk(proc.body)
                if isinstance(command, ir.For)]) == 1
    for a in range(8):
        for b in range(8):
            for choose in range(2):
                native = namespace["M"]()
                native.reset()
                expected = native.process(a, b, choose)
                _state, output, _hits, error = interp.run_step(
                    proc, {"a": a, "b": b, "choose": choose})
                assert not error
                assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_ranked_loop_post_ranges_feed_following_drain_loops(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, a, b, choose):
        i = 0
        j = 0
        while i < a and j < b:
            if choose:
                i += 1
            else:
                j += 1
        while i < a:
            i += 1
        while j < b:
            j += 1
        return {"y": i * 10 + j}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3, 3, 1], lowerer=lowerer)
    for a in range(8):
        for b in range(8):
            for choose in range(2):
                _state, output, _hits, error = interp.run_step(
                    proc, {"a": a, "b": b, "choose": choose})
                assert not error
                assert output.transactions == [{"y": a * 10 + b}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
@pytest.mark.parametrize("ranked", (False, True))
def test_later_iteration_scalar_branches_are_not_pruned(lowerer, ranked):
    update = ("if i & 1:\n"
              "                i += 2\n"
              "            else:\n"
              "                i += 1"
              if ranked else "i += 1")
    source = f"""class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        total = 0
        seen = 0
        while i < a:
            {update}
            total += 1
            if total == 2:
                seen = 1
        return {{"y": seen}}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], lowerer=lowerer)
    for value in range(8):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": int(value >= 2)}]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_constant_true_heap_walk_uses_a_cutoff_ranking(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, limit, choose):
        i = 0
        total = 0
        while True:
            left = 2 * i + 1
            right = left + 1
            if left >= limit:
                break
            if choose and right < limit:
                child = right
            else:
                child = left
            total += i + 1
            i = child
        else:
            total += 1000
        return {"y": total * 10 + i}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(
        source, input_widths=[3, 1], lowerer=lowerer)
    loops = [command for command in ir.walk(proc.body)
             if isinstance(command, ir.For)]
    assert len(loops) == 1
    for limit in range(8):
        for choose in range(2):
            native = namespace["M"]()
            native.reset()
            expected = native.process(limit, choose)
            _state, output, _hits, error = interp.run_step(
                proc, {"limit": limit, "choose": choose})
            assert not error
            assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
@pytest.mark.parametrize("width", [6, 32])
def test_branching_affine_guard_has_logarithmic_expansion_and_native_outputs(lowerer, width):
    source = """class M:
    def reset(self):
        pass

    def process(self, limit, choose):
        index = 0
        total = 0
        while 2 * index + 1 < limit:
            child = 2 * index + 1
            if child + 1 < limit and choose:
                child += 1
            total += index + 1
            index = child
        else:
            total += 1000
        return {"y": total * 100 + index}
"""
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(source, input_widths=[width, 1], lowerer=lowerer)
    loops = [command for command in ir.walk(proc.body) if isinstance(command, ir.For)]
    assert len(loops) == 1
    assert ranges.loop_trip_range(loops[0], proc.state_ranges).hi <= width + 1
    for limit in range(64):
        for choose in range(2):
            expected = namespace["M"]().process(limit, choose)
            _state, output, _hits, error = interp.run_step(
                proc, {"limit": limit, "choose": choose})
            assert not error
            assert output.transactions == [expected]


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_a_cutoff_ranked_loop_may_terminate_by_return(lowerer):
    source = """class M:
    def reset(self):
        pass

    def process(self, limit):
        i = 0
        while True:
            if i >= limit:
                return {"y": i}
            i += 1
"""
    proc = lower_with_state_ranges(
        source, input_widths=[3], lowerer=lowerer)
    for limit in range(8):
        _state, output, _hits, error = interp.run_step(
            proc, {"limit": limit})
        assert not error
        assert output.transactions == [{"y": limit}]


@pytest.mark.parametrize("compound", ("try", "with", "match"))
def test_hidden_continue_cannot_supply_an_invented_while_recurrence(compound):
    bodies = {
        "try": """try:
                continue
            finally:
                pass""",
        "with": """with manager:
                continue""",
        "match": """match a:
                case _:
                    continue""",
    }
    source = f"""class M:
    def reset(self):
        pass

    def process(self, a, manager):
        i = 0
        while i < a:
            {bodies[compound]}
            i += 1
        return {{"y": i}}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[3, 1])
    assert caught.value.code == "while-trip-count-unproved"


@pytest.mark.parametrize("body", (
    "i += 1",
    "if a:\n                i += 1\n            else:\n                break",
    "if i >= a:\n                break\n            i = 0",
))
def test_constant_true_loops_need_progress_and_a_cutoff_on_every_backedge(body):
    source = f"""class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        while True:
            {body}
        return {{"y": i}}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[3])
    assert caught.value.code == "while-trip-count-unproved"


def test_a_finite_while_above_the_old_unwind_limit_remains_a_model():
    source = """class M:
    def reset(self):
        pass

    def process(self, a):
        i = 0
        while i < a:
            i += 1
        return {"y": i}
"""
    proc = lower_with_state_ranges(source, input_widths=[8])
    _state, output, _hits, error = interp.run_step(proc, {"a": 255})
    assert not error
    assert output.transactions == [{"y": 255}]

    env = {**proc.state_ranges, "a": ranges.Range(0, 255)}
    prepared = plan.plan_encoding(proc, env)
    assert prepared.complete, prepared.unresolved
    assert tuple(prepared.loop_bounds.values()) == (255,)


def test_a_while_bound_can_be_a_proved_invariant_state_value():
    source = """class M:
    def __init__(self):
        self.limit = 5

    def reset(self):
        pass

    def process(self, a):
        i = 0
        while i < self.limit:
            i += 1
        return {"y": i + (a & 0)}
"""
    proc = lower_with_state_ranges(source, input_widths=[4])
    _state, output, _hits, error = interp.run_step(proc, {"a": 7})
    assert not error
    assert output.transactions == [{"y": 5}]

    changed = source.replace(
        "            i += 1\n",
        "            self.limit -= 1\n            i += 1\n",
    )
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(changed, input_widths=[4])
    assert caught.value.code == "while-trip-count-unproved"


def test_a_called_helper_cannot_silently_change_a_while_bound():
    source = """class M:
    def __init__(self):
        self.limit = 3

    def reset(self):
        pass

    def shrink(self):
        self.limit -= 1

    def process(self, a):
        i = 0
        while i < self.limit:
            i += 1
            self.shrink()
        return {"y": i + (a & 0)}
"""
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1])
    assert caught.value.code == "while-trip-count-unproved"
    assert "called helper" in str(caught.value)


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_while_effects_respect_nested_lexical_scopes(lowerer):
    source = """class M:
    def __init__(self):
        self.limit = 5

    def reset(self):
        pass

    def shrink(self):
        self.limit -= 1

    def process(self, a):
        i = 0
        total = 0
        while i < self.limit:
            def dormant():
                i = 100
                self.shrink()
                return i
            values = [i for i in range(2)]
            total += values[1]
            i += 1
        return {"y": i * 10 + total + (a & 0)}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[1], lowerer=lowerer)
    _state, output, _hits, error = interp.run_step(proc, {"a": 0})
    assert not error
    assert output.transactions == [{"y": 55}]
