"""Plan exactly the audited query, not unrelated loop expansion."""

from behave.sim.symbolic import closure_proof

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, plan, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines import local_ir, probing

pytestmark = pytest.mark.solver


@pytest.fixture(autouse=True)
def small_expansion_budget(monkeypatch):
    # Exercise the same production resource boundary without spending a
    # million visits on every regression. Both plan and encoder must agree.
    monkeypatch.setattr(plan, "TERM_BUDGET", 200)
    monkeypatch.setattr(encoder, "TERM_BUDGET", 200)


def _problem(*, relevant=False, failure=False, steps=16):
    loop = ir.For("i", steps, ir.For("j", steps,
        ir.Assign("value", ir.Op("add", ir.Var("value"), ir.Lit(1)))))
    guard = (ir.Op("eq", ir.Var("value"), ir.Lit(steps * steps)) if relevant
             else ir.Op("eq", ir.Var("a"), ir.Lit(3)))
    commands = [ir.Assign("value", ir.Lit(0))]
    if failure:
        commands.append(ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(3)),
                              ir.Fail("Stop", "failure"), ir.Skip()))
    commands += [loop, ir.If(guard, ir.Mark("target"), ir.Skip()),
                 ir.Assign("output", ir.Var("value"))]
    return ir.Procedure((), ("a",), ("i", "j", "value", "output"), (),
        ir._sequence(commands), initial={},
        types={name: types.INTEGER for name in ("a", "i", "j", "value", "output")},
        signature=ProcedureSignature((("a", FiniteType(2)),), ())), {
            "a": ranges.Range(0, 3)}


def test_unrelated_nested_expansion_does_not_block_goal_planning():
    proc, env = _problem()
    original = proc.body
    prepared = plan.plan_encoding(proc, env)
    assert any(item.what == "encoding-expansion" for item in prepared.unresolved)
    encoded = encoder.encode_goal_query(proc, ("target",), env=env)
    for value in range(4):
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value, encoded.hits["target"])
        assert solver.check() == (z3.sat if value == 3 else z3.unsat)
    assert proc.body is original
    assert ir.goals_of(proc.body) == {"target"}
    assert not encoded.output_checks
    # The dropped computation is not a published return value.
    assert "output" not in encoded.post
    with pytest.raises(encoder.EncodingError, match="encoding-expansion"):
        encoder.encode_step(proc, env=env, plan=prepared)


def test_relevant_nested_expansion_is_not_removed():
    proc, env = _problem(relevant=True)
    with pytest.raises(encoder.EncodingError, match="semantic command copies"):
        encoder.encode_goal_query(proc, ("target",), env=env)


def test_earlier_failure_is_retained_before_a_dropped_expansion():
    proc, env = _problem(failure=True)
    encoded = encoder.encode_goal_query(proc, ("target", "failure"), env=env)
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.hits["target"])
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.hits["failure"])
    assert solver.check() == z3.sat
    assert solver.model().eval(encoded.pre["a"]).as_long() == 3


@pytest.mark.parametrize("relevant,failure", [(False, False), (True, False), (False, True)])
def test_small_full_and_query_hits_agree_for_every_input(relevant, failure):
    proc, env = _problem(relevant=relevant, failure=failure, steps=3)
    goals = tuple(ir.goals_of(proc.body))
    full = encoder.encode_step(proc, env=env, tag="full")
    selected = encoder.encode_goal_query(proc, goals, env=env, tag="selected")
    solver = z3.Solver()
    solver.add(full.constraint, selected.constraint, full.pre["a"] == selected.pre["a"],
               z3.Or([full.hits[goal] != selected.hits[goal] for goal in goals]))
    assert solver.check() == z3.unsat
    for value in range(4):
        hits = interp.run_step(proc, {"a": value}, {})[2]
        solver = z3.Solver()
        solver.add(selected.constraint, selected.pre["a"] == value)
        assert solver.check() == z3.sat
        model = solver.model()
        assert {g for g in goals if z3.is_true(model.eval(selected.hits[g]))} == hits


def test_query_keeps_the_declared_input_domain():
    proc, env = _problem()
    with pytest.raises(encoder.EncodingError, match="finite procedure domain"):
        encoder.encode_goal_query(proc, ("target",), env={**env, "a": ranges.Range(0, 2)})


@pytest.mark.parametrize("failure", [False, True])
def test_local_search_does_not_plan_unrelated_expansion(failure):
    proc, env = _problem(failure=failure)
    owner = local_ir._LocalQueryPreparation(proc, env)
    for value in range(4):
        result = owner.probe({}, ("target",), fixed_inputs={"a": value},
            timeout_ms=60_000)
        expected = probing.Outcome.SAT if value == 3 and not failure else probing.Outcome.LOCAL_UNSAT
        assert result.outcome is expected, result.detail
        if result.candidate is not None:
            assert result.goal in interp.run_step(proc, result.candidate, {})[2]
    assert ir.goals_of(proc.body) == ({"target", "failure"} if failure else {"target"})


