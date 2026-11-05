"""Faults at supplemental publication boundaries must not create new attempts."""

import json
from pathlib import Path

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.eval import followup as supplement
from behave.runtime import records
from behave.runtime.records import write_json


def read(path):
    return json.loads(path.read_text())


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    root, local = tmp_path / "campaign", tmp_path / "local"
    local.mkdir()
    write_json(root / "supplement.json", {"runs": ["a"], "additional_budget_usd": "10"})
    write_json(root / "campaign.json", {"runs": [{"name": "a"}],
                                      "configuration": {"attempt": {"context_tokens": 131072}}})
    write_json(root / "runs/a/runtime.json", {"state": "archived"})
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: str(local))
    code = "model-permanent:model-budget-exhausted"
    monkeypatch.setattr("behave.eval.followup.load_outcomes", lambda _: {
        "task": {"result": {"submission": None, "judgment": None, "failure_code": code}}})
    prepared, calls, summaries = [], [], []

    def prepare(target, sources, *, failure_codes, spending):
        assert failure_codes == (code,)
        prepared.append(target)
        selection = {"rollout": ["task"]}
        write_json(target / "selection.json", selection)
        write_json(target / "settings.json", {"tasks": ["task"], "budget_usd": spending.limit,
            "spending_directory": spending.directory, "spending_account": spending.account})
        return selection

    def child(argv, log, stopped):
        target = Path(argv[-1])
        calls.append(target)
        write_json(local / "spending/receipt.json", {"paid": "1"})
        write_json(target / "runtime.json", {"state": "archived"})
        return 0

    def summary(account):
        summaries.append(account.policy)
        return {"spent": "1"}

    monkeypatch.setattr("behave.eval.followup.prepare", prepare)
    monkeypatch.setattr(supplement, "run_child", child)
    monkeypatch.setattr(supplement.SpendAccount, "summary", summary)
    return root, local, prepared, calls, summaries


@pytest.mark.parametrize("boundary", [
    "intent.json", "result.json", "workers_finished.json", "supplement_billing.json", "archive",
])
def test_committed_boundaries_resume_without_reselection_or_repayment(campaign, monkeypatch, boundary):
    root, local, prepared, calls, summaries = campaign
    publish, archive = supplement.publish_json, supplement.archive_spending
    failed = False

    def commit(path, value):
        nonlocal failed
        publish(path, value)
        if path.name == boundary and not failed:
            failed = True
            raise OSError("fixture lost acknowledgment")

    def copy(source, destination):
        nonlocal failed
        archive(source, destination)
        if boundary == "archive" and not failed:
            failed = True
            raise OSError("fixture lost acknowledgment")

    monkeypatch.setattr(supplement, "publish_json", commit)
    monkeypatch.setattr(supplement, "archive_spending", copy)
    with pytest.raises(OSError, match="lost acknowledgment"):
        supplement.run_supplement(root)
    assert read(root / "supplement_runtime.json")["state"] == "failed"
    plan = (root / "supplement_plan.json").read_bytes()
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: pytest.fail("no second budget"))
    state = supplement.run_supplement(root)
    assert state["state"] == "archived" and state["runs"][0]["tasks"] == ["task"]
    assert len(prepared) == len(calls) == len(summaries) == 1
    assert (root / "supplement_plan.json").read_bytes() == plan
    assert read(root / "supplement_spending/receipt.json") == {"paid": "1"}
    assert local.is_dir()  # Archiving records does not authorize deleting account state.
    assert supplement.run_supplement(root) == state
    assert len(calls) == 1


def test_failed_worker_reuses_same_target_and_does_not_finalize_money(campaign, monkeypatch):
    root, _, prepared, calls, summaries = campaign
    child = supplement.run_child
    monkeypatch.setattr(supplement, "run_child", lambda *_: 7)
    assert supplement.run_supplement(root)["state"] == "failed"
    assert not summaries and not (root / "supplement_billing.json").exists()
    monkeypatch.setattr(supplement, "run_child", child)
    assert supplement.run_supplement(root)["state"] == "archived"
    assert len(prepared) == len(calls) == len(summaries) == 1
    assert prepared == calls


