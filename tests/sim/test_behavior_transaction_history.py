"""Task observation history is part of the full Behavior question, not a seed."""

import json
from pathlib import Path

import pytest
import z3

from behave.sim import api
from behave.sim.behavior import instance as behavior_instance
from behave.sim.behavior import residual
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.query_cache import clear_analysis_answer_cache

from .test_behavior_public_exact_construction import _reference, _schema, _task
from .test_command_handler import _BaselineOnly
from .test_transaction_goal_queries import _lower


def _pair(before=7, after=9):
    return api.TransactionGoal("pair", "out", api.GoalCondition("and",
        api.GoalCondition("eq", api.GoalField("y", previous=True), before),
        api.GoalCondition("eq", api.GoalField("y"), after)))


@pytest.mark.parametrize("bound", (1, 2))
def test_stateless_model_still_needs_two_observations(bound):
    _contract, instance, _binding = _lower('        return {"y": a}\n', (_pair(),), bound=bound)
    goal = "observation:pair"
    verdict = instance.query((goal,), bound, timeout_ms=10_000, workers=1)
    assert not verdict.undecided
    if bound == 1:
        assert verdict.unreachable == {goal}
        assert not verdict.reachable
    else:
        trace = verdict.reachable[goal]
        assert list(trace) == [{"a": 7}, {"a": 9}]
        replay = instance.replay(trace)
        assert not replay.broken and not replay.error
        assert goal in replay.hits
        assert instance.audit((trace,)).status is AuditCheckStatus.AGREED


@pytest.mark.parametrize("body,trace", (
    ('        if a == 0:\n            return None\n        return {"y": a}\n',
     [{"a": 7}, {"a": 0}, {"a": 9}]),
    ('        return [{"y": 7}, {"y": 9}]\n', [{"a": 0}]),
))
def test_history_follows_emitted_sequence_not_invocation_count(body, trace):
    _contract, instance, _binding = _lower(body, (_pair(),), bound=len(trace))
    assert "observation:pair" in instance.replay(trace).hits
    assert instance.audit((trace,)).status is AuditCheckStatus.AGREED


def test_failed_invocation_observes_input_but_absorbed_suffix_does_not():
    rules = tuple(api.TransactionGoal(name, "in",
        api.GoalCondition("eq", api.GoalField("a"), value))
        for name, value in (("zero", 0), ("seven", 7))) + (_pair(),)
    _contract, instance, _binding = _lower('        return {"y": 1 // a}\n', rules, bound=3)
    trace = [{"a": 0}, {"a": 7}, {"a": 9}]
    replay = instance.replay(trace)
    assert replay.error and not replay.broken
    assert "observation:zero" in replay.hits
    assert "observation:seven" not in replay.hits
    assert "observation:pair" not in replay.hits
    assert instance.audit((trace,)).status is AuditCheckStatus.AGREED


def test_history_audit_detects_a_wrong_symbolic_hit():
    _contract, instance, binding = _lower('        return {"y": a}\n', (_pair(),), bound=2)
    run = residual.unroll(instance.proc, instance.env, 2,
                          plan=instance._position_plan, transactions=binding)
    run.transaction_components["observation:pair"] = (z3.BoolVal(False), z3.BoolVal(False))
    instance._prepared_transaction_audit = run
    check = instance.audit(([{"a": 7}, {"a": 9}],))
    assert check.status is AuditCheckStatus.INCONSISTENT


def test_full_question_keeps_source_goals_and_an_unreachable_transaction_goal():
    outside = api.TransactionGoal("outside", "out",
        api.GoalCondition("eq", api.GoalField("y"), 256))
    _contract, instance, _binding = _lower('        return {"y": a}\n', (_pair(), outside), bound=2)
    manifest = instance.catalogue_manifest()
    source_goal = next(iter(instance.frontend_catalogue_manifest()))
    verdict = instance.query((source_goal, "observation:pair", "observation:outside"),
                             2, timeout_ms=10_000, workers=1)
    assert not verdict.undecided
    assert {source_goal, "observation:pair"} <= verdict.reachable.keys()
    assert verdict.unreachable == {"observation:outside"}
    assert instance.catalogue_manifest() is manifest
    assert "observation:outside" in manifest


@pytest.mark.parametrize("bound, expected", ((1, "bounded-unreachable"), (2, "reached")))
def test_public_transaction_history_analysis_keeps_completed_score(tmp_path, bound, expected):
    clear_analysis_answer_cache()
    source = 'class M:\n    def reset(self):\n        pass\n    def process(self, a):\n        return {"y": a}\n'
    path = tmp_path / "candidate.py"
    path.write_text(source)
    identity = behavior_instance.lower(source, _schema(), file="stable/transactions.py", bound=bound).identity
    reference = _reference(source)
    base = _task(identity, reference=reference)
    contract = api.TaskContract(base.contract.endpoints, base.contract.clocks,
        observation_goals=(_pair(0, 1), api.TransactionGoal("zero", "out",
            api.GoalCondition("eq", api.GoalField("y"), 0))))
    task = api.admit(contract, base.realization, reference=reference)
    handle = api.prepare_behavior(task,
        api.BehaviorConfig(path, file="stable/transactions.py", timeout_s=90),
        api.GenerationSpec(horizon=2, queue_bound=2, reset_hold_cycles=0, drain=0),
        bounded_analysis=True, analysis_bound=bound)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True,
        reference=reference, feasibility_timeout_ms=10_000, query_timeout_ms=10_000,
        analysis_wall_timeout_seconds=180))

    assert result.status is api.CommandStatus.EVALUATED, result.status
    assert result.evaluation.passed
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert result.analysis.per_goal["observation:pair"]["status"] == expected
    assert result.analysis.per_goal["observation:zero"]["status"] == "reached"
    assert result.analysis.concrete == result.evaluation.fingerprint
    if bound == 2:
        support = result.analysis.per_goal["observation:pair"]["support_ref"]
        witness = next(row for row in result.analysis.evidence["witnesses"]
                       if row["witness_ref"] == support)
        assert witness["origin"] == "bounded-query"
        from behave.sim.core.audit import AuditedWitness
        assert AuditedWitness.from_record(witness["trace"]).inputs == ({"a": 0}, {"a": 1})
    saved = json.loads(Path(result.record_path).read_text())
    assert saved["authority"]["evaluation"] == json.loads(json.dumps(result.evaluation.as_evidence_dict()))