def test_local_search_keeps_relevant_expansion_and_original_input_contract():
    proc, env = _problem(relevant=True)
    result = local_ir.local_hit_any(proc, env, {}, ("target",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED
    assert "semantic command copies" in result.detail
    proc, env = _problem()
    result = local_ir.local_hit_any(proc, {**env, "a": ranges.Range(0, 2)}, {}, ("target",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED


def test_local_projection_retains_every_occurrence_and_complete_admission():
    proc, env = _problem()
    proc.body = ir.Seq(proc.body, ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)),
                                      ir.Mark("target"), ir.Skip()))
    for value in range(4):
        result = local_ir.local_hit_any(proc, env, {}, ("target",), fixed_inputs={"a": value},
            timeout_ms=60_000)
        assert result.outcome is (probing.Outcome.SAT if value in (1, 3)
                                  else probing.Outcome.LOCAL_UNSAT), result.detail
    # An invalid unused computation is still an admission error, not erased.
    proc.types["value"] = types.BOOLEAN
    result = local_ir.local_hit_any(proc, env, {}, ("target",),
        timeout_ms=60_000)
    assert result.outcome is probing.Outcome.UNSUPPORTED


def test_large_complete_carriers_can_serve_an_explicit_local_query():
    proc, env = _problem()
    prepared = plan.plan_encoding(proc, env)
    assert not prepared.complete
    assert prepared.representation_complete
    assert prepared.occurrence_carriers
    encoded = encoder.encode_goal_query(proc, ("target",), env=env, plan=prepared)
    for value in range(4):
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value, encoded.hits["target"])
        assert solver.check() == (z3.sat if value == 3 else z3.unsat)
    # A full evaluation still owes all its observations and work.
    with pytest.raises(encoder.EncodingError, match="encoding-expansion"):
        encoder.encode_step(proc, env=env, plan=prepared)


@pytest.mark.parametrize("bound", [4, 100])
def test_exact_bind_uses_finite_layers_before_materializing_goals(bound):
    from behave.sim.behavior.instance import BehaviorInstance

    proc, env = _problem()
    proc = proc.derived(state=("s",), initial={"s": 0},
        body=ir.Seq(proc.body, ir._sequence((
            ir.Assign("s", ir.Op("and", ir.Op("add", ir.Var("s"), ir.Var("a")), ir.Lit(3))),
            ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(3)), ir.Mark("state-target"), ir.Skip())))))
    env = {**env, "s": ranges.Range(0, 3)}
    exact = BehaviorInstance(proc, env, bound=bound).bind(bound)
    prepared = exact.instance._position_plan
    assert prepared.bound == bound and prepared.plan.complete
    assert all(step.representation_complete for step in prepared.plan.steps)
    assert any(not step.complete for step in prepared.plan.steps)
    assert all(layer["s"].lo >= 0 and layer["s"].hi <= 3
               for layer in prepared.plan.state_layers)
    answer = exact.instance.query(("state-target",), steps=bound, timeout_ms=60_000)
    assert "state-target" in answer.reachable, answer
    assert not answer.undecided and not answer.unreachable
    state, hits = dict(proc.initial), set()
    trace = answer.reachable["state-target"]
    assert 0 < len(trace) <= bound
    for row in trace:
        state, _outputs, observed, error = interp.run_step(proc, row, state)
        assert not error
        hits.update(observed)
    assert "state-target" in hits


def test_large_plan_keeps_finite_evidence_in_its_identity_and_validation():
    proc, env = _problem()
    prepared = plan.plan_encoding(proc, env)
    assert prepared.representation_complete and not prepared.complete
    loops = dict(prepared.loop_bounds)
    key = next(iter(loops))
    loops[key] += 1
    forged = prepared.replaced(loop_bounds=loops)
    assert forged.fingerprint != prepared.fingerprint
    with pytest.raises(plan.PlanMismatch):
        encoder.encode_goal_query(proc, ("target",), env=env, plan=forged)


def test_materialization_refusal_does_not_hide_missing_loop_domain():
    proc, env = _problem()
    proc = proc.derived(locals=(*proc.locals, "k"), body=ir.Seq(
        proc.body, ir.For("k", ir.Var("unknown"), ir.Mark("unproved"))))
    prepared = plan.plan_encoding(proc, env)
    assert not prepared.representation_complete
    with pytest.raises((encoder.EncodingError, ValueError)):
        closure_proof.prepare_positions(proc, env, 4)


