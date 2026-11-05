"""A sealed generation report may survive interrupted observational exports."""

import json

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.eval.generation import reconcile_completed_generation
from behave.runtime.records import write_json
from behave.task_execution.run_store import RunStore


@pytest.fixture
def completed(tmp_path):
    output = tmp_path / "rollout"
    store = RunStore(output / "run")
    plan = store.objects.put(b"fixture plan")
    result = store.objects.put(b"fixture sealed result")
    row = {"run_key": "one", "status": "pending_judgment", "result": result.as_dict()}
    summary = {"plan": plan.as_dict(), "requested": 1, "candidates": [row]}
    ref = store.objects.put(canonical_json(summary))
    journal = store.journal("report")
    events = []
    for kind, payload in (("evaluation_started", {"plan": plan.as_dict()}),
                          ("evaluation_case", {"index": 0, "row": row}),
                          ("evaluation_finished", {"summary": ref.as_dict()})):
        events.append(journal.append(events, kind, payload))
    write_json(output / "settings.json", {"tasks": ["one"], "score_on_submit": False})
    write_json(output / "report.json", summary)
    write_json(tmp_path / "generation.json", {"state": "running", "seed": 3})
    fence = {"old_workers_stopped": True, "sources": [str(output.resolve())],
             "owner": {"job_step": "fixture.1"}, "verified_at": "fixture time",
             "report_head": BlobRef.of(events[-1].to_bytes()).as_dict()}
    write_json(tmp_path / "fence.json", fence)
    return tmp_path, fence


def test_completed_report_is_reused_without_resampling(completed):
    root, _ = completed
    before = (root / "rollout/report.json").read_bytes()
    result = reconcile_completed_generation(root, root / "fence.json")
    assert result["candidates_retained"] == 1 and result["regenerated"] == 0
    assert (root / "rollout/report.json").read_bytes() == before
    assert json.loads((root / "generation.json").read_text())["state"] == "completed"
    assert json.loads((root / "rollout/cleanup.json").read_text())["method"] == "externally_fenced_owner"


@pytest.mark.parametrize("change", ["unfenced", "different_source", "head", "summary", "unsealed"])
def test_reconciliation_rejects_unproven_completion(completed, change):
    root, fence = completed
    if change == "unfenced":
        fence["old_workers_stopped"] = False
    elif change == "different_source":
        fence["sources"] = ["/different"]
    elif change == "head":
        fence["report_head"] = BlobRef.of(b"different").as_dict()
    elif change == "summary":
        write_json(root / "rollout/report.json", {})
    else:
        store = RunStore(root / "rollout/run")
        journal = store.journal("report")
        journal.append(journal.load(verify_artifacts=False), "interrupted", {})
    write_json(root / "fence.json", fence)
    with pytest.raises(ValueError):
        reconcile_completed_generation(root, root / "fence.json")
    assert json.loads((root / "generation.json").read_text())["state"] == "running"
    assert not (root / "rollout/cleanup.json").exists()
