"""One explicit retry wave per arm, including interrupted preparation."""

import json
import socket
from pathlib import Path

import pytest
from test_eval_followup import FAILURE, followup_inputs

from behave.agent.attempt import AttemptInterrupted
from behave.eval import followup as followup_module
from behave.eval import generation as generation_module
from behave.eval.report import rollout_sources
from behave.runtime.records import write_json


@pytest.fixture
def arm(tmp_path, publication, monkeypatch):
    root = tmp_path / "campaign"
    source = followup_inputs(root / "runs", publication)
    write_json(source / "runtime.json", {"state": "archived"})
    node = tmp_path / "node"
    node.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(node))
    monkeypatch.setattr("behave.runtime.records.signal.signal", lambda *_: None)
    return root, source


@pytest.mark.parametrize("boundary", ["receipt", "selection"])
def test_partial_preparation_is_reused_without_a_new_wave(arm, monkeypatch, boundary):
    _, source = arm
    publish = followup_module.publish_json

    def interrupted(path, value):
        publish(path, value)
        if path.name == ("followup_preparation.json" if boundary == "receipt" else "selection.json"):
            raise OSError("preparation interrupted")

    with monkeypatch.context() as fault:
        fault.setattr(followup_module, "publish_json", interrupted)
        with pytest.raises(OSError, match="preparation interrupted"), followup_module.retry_wave(source, failure_codes=[FAILURE]):
            pytest.fail("must not dispatch partial preparation")
    target = source / (source.name + "_infra_retry")
    assert rollout_sources(source) == [source, target]
    monkeypatch.setattr(followup_module, "load_outcomes", lambda *_: pytest.fail("must retain original selection"))
    with followup_module.retry_wave(source, failure_codes=[FAILURE]) as pending:
        assert pending[0] == target
        assert pending[1]["rollout"]
        assert (target / "settings.json").is_file()
    assert not (target / (target.name + "_infra_retry")).exists()


def test_another_request_cannot_take_over_a_pending_wave(arm):
    _, source = arm
    with followup_module.retry_wave(source, failure_codes=[FAILURE]) as pending:
        target, selection = pending
    with pytest.raises(ValueError, match="different follow-up"), followup_module.retry_wave(source, failure_codes=["other"]):
        pytest.fail("must not dispatch")
    assert json.loads((target / "selection.json").read_text()) == selection


def test_same_arm_cannot_schedule_two_waves_at_once(arm):
    _, source = arm
    with followup_module.retry_wave(source, failure_codes=[FAILURE]) as pending:
        assert pending is not None
        with pytest.raises(AttemptInterrupted, match="run-already-owned"), followup_module.retry_wave(source, failure_codes=[FAILURE]):
            pytest.fail("another owner cannot schedule")


def test_new_wave_does_not_inherit_the_original_paid_attempt_namespace(arm):
    _, source = arm
    settings = json.loads((source / "settings.json").read_text())
    write_json(source / "settings.json", {**settings, "attempt_namespace": "original-paid-attempt"})
    with followup_module.retry_wave(source, failure_codes=[FAILURE]) as (target, _):
        prepared = json.loads((target / "settings.json").read_text())
        assert prepared["attempt_namespace"] == str(target)
        assert prepared["generation_parameters"] == settings["generation_parameters"]
    with followup_module.retry_wave(source, failure_codes=[FAILURE]) as (resumed, _):
        assert resumed == target
        assert json.loads((target / "settings.json").read_text()) == prepared


def test_completed_submission_is_not_retried_again(arm):
    _, source = arm
    with followup_module.retry_wave(source, failure_codes=[FAILURE]) as (target, selection):
        old = selection["original_outcomes"]
        changed = {key: {**value, "result": {**value["result"], "failure_code": None,
                                            "submission": {"retained": True}}} for key, value in old.items()}
        write_json(target / "rejudged.json", changed)
        write_json(target / "runtime.json", {"state": "archived"})
    with followup_module.retry_wave(source, failure_codes=[FAILURE]) as pending:
        assert pending is None
    assert not (target / (target.name + "_infra_retry")).exists()


