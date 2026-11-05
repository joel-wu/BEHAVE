"""Public and saved-run Eval share explicit provider policies without network IO."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.agent.billing import TokenPrices
from behave.agent.providers.credentials import CredentialRef
from behave.agent.providers.hosted import HostedEndpoint
from behave.agent.spending import SpendPolicy
from behave.cli.evaluation_plan import prepare
from behave.core.artifacts import DirectoryReader
from behave.eval.generation import endpoint_for
from behave.eval.plan import EvaluationPlan
from behave.runtime.records import write_json


@pytest.fixture
def configuration():
    recipe = Path(__file__).resolve().parents[2] / "configs/eval/local.example.json"
    return json.loads(recipe.read_text())


def hosted(tmp_path, provider, *, funded):
    prices = TokenPrices("fixture-prices", "fixture-model", "USD", "1", "0.1", "2",
                         cache_write_5m_per_million="1", cache_write_1h_per_million="2")
    return HostedEndpoint(provider, "fixture-model", "fixture-deployment",
        CredentialRef(str(tmp_path / "absent-credentials.env"), "FIXTURE_API_KEY", "fixture-key-revision"),
        parameters={"max_output_tokens": 4096}, prices=prices if funded else None,
        spending=SpendPolicy(str(tmp_path / "account"), "fixture-account", "USD", "10", 8000) if funded else None)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "fireworks", "deepseek", "openrouter"])
@pytest.mark.parametrize("funded", [False, True])
def test_public_hosted_prepare_preserves_typed_endpoint_without_reading_credentials(
        tmp_path, publication, configuration, monkeypatch, provider, funded):
    endpoint = hosted(tmp_path, provider, funded=funded)
    configuration.update(provider=provider, endpoint=endpoint.as_dict())
    original = json.loads(json.dumps(configuration))
    for target in ("subprocess.Popen", "behave.sim.api.prepare_runtime",
                   "behave.agent.providers.credentials.load_credential"):
        monkeypatch.setattr(target, lambda *_a, **_k: pytest.fail("preparation is offline"))
    output = tmp_path / "prepared"
    result = prepare(configuration=configuration, task_objects=publication.root, release=publication.ref,
                     tasks=[publication.task.task_id], output=output)
    settings = json.loads((output / "settings.json").read_text())
    plan = EvaluationPlan.from_bytes((output / "plan.json").read_bytes(), DirectoryReader(output / "inputs"))
    assert result["candidates"] == 1 and not plan.score_on_submit
    assert plan.jobs[0].model == endpoint.command()
    assert dict(plan.jobs[0].attempt.model_policy) == endpoint.model_policy()
    assert endpoint_for(output, settings) == endpoint
    assert json.loads((output / "endpoint.json").read_text()) == endpoint.as_dict()
    assert configuration == original
    assert not (tmp_path / "account").exists()
    assert not (output / "run").exists()


@pytest.mark.parametrize("damage", ["provider", "url", "secret", "prices", "parameters"])
def test_bad_hosted_profile_is_rejected_before_importing_inputs(
        tmp_path, publication, configuration, monkeypatch, damage):
    endpoint = hosted(tmp_path, "fireworks", funded=True).as_dict()
    if damage == "provider":
        endpoint["provider"] = "deepseek"
    elif damage == "url":
        endpoint["url"] = "https://example.invalid/v1"
    elif damage == "secret":
        endpoint["credential"]["value"] = "not-a-real-key"
    elif damage == "prices":
        endpoint["prices"]["model"] = "different-model"
    else:
        endpoint["parameters"]["top_k"] = 20
    configuration.update(provider="fireworks", endpoint=endpoint)
    monkeypatch.setattr("behave.eval.generation.load_tasks",
                        lambda *_: pytest.fail("invalid configuration must not stage inputs"))
    with pytest.raises(ValueError):
        prepare(configuration=configuration, task_objects=publication.root, release=publication.ref,
                tasks=[publication.task.task_id], output=tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("key,value", [
    ("credentials", "/different/key.env"), ("model", "other"), ("generation_parameters", {}),
    ("prices", {}), ("maximum_input_tokens", 999), ("budget_usd", "50"),
])
def test_explicit_endpoint_cannot_silently_ignore_conflicting_legacy_fields(tmp_path, key, value):
    endpoint = hosted(tmp_path, "fireworks", funded=True)
    with pytest.raises(ValueError):
        endpoint_for(tmp_path, {"provider": "fireworks", "endpoint": endpoint.as_dict(), key: value})


def test_legacy_campaign_endpoint_keeps_its_wire_identity(tmp_path):
    prices = TokenPrices("fixture", "fixture-model", "USD", "1", "0.1", "2")
    settings = {"provider": "fireworks", "model": "fixture-model",
        "credentials": str(tmp_path / "unused.env"), "credential_key": "FIXTURE_API_KEY",
        "generation_parameters": {"max_output_tokens": 4096}, "prices": prices.as_dict(),
        "budget_usd": "10", "maximum_input_tokens": 8000}
    expected = HostedEndpoint("fireworks", "fixture-model", tmp_path.name,
        CredentialRef(settings["credentials"], "FIXTURE_API_KEY", "user-supplied"),
        parameters=settings["generation_parameters"], prices=prices,
        spending=SpendPolicy(str(tmp_path / "spending"), tmp_path.name, "USD", "10", 8000))
    assert endpoint_for(tmp_path, settings).command() == expected.command()


def test_unfunded_hosted_account_is_not_reported_as_a_free_local_run(
        tmp_path, publication, configuration, monkeypatch):
    from behave.core.artifacts import BlobRef
    from behave.eval import generation as generation_module

    endpoint = hosted(tmp_path, "fireworks", funded=False)
    settings = {**configuration, "provider": "fireworks", "endpoint": endpoint.as_dict(),
                "tasks": [publication.task.task_id], "task_objects": str(publication.root),
                "release": publication.ref.as_dict()}
    write_json(tmp_path / "settings.json", settings)
    monkeypatch.setattr("behave.agent.providers.credentials.load_credential", lambda *_: None)
    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", lambda *_: {})
    monkeypatch.setattr("behave.sim.api.prepare_runtime", lambda **_: None)
    monkeypatch.setattr(generation_module, "check_development_shell", lambda *_: None)
    monkeypatch.setattr("behave.runtime.records.signal.signal", lambda *_: None)
    monkeypatch.setattr("behave.agent.spending.SpendAccount.summary",
                        lambda *_: pytest.fail("no shared account was configured"))

    def run(**kw):
        assert kw["plan"].jobs[0].model == endpoint.command()
        return SimpleNamespace(summary={"candidates": [
            {"run_key": BlobRef.of(job.to_bytes()).sha256, "status": "pending_judgment"}
            for job in kw["plan"].jobs]})

    monkeypatch.setattr("behave.eval.runner.run_evaluation", run)
    assert generation_module.run(tmp_path, status_output=tmp_path) == 0
    assert json.loads((tmp_path / "billing.json").read_text()) == {
        "provider": "fireworks", "scope": "candidate-accounting", "hosted_api_charge_usd": None}


@pytest.mark.parametrize("funded", [False, True])
def test_followup_preserves_model_policy_and_rebinds_only_authorized_budget(
        tmp_path, publication, monkeypatch, funded):
    from test_eval_followup import FAILURE, followup_inputs, inventory

    from behave.eval import followup

    source = followup_inputs(tmp_path, publication)
    endpoint = hosted(tmp_path, "fireworks", funded=funded)
    settings = json.loads((source / "settings.json").read_text())
    for key in ("generation_parameters", "model", "prices", "credentials", "credential_key",
                "maximum_input_tokens", "budget_usd", "spending_directory", "spending_account"):
        settings.pop(key, None)
    settings.update(provider="fireworks", endpoint=endpoint.as_dict())
    write_json(source / "settings.json", settings)
    before = inventory(source)
    output = tmp_path / "followup"
    policy = SpendPolicy(str(tmp_path / "new-account"), "supplement", "USD", "50", 131072)
    if not funded:
        with pytest.raises(ValueError, match="existing hosted budget"):
            followup.prepare(output, [source], failure_codes=[FAILURE], spending=policy)
        assert not (output / "settings.json").exists()
        return
    selection = followup.prepare(output, [source], failure_codes=[FAILURE], spending=policy)
    saved = json.loads((output / "settings.json").read_text())
    actual = endpoint_for(output, saved)
    expected = replace(endpoint, spending=replace(endpoint.spending,
        directory=policy.directory, account=policy.account, limit=policy.limit))
    assert actual == expected and actual.spending.maximum_input_tokens == 8000
    assert inventory(source) == before
    committed = inventory(output)
    assert followup.prepare(output, [source], failure_codes=[FAILURE], spending=policy) == selection
    assert inventory(output) == committed
