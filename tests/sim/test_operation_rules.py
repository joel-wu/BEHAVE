"""Public typed rules bind source positions, not test-specific line numbers."""

from behave.sim.core.source_rules import SourceGoalRules
import pickle
from itertools import product

import pytest

from behave.sim import api
from behave.sim.behavior import instrumented
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.goal_policy import evaluation_goal_policy

from .test_baseline_public_sequence import _contract


def _rule(operator="add", target=174, *, kinds=()):
    return api.OperationRule(operator, api.OperationGoal(
        "target", api.GoalCondition("eq", api.GoalField("result"), target)), kinds)


def _declared(*rules):
    base = _contract()
    return api.TaskContract(base.endpoints, base.clocks, operation_rules=rules)


def test_operation_rule_wire_and_empty_policy_identity():
    contract = _declared(_rule())
    assert api.TaskContract.from_dict(contract.as_dict()).as_dict() == contract.as_dict()
    assert pickle.loads(pickle.dumps(contract)).digest == contract.digest
    assert _contract().digest != contract.digest
    old = _contract().as_dict()
    old.pop("operation_rules")
    old.pop("source_read_rules")
    old["version"] = "task-contract/13"
    assert api.TaskContract.from_dict(old).digest == _contract().digest
    with pytest.raises(ValueError, match="distinct"):
        _declared(_rule(), _rule())
    with pytest.raises(ValueError, match="unknown"):
        _rule("not-an-operation")
    policy = evaluation_goal_policy(contract, CatalogueManifest(()), artifact_kind="behavior")
    selection = next(row for row in policy.selections if row.schema.name == "operation-event")
    assert selection.reason.code == "no-matching-source-operation-sites"


def test_rtl_operation_rules_cannot_change_after_catalogue_binding(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    artifact = RTLArtifact(tmp_path / "M.sv", "M", tmp_path / "rtl", source_rules=SourceGoalRules(operations=(_rule(),)))
    with pytest.raises(api.Refused, match="rtl-source-goal-policy-mismatch"):
        artifact.bind_observation(_contract())


@pytest.mark.solver
@pytest.mark.parametrize("expression,operator,target", (
    ("a + b", "add", 3),
    ("(a > 0) + b", "add", 3),  # bool+int is not the integer-only variant
    ("([a] + [b])[0]", "add", 3),
    ("([a] * 2)[0]", "multiply", 3),
    ("(a + b) + a", "add", 3),
))
def test_automatically_bound_typed_variants_match_native_ir_and_solver(expression, operator, target):
    import z3

    from behave.sim.symbolic import encoder, interp, ranges
    from behave.sim.symbolic import plan as planning
    from behave.sim.behavior.construction import ConstructionFacts
    from behave.sim.behavior.lowering import lower_source

    source = f"class M:\n def process(self, a, b): return {{'y': {expression}}}\n"
    rules = (_rule(operator, target),)
    plan = plan_source(source_model(source), source, source_rules=SourceGoalRules(operations=rules))
    assert plan.operation_goals
    lowered = lower_source(source, input_widths=[3, 3], source_rules=SourceGoalRules(operations=rules),
                           construction=ConstructionFacts((), (), (), ()))
    assert lowered.catalogue == plan.manifest
    native, _, hits = instrumented.load_source(source, goal_plan=plan)
    plain, _, old_hits = instrumented.load_source(source)
    env = {**lowered.proc.state_ranges, "a": ranges.Range(0, 7), "b": ranges.Range(0, 7)}
    prepared = planning.plan_encoding(lowered.proc, env)
    assert prepared.complete, prepared.unresolved
    encoded = encoder.encode_step(lowered.proc, env=env, plan=prepared)
    operation_ids = {binding.id for binding in plan.operation_goals}
    for a, b in product(range(4), repeat=2):
        hits.clear()
        old_hits.clear()
        expected = plain["M"]().process(a, b)
        assert native["M"]().process(a, b) == expected
        assert [goal for goal in hits if goal not in operation_ids] == old_hits
        _, outputs, ir_hits, failure = interp.run_step(lowered.proc, {"a": a, "b": b})
        assert not failure and outputs.transactions == [expected]
        assert set(ir_hits) & operation_ids == set(hits) & operation_ids
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == a, encoded.pre["b"] == b)
        assert solver.check() == z3.sat
        for goal in operation_ids:
            assert z3.is_true(solver.model().eval(encoded.hits[goal], model_completion=True)) == (goal in hits)


