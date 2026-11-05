"""Shared follow-up planning retains old evidence without executing a model."""

import json
from pathlib import Path

import pytest

from behave.core.records import canonical_json
from behave.eval import followup as followup_module
from behave.runtime.records import write_json
from behave.task_execution.result import TaskResult
from behave.task_execution.run_store import RunStore

FAILURE = "model-unknown:fixture-interrupted"


def followup_inputs(root, publication):
    source = root / "source"
    store = RunStore(source / "run/candidates/fixture")
    result = TaskResult(
        publication.task.task_id, publication.ref, publication.release.tasks[0].manifest,
        "fixture-attempt", None, None, None, None, "solver", FAILURE,
        store.objects.put(canonical_json({"fixture": "accounting"})),
    )
    reference = store.objects.put(result.to_bytes())
    write_json(source / "run/report/000000.json", {"kind": "evaluation_case", "payload": {"row": {
        "task_id": result.task_id, "run_key": "fixture", "result": reference.as_dict(),
        "status": result.status, "score": result.score}}})
    write_json(source / "settings.json", {
        "tasks": [result.task_id], "setting": "joint", "release": publication.ref.as_dict(),
        "task_objects": str(publication.root), "judge_workers": 2,
        "score_on_submit": False, "generation_parameters": {"temperature": 0.7, "seed": 17},
        "attempt": {"max_turns": 60, "context_tokens": 131072}})
    return source


def inventory(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_preparation_and_reporting_preserve_sources_and_do_not_execute(tmp_path, publication, monkeypatch, capsys):
    from behave.eval.__main__ import main

    source = followup_inputs(tmp_path, publication)
    before, inputs = inventory(source), inventory(publication.root)
    output = tmp_path / "followup"

    def forbidden(*args, **kwargs):
        pytest.fail("follow-up planning/reporting must not start a model, judge or process")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("behave.sim.api.prepare_runtime", forbidden)
    monkeypatch.setattr(followup_module, "run_rejudgments", forbidden)
    assert main(["followup", str(output), "--source", str(source), "--failure-code", FAILURE]) is None
    assert json.loads(capsys.readouterr().out) == {"keep": 0, "rollout": 1, "rejudge": 0}
    settings = json.loads((output / "settings.json").read_bytes())
    original = json.loads((source / "settings.json").read_bytes())
    for key in ("release", "generation_parameters", "attempt", "score_on_submit"):
        assert settings[key] == original[key]
    assert inventory(Path(settings["task_objects"])) == inputs
    saved = inventory(output)
    # Prepared selection is immutable even if later source evidence is absent.
    with monkeypatch.context() as replay_guard:
        replay_guard.setattr(followup_module, "load_outcomes", forbidden)
        assert main(["followup", str(output), "--source", str(source), "--failure-code", FAILURE]) is None
        capsys.readouterr()
    assert inventory(output) == saved
    assert main(["followup", str(output), "--report"]) is None
    summary = json.loads(capsys.readouterr().out)
    assert summary["selected"] == {"keep": 0, "rollout": 1, "rejudge": 0}
    assert summary["pending_retries"] == 1
    assert not (output / "run").exists()
    assert inventory(source) == before and inventory(publication.root) == inputs


@pytest.mark.parametrize("arguments", [[], ["--reroll", "--failure-code", FAILURE]])
def test_invalid_preparation_does_not_create_an_output(tmp_path, arguments):
    output = tmp_path / "followup"
    if arguments:
        with pytest.raises(ValueError, match="choose general reroll"):
            followup_module.main([str(output), "--source", str(tmp_path / "absent"), *arguments])
    else:
        with pytest.raises(SystemExit) as error:
            followup_module.main([str(output)])
        assert error.value.code == 2
    assert not output.exists()
