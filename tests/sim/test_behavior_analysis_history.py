"""Whole-domain safety is not a query at one replay's fixed state.

The baseline below only supplies zero.  A different, two-transaction history
arms a division failure.  The public analysis must find and source-audit that
history without rewriting the already-completed baseline score.
"""

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.core.audit import AuditedWitness
from behave.sim.behavior import instance as behavior_instance
from behave.sim.behavior.construction import clear_construction_cache
from behave.sim.core.query_cache import clear_analysis_answer_cache

from .test_behavior_public_exact_construction import _reference, _schema, _task
from .test_command_handler import _BaselineOnly


SOURCE = """\
class M:
    def reset(self):
        self.armed = 0

    def process(self, a):
        if a:
            self.armed = 1
            return {"y": 0}
        return {"y": 0 // (1 - self.armed)}
"""

REFERENCE = """\
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": 0}
"""


@pytest.mark.parametrize(
    "bound, expected", [(1, "bounded-unreachable"), (2, "reached")])
def test_public_analysis_distinguishes_the_history_bound(tmp_path, bound, expected):
    clear_construction_cache()
    clear_analysis_answer_cache()
    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    identity = behavior_instance.lower(
        SOURCE, _schema(), file="stable/history.py", bound=bound).identity
    reference = _reference(REFERENCE)
    task = _task(identity, reference=reference)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="stable/history.py", timeout_s=90),
        api.GenerationSpec(
            horizon=2, queue_bound=2, reset_hold_cycles=0, drain=0),
        bounded_analysis=True, analysis_bound=bound)
    catalogue = handle.frontend_catalogue
    safety, = (goal.id for goal in catalogue.values()
               if goal.cls == "safety" and goal.family == "divzero")

    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True,
        reference=reference, feasibility_timeout_ms=10_000,
        query_timeout_ms=10_000,
        analysis_wall_timeout_seconds=180))

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert not result.evaluation.safety_hits
    accepted = [dict(record.accepted)["in"]
                for run in result.evaluation.results
                for record in run.trace.records if "in" in dict(record.accepted)]
    assert accepted and set(accepted) == {(0,)}
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert result.analysis.per_goal[safety]["status"] == expected
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert handle.frontend_catalogue == catalogue

    if bound == 2:
        support = result.analysis.per_goal[safety]["support_ref"]
        witness = next(row for row in result.analysis.evidence["witnesses"]
                       if row["witness_ref"] == support)
        assert witness["origin"] == "bounded-query"
        assert AuditedWitness.from_record(witness["trace"]).inputs == ({"a": 1}, {"a": 0})

    # Ancillary analysis reports a diagnostic; the persisted scoring decision is
    # still the decision made from the concrete inputs, not a second evaluation.
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["evaluation"] == json.loads(
        json.dumps(result.evaluation.as_evidence_dict()))


def test_local_unsat_at_baseline_state_does_not_exclude_another_history():
    from behave.sim.symbolic import interp
    from behave.sim.engines import local_ir, probing

    instance = behavior_instance.lower(
        SOURCE, _schema(), file="stable/history.py", bound=2)
    exact = instance.bind(2, task_contract=_task(instance.identity).contract.digest)
    catalogue = exact.catalogue_manifest()
    safety, = (goal.id for goal in catalogue.values()
               if goal.cls == "safety" and goal.family == "divzero")
    after_zero, _outputs, hits, error = interp.run_step(
        instance.proc, {"a": 0}, instance.proc.initial)
    assert not error and safety not in hits
    local = local_ir.local_hit_any(
        instance.proc, instance.env, after_zero, (safety,),
        position_plan=instance._position_plan, position=1, timeout_ms=10_000)
    assert local.outcome is probing.Outcome.LOCAL_UNSAT

    # This is a new whole-history question, not a promotion of the local answer.
    whole = exact.query((safety,), timeout_ms=10_000)
    assert whole.reachable[safety] == ({"a": 1}, {"a": 0})
    assert not whole.unreachable
    assert exact.catalogue_manifest() == catalogue


