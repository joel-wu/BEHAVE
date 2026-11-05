from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.pipeline_campaign import arm_directory, arms, settings_for, slurm_command, wait_until


def test_review_entry_keeps_its_explicit_validation_gate(tmp_path, monkeypatch):
    from tools import pipeline_review_trial

    calls = []
    monkeypatch.setattr(pytest, 'main', lambda args: calls.append(args) or 0)
    assert pipeline_review_trial.worker('validate', tmp_path) == 0
    tests = [name for name in calls[0] if name.startswith('tests/')]
    assert tests == ['tests/infrastructure/test_external_pipelines.py',
                     'tests/infrastructure/test_solver_sim.py',
                     'tests/infrastructure/test_sim_tool.py',
                     'tests/infrastructure/test_eval_review_trial.py']


def configuration():
    return {
        "models": {"terra": {"model": "gpt-5.6-terra"}, "deepseek": {"model": "deepseek-flash"}},
        "workers": {"joint_sim": 24, "mage": 32, "chipmate": 32},
        "budget_usd": "500", "account": "new-campaign", "spending_directory": "/tmp/shared/spending",
        "cpus_per_arm": 24, "judge_workers": 6, "judge_cpu_capacity": 4,
        "tasks": ["task"], "release": {"sha256": "release"}, "task_objects": "/published/inputs",
        "allocation": 27421,
    }


@pytest.mark.parametrize('review', [False, True], ids=['campaign', 'review'])
def test_launch_preserves_commands_and_observes_existing_owners(tmp_path, monkeypatch, review):
    from behave.runtime import jobs
    from behave.runtime.records import write_json
    from tools import dataset_validation_job as legacy
    from tools import pipeline_campaign, pipeline_review_trial

    module = pipeline_review_trial if review else pipeline_campaign
    config = {**configuration(), 'cpus': 37, 'methods': ['joint'], 'source_snapshot': 'retained-snapshot'}
    if not review:
        config['models'] = {'terra': config['models']['terra']}
        config['workers'] = {'joint': 24}
    write_json(tmp_path / 'configuration.json', config)
    calls = []

    def capture(path, argv, **options):
        calls.append((path, argv, options))
        write_json(path / 'launch.json', {'argv': argv})
        return {'state': 'submitted'}

    monkeypatch.setattr(jobs, 'submit', capture)
    monkeypatch.setattr(legacy, 'submit', lambda *a, **k: pytest.fail('not a validation job'))
    monkeypatch.setattr('behave.core.process_limits.configure_worker_process_limit', lambda: None)
    observed = []
    monkeypatch.setattr(jobs, 'status_of', lambda path: observed.append(path) or {'state': 'failed'})
    module.launch(tmp_path)
    module.launch(tmp_path)
    assert len(calls) == 1 and observed == [calls[0][0]]
    path, argv, options = calls[0]
    assert options['cwd'] == legacy.REPO and options['wall_seconds'] is None
    assert 'cleanup_seconds' not in options  # Keep the shared default.
    if review:
        assert path == tmp_path / 'control'
        assert '--cpus-per-task=37' in argv and '--jobid=27421' in argv and '--gres=none' in argv
        assert str(tmp_path / 'snapshot') in argv
        assert options['metadata']['methods'] == ['joint']
    else:
        assert path == tmp_path / 'runs/terra_joint/control'
        assert str(tmp_path / 'retained-snapshot') in argv
        assert argv[-2:] == ['terra', 'joint']
        assert options['metadata']['cpus'] == 24 and options['metadata']['allocation'] == 27421


def test_models_share_money_not_provider_settings():
    config = configuration()
    for model, data in config["models"].items():
        original = {"model": data["model"], "generation_parameters": {"model-specific": model},
                    "budget_usd": "old", "attempt": {"max_turns": 60, "context_tokens": 131072},
                    "retry": {"old": True}, "cases": ["old"]}
        result = settings_for(config, model, original)
        assert result["budget_usd"] == "500"
        assert result["spending_account"] == "new-campaign"
        assert result["spending_directory"] == "/tmp/shared/spending"
        assert result["generation_parameters"] == {"model-specific": model}
        assert result["attempt"] == original["attempt"]
        assert not result["score_on_submit"] and "retry" not in result and "cases" not in result
        assert original["budget_usd"] == "old"
    assert len(arms(config)) == 6


