"""Saved pipeline evidence works without a checkout or an execution backend."""

import json

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json
from behave.eval import report as report_module
from behave.runtime.records import write_json
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
from behave.task_execution.result import TaskResult


def pipeline_inputs(root):
    """Small native and external archives, including absent/unpriced evidence."""
    directory = root / "runs/fixture_joint_sim"
    objects = directory / "run/objects"
    objects.mkdir(parents=True)

    def put(value):
        data = canonical_json(value)
        ref = BlobRef.of(data)
        (objects / ref.sha256).write_bytes(data)
        return ref

    identity = put({"fixture": "identity"})
    head = None
    for outcome, code in (("failure", "context-input-full"), ("reply", None)):
        head = put({"parent": None if head is None else head.as_dict(), "kind": "model_settled",
                    "payload": {"call_id": outcome, "outcome": outcome,
                                "record": put({"charge": None, "code": code}).as_dict(),
                                "raw": put({}).as_dict()}})
    accounting = put({"heads": {"provider": head.as_dict()}})
    passed = ComponentJudgment(1, "passed")
    results = [
        TaskResult("a", identity, identity, "a", identity, "final_action", identity,
                   SubmissionJudgment(identity, passed, passed, identity), None, None, accounting),
        TaskResult("b", identity, identity, "b", identity, "stopped", None,
                   None, None, None, accounting),
        TaskResult("c", identity, identity, "c", None, None, None,
                   None, "solver", "fixture-interrupted", accounting),
    ]
    cases = [{"task_id": result.task_id, "result": put(result.as_dict()).as_dict()}
             for result in results] + [{"task_id": "d", "result": None}]
    write_json(directory / "runtime.json", {"state": "archived"})
    write_json(directory / "run/judgments/named/summary.json", {"candidates": cases})
    # An unrelated scoring publication must never be selected implicitly.
    write_json(directory / "run/judgments/newer/summary.json", {"candidates": []})
    write_json(directory / "report.json", {"candidates": [{
        "task_id": "a", "run_key": "a-joint", "billing": {
            "known_estimates_by_currency": {"USD": "0.02"}, "unpriced_dispatches": 2,
            "unresolved_dispatches": 1, "accounting": accounting.as_dict()}}]})
    lineage = {task: {"task": task, "revision": identity.as_dict()} for task in "abcd"}
    panel = {"tasks": list(lineage), "lineage": lineage, "records": []}
    write_json(root / "panel.json", panel)
    write_json(root / "task_lineage.json", list(lineage.values()))
    write_json(root / "configuration.json", {
        "models": {"fixture": {}}, "workers": {"joint_sim": 1}, "judgment": "named"})
    external = root / "external"
    write_json(external / "runtime.json", {"state": "archived"})
    write_json(external / "rollout.json", [{
        "task": "a", "status": "no_submission", "cost": {"USD": "0.07"},
        "unpriced_dispatches": 0, "unresolved_dispatches": 0}])
    write_json(external / "judgments/named/summary.json", [{"task": "a", "status": "no_submission"}])
    return root


