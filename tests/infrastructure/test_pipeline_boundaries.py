"""External-method configuration fails before scratch, provider or score work."""

import json
from dataclasses import replace

import pytest

from behave.runtime.records import write_json


@pytest.mark.parametrize("sampling", ["typo", "", None, False, [], {}])
def test_invalid_sampling_is_rejected_before_preparation(tmp_path, monkeypatch, sampling):
    from behave.pipelines import configuration

    def forbidden(*args, **kwargs):
        pytest.fail("invalid sampling reached Eval preparation")

    monkeypatch.setattr(configuration, "prepare_eval", forbidden)
    with pytest.raises(ValueError, match="sampling"):
        configuration.prepare(tmp_path / "output", tmp_path / "unread", "mage", provider_sampling=sampling)
    assert not tuple(tmp_path.iterdir())


def test_invalid_saved_sampling_is_rejected_before_provider_or_scratch(tmp_path, monkeypatch):
    from behave.pipelines import execution

    write_json(tmp_path / "settings.json", {})
    write_json(tmp_path / "pipeline.json", {"method": "mage", "options": {}, "provider_sampling": "typo"})
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail("invalid saved sampling reached execution")

    monkeypatch.setattr(execution, "endpoint_for", forbidden)
    monkeypatch.setattr(execution, "local_run", forbidden)
    with pytest.raises(ValueError, match="sampling"):
        execution.rollout(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("workers", [0, False, True, -1, 1.5])
def test_invalid_worker_override_is_not_replaced_by_defaults(tmp_path, monkeypatch, workers):
    from behave.pipelines import execution

    write_json(tmp_path / "settings.json", {"judge_workers": 2})

    def forbidden(*args, **kwargs):
        pytest.fail("invalid worker count acquired scratch")

    monkeypatch.setattr(execution, "local_run", forbidden)
    with pytest.raises(ValueError, match="concurrency"):
        execution.score(tmp_path, workers=workers)
    assert {p.name for p in tmp_path.iterdir()} == {"settings.json"}


@pytest.mark.parametrize("name", ["", ".", "..", "../outside", "/outside", "one/two", "one/", "bad\0name"])
@pytest.mark.parametrize("entry", ["score", "scratch"])
def test_invalid_score_name_never_acquires_a_run(tmp_path, monkeypatch, name, entry):
    from behave.pipelines import execution

    write_json(tmp_path / "settings.json", {"judge_workers": 1})

    def forbidden(*args, **kwargs):
        pytest.fail("invalid score name reached scratch setup")

    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", forbidden)
    with pytest.raises(ValueError, match="directory name"):
        if entry == "score":
            execution.score(tmp_path, name=name)
        else:
            with execution.local_run(tmp_path, "score", judgment=name):
                pytest.fail("invalid score acquired scratch")
    assert {p.name for p in tmp_path.iterdir()} == {"settings.json"}


@pytest.mark.parametrize("sampling", ["upstream", "provider_default"])
def test_root_and_per_call_sampling_share_exact_endpoint_identity(sampling):
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.pipelines.provider import model_factory, sampling_endpoint

    endpoint = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "fixture", "fixture",
        parameters={"temperature": .3, "top_p": .6, "top_k": 20, "seed": 17})
    parameters = {"temperature": .85, "top_p": .95}
    before = endpoint.as_dict()
    expected = {"top_k": 20, "seed": 17}
    if sampling == "upstream":
        expected.update(parameters)
    configured = sampling_endpoint(endpoint, parameters, provider_sampling=sampling)
    assert configured == replace(endpoint, parameters=expected)
    backend, policy = model_factory(endpoint, provider_sampling=sampling)(parameters)
    assert backend.command == configured.command()
    assert policy == configured.model_policy()
    assert json.loads(backend.command.argv[-1])["parameters"] == expected
    assert endpoint.as_dict() == before
    assert parameters == {"temperature": .85, "top_p": .95}
