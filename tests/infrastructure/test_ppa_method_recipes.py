"""Portable operator recipes resolve once; frozen campaigns stay explicit."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_ppa_campaign_inputs import input_plan
from test_ppa_cli import published
from test_ppa_planning import study

from behave.ppa.planning import read_methods, validate_campaign


def method():
    return {"name": "local", "provider": "provider.json", "prompt": "prompts/objective.md",
            "behavior_source": "provided", "sim_feedback": True}


def test_relative_paths_use_selected_recipe_not_current_directory(tmp_path, monkeypatch):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    path = recipe / "methods.json"
    path.write_text(json.dumps({"methods": [method()]}))
    before = path.read_bytes()
    for cwd in (tmp_path, recipe):
        monkeypatch.chdir(cwd)
        assert read_methods(path.relative_to(cwd)) == [{**method(),
            "provider": str(recipe / "provider.json"), "prompt": str(recipe / "prompts/objective.md")}]
    assert path.read_bytes() == before
    assert sorted(p.name for p in recipe.iterdir()) == ["methods.json"]


def test_absolute_legacy_paths_keep_their_exact_spelling(tmp_path):
    original = {**method(), "provider": str(tmp_path / "nested/../provider.json"),
                "prompt": "/explicit/prompt.md"}
    path = tmp_path / "methods.json"
    path.write_text(json.dumps({"methods": [original]}))
    assert read_methods(path) == [original]


@pytest.mark.parametrize("row", [None, [], {}, {"methods": [], "ignored": True},
    {"methods": None}, {"methods": {}}, {"methods": []}, {"methods": [None]},
    {"methods": [method(), method()]}, {"methods": [{**method(), "provider": None}]},
    {"methods": [{**method(), "prompt": ""}]}, {"methods": [{**method(), "prompt": "bad\x00path"}]},
    {"methods": [{**method(), "name": "../escape"}]},
    {"methods": [{**method(), "behavior_source": []}]},
    {"methods": [{**method(), "sim_feedback": "disabled"}]},
    {"methods": [{**method(), "unexpected": True}]},
])
def test_invalid_method_recipes_fail_closed(tmp_path, row):
    path = tmp_path / "methods.json"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        read_methods(path)
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("field", ["provider", "prompt"])
def test_frozen_campaigns_still_reject_relative_paths(tmp_path, publication, field):
    plan = input_plan(tmp_path, publication)
    plan["methods"][0][field] = "relative"
    before = deepcopy(plan)
    with pytest.raises(ValueError, match="paths must be absolute"):
        validate_campaign(plan)
    assert plan == before


def test_prepare_resolves_relative_recipe_and_preserves_legacy_frozen_bytes(tmp_path, monkeypatch, capsys):
    from behave.ppa import __main__ as cli

    published(tmp_path)
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    path = recipe / "methods.json"
    paths = {"provider": "../provider.json", "prompt": "../prompt.txt"}
    path.write_text(json.dumps({"methods": [{**method(), **paths}]}))
    config = recipe / "study.json"
    config.write_text(json.dumps(study()))
    output = tmp_path / "prepared"
    monkeypatch.chdir(recipe)
    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kw: pytest.fail("preparation must not initialize EDA"))
    args = ["prepare", "--study", str(config), "--methods", str(path), "--objects", str(tmp_path / "inputs"),
            "--release", str(tmp_path / "release.json"), "--output", str(output),
            "--liberty", str(tmp_path / "unused.lib"), "--sta", str(tmp_path / "unused-sta")]
    assert cli.main(args) == 0
    report = capsys.readouterr().out
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    absolute = {key: str((recipe / value).resolve()) for key, value in paths.items()}
    path.write_text(json.dumps({"methods": [{**method(), **absolute}]}))
    monkeypatch.chdir(tmp_path)
    assert cli.main(args) == 0
    assert capsys.readouterr().out == report
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
    assert json.loads(before["plan.json"])["methods"] == [{**method(), **absolute}]


def test_public_example_uses_shared_endpoint_and_objective():
    from behave.agent.providers.local_chat import LocalChatEndpoint

    root = Path(__file__).resolve().parents[2]
    (entry,) = read_methods(root / "configs/ppa/methods.example.json")
    assert Path(entry["prompt"]) == root / "src/behave/agent/prompts/ppa/objective.md"
    endpoint = LocalChatEndpoint.from_dict(json.loads(Path(entry["provider"]).read_text()))
    assert endpoint.parameters["seed"] == 17