def inventory(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_named_native_and_external_archives_are_read_only(tmp_path, monkeypatch):
    root = pipeline_inputs(tmp_path)
    before = inventory(root)
    panel = json.loads((root / "panel.json").read_bytes())

    def forbidden(*args, **kwargs):
        pytest.fail("reporting must not dispatch a process or write a record")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("behave.runtime.records.write_json", forbidden)
    merged = report_module.merge_campaign(panel, root)
    assert panel["records"] == []
    rows = {row["task"]: row for row in merged["records"]}
    assert [rows[task]["rtl"]["score"] for task in "abcd"] == [1, 0, None, None]
    assert rows["c"]["rtl"]["code"] == "fixture-interrupted"
    assert rows["d"]["rtl"]["status"] == "pending"
    assert all(row["judgment"] == "named" for row in rows.values())
    bill = report_module.archived_cost(rows["a"], {})
    assert bill["recorded_usd"] == "0.02"
    assert bill["unknown_cost_dispatches"] == 1
    assert bill["unpriced_dispatches"] == 2 and bill["unresolved_dispatches"] == 1
    assert [event["category"] for event in bill["unpriced_events"]] == ["unpriced", "local_context_rejection"]
    external = report_module.archived_arm(root / "external", "fixture", "mage", "named", panel["lineage"])
    assert external[0]["rtl"]["score"] == 0
    assert report_module.archived_cost(external[0], {})["recorded_usd"] == "0.07"
    assert inventory(root) == before


@pytest.mark.parametrize("state", [None, "running", "failed"])
def test_unpublished_archive_never_requires_summary_or_objects(tmp_path, state):
    if state is not None:
        write_json(tmp_path / "runtime.json", {"state": state})
    assert report_module.archived_arm(tmp_path, "fixture", "joint_sim", "named", {}) == []


def test_missing_named_summary_is_not_replaced_by_another_publication(tmp_path):
    root = pipeline_inputs(tmp_path)
    with pytest.raises(FileNotFoundError):
        report_module.archived_arm(root / "runs/fixture_joint_sim", "fixture", "joint_sim", "absent", {})


def test_recovery_cannot_replace_an_unrelated_ancestor(tmp_path):
    root = pipeline_inputs(tmp_path)
    panel = report_module.merge_campaign(json.loads((root / "panel.json").read_bytes()), root)
    write_json(root / "configuration.json", {
        "models": {}, "workers": {}, "judgment": "named", "recoveries": [{
            "model": "fixture", "method": "joint_sim", "run_directory": "retry",
            "replaces": "unrelated", "tasks": ["a"], "reason": "fixture retry"}]})
    with pytest.raises(ValueError, match="different ancestor"):
        report_module.merge_campaign(panel, root)


def test_native_result_revision_and_content_remain_verified(tmp_path):
    root = pipeline_inputs(tmp_path)
    directory = root / "runs/fixture_joint_sim"
    panel = json.loads((root / "panel.json").read_bytes())
    lineage = panel["lineage"]
    wrong = {**lineage, "a": {"revision": BlobRef.of(b"other").as_dict()}}
    with pytest.raises(ValueError, match="revision differs"):
        report_module.archived_arm(directory, "fixture", "joint_sim", "named", wrong)
    summary = json.loads((directory / "run/judgments/named/summary.json").read_bytes())
    ref = summary["candidates"][0]["result"]
    (directory / "run/objects" / ref["sha256"]).write_bytes(b"!" * ref["size_bytes"])
    with pytest.raises(RecordError, match="digest differs"):
        report_module.archived_arm(directory, "fixture", "joint_sim", "named", lineage)


@pytest.mark.parametrize("fault", ["tasks", "revision"])
def test_campaign_identity_mismatch_is_not_silently_merged(tmp_path, fault):
    root = pipeline_inputs(tmp_path)
    panel = json.loads((root / "panel.json").read_bytes())
    if fault == "tasks":
        panel["tasks"].pop()
    else:
        panel["lineage"]["a"]["revision"] = BlobRef.of(b"other").as_dict()
    with pytest.raises(ValueError, match="different task sets|revision differs"):
        report_module.merge_campaign(panel, root)


@pytest.mark.parametrize("fault", ["currency", "unpriced_count"])
def test_billing_mismatch_does_not_become_zero_cost(tmp_path, fault):
    root = pipeline_inputs(tmp_path)
    directory = root / "runs/fixture_joint_sim"
    report = json.loads((directory / "report.json").read_bytes())
    billing = report["candidates"][0]["billing"]
    if fault == "currency":
        billing["known_estimates_by_currency"] = {"EUR": "0.02"}
    else:
        billing["unpriced_dispatches"] += 1
    write_json(directory / "report.json", report)
    with pytest.raises(ValueError, match="USD accounting|dispatch audit differ"):
        report_module.archived_cost({"source_run": str(directory), "setting": "joint_sim", "task": "a"}, {})
