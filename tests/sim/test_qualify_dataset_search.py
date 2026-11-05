"""Search qualification is scheduling/reporting, never another score policy."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.qualify_dataset_search import inventory, run, select_tasks, summarize


def test_shards_cover_exact_selected_tasks_without_duplicates():
    tasks = [f"task-{index}" for index in range(11)]
    groups = [select_tasks(tasks, exclude=["task-2"], shard=i, shards=3) for i in range(3)]
    assert len({task for group in groups for task in group}) == sum(map(len, groups)) == 10
    assert set().union(*map(set, groups)) == set(tasks) - {"task-2"}
    assert select_tasks(tasks, include=["task-7", "task-1"]) == ["task-1", "task-7"]
    with pytest.raises(ValueError, match="outside"):
        select_tasks(tasks, include=["unknown"])
    with pytest.raises(ValueError, match="shard"):
        select_tasks(tasks, shard=3, shards=3)


def test_summary_keeps_score_and_search_separate(tmp_path):
    assert summarize(tmp_path)["decision"] is None
    (tmp_path / "measurement.json").write_text(json.dumps({
        "status": "evaluated", "decision": {"passed": True},
        "search_observation": {"exercised": False, "directions": [
            {"state": "needs-review", "provider_refusal": "missing-source-query"}]}}))
    result = summarize(tmp_path)
    assert result["decision"] == {"passed": True}
    assert result["search"]["directions"][0]["state"] == "needs-review"
    assert not result["search"]["exercised"]


def test_summary_preserves_returned_score_but_not_missing_diagnostic_acceptance(tmp_path):
    (tmp_path / "measurement.json").write_text(json.dumps({
        "status": "evaluated", "decision": {"passed": True},
        "diagnostics": {"status": "running"}}))
    (tmp_path / "worker.json").write_text(json.dumps({"status": "timed-out"}))
    result = summarize(tmp_path)
    assert result["decision"] == {"passed": True}
    assert result["worker_status"] == "timed-out"
    assert result["diagnostics"] == {"status": "running"}
    assert result["search"] is None


def test_monitor_inventory_counts_each_task_goal_once_not_query_acceptance(tmp_path, monkeypatch):
    from behave.sim import api

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / "plan.json").write_text(json.dumps({
        "tasks": ["rv", "sampled", "settled", "content"], "snapshot": "unchanged-task-bytes"}))

    def configuration(path):
        sampled = path.name == "sampled"
        settled = path.name == "settled"
        clock = None if settled else "clk"
        # Content matching has no registered finite product, so its goals keep no bindings.
        matching = api.Matching.CONTENT if path.name == "content" else api.Matching.ORDERED
        return api.TaskContract((
            api.Endpoint("in", api.Direction.IN,
                         (api.Field("data", api.FiniteType(8), "in_data"),),
                         api.settled("in") if settled else
                         api.sampled_edge("in") if sampled else
                         api.ready_valid("in", "in_valid", "in_ready"),
                         clock, kind=api.Kind.TRANSACTION),
            api.Endpoint("out", api.Direction.OUT,
                         (api.Field("data", api.FiniteType(8), "out_data"),),
                         api.settled("out") if settled else
                         api.sampled_edge("out") if sampled else
                         api.ready_valid("out", "out_valid", "out_ready"),
                         clock, kind=api.Kind.TRANSACTION, answers_per_transaction=1,
                         matching=matching),
        ), () if settled else (api.ClockDomain("clk", "clk", "rst"),)), None

    monkeypatch.setattr("tools.measure_search_score.task_configuration", configuration)
    output = tmp_path / "inventory"
    summary = inventory(campaign, output)
    rows = json.loads((output / "goals.json").read_text())
    assert summary["tasks_planned"] == summary["counts"]["tasks_inspected"] == 4
    assert summary["counts"]["tasks_missing_local_bindings"] == 1
    assert summary["counts"]["tasks_all_local_bindings_present"] == 3
    assert summary["counts"]["tasks_missing_exact_bindings"] == 1
    assert summary["counts"]["tasks_all_monitor_bindings_present"] == 3
    assert len(rows) == len({(r["task"], r["goal"]) for r in rows})
    # Settled endpoints now have a registered exact control table.
    assert all(r["local_binding"] and r["exact_binding"]
               for r in rows if r["task"] in ("rv", "sampled", "settled"))
    unbound = [r for r in rows if r["task"] == "content"]
    assert unbound and all(r["local_binding"] is None and r["exact_binding"] is None
                           for r in unbound)
    assert "no source catalogue, query" in summary["scope"]
    assert summary["task_snapshot"] == "unchanged-task-bytes"


def test_monitor_inventory_keeps_failed_contracts_out_of_accepted_counts(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / "plan.json").write_text(json.dumps({"tasks": ["broken"], "snapshot": "old"}))

    def broken(_path):
        raise ValueError("missing contract declaration")

    monkeypatch.setattr("tools.measure_search_score.task_configuration", broken)
    output = tmp_path / "inventory"
    summary = inventory(campaign, output)
    assert summary["counts"] == {"contract_errors": 1}
    assert json.loads((output / "goals.json").read_text()) == []
    assert json.loads((output / "tasks.json").read_text()) == [{
        "task": "broken", "status": "contract-error",
        "error": "ValueError: missing contract declaration"}]


@pytest.mark.parametrize("replay_workers", [None, 1])
def test_batch_reuses_scheduler_and_worker_and_exports_both_artifact_receipts(
        tmp_path, monkeypatch, replay_workers):
    from behave.agent import cpu_resources
    from behave.isolation import service

    campaign = tmp_path / "campaign"
    (campaign / "snapshot").mkdir(parents=True)
    (campaign / "snapshot" / "unchanged.txt").write_text("retained input")
    (campaign / "plan.json").write_text(json.dumps({"tasks": ["data/problems/test/task"], "snapshot": "frozen"}))
    cpus = tuple(sorted(cpu_resources.os.sched_getaffinity(0))[:2])
    monkeypatch.setattr(cpu_resources, "allocated_cpus", lambda count: cpus[:count])
    # Keep the test's scratch inside its fixture, without removing real batches.
    monkeypatch.setattr("tools.qualify_dataset_search.tempfile.mkdtemp",
                        lambda **kw: str(tmp_path / "local"))
    launches = []

    def execute(argv, **options):
        launches.append((argv, options))
        directory = Path(argv[5])
        directory.mkdir(parents=True)
        assert argv[-1] == "--default-baseline"
        assert not {"--probe-budget", "--search-seconds", "--analysis"} & set(argv)
        assert len(cpu_resources.current_cpu_lease().cpus) == 1
        assert options["deadline"] is None and options["output_bytes"] is None
        assert options["environment"].get("BEHAVE_REPLAY_WORKERS") == (
            None if replay_workers is None else str(replay_workers))
        (directory / "measurement.json").write_text(json.dumps({"status": "evaluated", "decision": {"passed": True}}))
        (directory / "worker.json").write_text(json.dumps({"status": "completed"}))
        return service.ServiceResult("exited", 0, b"full stdout", b"full stderr", .01)

    monkeypatch.setattr(service, "run_service", execute)
    args = SimpleNamespace(campaign=campaign, output=tmp_path / "out", include=None, exclude=None,
                           shard=0, shards=1, workers=2, cpus=2, artifacts=["rtl", "behavior"],
                           replay_workers=replay_workers)
    assert run(args)["completed"] == 2
    assert len(launches) == 2
    stage = json.loads((args.output / "stage.json").read_text())
    assert stage["replay_workers"] == replay_workers
    resources = json.loads((args.output / "resources.json").read_text())
    assert resources["active"] == resources["queued"] == resources["retired"] == 0
    rows = json.loads((args.output / "results.json").read_text())
    assert {row["artifact"] for row in rows} == {"rtl", "behavior"}
    assert all(row["cleanup"] == "complete" and row["decision"]["passed"] for row in rows)
    assert (campaign / "snapshot" / "unchanged.txt").read_text() == "retained input"
