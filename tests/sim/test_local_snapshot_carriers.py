"""A fixed local state does not require a whole-run state invariant."""

import pytest

from behave.sim.symbolic import cfg, interp, ir, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir
from behave.sim.engines.probing import Outcome

pytestmark = pytest.mark.solver


def _problem(*, array=False, loop=False):
    value = ir.Sel(ir.Var("s"), ir.Lit(0)) if array else ir.Var("s")
    body = ir.If(ir.Op("eq", value, ir.Var("a")),
                 ir.Mark("equal"), ir.Mark("different"))
    if loop:
        body = ir.For("i", 2, body)
    proc = ir.Procedure(
        ("s",), ("a",), ("i",) if loop else (), (), body,
        initial={"s": [0, 0] if array else 0},
        types={"s": types.Array(2, types.INTEGER) if array else types.INTEGER},
        signature=ProcedureSignature((("a", FiniteType(3)),), ()))
    return proc, {"s": None, "a": ranges.Range(0, 7)}


@pytest.mark.parametrize("array", [False, True])
@pytest.mark.parametrize("loop", [False, True])
def test_queries_match_exhaustive_replay_from_each_actual_snapshot(array, loop):
    proc, env = _problem(array=array, loop=loop)
    prepared = local_ir._LocalQueryPreparation(proc, env)
    for value in (0, 7, 8, -3, 1 << 130, 0):
        state = {"s": [value, -value] if array else value}
        for goal in ("equal", "different"):
            for a in range(8):
                expected = interp.run_step(proc, {"a": a}, state)[2]
                answer = prepared.probe(state, (goal,), fixed_inputs={"a": a},
                    timeout_ms=60_000)
                assert answer.outcome is (
                    Outcome.SAT if goal in expected else Outcome.LOCAL_UNSAT
                ), answer.detail
                if answer.candidate is not None:
                    assert answer.goal in interp.run_step(proc, answer.candidate, state)[2]
    assert env["s"] is None and prepared._env["s"] is None


def test_local_queries_use_persistent_arrays_and_match_native_updates(monkeypatch):
    from behave.sim.symbolic import encoder
    from behave.sim.symbolic.functional_array import FunctionalArray

    proc = ir.Procedure(('mem',), ('a', 'b'), (), (), ir._sequence((
        ir.Store('mem', ir.Var('a'), ir.Var('b')),
        ir.If(ir.Op('eq', ir.Sel(ir.Var('mem'), ir.Lit(1)), ir.Lit(3)),
              ir.Mark('hit'), ir.Mark('miss')))),
        initial={'mem': [0] * 4}, types={'mem': types.Array(4)},
        signature=ProcedureSignature((('a', FiniteType(2)), ('b', FiniteType(2))), ()))
    env = {'mem': ranges.Range(0, 3), 'a': ranges.Range(0, 3), 'b': ranges.Range(0, 3)}
    owner = local_ir._LocalQueryPreparation(proc, env)
    assert owner.validation().compact_arrays
    original, stores = FunctionalArray.store, []

    def observed_store(self, *args, **kwargs):
        stores.append(self.length)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FunctionalArray, 'store', observed_store)
    for a in range(4):
        for b in range(4):
            expected = interp.run_step(proc, {'a': a, 'b': b})[2]
            for goal in ('hit', 'miss'):
                answer = owner.probe(proc.initial, (goal,), fixed_inputs={'a': a, 'b': b},
                                     timeout_ms=20_000)
                assert answer.outcome is (Outcome.SAT if goal in expected else Outcome.LOCAL_UNSAT)
    assert stores and set(stores) == {4}
    # Public dense encoding remains independently available; no global limit changed.
    assert not encoder._procedure_validation(proc).compact_arrays


@pytest.mark.parametrize("snapshot", [{}, {"s": "not an integer"}, {"s": [0]}])
def test_missing_or_malformed_state_is_not_left_symbolic(snapshot):
    proc, env = _problem()
    answer = local_ir.local_hit_any(proc, env, snapshot, ("equal",),
        timeout_ms=60_000)
    assert answer.outcome is Outcome.UNSUPPORTED


def test_known_state_carrier_and_declared_input_domain_still_checked():
    proc, env = _problem()
    answer = local_ir.local_hit_any(
        proc, {**env, "s": ranges.Range(0, 3)}, {"s": 9}, ("equal",),
        timeout_ms=60_000)
    assert answer.outcome is Outcome.UNSUPPORTED
    answer = local_ir.local_hit_any(
        proc, {**env, "a": ranges.Range(0, 3)}, {"s": 0}, ("equal",),
        timeout_ms=60_000)
    assert answer.outcome is Outcome.UNSUPPORTED


