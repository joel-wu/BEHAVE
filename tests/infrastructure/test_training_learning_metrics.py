"""Learning curves separate generating-policy scores from optimizer evidence."""

import csv
import json

from test_slime_records import data

from behave.train.learning_report import (
    credit_usage,
    export,
    group_outcomes,
    paired_rounds,
    round_metrics,
    sampling_summary,
    token_availability,
)
from behave.train.slime_launch import preset
from behave.train.slime_rollout import pack_report


def test_sampling_export_reuses_selection_without_reordering_or_reward_filtering():
    selection = {"strategy": "first-complete-groups", "launched_groups": 3,
        "launched_candidates": 6, "selected_groups": ["hard", "easy"],
        "selected_indices": [0, 1, 4, 5], "completion_order": ["hard", "easy", "late"]}
    record = {"round": 2, "group_selection": selection,
              "replacement_attempts": 3, "replaced_slots": 2}
    original = json.dumps(record)
    row, = sampling_summary(record)
    assert row == {"round": 2, "strategy": "first-complete-groups",
        "launched_groups": 3, "launched_candidates": 6,
        "selected_groups": 2, "selected_candidates": 4,
        "selected_task_ids": '["hard", "easy"]',
        "recorded_completion_order": '["hard", "easy", "late"]',
        "replacement_attempts": 3, "replaced_slots": 2}
    assert json.dumps(record) == original
    del record["replaced_slots"]
    assert sampling_summary(record)[0]["replaced_slots"] is None
    assert sampling_summary({"round": 0, "group_selection": None}) == []


def test_token_availability_keeps_missing_evidence_separate_from_zero():
    candidates = [
        {"task": "a", "retained_turns": 3, "excluded_turns": 1, "unavailable_turns": 0},
        {"task": "a", "retained_turns": 2, "excluded_turns": 0, "unavailable_turns": 1},
        {"task": "b", "retained_turns": 0, "excluded_turns": 2, "unavailable_turns": 0},
    ]
    original = json.dumps(candidates)
    total, a, b = token_availability({"round": 2, "candidates": candidates})
    assert total == {"round": 2, "task": "", "candidates": 3, "retained_turns": 5,
        "excluded_turns": 3, "unavailable_turns": 1, "candidates_with_training_tokens": 2,
        "partially_available_candidates": 1}
    assert a["partially_available_candidates"] == 1
    assert b["candidates_with_training_tokens"] == 0
    assert json.dumps(candidates) == original
    legacy, = token_availability({"round": 0, "candidates": [
        {"task": "", "retained_turns": 2, "excluded_turns": 0}]})
    assert legacy["candidates_with_training_tokens"] == 1
    assert legacy["unavailable_turns"] is None
    assert legacy["partially_available_candidates"] is None
    assert token_availability({"round": 0, "candidates": []}) == []


def test_filtered_groups_keep_reward_observations_separate_from_selected_accuracy():
    record = {"round": 3, "group_selection": {"strategy": "first-reward-varying-groups",
        "planned_groups": 20, "planned_candidates": 40, "launched_groups": 4, "launched_candidates": 7,
        "selected_groups": ["mixed"], "selected_indices": [2, 3],
        "completion_order": ["easy", "mixed", "late"], "observations": [
            {"group_id": "easy", "members": [0, 1], "rewards": [1, 1], "status": "constant"},
            {"group_id": "mixed", "members": [2, 3], "rewards": [0, .5], "status": "selected"},
            {"group_id": "missing", "members": [4, 5], "rewards": [1, None], "status": "unavailable"}]}}
    sampling, = sampling_summary(record)
    assert sampling["constant_groups"] == sampling["unavailable_groups"] == 1
    assert sampling["selected_candidates"] == 2 and sampling["launched_candidates"] == 7
    rows = group_outcomes(record)
    assert [r["mean_reward"] for r in rows] == [1, .25, None]
    assert all("rtl_accuracy" not in row for row in rows)
    assert group_outcomes({"round": 0, "group_selection": None}) == []