@pytest.mark.parametrize("body", [
    """total = 0
        for i in range(3):
            dead = a * a + i
            if self.s == a:
                total += i
            self.s = (self.s + 1) & 3
        return {"y": total}""",
    """total = 0
        for i in range(a & 3):
            for j in range(2):
                if self.s == j:
                    total += a
                self.s = (self.s + 1) & 3
            i = a
        return {"y": total}""",
    """total = 0
        for i in range(3):
            if a == i:
                continue
            if a == i + 2:
                break
            total += self.cells[i]
            self.cells[(i + 1) & 3] = a
        return {"y": total}""",
    """hole = 3
        total = 0
        while hole > 0:
            if self.s == a:
                total += hole
            self.s = (self.s + hole) & 3
            hole = (hole - 1) // 2
        return {"y": total}""",
    """total = 0
        for i in range(3):
            if a == i:
                return {"y": self.s}
            total += self.cells[a]
            self.s = (self.s + 1) & 3
        return {"y": total}""",
])
def test_source_loop_query_projection_preserves_hits_and_failures(body):
    from behave.sim.symbolic import slicing, ssa
    from behave.sim.behavior.lowering import lower_procedure

    source = '''class Model:
    def reset(self):
        self.s = 0
        self.cells = [0, 1, 2, 3]
    def process(self, a):
        ''' + body + '\n'
    proc = lower_procedure(source, outputs=["y"], input_widths=(3,))
    validation = encoder._procedure_validation(proc)
    events = slicing.linearize(proc.body)
    goals = sorted(ir.goals_of(proc.body))
    selections = [frozenset(goals), *(frozenset((goal,)) for goal in goals)]
    original_loops = {command.index: command for command in ir.walk(proc.body)
                      if isinstance(command, ir.For)}
    reduced_loop = False
    expected = {(a, s): interp.run_step(proc, {"a": a}, {**proc.initial, "s": s})
                for a in range(8) for s in (0, 2)}
    for selected in selections:
        keep = slicing.for_run(proc, selected, steps=1, events=events)
        assert not slicing.audit(proc, selected, keep, events=events)
        projected = encoder._query_procedure(proc, keep, validation)
        encoder._procedure_validation(projected)
        assert not projected.boundary_complete
        assert projected.inputs == proc.inputs
        assert projected.initial == proc.initial
        for command in ir.walk(projected.body):
            if isinstance(command, ir.For):
                assert command.flow is not None
                ssa.loop_flow_facts(command)
                original_loop = original_loops[command.index]
                assert command.trip_count == original_loop.trip_count
                reduced_loop |= sum(isinstance(c, (ir.Assign, ir.Store))
                                    for c in ir.walk(command.body)) < sum(
                    isinstance(c, (ir.Assign, ir.Store))
                    for c in ir.walk(original_loop.body))
        for (a, s), original in expected.items():
            actual = interp.run_step(projected, {"a": a}, {**proc.initial, "s": s})
            assert actual[2] & selected == original[2] & selected, (a, s, selected)
            assert actual[3] == original[3], (a, s, selected)
    if "dead =" in body:
        assert reduced_loop  # The change removes computation, not just marks.


def test_source_loop_projection_has_the_same_symbolic_hit_predicates(monkeypatch):
    from behave.sim.behavior.lowering import lower_procedure

    monkeypatch.setattr(plan, "TERM_BUDGET", 10000)
    monkeypatch.setattr(encoder, "TERM_BUDGET", 10000)
    source = '''class Model:
    def reset(self):
        self.s = 0
    def process(self, a):
        total = 0
        for i in range(2):
            dead = a * a + i
            if self.s == a:
                total += i
            self.s = (self.s + 1) & 3
        return {"y": total}
'''
    proc = lower_procedure(source, outputs=["y"], input_widths=(2,))
    env = {"a": ranges.Range(0, 3), "s": ranges.Range(0, 3)}
    full = encoder.encode_step(proc, env=env, tag="source_full")
    for index, goal in enumerate(sorted(ir.goals_of(proc.body))):
        selected = encoder.encode_goal_query(proc, (goal,), env=env,
                                             tag=f"source_selected_{index}")
        solver = z3.Solver()
        # Equality of integer values, not carrier widths (projection can narrow
        # a carrier). These fixture's input/state carriers are nonnegative.
        solver.add(full.constraint, selected.constraint,
                   *(z3.BV2Int(full.pre[name]) == z3.BV2Int(selected.pre[name])
                     for name in ("a", "s")),
                   full.hits[goal] != selected.hits[goal])
        assert solver.check() == z3.unsat, goal
