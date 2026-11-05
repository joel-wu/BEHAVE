"""Package-owned recovery keeps recorded evidence and never dispatches work."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_ppa_campaign_inputs import input_plan

from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.ppa import campaign_recovery as recovery
from behave.ppa.campaign_inputs import freeze_plan, publish
from test_ppa_campaign import campaign  # noqa: F401


@pytest.fixture
def retained(tmp_path, publication):
    plan = input_plan(tmp_path, publication, pointer=True)
    previous = tmp_path / "previous"
    freeze_plan(plan, previous)
    return plan, previous, recovery.recovery_binding(plan, previous)


def files(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("ok", [False, True])
def test_selection_retains_old_files_and_records_same_decision_on_replay(tmp_path, retained, monkeypatch, ok):
    _, previous, binding = retained
    label = "fixture/r00"
    service = previous / "runs" / label / "search-service"
    outcome = {"ok": ok, "status": "exited", "cleanup": "complete", "returncode": 0 if ok else 2}
    publish(service / "requested.json", {"fixture": "already-dispatched"})
    publish(service / "service.json", outcome)
    before = files(previous)
    destination = tmp_path / "next"

    def forbidden(*args, **kwargs):
        raise AssertionError("recovery must not dispatch work or scan archive trees")

    with monkeypatch.context() as patch:
        patch.setattr("subprocess.run", forbidden)
        patch.setattr("subprocess.Popen", forbidden)
        patch.setattr(Path, "rglob", forbidden)
        result = recovery.selected_search(binding, label, destination, final_only=True)
        assert recovery.selected_search(binding, label, destination, final_only=True) == result
    assert result == {"label": label, "phase": "search", **outcome,
                      "search_directory": str(previous / "runs" / label / "search"),
                      "recovery": "reuse" if ok else "keep-failure"}
    receipt = parse_json((destination / "recovery.json").read_bytes())
    assert receipt["receipt"] == BlobRef.of((service / "service.json").read_bytes()).as_dict()
    assert receipt["action"] == result["recovery"]
    assert files(previous) == before


@pytest.mark.parametrize("cleanup", [None, "incomplete"])
def test_unsettled_or_unclean_attempt_does_not_publish_recovery(tmp_path, retained, cleanup):
    _, previous, binding = retained
    service = previous / "runs/fixture/r00/search-service"
    publish(service / "requested.json", {})
    if cleanup is not None:
        publish(service / "service.json", {"ok": False, "cleanup": cleanup})
    binding = {**binding, "wait_seconds": 0}
    before = files(previous)
    with pytest.raises(RuntimeError, match="unsettled|cleanup unconfirmed"):
        recovery.selected_search(binding, "fixture/r00", tmp_path / "next")
    assert files(previous) == before
    assert not (tmp_path / "next").exists()


def test_waits_for_existing_service_receipt_without_dispatch(tmp_path, retained, monkeypatch):
    _, previous, binding = retained
    service = previous / "runs/fixture/r00/search-service"
    publish(service / "requested.json", {})
    waited = []

    def finish_existing(seconds):
        waited.append(seconds)
        publish(service / "service.json", {"ok": True, "cleanup": "complete"})

    monkeypatch.setattr(recovery, "time", SimpleNamespace(monotonic=lambda: 0, sleep=finish_existing))
    result = recovery.selected_search(binding, "fixture/r00", tmp_path / "next")
    assert result["recovery"] == "reuse" and waited == [5]


@pytest.mark.parametrize("changed", ["plan.json", "interruption.json"])
def test_changed_binding_evidence_is_refused_before_writing(tmp_path, retained, changed):
    plan, previous, _ = retained
    publish(previous / "interruption.json", {
        "schema": "behave.ppa-campaign-stop/1", "step": "fixture.1", "cleanup": "complete"})
    binding = recovery.recovery_binding(plan, previous)
    path = previous / changed
    path.chmod(0o600)
    path.write_bytes(b"changed evidence")
    with pytest.raises(ValueError, match="changed"):
        recovery.selected_search(binding, "fixture/r00", tmp_path / "next")
    assert not (tmp_path / "next").exists()


def test_input_digest_is_checked_when_creating_binding(retained):
    plan, previous, _ = retained
    Path(plan["liberty"]).write_bytes(b"changed technology")
    with pytest.raises(ValueError, match="original campaign input changed"):
        recovery.recovery_binding(plan, previous)


def test_final_only_does_not_turn_fenced_stop_into_a_new_search(tmp_path, retained):
    plan, previous, _ = retained
    publish(previous / "interruption.json", {
        "schema": "behave.ppa-campaign-stop/1", "step": "fixture.1", "cleanup": "complete"})
    binding = recovery.recovery_binding(plan, previous)
    before = files(previous)
    with pytest.raises(RuntimeError, match="no model dispatched"):
        recovery.selected_search(binding, "fixture/r00", tmp_path / "next", final_only=True)
    decision = parse_json((tmp_path / "next/recovery.json").read_bytes())
    assert decision["action"] == "new-attempt"  # Eligibility is not dispatch.
    assert files(previous) == before