def test_zero_credit_work_is_measured_without_changing_the_denominator():
    candidates = [
        {"task": "a", "credit": 0., "reward": 0., "training_tokens": 20, "loss_tokens": 10},
        {"task": "a", "credit": 0., "reward": 0., "training_tokens": 30, "loss_tokens": 20},
        {"task": "b", "credit": -1., "reward": 0., "training_tokens": 50, "loss_tokens": 40},
        {"task": "b", "credit": 1., "reward": 1., "training_tokens": 80, "loss_tokens": 60},
    ]
    total, a, b = credit_usage({"round": 2, "candidates": candidates})
    assert total == {"round": 2, "task": "", "candidates": 4, "zero_credit_candidates": 2,
                     "training_tokens": 180, "zero_credit_training_tokens": 50,
                     "loss_tokens": 130, "zero_credit_loss_tokens": 30}
    assert a["zero_credit_candidates"] == 2 and b["zero_credit_candidates"] == 0
    assert len(candidates) == 4 and candidates[0]["reward"] == 0.
    legacy, = credit_usage({"round": 0, "candidates": [
        {"task": "", "credit": 0., "reward": 0.}]})
    assert legacy["training_tokens"] is None
    assert legacy["zero_credit_loss_tokens"] is None
    assert credit_usage({"round": 0, "candidates": [{"task": "a"}]}) == []


def test_comparison_uses_common_tasks_and_keeps_execution_failures():
    def task(accuracy):
        return {"candidates": 8, "rtl_accuracy": accuracy, "behavior_accuracy": accuracy,
                "mean_reward": accuracy}

    before = {"round": 0, "tasks": {"a": task(.25), "old_easy": task(1.)},
              "candidates": [{"task": "a", "failure_code": "transport"}]}
    after = {"round": 1, "tasks": {"a": task(.5), "new_hard": task(0.)},
             "candidates": [{"task": "a", "failure_code": None}]}
    row, = paired_rounds([before, after])
    assert row["common_tasks"] == '["a"]'
    assert row["common_task_count"] == 1
    assert row["candidates_before"] == row["candidates_after"] == 8
    assert row["rtl_accuracy_delta"] == .25
    assert row["execution_failures_before"] == 1
    assert row["execution_failures_after"] == 0
    assert paired_rounds([before, {**after, "tasks": {"new_hard": task(0.)}}]) == []


