import json
from pathlib import Path

import pytest

from behave.eval import followup as supplement
from tools.eval_review_trial import write_json


def test_retry_chain_keeps_all_sources_in_order(tmp_path):
    from behave.eval.report import rollout_sources
    first = tmp_path / (tmp_path.name + "_infra_retry")
    second = first / (first.name + "_infra_retry")
    write_json(first / "selection.json", {})
    write_json(second / "selection.json", {})
    assert rollout_sources(tmp_path) == [tmp_path, first, second]


def test_spending_archive_is_incremental_and_keeps_readonly_records(tmp_path):
    from tools.eval_campaign import archive_spending

    source, target = tmp_path / "local", tmp_path / "archive"
    write_json(source / "objects/first", {"amount": "1"})
    write_json(source / "spending/000001.json", {"sequence": 1})
    write_json(source / "spending_cache.json", {"spent": "1"})
    write_json(source / "owner.lock", {})
    archive_spending(source, target)
    original = target / "objects/first"
    original.chmod(0o444)
    identity = original.stat()
    (target / "spending_cache.json").chmod(0o444)

    write_json(source / "objects/second", {"amount": "2"})
    write_json(source / "spending/000002.json", {"sequence": 2})
    write_json(source / "spending_cache.json", {"spent": "3"})
    archive_spending(source, target)
    assert original.stat().st_ino == identity.st_ino
    assert original.stat().st_mtime_ns == identity.st_mtime_ns
    assert original.stat().st_mode == identity.st_mode
    assert json.loads((target / "objects/second").read_text()) == {"amount": "2"}
    assert json.loads((target / "spending_cache.json").read_text()) == {"spent": "3"}
    assert not (target / "owner.lock").exists()


def test_hosted_repair_resume_does_not_repeat_archived_parser_retry(tmp_path, monkeypatch):
    from tools import eval_campaign as campaign

    directory = tmp_path / "runs/sonnet_joint"
    retry = directory / "sonnet_joint_infra_retry"
    write_json(tmp_path / "hosted_runtime.json", {"state": "failed", "host": campaign.socket.gethostname(),
                                               "local_directory": str(tmp_path / "local")})
    write_json(tmp_path / "campaign.json", {"runs": [{"name": "sonnet_joint", "provider": "anthropic"},
                                                    {"name": "other", "provider": "anthropic"}]})
    for path in (directory, retry):
        write_json(path / "runtime.json", {"state": "archived"})
    write_json(retry / "selection.json", {})
    other = tmp_path / "runs/other"
    write_json(other / "runtime.json", {"state": "archived"})
    write_json(other / "other_infra_retry/runtime.json", {"state": "running"})
    write_json(other / "other_infra_retry/selection.json", {})
    monkeypatch.setattr(campaign, "recover_archive", lambda *_: None)
    monkeypatch.setattr(campaign, "archive_billing", lambda *_: None)
    observed = []

    def load(paths):
        observed.append(paths)
        code = ("model-unknown:anthropic-response-invalid" if paths == [directory]
                else "model-permanent:model-budget-exhausted")
        return {"task": {"result": {"submission": None, "judgment": None,
                                   "failure_code": code}}}

    monkeypatch.setattr("behave.eval.followup.load_outcomes", load)
    monkeypatch.setattr("tools.eval_review_trial.run", lambda *_: pytest.fail("no new model call"))
    assert campaign.repair_hosted(tmp_path, ("model-unknown:anthropic-response-invalid",)) == []
    assert observed == [[directory], [directory, retry], [other]]


