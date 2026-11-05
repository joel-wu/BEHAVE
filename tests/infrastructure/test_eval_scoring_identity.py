"""A completed older scoring pass cannot satisfy a newly requested pass."""

import json

import pytest
from test_eval_checkpoint_study import fixture

from behave.eval import checkpoint as checkpoint_module
from behave.runtime.records import write_json


def prepared(root, *, pass_name="new-baseline", receipt_output="baseline", receipt_state="completed"):
    config = fixture(root)
    config["scoring_pass"] = pass_name
    write_json(root / "configuration.json", config)
    entry = {"model_line": "main", "update": 5, "inference_seeds": [3]}
    write_json(root / "queue.json", {"checkpoints": [entry]})
    directory = checkpoint_module.measurement_directory(root, entry, 3)
    write_json(directory / "generation.json", {"state": "completed"})
    receipt = {"state": receipt_state}
    if receipt_output is not None:
        receipt["output"] = str(directory / "scoring" / receipt_output)
    write_json(directory / "scoring.json", receipt)
    return entry, directory


@pytest.mark.parametrize("receipt_output,receipt_state,expected_calls", [
    ("baseline", "completed", 1),
    ("new-baseline", "completed", 0),
    ("new-baseline", "failed", 1),
    (None, "completed", 1),
])
def test_queue_reuses_only_the_requested_completed_pass(
        tmp_path, monkeypatch, receipt_output, receipt_state, expected_calls):
    entry, directory = prepared(tmp_path, receipt_output=receipt_output, receipt_state=receipt_state)
    before = {name: (directory / name).read_bytes() for name in ("generation.json", "scoring.json")}

    def no_generation(*args, **kwargs):
        pytest.fail("saved generation must not be repeated or reload weights")

    for name in ("resolve_checkpoint", "prepare_measurement", "submit_checkpoint"):
        monkeypatch.setattr(checkpoint_module, name, no_generation)
    calls = []
    monkeypatch.setattr(checkpoint_module, "score", lambda *a, **kw: calls.append(a) or True)
    assert checkpoint_module.run(tmp_path, repository=tmp_path) == 0
    assert calls == [(tmp_path, entry, 3)] * expected_calls
    assert before == {name: (directory / name).read_bytes() for name in before}


@pytest.mark.parametrize("receipt_output,reused", [("baseline", False), ("new-baseline", True), (None, False)])
def test_dispatch_does_not_drop_a_new_scoring_pass(tmp_path, receipt_output, reused):
    entry, directory = prepared(tmp_path, receipt_output=receipt_output)
    before = (directory / "scoring.json").read_bytes()
    manifest = checkpoint_module.prepare_dispatch(tmp_path, {
        "lane": {"allocation": 12, "model_lines": ["main"]}},
        code_directory="code", probe_request=tmp_path / "probe.json", launch_id="next")
    assert len(manifest["completed_reused"]) == int(reused)
    assert len(manifest["measurements"]) == int(not reused)
    lane = tmp_path / "dispatch/next/lane"
    assert json.loads((lane / "queue.json").read_text())["checkpoints"] == ([] if reused else [entry])
    assert (directory / "scoring.json").read_bytes() == before


def test_other_measurements_receipt_is_not_reused(tmp_path, monkeypatch):
    entry, directory = prepared(tmp_path, receipt_output="new-baseline")
    write_json(directory / "scoring.json", {"state": "completed",
        "output": str(tmp_path / "other/eval-seed-03/scoring/new-baseline")})
    calls = []
    monkeypatch.setattr(checkpoint_module, "score", lambda *a, **kw: calls.append(a) or True)
    monkeypatch.setattr(checkpoint_module, "resolve_checkpoint", lambda *_: pytest.fail("weights not needed"))
    assert checkpoint_module.run(tmp_path, repository=tmp_path) == 0
    assert calls == [(tmp_path, entry, 3)]


def test_completed_pass_accepts_an_equivalent_archive_alias(tmp_path):
    root = tmp_path / "archive"
    entry, directory = prepared(root, receipt_output="new-baseline")
    alias = tmp_path / "retained-path"
    alias.symlink_to(root, target_is_directory=True)
    write_json(directory / "scoring.json", {"state": "completed",
        "output": str(alias / directory.relative_to(root) / "scoring/new-baseline")})
    assert checkpoint_module.completed_scoring(root, entry, 3)


def test_default_pass_still_reuses_completed_baseline(tmp_path):
    entry, directory = prepared(tmp_path, receipt_output="baseline")
    config = checkpoint_module.read(tmp_path / "configuration.json")
    config.pop("scoring_pass")
    write_json(tmp_path / "configuration.json", config)
    assert checkpoint_module.scoring_directory(tmp_path, entry, 3) == directory / "scoring/baseline"
    assert checkpoint_module.completed_scoring(tmp_path, entry, 3)


def test_shared_measurement_with_two_passes_is_scored_once_per_pass(tmp_path, monkeypatch):
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        entry, _ = prepared(root, pass_name=f"pass-{index}")
        config = checkpoint_module.read(root / "configuration.json")
        write_json(root / "configuration.json", {**config, "archive_root": str(tmp_path / "archive")})
    directory = checkpoint_module.measurement_directory(roots[0], entry, 3)
    write_json(directory / "generation.json", {"state": "completed"})
    write_json(directory / "scoring.json", {"state": "completed", "output": str(directory / "scoring/older")})
    calls = []
    monkeypatch.setattr(checkpoint_module, "resolve_checkpoint", lambda *_: pytest.fail("must reuse generation"))
    monkeypatch.setattr(checkpoint_module, "score", lambda *a, **kw: calls.append(a) or True)
    assert checkpoint_module.run_stages(roots, repository=tmp_path) == 0
    assert calls == [(root, entry, 3) for root in roots]