def test_wrong_model_is_not_silently_substituted():
    with pytest.raises(ValueError, match="wrong model"):
        settings_for(configuration(), "terra", {"model": "gpt-5.6-luna"})


def test_cpu_steps_do_not_overlap_or_claim_training_gpus():
    command = slurm_command(Path("/campaign"), configuration(), "terra", "mage")
    assert "--cpus-per-task=24" in command
    assert "--jobid=27421" in command
    assert "--gres=none" in command and "--exact" in command
    assert "--overlap" not in command
    assert command[-2:] == ["terra", "mage"]


def test_not_before_survives_early_wakeup():
    target = datetime(2026, 9, 18, 9, 11, tzinfo=UTC)
    now = [target.timestamp() - 65]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        now[0] += 5 if len(waits) == 1 else seconds
    wait_until(target.isoformat(), clock=lambda: now[0], sleep=sleep)
    assert now[0] == target.timestamp()
    assert waits == [30, 30, 30]


def test_elapsed_delay_does_not_sleep():
    calls = []
    wait_until("2026-09-18T09:11:00+00:00", clock=lambda: 9999999999, sleep=calls.append)
    assert calls == []


def test_retry_keeps_old_arm_and_selects_versioned_snapshot():
    config = configuration()
    config.update(source_snapshot="code/recovery1/snapshot", run_directories={"terra_mage": "terra_mage_retry1"})
    root = Path("/campaign")
    assert arm_directory(root, config, "terra", "mage") == root / "runs/terra_mage_retry1"
    assert arm_directory(root, config, "terra", "joint_sim") == root / "runs/terra_joint_sim"
    assert str(root / "code/recovery1/snapshot") in slurm_command(root, config, "terra", "mage")


def test_delayed_dispatch_configures_owned_client_before_waiting(tmp_path, monkeypatch):
    from behave.core import process_limits
    from tools import pipeline_campaign
    from tools.eval_review_trial import write_json

    config = configuration()
    config["models"]["deepseek"]["not_before"] = "2026-09-18T10:11:00+00:00"
    write_json(tmp_path / "configuration.json", config)
    events = []
    monkeypatch.setattr(process_limits, "configure_worker_process_limit", lambda: events.append("limits"))
    monkeypatch.setattr(pipeline_campaign, "wait_until", lambda timestamp: events.append(timestamp))
    monkeypatch.setattr(pipeline_campaign.os, "execvp", lambda command, args: events.append(command))
    pipeline_campaign.dispatch(tmp_path, "deepseek", "mage")
    assert events == ["limits", "2026-09-18T10:11:00+00:00", "srun"]