def test_supplement_shares_one_new_account_and_reuses_original_selection(tmp_path, monkeypatch):
    from tools import eval_campaign as campaign
    write_json(tmp_path / "supplement.json", {"additional_budget_usd": "500", "runs": ["a", "b"]})
    write_json(tmp_path / "campaign.json", {"configuration": {"attempt": {"context_tokens": 131072}},
                                          "runs": [{"name": "a"}, {"name": "b"}]})
    for name in ("a", "b"):
        write_json(tmp_path / "runs" / name / "runtime.json", {"state": "archived"})
    write_json(tmp_path / "spending/original.json", {"spent": "999"})
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: str(local))
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    code = "model-permanent:model-budget-exhausted"
    outcomes = {"budget": {"result": {"submission": None, "judgment": None, "failure_code": code}},
                "submitted": {"result": {"submission": "rtl", "judgment": None, "failure_code": code}},
                "wrong": {"result": {"submission": "rtl", "judgment": {"rtl": {"score": 0}}, "failure_code": None}}}
    monkeypatch.setattr("behave.eval.followup.load_outcomes", lambda _: outcomes)

    def prepare(target, sources, *, failure_codes, spending):
        assert failure_codes == (code,)
        assert sources == [target.parent]
        assert spending.limit == "500" and spending.currency == "USD"
        write_json(target / "settings.json", {"score_on_submit": False, "budget_usd": spending.limit,
            "spending_directory": spending.directory, "spending_account": spending.account})
        write_json(target / "selection.json", {"rollout": ["budget"]})
        return {"rollout": ["budget"]}

    monkeypatch.setattr("behave.eval.followup.prepare", prepare)
    accounts = []

    def child(argv, log, stopped):
        config = json.loads((Path(argv[-1]) / "settings.json").read_text())
        assert not config["score_on_submit"]
        accounts.append((config["budget_usd"], config["spending_directory"], config["spending_account"]))
        write_json(local / "spending/fixture.json", {})
        write_json(Path(argv[-1]) / "runtime.json", {"state": "archived"})
        return 0

    monkeypatch.setattr(supplement, "run_child", child)
    monkeypatch.setattr("behave.agent.spending.SpendAccount.summary", lambda _: {"spent": "0"})
    result = campaign.supplement_hosted(tmp_path)
    assert result["state"] == "archived"
    assert all(row["tasks"] == ["budget"] for row in result["runs"])
    assert len(set(accounts)) == 1 and accounts[0][0] == "500"
    assert json.loads((tmp_path / "spending/original.json").read_text()) == {"spent": "999"}
    assert campaign.supplement_hosted(tmp_path) == result

    # A failure in final publication resumes the same account and only
    # publishes. Successful submissions must not become new paid attempts.
    state = json.loads((tmp_path / "supplement_runtime.json").read_text())
    write_json(tmp_path / "supplement_runtime.json", {**state, "state": "failed"})
    monkeypatch.setattr("behave.eval.followup.load_outcomes", lambda _: {
        "done": {"result": {"submission": "rtl", "judgment": None, "failure_code": None}}})
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: pytest.fail("must not allocate another budget account"))
    monkeypatch.setattr(supplement, "run_child", lambda *_: pytest.fail("must not rerun submitted candidates"))
    resumed = campaign.supplement_hosted(tmp_path)
    assert resumed["state"] == "archived"
    assert resumed["local_directory"] == str(local)
    assert resumed["runs"] == result["runs"]  # Replay the original selection, not a new retry wave.