def test_snapshot_range_is_not_an_invariant_after_state_updates():
    proc, env = _problem(loop=True)
    proc.body = ir.Seq(ir.Assign("s", ir.Op("add", ir.Var("s"), ir.Lit(1))), proc.body)
    for value in (-1, 0, 6, 7):
        answer = local_ir.local_hit_any(proc, env, {"s": value}, ("equal",),
            timeout_ms=60_000)
        assert answer.outcome is (Outcome.SAT if 0 <= value + 1 <= 7 else Outcome.LOCAL_UNSAT)
        if answer.candidate is not None:
            assert answer.candidate == {"a": value + 1}


@pytest.mark.parametrize("initial", [False, True])
def test_boolean_snapshot_keeps_its_semantic_type(initial):
    proc = ir.Procedure(("flag",), ("a",), (), (),
        ir.If(ir.Var("flag"), ir.Mark("set"), ir.Mark("clear")),
        initial={"flag": False}, types={"flag": types.BOOLEAN},
        signature=ProcedureSignature((("a", FiniteType(1)),), ()))
    env = {"flag": None, "a": ranges.Range(0, 1)}
    for goal in ("set", "clear"):
        answer = local_ir.local_hit_any(proc, env, {"flag": initial}, (goal,),
            timeout_ms=60_000)
        assert answer.outcome is (
            Outcome.SAT if (goal == "set") == initial else Outcome.LOCAL_UNSAT)
    malformed = local_ir.local_hit_any(proc, env, {"flag": int(initial)}, ("set",),
        timeout_ms=60_000)
    assert malformed.outcome is Outcome.UNSUPPORTED


def test_only_current_snapshot_preparation_is_retained():
    proc, env = _problem(loop=True)
    prepared = local_ir._LocalQueryPreparation(proc, env)
    first = prepared.for_state({"s": 3})
    assert first is prepared.for_state({"s": 3})
    second = prepared.for_state({"s": 7})
    assert second is not first
    assert prepared._snapshot_preparation is second
    assert first._env["s"] == ranges.Range(3, 3)
    assert second._env["s"] == ranges.Range(7, 7)
    assert first._snapshot_preparation is None


def _source_loop():
    graph = cfg.CFG({
        "entry": cfg.Block("entry", [
            ir.For("i", ir.Var("s"), ir.If(
                ir.Op("eq", ir.Var("a"), ir.Var("i")), ir.Mark("loop"), ir.Skip())),
            ir.Assign("s", ir.Op("add", ir.Var("s"), ir.Lit(1))),
        ], cfg.Branch(ir.Op("eq", ir.Var("a"), ir.Var("s")), "grown", "exit")),
        "grown": cfg.Block("grown", [ir.Mark("grown")], cfg.Goto("exit")),
        "exit": cfg.Block("exit", [], cfg.Return()),
    }, "entry")
    body = cfg.if_convert(graph)
    proc = ir.Procedure(("s",), ("a",), ("i",), (), body, initial={"s": 0},
                        signature=ProcedureSignature((("a", FiniteType(3)),), ()))
    ir._bind_control_flow(proc, ir._make_control_flow_authority(graph, body))
    return proc, {"s": ranges.Range(0, 1000), "a": ranges.Range(0, 7)}


def test_cfg_entry_planning_uses_pinned_state_but_all_legal_inputs(monkeypatch):
    proc, env = _source_loop()
    owner = local_ir._LocalQueryPreparation(proc, env)
    original, cones = local_ir._goal_occurrence_keep, []

    def count_cones(*args, **kwargs):
        cones.append(args[1:3])
        return original(*args, **kwargs)

    monkeypatch.setattr(local_ir, "_goal_occurrence_keep", count_cones)
    plans = []
    for value in (0, 2, 5, 0):
        state = {"s": value}
        current = owner.for_state(state)
        assert current._env["s"] == ranges.Range(value, value)
        assert current._env["a"] == env["a"]
        assert current._structure is owner._structure
        for a in range(8):
            hits = interp.run_step(proc, {"a": a}, state)[2]
            for goal in ("loop", "grown"):
                answer = owner.probe(state, (goal,), fixed_inputs={"a": a},
                    timeout_ms=60_000)
                assert answer.outcome is (Outcome.SAT if goal in hits else Outcome.LOCAL_UNSAT), answer.detail
        plan = current.plan(current.validation())
        assert not plan.unresolved
        assert max(plan.loop_bounds.values()) == value
        plans.append(plan)
    assert len(cones) == len(set(cones))
    assert plans[0] is not plans[-1], "only the latest snapshot plan is retained"
    assert env["s"] == owner._env["s"] == ranges.Range(0, 1000)


