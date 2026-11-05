"""Shared, offline learning reports preserve evidence and legacy entry points."""

import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest

from behave.train import learning_report
from behave.train.__main__ import main


def report_inputs(root):
    """Small native-run fixture, not a model, token store or rollout replay."""
    root.mkdir(parents=True)
    (root / "configuration.json").write_text(json.dumps({
        "layout": {"actor_nodes": 1, "actor_gpus_per_node": 2}}))
    task = {"candidates": 2, "rtl_accuracy": .5, "behavior_accuracy": 1.,
            "mean_reward": .75, "reward_std": .25, "submitted": 2}
    metrics = {
        "round": 0, "selected": task, "tasks": {"task": task},
        "candidates": [{"task": "task", "failure_code": None, "credit": credit}
                       for credit in (-1, 1)],
        "started_at": "2026-10-18T01:00:00+00:00",
        "finished_at": "2026-10-18T01:10:00+00:00",
        "producer_seconds": 600., "preparation_seconds": 1.,
        "training_tokens": 100, "loss_tokens": 50, "training_segments": 2,
        "replacement_attempts": 0, "zero_variance_groups": 0,
        "group_selection": {
            "strategy": "first-reward-varying-groups", "launched_groups": 2,
            "planned_groups": 2, "planned_candidates": 4,
            "launched_candidates": 4, "selected_groups": ["task"],
            "selected_indices": [0, 1], "completion_order": ["task"],
            "observations": [
                {"group_id": "task", "status": "selected", "members": [0, 1],
                 "rewards": [.5, 1.]},
                {"group_id": "late", "status": "unavailable", "members": [], "rewards": []}],
        },
    }
    directory = root / "artifacts/plan/rounds/round_00000000"
    directory.mkdir(parents=True)
    (directory / "metrics.json").write_text(json.dumps(metrics))
    timing = root / "artifacts/timing"
    timing.mkdir()
    (timing / "rank_00.jsonl").write_text(json.dumps({
        "rollout": 0, "rank": 0, "event": "optimizer_finished",
        "monotonic_seconds": 12, "grad_norm": .25, "update_successful": True}) + "\n")
    return root


@pytest.mark.parametrize(("rewards", "mean", "kind"), [
    ([], None, "unavailable"), ([1, None], None, "unavailable"),
    ([0, 0], 0, "other_constant"), ([.5, .5], .5, "other_constant"),
    ([1, 1], 1, "full_reward"), ([.5, 1], .75, "varying"),
])
def test_group_evidence_keeps_unavailable_distinct_from_zero(rewards, mean, kind):
    record = {"round": 0, "group_selection": {"observations": [{
        "group_id": "task", "status": "recorded", "members": list(range(len(rewards))),
        "rewards": rewards}]}}
    before = deepcopy(record)
    row, = learning_report.group_outcomes(record)
    assert row["mean_reward"] == mean
    assert row["status"] == "recorded" and json.loads(row["rewards"]) == rewards
    assert learning_report.group_class(rewards) == kind
    assert record == before