@pytest.mark.isolation
@pytest.mark.solver
def test_task_bound_operation_query_and_source_audit_keep_all_goals():
    from behave.sim.behavior.task_boundary import lower_task_behavior
    from behave.sim.core.analysis import AuditCheckStatus

    source = "class M:\n def reset(self): pass\n def process(self, a): return {'y': (a + 1) & 255}\n"
    impossible = api.OperationRule("add", api.OperationGoal("outside", api.GoalCondition(
        "eq", api.GoalField("result"), 300)))
    contract = _declared(_rule(), impossible)
    raw, _ = lower_task_behavior(source, "model.py", contract, 1)
    manifest = raw.catalogue_manifest()
    instance = raw.bind(1, task_contract=contract.digest, catalogue_manifest=manifest).instance
    plan = plan_source(source_model(source, file="model.py"), source, "model.py",
                       source_rules=SourceGoalRules(operations=contract.operation_rules))
    goals = {binding.predicate.rule.name: binding.id for binding in plan.operation_goals}
    answer = instance.query(tuple(goals.values()), 1, timeout_ms=10_000, workers=1)
    assert not answer.undecided and answer.unreachable == {goals["outside"]}
    witness = answer.reachable[goals["target"]]
    assert goals["target"] in instance.replay(witness).hits
    assert instance.audit((witness,)).status is AuditCheckStatus.AGREED
    assert instance.source_audit((witness,)).status is AuditCheckStatus.AGREED
    assert goals["outside"] in instance.catalogue_manifest()


@pytest.mark.isolation
def test_isolated_native_operation_rule_handoff():
    from behave.sim.behavior.isolated import IsolatedModel, Limits, hits_of

    source = "class M:\n def process(self, a): return {'y': (a + 1) & 255}\n"
    plan = plan_source(source_model(source), source, source_rules=SourceGoalRules(operations=(_rule(),)))
    goal, = (binding.id for binding in plan.operation_goals)
    with IsolatedModel(source, limits=Limits(seconds=30, wall_seconds=60),
                       source_rules=SourceGoalRules(operations=(_rule(),)), strict_module=True,
                       snapshot_policy="on-demand") as model:
        assert model.process(a=0) == {"y": 1}
        assert goal not in hits_of(model)
        assert model.process(a=173) == {"y": 174}
        assert goal in hits_of(model)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("direction", ("candidate", "reference", "both"))
def test_public_operation_baseline_miss_search_and_native_audit(tmp_path, direction):
    source = "class M:\n def reset(self): pass\n def process(self, a): return {'y': (a + 1) & 255}\n"
    path = tmp_path / "candidate.py"
    path.write_text(source)
    contract = _declared(_rule())
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract, file="candidate.py"),
                     reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
                                  bounded_analysis=False)
    providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                      if direction == "both" or ("candidate:" in p.name) == (direction == "candidate"))
    assert len(providers) == (2 if direction == "both" else 1)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.evaluation.complete, [run.run_failure for run in result.evaluation.results]
    assert result.decision.authoritative and result.decision.passed
    goal, = (goal.id for goal in result.evaluation.catalogue.values() if goal.family == "operation-event")
    assert goal in result.evaluation.hits
    missed, targets = [], set()
    for search in result.generated.searches:
        file = "candidate.py" if "candidate:" in search.provider else "behavior.py"
        plan = plan_source(source_model(source, file=file), source, file, source_rules=SourceGoalRules(operations=(_rule(),)))
        target, = (binding.id for binding in plan.operation_goals)
        baseline = search.telemetry["baseline_coverage"]
        assert target in set(baseline["hits"]) | set(baseline["unhit"])
        missed.append(target in baseline["unhit"])
        targets.add(target)
    assert any(missed)
    assert any(targets.intersection(dict(row)["targets"]) for search in result.generated.searches
               for rows in search.provenance.values() for row in rows)
