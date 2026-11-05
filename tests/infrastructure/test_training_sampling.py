"""Task sampling is independent of scoring and resumes from a fixed update index."""

from dataclasses import replace

import pytest
from test_training_rollouts import plan_at

from behave.core.artifacts import BlobRef
from behave.train.pool import SamplingPolicy, sample_plan


def pool_plan(tmp_path, publication):
    plan = plan_at(tmp_path, publication)
    job = plan.execution.jobs[0]
    jobs = tuple(replace(job, task=replace(job.task, task_id=task),
        attempt=replace(job.attempt, attempt_id=f"{task}:{rollout}"))
        for task in ("a", "b", "c", "d", "e") for rollout in range(3))
    return replace(plan, execution=replace(plan.execution, jobs=jobs),
                   group_ids=tuple(job.task.task_id for job in jobs))


def test_cycle_uses_complete_groups_without_epoch_tail_padding(tmp_path, publication):
    plan = pool_plan(tmp_path, publication)
    policy = SamplingPolicy(2, "cycle", "seed")
    assert [policy.select(tuple("abcde"), update) for update in range(4)] == [
        ("a", "b"), ("c", "d"), ("e", "a"), ("b", "c")]
    selected = sample_plan(plan, policy, 2)
    assert selected.group_ids == ("e",) * 3 + ("a",) * 3
    assert [job.attempt.attempt_id for job in selected.execution.jobs] == [
        "e:0", "e:1", "e:2", "a:0", "a:1", "a:2"]
    assert selected.manifest == plan.manifest and selected.requirements == plan.requirements
    assert selected.execution.verification == plan.execution.verification


def test_shuffle_is_reproducible_after_cold_restore_and_not_score_based(tmp_path, publication):
    plan = pool_plan(tmp_path, publication)
    policy = SamplingPolicy(2, "shuffle", "experiment-fixed-seed")
    expected = [BlobRef.of(sample_plan(plan, policy, update).to_bytes()) for update in range(9)]
    restored = SamplingPolicy.from_dict(policy.as_dict())
    assert [BlobRef.of(sample_plan(plan, restored, update).to_bytes()) for update in range(5, 9)] == expected[5:]
    assert len(set(expected)) > 1
    for update in range(9):
        selected = sample_plan(plan, restored, update)
        assert len(set(job.task.task_id for job in selected.execution.jobs)) == 2
        assert len(selected.execution.jobs) == 6


@pytest.mark.parametrize("policy,update", [
    ((6, "cycle", "seed"), 0), ((1, "cycle", "seed"), -1), ((1, "cycle", "seed"), True),
    ((0, "cycle", "seed"), 0), ((1, "best_score", "seed"), 0),
])
def test_invalid_sampling_does_not_change_membership(tmp_path, publication, policy, update):
    plan = pool_plan(tmp_path, publication)
    with pytest.raises(ValueError):
        sample_plan(plan, SamplingPolicy(*policy), update)


def test_native_handoff_rejects_a_different_scored_group_before_fanout(monkeypatch):
    from types import SimpleNamespace

    from test_native_chat_tokens import native

    from behave.core.records import RecordError
    from behave.train import slime_rollout as slime_rollout_module
    from behave.train.slime_launch import preset
    from behave.train.training_data import NativeChatVerifier

    endpoint, _ = native("sglang")
    monkeypatch.setattr(slime_rollout_module, "load_recorded_batch", lambda *a, **kw:
        slime_rollout_module.pack_report({"plan": BlobRef.of(b"other update").as_dict()}, None,
                                  recipe=kw["recipe"], expected_plan=kw["expected_plan"]))
    endpoint = replace(endpoint, parameters={"temperature": 1, "top_p": 1, "top_k": -1})
    with pytest.raises(RecordError, match="requested update"):
        slime_rollout_module.from_training_report(None, BlobRef.of(b"head"),
            approved_manifest=BlobRef.of(b"membership"), verifier=NativeChatVerifier(endpoint),
            recipe=preset("grpo"), expected_plan=BlobRef.of(b"requested update"))