def test_settlement_waits_for_declared_recovery(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from behave.agent import spending
    from behave.runtime import records as records_module
    from tools import pipeline_campaign
    from tools.eval_review_trial import write_json

    config = configuration()
    config["recoveries"] = [{"run_directory": "terra_mage_transport1"}]
    write_json(tmp_path / "configuration.json", config)
    for model, method in arms(config):
        write_json(arm_directory(tmp_path, config, model, method) / "runtime.json", {"state": "archived"})
    recovery = tmp_path / "runs/terra_mage_transport1/runtime.json"
    write_json(recovery, {"state": "rollout"})
    with pytest.raises(ValueError, match="active arms"):
        pipeline_campaign.settle(tmp_path)
    write_json(recovery, {"state": "archived"})
    archived = []
    monkeypatch.setattr(spending, "SpendAccount", lambda _: SimpleNamespace(summary=lambda: {"spent": "1"}))
    monkeypatch.setattr(records_module, "archive_spending", lambda source, target: archived.append((source, target)))
    pipeline_campaign.settle(tmp_path)
    assert archived == [(Path(config["spending_directory"]), tmp_path / "spending")]


def test_recovery_selects_only_named_failure_without_submission(tmp_path):
    from tools.eval_review_trial import write_json
    from tools.pipeline_campaign import retry_tasks

    write_json(tmp_path / "runtime.json", {"state": "archived"})
    rows = [
        {"task": "transport", "error": "model-unknown:deepseek-exchange-interrupted", "submission": None},
        {"task": "context", "error": "external-model-context_limit", "submission": None},
        {"task": "wrong", "error": None, "submission": {"rtl": "saved"}},
        {"task": "submitted_transport", "error": "model-unknown:deepseek-exchange-interrupted",
         "submission": {"rtl": "saved"}},
    ]
    write_json(tmp_path / "rollout.json", rows)
    codes = ("model-unknown:deepseek-exchange-interrupted",)
    assert retry_tasks(tmp_path, "mage", codes) == ["transport"]
    write_json(tmp_path / "runtime.json", {"state": "archiving"})
    with pytest.raises(ValueError, match="finish archiving"):
        retry_tasks(tmp_path, "mage", codes)


@pytest.mark.parametrize("method", ("joint", "rtl_only", "joint_sim", "rtl_sim"))
def test_native_recovery_reuses_existing_failure_selector(tmp_path, monkeypatch, method):
    from behave.eval import followup as eval_review_retry
    from tools.eval_review_trial import write_json
    from tools.pipeline_campaign import retry_tasks

    write_json(tmp_path / "runtime.json", {"state": "archived"})
    outcomes = {"transport": {"result": {"judgment": None, "submission": None,
                                       "failure_code": "transport"}},
                "context": {"result": {"judgment": None, "submission": None,
                                       "failure_code": "context_limit"}}}
    calls = []
    monkeypatch.setattr(eval_review_retry, "load_outcomes", lambda sources: calls.append(sources) or outcomes)
    assert retry_tasks(tmp_path, method, ("transport",)) == ["transport"]
    assert calls == [[tmp_path]]


def test_prepared_recovery_shares_account_and_keeps_declared_ancestor(tmp_path, monkeypatch):
    import json

    from behave.pipelines import configuration as pipeline_configuration
    from tools.eval_review_trial import prepare as prepare_solver
    from tools.eval_review_trial import write_json
    from tools.pipeline_campaign import prepare_recovery

    config = configuration()
    config.update(repository=str(tmp_path), source_snapshot="frozen-source")
    write_json(tmp_path / "configuration.json", config)
    (tmp_path / "judge_policy.json").write_text('{"frozen": true}\n')
    source = tmp_path / "runs/deepseek_mage"
    original = {"model": "deepseek-flash", "attempt_namespace": "old-paid-call-namespace",
                "generation_parameters": {"provider-specific": True},
                "attempt": {"max_turns": 60, "context_tokens": 131072}}
    method = {"method": "mage", "options": {"preserve-original": 20}, "provider_sampling": "provider_default"}
    write_json(source / "settings.json", original)
    write_json(source / "pipeline.json", method)
    write_json(source / "runtime.json", {"state": "archived"})
    write_json(source / "rollout.json", [{"task": "task", "submission": None, "error": "transport"}])
    calls = []

    def prepare_external(output, template, name, *, workers, options, provider_sampling):
        calls.append((name, options, provider_sampling))
        prepare_solver(output, template, setting="rtl_only", workers=workers, rollout_only=True)
        write_json(output / "pipeline.json", {"would-add-current-default": True})

    monkeypatch.setattr(pipeline_configuration, "prepare", prepare_external)
    result = prepare_recovery(tmp_path, "deepseek", "mage", "deepseek_mage", "ds_recovery1",
                              failure_codes=("transport",), cpus=4, workers=2)
    child, output = Path(result["campaign"]), Path(result["output"])
    settings = json.loads((output / "settings.json").read_text())
    assert settings["attempt_namespace"] == str(output.resolve())
    assert settings["spending_account"] == config["account"] and settings["budget_usd"] == "500"
    assert settings["spending_directory"] == config["spending_directory"]
    assert settings["release"] == config["release"] and settings["tasks"] == ["task"]
    assert settings["generation_parameters"] == original["generation_parameters"]
    assert settings["workers"] == settings["model_workers"] == 2
    assert (child / "judge_policy.json").read_bytes() == (tmp_path / "judge_policy.json").read_bytes()
    assert json.loads((output / "pipeline.json").read_text()) == method
    assert calls == [("mage", method["options"], "provider_default")]
    declaration = json.loads((tmp_path / "configuration.json").read_text())["recoveries"][0]
    assert declaration["tasks"] == ["task"] and declaration["replaces"] == "deepseek_mage"
    assert json.loads((source / "settings.json").read_text()) == original
    assert not (output / "control/launch.json").exists()  # Preparation does not call a model.
    assert prepare_recovery(tmp_path, "deepseek", "mage", "deepseek_mage", "ds_recovery2",
                            failure_codes=("transport",), cpus=4, workers=2) == {"tasks": [], "prepared": False}
    assert not (tmp_path / "retries/ds_recovery2").exists()
    from tools.pipeline_campaign import settle

    with pytest.raises(ValueError, match="parent campaign"):
        settle(child)