def test_metrics_use_producer_records_not_reencoded_or_replayed_tokens(tmp_path):
    store, _, report, _ = data(tmp_path)
    batch = pack_report(report, store, recipe=preset("grpo"), directory=tmp_path)
    metrics = round_metrics(batch, store, round_id=2, policy={"weight_version": 3},
        started_at="2026-09-18T01:00:00+00:00", finished_at="2026-09-18T01:10:00+00:00",
        preparation_seconds=5., producer_seconds=600., directory=tmp_path)
    assert metrics["round"] == 2
    assert metrics["selected"]["candidates"] == len(batch.candidates)
    assert metrics["selected"]["mean_reward"] == sum(c["reward"] for c in batch.candidates) / len(batch.candidates)
    assert metrics["loss_tokens"] == sum(sum(p["loss_mask"]) for p in batch.payloads)
    assert metrics["training_segments"] == len(batch.payloads)
    assert sum(row["training_tokens"] for row in metrics["candidates"]) == metrics["training_tokens"]
    assert sum(row["loss_tokens"] for row in metrics["tasks"].values()) == metrics["loss_tokens"]
    for row in metrics["candidates"]:
        parts = [p for p in batch.payloads if p["rollout_id"] == row["rollout_id"]]
        assert row["training_tokens"] == sum(len(p["tokens"]) for p in parts)
        assert row["loss_tokens"] == sum(sum(p["loss_mask"]) for p in parts)
    assert metrics["generating_policy"] == {"weight_version": 3}
    assert metrics["replacement_attempts"] == 0
    run = tmp_path / "run"
    target = run / "artifacts/plan/rounds/round_00000002"
    target.mkdir(parents=True)
    metrics["candidates"][0]["failure_code"] = "tool-infrastructure-ConnectionResetError"
    metrics["group_selection"] = {"strategy": "first-complete-groups", "launched_groups": 3,
        "launched_candidates": 6, "selected_groups": ["first", "second"],
        "selected_indices": [0, 1, 4, 5], "completion_order": ["first", "second"]}
    (target / "metrics.json").write_text(json.dumps(metrics))
    (run / "configuration.json").write_text(json.dumps({"layout": {"actor_nodes": 1, "actor_gpus_per_node": 2}}))
    timing = run / "artifacts/timing"
    timing.mkdir()
    records = [{"rollout": 2, "rank": 0, "event": name, "monotonic_seconds": value,
                "grad_norm": .25, "update_successful": True}
               for name, value in (("train_step_started", 0), ("forward_backward_started", 0),
                   ("forward_backward_finished", 9), ("optimizer_started", 9), ("optimizer_finished", 12))]
    (timing / "rank_00.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    output = tmp_path / "curves"
    assert export(run, output)["rounds"] == 1
    row, = list(csv.DictReader((output / "rounds.csv").open()))
    assert row["update_successful"] == ""  # One rank cannot prove an entire update.
    assert row["execution_failures"] == "1"
    sampling, = list(csv.DictReader((output / "sampling.csv").open()))
    assert sampling["selected_task_ids"] == '["first", "second"]'
    assert sampling["launched_candidates"] == "6"
    assert sampling["selected_candidates"] == "4"
    assert int(row["candidates"]) == len(batch.candidates)
    availability = list(csv.DictReader((output / "token_availability.csv").open()))
    assert availability[0]["task"] == ""
    assert int(availability[0]["candidates"]) == len(batch.candidates)
    assert int(availability[0]["candidates_with_training_tokens"]) == len({
        payload["rollout_id"] for payload in batch.payloads})
    assert availability[0]["unavailable_turns"] == "0"
    failure, = list(csv.DictReader((output / "failures.csv").open()))
    assert failure == {"round": "2", "failure_code": "tool-infrastructure-ConnectionResetError", "candidates": "1"}
    (timing / "rank_01.jsonl").write_text("".join(json.dumps({**row, "rank": 1}) + "\n" for row in records))
    export(run, output)
    row, = list(csv.DictReader((output / "rounds.csv").open()))
    assert row["update_seconds"] == "12" and row["update_successful"] == "True"
    assert row["cycle_seconds"] == row["producer_end_to_next_start_seconds"] == ""

    following = {**metrics, "round": 3,
        "started_at": "2026-09-18T01:15:00+00:00", "finished_at": "2026-09-18T01:25:00+00:00"}
    following_dir = target.parent / "round_00000003"
    following_dir.mkdir()
    (following_dir / "metrics.json").write_text(json.dumps(following))
    export(run, output)
    before, after = list(csv.DictReader((output / "rounds.csv").open()))
    assert before["cycle_seconds"] == "900.0"
    assert before["producer_end_to_next_start_seconds"] == "300.0"
    assert after["cycle_seconds"] == after["producer_end_to_next_start_seconds"] == ""
    assert after["update_successful"] == ""


def test_curve_export_preserves_missing_historical_token_measurements(tmp_path):
    run = tmp_path / "run"
    (run / "artifacts/plan/rounds").mkdir(parents=True)
    (run / "configuration.json").write_text(json.dumps({"layout": {"actor_nodes": 1, "actor_gpus_per_node": 1}}))
    task = {"candidates": 8, "rtl_accuracy": .5, "behavior_accuracy": .5,
            "mean_reward": .5, "reward_std": .5, "submitted": 8}
    base = {"selected": task, "tasks": {"a": task}, "candidates": [],
            "producer_seconds": 60., "preparation_seconds": 1., "training_tokens": 100,
            "loss_tokens": 50, "training_segments": 8, "replacement_attempts": 0,
            "zero_variance_groups": 0}
    for index in (0, 1):
        directory = run / f"artifacts/plan/rounds/round_{index:08d}"
        directory.mkdir()
        tasks = {"a": {**task, **({"training_tokens": 100, "loss_tokens": 50,
                   "max_segment_tokens": 20} if index else {})}}
        (directory / "metrics.json").write_text(json.dumps({**base, "round": index, "tasks": tasks,
            "started_at": f"2026-09-18T01:0{index}:00+00:00",
            "finished_at": f"2026-09-18T01:0{index}:59+00:00"}))
    output = tmp_path / "curves"
    learning = run / "artifacts/plan/learning/initial"
    learning.mkdir(parents=True)
    measured = ("[2026-09-18 01:00:00] timer.py:32 - Timer update_weights end (elapsed: 17.4s)\n"
        "[2026-09-18 01:01:00] model.py:918 - step 0: "
        "{'train/step': 0, 'train/grad_norm': 0.25, 'train/pg_clipfrac': 0.1}\n")
    (learning / "stderr").write_text(measured)
    (learning / "stdout").write_text(measured)
    export(run, output)
    before, after = list(csv.DictReader((output / "tasks.csv").open()))
    assert before["training_tokens"] == ""
    assert after["training_tokens"] == "100"
    phase, = list(csv.DictReader((output / "native_phases.csv").open()))
    assert phase == {"process_stage": "initial", "logged_at": "2026-09-18 01:00:00",
                     "phase": "update_weights", "seconds": "17.4"}
    update, = list(csv.DictReader((output / "optimization.csv").open()))
    assert update == {"process_stage": "initial", "logged_at": "2026-09-18 01:01:00",
                      "train/step": "0", "train/grad_norm": "0.25", "train/pg_clipfrac": "0.1"}