def test_preparation_receipt_is_visible_to_status_and_completion_barriers(arm, monkeypatch):
    from tools import eval_campaign as campaign

    root, source = arm
    write_json(root / "campaign.json", {"rollouts": 1, "runs": [{"name": source.name, "tasks": 1}]})
    monkeypatch.setattr(followup_module, "_publish_preparation", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError), followup_module.retry_wave(source, failure_codes=[FAILURE]):
        pytest.fail("must not dispatch")
    state = campaign.status(root)
    assert state["rollout_barrier_complete"] is False
    assert state["runs"][0]["infra_retry"]["state"] == "waiting"
    assert state["runs"][0]["infra_retry"]["tasks"] == 1
    with pytest.raises(ValueError, match="rollouts are not finished"):
        campaign.require_rollouts_finished(root)


@pytest.mark.parametrize("launcher", ["repair", "supplement"])
def test_real_hosted_caller_continues_same_worker_after_failure(arm, monkeypatch, launcher):
    from tools import eval_campaign as campaign

    root, source = arm
    write_json(root / "campaign.json", {"runs": [{"name": source.name, "provider": "openai", "tasks": 1}],
                                       "configuration": {"attempt": {"context_tokens": 131072}}})
    local = root / "local"
    local.mkdir()
    write_json(root / "hosted_runtime.json", {"state": "failed", "host": socket.gethostname(),
                                             "local_directory": str(local)})
    monkeypatch.setattr(campaign, "archive_billing", lambda *_: None)
    monkeypatch.setattr("behave.agent.spending.SpendAccount.summary", lambda _: {"spent": "0"})
    calls, allocated = [], []

    def worker(path, **_):
        calls.append(path)
        if len(calls) == 1:
            write_json(path / "kept.json", {"result": "first"})
            raise OSError("worker interrupted")
        assert json.loads((path / "kept.json").read_text()) == {"result": "first"}
        write_json(local / "spending/fixture.json", {})
        return 0

    monkeypatch.setattr(generation_module, "run", worker)
    if launcher == "repair":
        invoke = lambda: campaign.repair_hosted(root, (FAILURE,))
    else:
        from dataclasses import replace

        from behave.core.artifacts import BlobRef
        from behave.eval import followup as followup_module
        from behave.task_execution.result import TaskResult
        from behave.task_execution.run_store import RunStore

        event_path = source / "run/report/000000.json"
        event = json.loads(event_path.read_text())
        row = event["payload"]["row"]
        objects = RunStore(source / "run/candidates/fixture").objects
        result = TaskResult.from_bytes(objects.read(BlobRef.from_dict(row["result"])))
        row["result"] = objects.put(replace(result, failure_code="model-permanent:model-budget-exhausted").to_bytes()).as_dict()
        write_json(event_path, event)
        write_json(root / "supplement.json", {"runs": [source.name], "additional_budget_usd": "50"})

        def allocate(**_):
            allocated.append(local)
            return str(local)

        monkeypatch.setattr(followup_module, "mkdtemp", allocate)
        monkeypatch.setattr(followup_module, "run_child",
                            lambda argv, *_: followup_module.run_review(Path(argv[-1]), resume=True))
        invoke = lambda: campaign.supplement_hosted(root)
    with pytest.raises(OSError, match="worker interrupted"):
        invoke()
    target = rollout_sources(source)[-1]
    settings = (target / "settings.json").read_bytes()
    invoke()
    assert len(calls) == 2 and calls[0] == calls[1]
    assert (target / "settings.json").read_bytes() == settings
    assert json.loads((target / "runtime.json").read_text())["state"] == "archived"
    assert rollout_sources(source) == [source, target]
    assert not calls[0].exists()
    if launcher == "supplement":
        assert allocated == [local]