def test_report_command_preserves_inputs(tmp_path, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("report command must not run training or start a judge")

    for name in ("behave.train.rollouts.run_rollouts", "behave.task_execution.simulator_judge.SimulatorJudge",
                 "behave.task_execution.cocotb_worker.CocotbBackend"):
        monkeypatch.setattr(name, forbidden)
    run = report_inputs(tmp_path / "run")
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    public = tmp_path / "public"
    assert main(["report", str(run), str(public)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "rounds": 1, "task_rows": 1, "output": str(public)}
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
    with (public / "rounds.csv").open() as stream:
        row, = csv.DictReader(stream)
    assert row["update_successful"] == row["update_seconds"] == ""
    assert row["cycle_seconds"] == "" and row["mean_reward"] == "0.75"
    with (public / "group_outcomes.csv").open() as stream:
        mixed, unavailable = csv.DictReader(stream)
    assert mixed["mean_reward"] == "0.75"
    assert unavailable["mean_reward"] == "" and unavailable["rewards"] == "[]"
    with (public / "signal.csv").open() as stream:
        total, _ = csv.DictReader(stream)
    assert total["training_tokens"] == "" and total["zero_credit_candidates"] == "0"


def test_missing_run_does_not_create_input_or_output(tmp_path):
    run, output = tmp_path / "absent", tmp_path / "out"
    with pytest.raises(FileNotFoundError):
        main(["report", str(run), str(output)])
    assert not run.exists() and not output.exists()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_native_logs_stream_without_changing_evidence(tmp_path, monkeypatch, newline):
    run = report_inputs(tmp_path / "run")
    stamp = "2026-10-18 01:00:00"
    phase = f"[{stamp}] timer.py:32 - Timer actor_train end (elapsed: 17.4s)"
    metric = (f"[{stamp}] model.py:918 - step 0: "
              "{'train/step': 0, 'train/grad_norm': 0.25, 'train/pg_clipfrac': 0.1}")
    later = metric.replace(stamp, "2026-10-18 01:01:00")
    zero = metric.replace("0.25", "0.0")
    text = newline.join(["serving noise " + "x" * 500] * 1000 + [phase, metric, metric, later, zero])
    logs = set()
    for stage in ("initial", "resume"):
        directory = run / "artifacts/plan/learning" / stage
        directory.mkdir(parents=True)
        for name in ("stdout", "stderr"):
            path = directory / name
            path.write_text(text)  # Final record deliberately has no newline.
            logs.add(path)
    for rank in (0, 1):
        rows = [{"rollout": 0, "rank": rank, "event": event, "monotonic_seconds": value,
                 "grad_norm": .25, "update_successful": successful}
                for event, value, successful in (
                    ("train_step_started", 0, False), ("optimizer_finished", 9, False),
                    ("optimizer_finished", 12, True))]
        path = run / f"artifacts/timing/rank_{rank:02d}.jsonl"
        path.write_text(newline.join(json.dumps(row) for row in rows))
        logs.add(path)
    opened = []
    original_open = Path.open

    class LinesOnly:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __iter__(self):
            return iter(self.stream)

        def read(self, *args):
            pytest.fail("native logs must not be loaded wholesale")

        readlines = read

    def open_stream(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path in logs:
            opened.append(path)
            return LinesOnly(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_stream)
    output = tmp_path / "report"
    learning_report.export(run, output)
    assert len(opened) == len(logs) and set(opened) == logs
    with (output / "native_phases.csv").open() as stream:
        phases = list(csv.DictReader(stream))
    assert [row["process_stage"] for row in phases] == ["initial", "resume"]
    assert all(row["seconds"] == "17.4" for row in phases)
    with (output / "optimization.csv").open() as stream:
        metrics = list(csv.DictReader(stream))
    assert len(metrics) == 6  # Deduplicate within stages, not across resumptions.
    for stage in ("initial", "resume"):
        rows = [r for r in metrics if r["process_stage"] == stage]
        assert [r["train/grad_norm"] for r in rows] == ["0.25", "0.0", "0.25"]
        assert [r["logged_at"] for r in rows] == [stamp, stamp, "2026-10-18 01:01:00"]
    with (output / "rounds.csv").open() as stream:
        row, = csv.DictReader(stream)
    assert row["update_seconds"] == "12" and row["update_successful"] == "True"


@pytest.mark.parametrize("broken", ["timing", "optimizer"])
def test_streaming_does_not_hide_malformed_native_evidence(tmp_path, broken):
    run = report_inputs(tmp_path / "run")
    if broken == "timing":
        (run / "artifacts/timing/rank_00.jsonl").write_text('{"rollout":')
    else:
        directory = run / "artifacts/plan/learning/initial"
        directory.mkdir(parents=True)
        (directory / "stderr").write_text(
            "[2026-10-18 01:00:00] model.py:918 - step 0: "
            "{'train/step': 0, 'train/grad_norm': -1}\n")
    output = tmp_path / "report"
    with pytest.raises(ValueError):
        learning_report.export(run, output)
    assert not output.exists()