@pytest.mark.parametrize("damage", ["budget", "manifest", "lost-account", "owner", "settings", "intent"])
def test_changed_plan_or_lost_local_state_blocks_redispatch(campaign, monkeypatch, damage):
    root, local, _, calls, _ = campaign
    monkeypatch.setattr(supplement, "run_child", lambda *_: 7)
    supplement.run_supplement(root)
    if damage == "budget":
        write_json(root / "supplement.json", {"runs": ["a"], "additional_budget_usd": "100"})
    elif damage == "manifest":
        write_json(root / "campaign.json", {**read(root / "campaign.json"), "changed": True})
    elif damage == "lost-account":
        local.rename(local.with_name("retained-account"))
    elif damage == "owner":
        write_json(local / "supplement_owner.json", {"root": "different"})
    elif damage == "settings":
        write_json(root / "runs/a/a_infra_retry/settings.json", {"tasks": ["another"]})
    else:
        intent = root / "supplement_controller/arms/a/intent.json"
        write_json(intent, {**read(intent), "target": str(root / "runs/a/../other")})
    monkeypatch.setattr(supplement, "run_child", lambda *_: pytest.fail("invalid inputs must not dispatch"))
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: pytest.fail("do not recreate lost account"))
    with pytest.raises(ValueError):
        supplement.run_supplement(root)
    assert not calls


def test_legacy_active_account_is_not_adopted_from_a_status_label(campaign):
    root, local, _, calls, _ = campaign
    write_json(root / "supplement_runtime.json", {"state": "failed", "local_directory": str(local)})
    with pytest.raises(ValueError, match="legacy"):
        supplement.run_supplement(root)
    assert not calls and not (root / "supplement_plan.json").exists()


def test_controller_owner_blocks_duplicate_launch(campaign):
    root, _, _, calls, _ = campaign
    with JournalDirectory(root / "supplement_controller").owner():
        with pytest.raises(AttemptInterrupted, match="run-already-owned"):
            supplement.run_supplement(root)
    assert not calls and not (root / "supplement_plan.json").exists()


def test_finalization_requires_exclusive_arm_ownership(campaign, monkeypatch):
    root, _, _, calls, summaries = campaign
    original = supplement._finalize

    def contested(root, plan, results):
        with JournalDirectory(root / "runs/a/retry_control").owner():
            return original(root, plan, results)

    monkeypatch.setattr(supplement, "_finalize", contested)
    with pytest.raises(AttemptInterrupted, match="run-already-owned"):
        supplement.run_supplement(root)
    assert len(calls) == 1 and summaries == []
    assert not (root / "supplement_billing.json").exists()
    monkeypatch.setattr(supplement, "_finalize", original)
    assert supplement.run_supplement(root)["state"] == "archived"
    assert len(calls) == len(summaries) == 1


@pytest.mark.parametrize("damage", ["conflict", "source-link", "destination-link", "root-link"])
def test_spending_publication_rejects_conflicts_and_symlinks(tmp_path, damage):
    source, target, outside = (tmp_path / name for name in ("source", "target", "outside"))
    write_json(source / "objects/receipt", {"amount": "1"})
    write_json(outside / "receipt", {"amount": "9"})
    target.mkdir()
    if damage == "conflict":
        write_json(target / "objects/receipt", {"amount": "2"})
    elif damage == "source-link":
        (source / "escape").symlink_to(outside, target_is_directory=True)
    elif damage == "destination-link":
        (target / "objects").symlink_to(outside, target_is_directory=True)
    else:
        target = tmp_path / "target-link"
        target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        records.archive_spending(source, target)
    assert read(outside / "receipt") == {"amount": "9"}
    if damage == "conflict":
        assert read(target / "objects/receipt") == {"amount": "2"}