@pytest.mark.parametrize("bound, expected", [(2, "bounded-unreachable"), (None, "reached")])
def test_public_analysis_can_reach_a_transaction_after_the_generation_slot_horizon(
        tmp_path, bound, expected):
    source = '''class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        if a:
            self.n = min(self.n + 1, 3)
        return {"y": 0 // (3 - self.n)}
'''
    path = tmp_path / "candidate.py"
    path.write_text(source)
    reference = _reference(REFERENCE)
    contract = _task("fixture").contract
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, model="M"), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"),
        api.GenerationSpec(2, 3, 0, 0), bounded_analysis=True, analysis_bound=bound)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        reference=reference, search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=True, analysis_wall_timeout_seconds=180))
    assert result.evaluation.passed, result.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    goal, = (g.id for g in handle.frontend_catalogue.values()
             if g.cls == "safety" and g.family == "divzero")
    assert result.analysis.per_goal[goal]["status"] == expected
    if bound is None:
        support = result.analysis.per_goal[goal]["support_ref"]
        witness = next(w for w in result.analysis.evidence["witnesses"]
                       if w["witness_ref"] == support)
        assert AuditedWitness.from_record(witness["trace"]).inputs == ({"a": 1},) * 3
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is True
    assert result.analysis.concrete == result.evaluation.fingerprint


def test_public_analysis_reuses_a_full_hundred_transaction_baseline(tmp_path):
    from .test_behavior_public_exact_construction import STRAIGHT_LINE_SOURCE

    path = tmp_path / "candidate.py"
    path.write_text(STRAIGHT_LINE_SOURCE)
    reference = _reference(STRAIGHT_LINE_SOURCE)
    contract = _task("fixture").contract
    task = api.admit(contract, api.realization_of_behavior_source(
        STRAIGHT_LINE_SOURCE, contract, model="M"), reference=reference)
    generation = api.GenerationSpec(205, 100, 3, 200)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"),
        generation, bounded_analysis=True)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1), reference=reference,
        search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=True, analysis_wall_timeout_seconds=180))
    assert result.evaluation.complete and result.evaluation.passed, result.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert result.analysis.evidence["domain"]["bound"] == 100
    assert result.analysis.evidence["feasibility"]["established_by"] == "audited-pool"
    assert all(row["status"] == "reached" for row in result.analysis.per_goal.values())
    assert not result.analysis.evidence["queries"]
    assert any(w["origin"] == "evaluated-record" and len(AuditedWitness.from_record(w["trace"]).inputs) == 100
               for w in result.analysis.evidence["witnesses"])
    assert result.analysis.concrete == result.evaluation.fingerprint


def test_public_analysis_reuses_search_hits_at_the_hundred_input_bound(tmp_path):
    source = '''class M:
 def reset(self): pass
 def process(self, a):
  if a:
   return {"y": 1}
  return {"y": 0}
'''
    path = tmp_path / "candidate.py"
    path.write_text(source)
    reference = _reference(source)
    contract = _task("fixture").contract
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, model="M"), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"),
        api.GenerationSpec(100, 100, 0, 0), bounded_analysis=True)
    providers = handle.generation_providers(reference, workdir=tmp_path / "search")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1), reference=reference,
        providers=providers, search_policy=api.SearchPolicy(time_budget_s=60, max_probes=4),
        run_analysis=True, analysis_wall_timeout_seconds=180))
    assert result.evaluation.complete and result.evaluation.passed, result.as_dict()
    assert len(result.generated.stimuli) > len(result.generated.baseline)
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert result.analysis.evidence["domain"]["bound"] == 100
    assert not result.analysis.evidence["queries"]
    assert all(row["status"] == "reached" for row in result.analysis.per_goal.values())
    assert any(w["origin"] == "evaluated-record" and len(AuditedWitness.from_record(w["trace"]).inputs) == 100
               and any(row["a"] == 1 for row in AuditedWitness.from_record(w["trace"]).inputs)
               for w in result.analysis.evidence["witnesses"])
    assert result.analysis.concrete == result.evaluation.fingerprint
