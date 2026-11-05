"""Whole-group racing changes task selection, not within-group credit or masks."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.train.pool import CompletedGroups, ReplenishingGroups
from behave.train.slime_launch import preset
from behave.train.slime_rollout import from_prepared_report, load_batch, pack_report, save_batch


def test_first_complete_groups_not_first_candidates_or_rewards():
    race = CompletedGroups(("a", "a", "b", "b", "c", "c"), 2)
    for index in (0, 2, 4, 5):
        race.complete(index)
        assert not race.stop.is_set()
    race.complete(3)
    assert race.stop.is_set()
    assert race.summary()["selected_indices"] == [2, 3, 4, 5]
    # Late completion and replay of a committed case cannot change selection.
    race.complete(1)
    race.complete(0)
    assert race.summary() == {
        "strategy": "first-complete-groups", "update_groups": 2,
        "launched_groups": 3, "launched_candidates": 6,
        "completion_order": ["c", "b", "a"], "selected_groups": ["c", "b"],
        "selected_indices": [2, 3, 4, 5],
    }


def test_twelve_by_eight_launches_select_eight_whole_groups():
    groups = tuple(str(i) for i in range(12) for _ in range(8))
    race = CompletedGroups(groups, 8)
    # Give the first four groups seven quick candidates each, then complete
    # groups 4..11. No individual fast candidate from 0..3 enters training.
    for index in [i for i in range(32) if i % 8 != 7] + list(range(32, 96)):
        race.complete(index)
    assert race.stop.is_set()
    assert race.summary()["selected_indices"] == list(range(32, 96))


@pytest.mark.parametrize("count", [0, 3, True])
def test_runtime_update_count_must_fit_launched_groups(count):
    with pytest.raises(ValueError, match="update_groups"):
        CompletedGroups(("a", "b"), count)


def test_packing_selection_preserves_original_ids_and_credit(tmp_path):
    from test_slime_records import data

    store, ref, report, _ = data(tmp_path)
    for i, row in enumerate(report["candidates"]):
        row["group_id"] = "slow" if i < 2 else "fast"
    selector = CompletedGroups(("slow", "slow", "fast", "fast"), 1)
    for index in (0, 2, 3, 1):
        selector.complete(index)
    report["group_selection"] = selector.summary()
    # The unselected group may have missing records. It is not considered for
    # admission, and does not prevent training the completed selected group.
    report["candidates"][0]["examples"] = None
    report["candidates"][1]["examples"] = None
    actual = pack_report(report, store, recipe=preset("grpo"), directory=tmp_path)
    assert [row["rollout_id"] for row in actual.candidates] == [2, 3]
    assert [row["reward"] for row in actual.candidates] == [1, 0]
    assert actual.candidates[0]["credit"] == -actual.candidates[1]["credit"]
    assert {p["rollout_id"] for p in actual.payloads} == {2}
    assert sum(sum(p["loss_mask"]) for p in actual.payloads) == 6
    assert actual.skipped_groups == ()
    assert actual.group_selection == selector.summary()
    path = tmp_path / "prepared.json"
    save_batch(path, actual, head=ref, manifest=ref, plan=ref)
    assert load_batch(path, head=ref, manifest=ref, plan=ref, recipe=preset("grpo")) == actual


def test_inprocess_prepared_path_uses_the_same_group_selection(tmp_path):
    from test_slime_records import data

    store, _, report, examples = data(tmp_path)
    groups = ("slow", "slow", "fast", "fast")
    for row, group in zip(report["candidates"], groups, strict=True):
        row["group_id"] = group
    selector = CompletedGroups(groups, 1)
    for index in (2, 3, 0, 1):
        selector.complete(index)
    report["group_selection"] = selector.summary()
    examples = tuple(replace(example, turns=tuple(replace(turn,
        capture={"endpoint": "fixture", "prefix_cache": None}) if turn.status == "eligible" else turn
        for turn in example.turns)) for example in examples)
    prepared = SimpleNamespace(summary=report, plan=SimpleNamespace(group_ids=groups),
                               examples=examples, reader=None)
    verifier = SimpleNamespace(endpoints={"fixture": SimpleNamespace(parameters={
        "temperature": 1.0, "top_p": 1.0, "top_k": -1})})
    expected = pack_report(report, store, recipe=preset("grpo"), directory=tmp_path)
    actual = from_prepared_report(prepared, verifier=verifier, recipe=preset("grpo"))
    assert actual == expected


@pytest.mark.parametrize("replenish", [False, True])
def test_native_schedule_keeps_nonconsecutive_launch_ids_and_update_size(tmp_path, replenish):
    pytest.importorskip("slime", reason="pinned native CPU dependency test")
    from slime.utils.dp_schedule import build_dp_schedule
    from test_slime_recipe import arguments, samples
    from test_slime_records import data

    from behave.train.slime_rollout import post_process_rewards

    store, _, report, _ = data(tmp_path)
    groups = ("slow", "slow", "fast", "fast")
    for row, group in zip(report["candidates"], groups, strict=True):
        row["group_id"] = group
    report["candidates"][3]["examples"]["turns"] = report["candidates"][2]["examples"]["turns"]
    selector = ReplenishingGroups(groups, 1, 2) if replenish else CompletedGroups(groups, 1)
    for index in (2, 3, 0, 1):
        if replenish:
            selector.record(index, report["candidates"][index]["examples"]["reward"])
        else:
            selector.complete(index)
    report["group_selection"] = selector.summary()
    batch = pack_report(report, store, recipe=preset("grpo"), directory=tmp_path)
    actual = samples(batch)
    args = arguments(batch.recipe)
    args.global_batch_size = 2
    post_process_rewards(args, actual)
    args.micro_batch_size = 1
    args.use_dynamic_batch_size = args.balance_data = args.balance_by_flops = False
    partitions, _, microbatches, sizes = build_dp_schedule(args,
        {"dp_size": 2, "cp_size": 1, "vpp_size": 1, "microbatch_group_size_per_vp_stage": 1},
        [len(sample.tokens) for sample in actual], global_batch_size=args.global_batch_size,
        rollout_indices=[sample.rollout_id for sample in actual])
    assert args.global_batch_size == 2 and sizes == [2]
    assert sorted(i for partition in partitions for i in partition) == list(range(len(actual)))
    assert {sample.rollout_id for sample in actual} == {2, 3}
    assert microbatches == [2]  # Two packed context segments per candidate.


def test_rollout_selection_is_recorded_and_replay_does_not_dispatch(tmp_path, publication, monkeypatch):
    from test_training_rollouts import plan_at

    from behave.agent.run_store import JournalDirectory
    from behave.task_execution.run_store import RunStore
    from behave.train import rollouts
    from behave.train.learning_report import PreparedTrainingReport

    plan = plan_at(tmp_path, publication)
    # The execution is stubbed at the shared batch boundary. Each case is
    # still committed before notifying selection, as run_batch does.
    job = plan.execution.jobs[0]
    jobs = tuple(replace(job, attempt=replace(job.attempt, attempt_id=f"candidate-{i}")) for i in range(4))
    plan = replace(plan, execution=replace(plan.execution, jobs=jobs),
                   group_ids=("slow", "slow", "fast", "fast"))
    run = JournalDirectory(tmp_path / "rollout")
    calls = []

    def batch(**kwargs):
        calls.append(1)
        child = RunStore(kwargs["directory"])
        journal = child.journal("report")
        for index in (0, 2, 3, 1):
            journal.append(journal.load(), "evaluation_case", {"index": index})
            kwargs["on_case"](index)
            assert kwargs["cancelled"]() == (index in (3, 1))
        return SimpleNamespace()

    monkeypatch.setattr(rollouts, "run_batch", batch)
    monkeypatch.setattr(rollouts, "prepare_rollouts", lambda plan, *a, **kw:
                        PreparedTrainingReport({"candidates": []}, plan, ()))
    judge = SimpleNamespace(identity=plan.execution.judge_backend)
    result = rollouts.run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader,
                                  run=run, judge=judge, update_groups=1)
    assert result["group_selection"]["selected_indices"] == [2, 3]
    assert rollouts.run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader,
                                run=run, judge=judge, update_groups=1) == result
    assert calls == [1]


@pytest.mark.isolation
def test_real_batch_cancellation_and_sealed_selection_replay(tmp_path, publication, monkeypatch):
    import time

    from test_evaluation import Judge
    from test_task_workflow import setup
    from test_training_replenishment import FixtureTokenVerifier, native_fixture_program
    from test_training_rollouts import plan_at

    from behave.agent.run_store import JournalDirectory
    from behave.task_execution import batch as batch_module
    from behave.train.learning_report import load_training_report
    from behave.train.rollouts import run_rollouts

    plan = plan_at(tmp_path, publication)
    job, _ = setup(tmp_path / "native-fixture", publication,
                   native_fixture_program(plan.requirements, "calls = ()\n"))
    verifier = FixtureTokenVerifier(plan.requirements)
    jobs = tuple(replace(job, attempt=replace(job.attempt, attempt_id=f"candidate-{i}")) for i in range(4))
    plan = replace(plan, execution=replace(plan.execution, jobs=jobs, workers=4),
                   group_ids=("slow", "slow", "fast", "fast"))
    execute = batch_module.execute_task

    def gated(**kwargs):
        if kwargs["job"].attempt.attempt_id in {"candidate-0", "candidate-1"}:
            deadline = time.monotonic() + 20
            while not kwargs["cancelled"]():
                assert time.monotonic() < deadline, "group selection never cancelled surplus work"
                time.sleep(.01)
        return execute(**kwargs)

    monkeypatch.setattr(batch_module, "execute_task", gated)
    run = JournalDirectory(tmp_path / "actual-rollout")
    result = run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader,
                          run=run, judge=Judge(), update_groups=1, verifier=verifier)
    assert result["group_selection"]["selected_indices"] == [2, 3]
    assert len(result["candidates"]) == 4
    head, _ = run.seal("training")
    assert load_training_report(run.objects, head, approved_manifest=plan.manifest, verifier=verifier) == result
    monkeypatch.setattr(batch_module, "execute_task", lambda **kw: pytest.fail("dispatch during replay"))
    assert run_rollouts(plan=plan, approved_manifest=plan.manifest, reader=publication.reader,
                        run=run, judge=Judge(), update_groups=1, verifier=verifier) == result
