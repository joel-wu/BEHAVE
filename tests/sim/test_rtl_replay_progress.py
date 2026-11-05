"""Observed progress is useful after interruption but cannot establish a score."""

import json
from types import SimpleNamespace

import pytest

from behave.sim.core.stimulus import Stimulus
from behave.sim.rtl import replay_progress


class Driver:
    def progress_counts(self):
        return {"slots": 41999, "accepted": {"x": 2}, "emitted": {"y": 37}}


def test_progress_has_runtime_cadence_and_preserves_transaction_units(tmp_path, monkeypatch, capsys):
    now = [10.0]
    monkeypatch.setattr(replay_progress, "monotonic", lambda: now[0])
    monkeypatch.setattr(replay_progress.defaults, "SIM_RTL_REPLAY_PROGRESS_SECONDS", 3.0)
    stimulus = Stimulus({"x": [(0,), (1,)]})
    progress = replay_progress.ReplayProgress(tmp_path, stimulus, {"y": 64})
    progress.publish(Driver())
    first = progress.path.read_bytes()
    image = json.loads(first)
    assert image["slots"] == 41999 and image["accepted"] == {"x": 2}
    assert image["emitted"] == {"y": 37} and image["expected_outputs"] == {"y": 64}
    assert image["queued"] == {"x": 2} and image["stage"] == "executing"
    assert not {"score", "passed", "checked_inputs", "completed_inputs"}.intersection(image)
    now[0] = 12.0
    progress.publish(Driver())
    assert progress.path.read_bytes() == first
    now[0] = 13.0
    progress.publish(Driver())
    assert json.loads(progress.path.read_bytes())["elapsed_seconds"] == 3.0
    now[0] = 13.1
    progress.publish(Driver(), stage="publishing", force=True)
    assert json.loads(progress.path.read_bytes())["stage"] == "publishing"
    assert len(capsys.readouterr().out.splitlines()) == 3
    assert replay_progress.progress_detail(SimpleNamespace(outputs={})) == {}
    assert replay_progress.progress_detail(SimpleNamespace(outputs={"replay_progress.json": first})) == {
        "replay_progress": first.decode()}


def test_progress_publication_failure_preserves_prior_checkpoint(tmp_path, monkeypatch):
    progress = replay_progress.ReplayProgress(tmp_path, Stimulus(), None)
    progress.publish(Driver(), force=True)
    before = progress.path.read_bytes()
    def fail(*args):
        raise OSError("test publication failure")
    monkeypatch.setattr(type(progress.path), "replace", fail)
    with pytest.raises(OSError, match="publication failure"):
        progress.publish(Driver(), force=True, stage="published")
    assert progress.path.read_bytes() == before