def test_selected_supplement_arm_uses_live_shared_account_without_reconciling(tmp_path, monkeypatch):
    from tools import eval_campaign as campaign

    local = tmp_path / "local"
    local.mkdir()
    write_json(tmp_path / "supplement.json", {"additional_budget_usd": "500", "runs": ["a", "b"]})
    write_json(tmp_path / "campaign.json", {"configuration": {"attempt": {"context_tokens": 131072}},
                                          "runs": [{"name": "a"}, {"name": "b"}]})
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: str(local))
    plan = supplement._plan(tmp_path, json.loads((tmp_path / "supplement.json").read_text()),
                            json.loads((tmp_path / "campaign.json").read_text()),
                            Path(campaign.__file__).resolve().parents[1])
    state = {"state": "running", "host": campaign.socket.gethostname(),
             "local_directory": str(local), "budget_usd": "500", "account": plan["account"]}
    write_json(tmp_path / "supplement_runtime.json", state)
    write_json(tmp_path / "runs/a/runtime.json", {"state": "archived"})
    write_json(tmp_path / "runs/b/runtime.json", {"state": "running"})
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: pytest.fail("must retain the existing account"))
    monkeypatch.setattr("behave.eval.followup.load_outcomes", lambda _: {
        "pending": {"result": {"submission": None, "judgment": None,
                               "failure_code": "model-permanent:model-budget-exhausted"}}})
    def prepare(target, *_, spending, **kwargs):
        write_json(target / "settings.json", {"score_on_submit": False, "budget_usd": spending.limit,
            "spending_directory": spending.directory, "spending_account": spending.account})
        write_json(target / "selection.json", {"rollout": ["pending"]})
        return {"rollout": ["pending"]}

    monkeypatch.setattr("behave.eval.followup.prepare", prepare)
    monkeypatch.setattr("behave.agent.spending.SpendAccount.summary", lambda _: pytest.fail("other arms are live"))

    def child(argv, *_):
        config = json.loads((Path(argv[-1]) / "settings.json").read_text())
        assert config["spending_account"] == plan["account"]
        assert config["spending_directory"] == str(local / "spending")
        assert config["budget_usd"] == "500"
        write_json(Path(argv[-1]) / "runtime.json", {"state": "archived"})
        return 0

    monkeypatch.setattr(supplement, "run_child", child)
    assert campaign.supplement_hosted(tmp_path, run_name="a")["tasks"] == ["pending"]
    assert json.loads((tmp_path / "supplement_runtime.json").read_text()) == state
    with pytest.raises(ValueError, match="rollout still unfinished"):
        campaign.supplement_hosted(tmp_path, run_name="b")


def test_supplement_publishes_authorized_settings_through_real_preparation(tmp_path, publication, monkeypatch):
    from dataclasses import replace

    from test_eval_followup import followup_inputs, inventory

    from behave.agent.spending import SpendPolicy
    from behave.core.artifacts import BlobRef
    from behave.eval.followup import prepare
    from behave.task_execution.result import TaskResult
    from behave.task_execution.run_store import RunStore
    from tools import eval_campaign as campaign

    source = followup_inputs(tmp_path / "runs", publication)
    path = source / "run/report/000000.json"
    event = json.loads(path.read_text())
    row = event["payload"]["row"]
    store = RunStore(source / "run/candidates/fixture")
    result = TaskResult.from_bytes(store.objects.read(BlobRef.from_dict(row["result"])))
    code = "model-permanent:model-budget-exhausted"
    row["result"] = store.objects.put(replace(result, failure_code=code).to_bytes()).as_dict()
    write_json(path, event)
    write_json(source / "runtime.json", {"state": "archived"})
    write_json(tmp_path / "campaign.json", {"runs": [{"name": "source"}],
                                          "configuration": {"attempt": {"context_tokens": 131072}}})
    write_json(tmp_path / "supplement.json", {"runs": ["source"], "additional_budget_usd": "50"})
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(supplement, "mkdtemp", lambda **_: str(local))
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    monkeypatch.setattr("behave.agent.spending.SpendAccount.summary", lambda _: {"spent": "0"})
    before = inventory(source)

    def child(argv, *_):
        target = Path(argv[-1])
        settings = json.loads((target / "settings.json").read_text())
        plan = json.loads((target / "followup_preparation.json").read_text())
        assert settings == plan["settings"]  # No post-publication budget rewrite.
        assert settings["budget_usd"] == "50" and settings["spending_directory"] == str(local / "spending")
        binding = SpendPolicy(settings["spending_directory"], settings["spending_account"], "USD", "50", 131072)
        saved = inventory(target)
        assert prepare(target, [source], failure_codes=[code], spending=binding) == plan["selection"]
        assert inventory(target) == saved
        write_json(local / "spending/fixture.json", {})
        write_json(target / "runtime.json", {"state": "archived"})
        return 0

    monkeypatch.setattr(supplement, "run_child", child)
    assert campaign.supplement_hosted(tmp_path)["state"] == "archived"
    assert all((source / name).read_bytes() == value for name, value in before.items())