@pytest.mark.parametrize("state", [{}, {"s": -1}, {"s": 1001}, {"s": True}, {"s": [1]}])
def test_cfg_specialization_does_not_bypass_original_state_admission(state):
    proc, env = _source_loop()
    owner = local_ir._LocalQueryPreparation(proc, env)
    answer = owner.probe(state, ("grown",),
        timeout_ms=60_000)
    assert answer.outcome is Outcome.UNSUPPORTED
    assert owner._snapshot_preparation is None
    assert owner.probe({"s": 0}, ("grown",),
        timeout_ms=60_000).outcome is Outcome.SAT


def test_cfg_array_snapshot_plans_do_not_reuse_other_states_or_post_bounds():
    graph = cfg.CFG({
        "entry": cfg.Block("entry", [
            ir.Store("buf", ir.Lit(0), ir.Op("add", ir.Sel(ir.Var("buf"), ir.Lit(0)), ir.Lit(1))),
        ], cfg.Branch(ir.Op("eq", ir.Var("a"), ir.Sel(ir.Var("buf"), ir.Lit(0))), "hit", "exit")),
        "hit": cfg.Block("hit", [ir.Mark("hit")], cfg.Goto("exit")),
        "exit": cfg.Block("exit", [], cfg.Return()),
    }, "entry")
    body = cfg.if_convert(graph)
    proc = ir.Procedure(("buf",), ("a",), (), (), body, initial={"buf": [0, 0]},
                        types={"buf": types.Array(2)},
                        signature=ProcedureSignature((("a", FiniteType(3)),), ()))
    ir._bind_control_flow(proc, ir._make_control_flow_authority(graph, body))
    owner = local_ir._LocalQueryPreparation(proc, {"buf": ranges.Range(-3, 7), "a": ranges.Range(0, 7)})
    for values in ([0, 0], [7, 7], [-3, -3], [6, 6], [0, 7], [7, 0]):
        for a in range(8):
            state = {"buf": list(values)}
            hit = "hit" in interp.run_step(proc, {"a": a}, state)[2]
            answer = owner.probe(state, ("hit",), fixed_inputs={"a": a},
                timeout_ms=60_000)
            assert answer.outcome is (Outcome.SAT if hit else Outcome.LOCAL_UNSAT), answer.detail
    for values in ([0], [0, True], [0, 8]):
        assert owner.probe({"buf": values}, ("hit",),
            timeout_ms=60_000).outcome is Outcome.UNSUPPORTED


@pytest.mark.parametrize('cells', [(2, 5), (-3, -1)])
def test_compact_snapshot_has_the_same_queries_as_its_actual_cells(cells):
    from behave.sim.symbolic.uniform_array import UniformArray

    proc, env = _problem(array=True, loop=True)
    owner = local_ir._LocalQueryPreparation(proc, env)
    compact = UniformArray.uniform(2, 0).store(0, cells[0]).store(1, cells[1])
    for a in range(8):
        for goal in ('equal', 'different'):
            dense = owner.probe({'s': list(cells)}, (goal,), fixed_inputs={'a': a},
                timeout_ms=60_000)
            sparse = owner.probe({'s': compact}, (goal,), fixed_inputs={'a': a},
                timeout_ms=60_000)
            assert sparse.outcome is dense.outcome, sparse.detail
            assert sparse.outcome is not Outcome.UNSUPPORTED
            if sparse.candidate is not None:
                assert sparse.goal in interp.run_step(proc, sparse.candidate, {'s': compact})[2]


def test_admitted_source_queries_do_not_rehash_graph_on_every_probe(monkeypatch):
    proc, env = _source_loop()
    owner = local_ir._LocalQueryPreparation(proc, env)
    state = {"s": 2}
    for goal in ("loop", "grown"):
        assert owner.probe(state, (goal,),
            timeout_ms=60_000).outcome is Outcome.SAT
    expected = {a: interp.run_step(proc, {"a": a}, state)[2] for a in range(8)}

    property_of_graph, reads = ir.Procedure.graph, []

    def read_graph(instance):
        reads.append(instance)
        return property_of_graph.fget(instance)

    monkeypatch.setattr(ir.Procedure, "graph", property(read_graph, property_of_graph.fset))
    for a in range(8):
        for goal in ("loop", "grown"):
            answer = owner.probe(state, (goal,), fixed_inputs={"a": a},
                timeout_ms=60_000)
            assert answer.outcome is (Outcome.SAT if goal in expected[a] else Outcome.LOCAL_UNSAT)
    assert not reads, "immutable admission belongs to the worker, not each query"

    # A new public invocation still checks the caller-owned graph/body. The
    # private owner's prior validation cannot admit a later modified source.
    assert local_ir.local_hit(proc, env, state, "grown",
        timeout_ms=60_000).outcome is Outcome.SAT
    assert reads
    proc._graph.blocks["grown"].instrs.clear()
    answer = local_ir.local_hit(proc, env, state, "grown",
        timeout_ms=60_000)
    assert answer.outcome is Outcome.UNSUPPORTED
    assert "control-flow-authority-changed" in answer.detail
