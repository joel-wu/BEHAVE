import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_slime_recipe import arguments, candidate, examples, samples
from test_slime_records import data

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json
from behave.train.slime_launch import preset
from behave.train.slime_rollout import (
    from_prepared_report,
    load_batch,
    load_recorded_batch,
    pack_report,
    post_process_rewards,
    prepare_batch,
    save_batch,
    select_scored_groups,
)


def test_unscored_member_is_zero_and_does_not_exclude_its_group(tmp_path, monkeypatch):
    base = examples(tmp_path, monkeypatch)
    candidates = tuple(candidate(base, str(i), reward) for i, reward in enumerate((1., 0., 1., None)))
    batch = prepare_batch(candidates, group_ids=("a", "a", "b", "b"),
                          recipe=preset("grpo_no_std"), missing_reward_policy="skip_group")
    assert [row["reward"] for row in batch.candidates] == [1., 0., 1., 0.]
    assert [row["credit"] for row in batch.candidates] == [.5, -.5, .5, -.5]
    assert [row["rollout_id"] for row in batch.candidates] == [0, 1, 2, 3]
    assert not batch.skipped_groups
    assert candidates[-1].candidate.result.score is None  # Raw result is never patched.

    args = arguments(batch.recipe)
    args.global_batch_size = 4
    assert post_process_rewards(args, samples(batch)) == ([1., 0., 1., 0.], [.5, -.5, .5, -.5])
    assert args.global_batch_size == 4
    complete = prepare_batch(candidates[:3] + (candidate(base, "3", 0.),),
        group_ids=("a", "a", "b", "b"), recipe=batch.recipe, missing_reward_policy="skip_group")
    post_process_rewards(args, samples(complete))
    assert args.global_batch_size == 4  # A later full round restores the planned size.

    ref = BlobRef.of(b"batch")
    path = tmp_path / "prepared.json"
    save_batch(path, batch, head=ref, manifest=ref, plan=ref)
    assert load_batch(path, head=ref, manifest=ref, plan=ref, recipe=batch.recipe) == batch


def test_all_unscored_retains_the_zero_reward_group(tmp_path, monkeypatch):
    base = examples(tmp_path, monkeypatch)
    batch = prepare_batch((candidate(base, "0", None), candidate(base, "1", None)),
        group_ids=("a", "a"), recipe=preset("grpo"), missing_reward_policy="skip_group")
    assert [row["reward"] for row in batch.candidates] == [0., 0.]
    assert [row["credit"] for row in batch.candidates] == [0., 0.]
    assert len(batch.payloads) == 2 and not batch.skipped_groups


def test_retained_source_plan_checked_before_group_selection(tmp_path):
    store, manifest, report, candidates = data(tmp_path)
    for i, row in enumerate(report["candidates"]):
        row["group_id"] = "a" if i < 2 else "b"
    missing = report["candidates"][2]
    pending_result = replace(candidates[2].candidate.result, judgment=None)
    missing["examples"].update(reward=None, result=store.put(pending_result.to_bytes()).as_dict())
    plan = store.put(canonical_json({"manifest": manifest.as_dict(), "group_ids": ["a", "a", "b", "b"]}))
    report["plan"] = plan.as_dict()
    ref = store.put(canonical_json(report))
    head = store.put(canonical_json({"kind": "training_finished", "payload": {"result": ref.as_dict()}}))
    batch = load_recorded_batch(store, head, approved_manifest=manifest, expected_plan=plan,
        recipe=preset("grpo"), first_rollout=10, first_sample=0, workers=1, start_method="spawn",
        directory=tmp_path / "packing", missing_reward_policy="skip_group")
    assert len(batch.candidates) == 4 and len(batch.payloads) == 6
    assert [row["rollout_id"] for row in batch.candidates] == [10, 11, 12, 13]
    assert batch.candidates[2]["reward"] == 0
    admission = json.loads(next((tmp_path / "packing").glob("*/admission.json")).read_text())
    assert admission["selected_indices"] == [0, 1, 2, 3]
    assert admission["planned_candidates"] == 4 and not admission["skipped_groups"]
    live = from_prepared_report(SimpleNamespace(summary=report, reader=store, captures={}),
        verifier=SimpleNamespace(endpoints={}), recipe=preset("grpo"), first_rollout=10,
        missing_reward_policy="skip_group", preparation_directory=tmp_path / "live")
    assert live == batch
    # No runtime opt-in is needed for zero settlement.
    strict = pack_report(report, store, recipe=preset("grpo"), directory=tmp_path / "strict")
    assert len(strict.candidates) == 4 and strict.candidates[2]["reward"] == 0
    assert missing["examples"]["reward"] is None  # Historical export is unchanged.


def test_missing_entire_result_and_ungrouped_candidates():
    rows = [{"task_id": "task", "attempt_id": str(i), "group_id": group, "examples": example}
            for i, (group, example) in enumerate((
                ("a", None), ("a", {"reward": 1, "result": {}}),
                (None, {"reward": 0, "result": {}}), (None, None)))]
    kept, skipped = select_scored_groups(rows, "skip_group")
    assert kept == (2,) and len(skipped) == 2
    assert [len(group["members"]) for group in skipped] == [2, 1]


def test_native_schedule_uses_actual_count_without_trailing_drop(tmp_path, monkeypatch):
    pytest.importorskip("slime", reason="pinned native CPU dependency test")
    from slime.utils.dp_schedule import build_dp_schedule

    base = examples(tmp_path, monkeypatch)
    candidates = tuple(candidate(base, str(i), None if i == 7 else float(i % 2)) for i in range(8))
    batch = prepare_batch(candidates, group_ids=("a", "a", "b", "b", "c", "c", "d", "d"),
        recipe=preset("grpo"), missing_reward_policy="skip_group")
    args = arguments(batch.recipe)
    args.global_batch_size = 8
    actual = samples(batch)
    post_process_rewards(args, actual)
    args.micro_batch_size = 1
    args.use_dynamic_batch_size = args.balance_data = args.balance_by_flops = False
    partitions, _, microbatches, sizes = build_dp_schedule(args,
        {"dp_size": 2, "cp_size": 1, "vpp_size": 1, "microbatch_group_size_per_vp_stage": 1},
        [len(s.tokens) for s in actual], global_batch_size=args.global_batch_size,
        rollout_indices=[s.rollout_id for s in actual])
    assert sizes == [8] and microbatches == [4]
    assert sorted(i for partition in partitions for i in partition) == list(range(8))
