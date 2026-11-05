"""Source-read predicates agree at native, IR and query evaluation points."""

import pickle

import pytest

from behave.sim import api
from behave.sim.behavior import instrumented
from behave.sim.behavior.construction import ConstructionFacts
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.source_rules import SourceGoalRules

from .test_baseline_public_sequence import _contract


def _read(symbol, target, name="target", kind="integer"):
    return api.SourceReadRule(symbol, name, api.GoalCondition("eq", api.GoalField("value"), target), kind)


def test_read_rules_wire_and_prior_contract_identity():
    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks, source_read_rules=[_read("a", 173)])
    assert api.TaskContract.from_dict(contract.as_dict()).as_dict() == contract.as_dict()
    assert pickle.loads(pickle.dumps(contract)).digest == contract.digest
    assert contract.digest != base.digest
    old = base.as_dict()
    old.pop("source_read_rules")
    old["version"] = "task-contract/15"
    assert api.TaskContract.from_dict(old).digest == base.digest
    with pytest.raises(ValueError, match="history"):
        api.SourceReadRule("a", "previous", api.GoalCondition("eq", api.GoalField("value", previous=True), 0))
    with pytest.raises(ValueError, match="only"):
        api.SourceReadRule("a", "wrong-field", api.GoalCondition("eq", api.GoalField("a"), 0))


@pytest.mark.solver
@pytest.mark.parametrize("body", [
    "b = a\n  a = 5\n  return {'y': b + a}",
    "b = (a > 0)\n  return {'y': int(b)}",
    "return {'y': a if a else a}",
    "return {'y': a if True else a}",
    "b = a\n  return {'y': self.copy(b)}",
    "b = a\n  a = bool(a)\n  return {'y': b + int(a)}",
    "a = bool(a)\n  b = a & True\n  a = int(a)\n  return {'y': a}",
])
def test_behavior_read_samples_match_native_ir_and_solver(body):
    import z3

    from behave.sim.symbolic import encoder, interp, ranges
    from behave.sim.symbolic import plan as planning
    from behave.sim.behavior.lowering import lower_source

    source = "class M:\n def copy(self, b): return b\n def process(self, a):\n  " + body + "\n"
    rules = SourceGoalRules(reads=(_read("a", 3), _read("a", 5, "after-write"),
                                  _read("b", True, "boolean", "boolean"), _read("b", 3),
                                  _read("a", True, "boolean", "boolean")))
    plan = plan_source(source_model(source), source, source_rules=rules)
    lowered = lower_source(source, input_widths=[4], source_rules=rules,
                           construction=ConstructionFacts((), (), (), ()))
    assert lowered.catalogue == plan.manifest
    native, _, hits = instrumented.load_source(source, goal_plan=plan)
    plain, _, structural = instrumented.load_source(source)
    env = {"a": ranges.Range(0, 15)}
    encoded = encoder.encode_step(lowered.proc, env=env, plan=planning.plan_encoding(lowered.proc, env))
    ids = {binding.id for binding in plan.read_goals}
    assert ids
    for a in range(8):
        hits.clear()
        structural.clear()
        expected = plain["M"]().process(a)
        assert native["M"]().process(a) == expected
        assert [goal for goal in hits if goal not in ids] == structural
        _, outputs, ir_hits, failure = interp.run_step(lowered.proc, {"a": a})
        assert not failure and outputs.transactions == [expected]
        assert set(ir_hits) & ids == set(hits) & ids
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == a)
        assert solver.check() == z3.sat
        for goal in ids:
            assert z3.is_true(solver.model().eval(encoded.hits[goal], model_completion=True)) == (goal in hits)


def test_repeated_attribute_reads_are_not_post_statement_snapshots():
    source = '''class M:
 def reset(self): self.q = 0
 def process(self, a):
  before = self.q
  self.q = a
  after = self.q
  return {"y": before + after}
'''
    rules = SourceGoalRules(reads=(_read("self.q", 0, "zero"), _read("self.q", 5, "five")))
    plan = plan_source(source_model(source), source, source_rules=rules)
    assert len(plan.read_goals) == 4  # no reset/store targets
    native, _, hits = instrumented.load_source(source, goal_plan=plan)
    model = native["M"]()
    model.reset()
    hits.clear()
    assert model.process(5) == {"y": 5}
    sampled = {(binding.site.line, binding.predicate.rule.name) for binding in plan.read_goals if binding.id in hits}
    assert sampled == {(4, "zero"), (6, "five")}


@pytest.mark.isolation
@pytest.mark.solver
def test_public_behavior_baseline_miss_search_native_confirmation(tmp_path, record_property):
    import json
    from time import monotonic

    start = monotonic()
    source = "class M:\n def reset(self): pass\n def process(self,a): return {'y': a}\n"
    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks, source_read_rules=(_read("a", 173),))
    path = tmp_path / "candidate.py"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract, file=path.name), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name), bounded_analysis=False)
    providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                      if "candidate:" in p.name)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    goal, = (goal.id for goal in result.evaluation.catalogue.values() if goal.family == "value-observation")
    assert goal in result.evaluation.hits
    search, = result.generated.searches
    assert goal in search.telemetry["baseline_coverage"]["unhit"]
    assert any(goal in dict(row)["targets"] for rows in search.provenance.values() for row in rows)
    record_property("source_read_loop", json.dumps({"goal": goal, "baseline_hit": False,
                                                   "confirmed_hit": True, "total_s": monotonic() - start}))
