"""Installed and legacy job commands share submission and maintenance policy."""

import json
from pathlib import Path

import pytest
from test_slime_training_plan import inputs  # noqa: F401 -- shared fixture
from test_training_release_inputs import repository

from behave.cli import training_job
from behave.train import jobs as jobs_module
from tools import slime_training_job

REPO = Path(__file__).resolve().parents[2]


def arguments(output):
    return [str(output), "--allocation", "123", "--configuration", "config.json",
            "--release", "release", "--train-split", "train.txt", "--task", "a", "--cpus", "144"]


@pytest.mark.parametrize("router", ["direct", "infra"])
def test_public_and_legacy_cli_submit_the_same_request(tmp_path, monkeypatch, router):
    from behave.cli.__main__ import main as infra_main
    from behave.train.__main__ import main as train_main

    calls = []
    monkeypatch.setattr(training_job, "submit", lambda *a, **kw: calls.append((a, kw)) or {"submitted": True})
    args = arguments(tmp_path / "run") + ["--updates", "60", "--training-seed", "20260920",
        "--workers", "32", "--rollout-gpus-per-engine", "4", "--actor-gpus-per-node", "8",
        "--context-parallel", "2", "--resume-from", "parent", "--resume-rollouts",
        "--workers-stopped", "--checkpoint-directory", "native"]
    assert slime_training_job.main(args) == 0
    args += ["--repository", str(REPO)]
    assert (train_main(["submit", *args]) if router == "direct" else
            infra_main(["train", "submit", *args])) == 0
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][1]["repository"] == REPO
    assert calls[0][1]["updates"] == 60


def test_public_submit_requires_repository_before_any_work(tmp_path, monkeypatch):
    monkeypatch.setattr(training_job, "submit", lambda *a, **kw: pytest.fail("no submission"))
    with pytest.raises(SystemExit) as error:
        training_job.main(arguments(tmp_path / "run"))
    assert error.value.code == 2 and not (tmp_path / "run").exists()


def test_shared_submit_freezes_explicit_repo_and_preserves_settings(tmp_path, monkeypatch, inputs):  # noqa: F811
    config, release, split, task = inputs
    root = repository(tmp_path)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))
    freezes, launches = [], []
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: freezes.append((a, kw)))
    monkeypatch.setattr("behave.runtime.jobs.submit",
                        lambda *a, **kw: launches.append((a, kw)) or {"submitted": True})
    monkeypatch.setattr("behave.core.runtime_locations.profile_identity", lambda: None)
    monkeypatch.setattr("behave.train.checkpoint_storage.storage_destination", lambda *a: None)
    output = tmp_path / "run"
    assert jobs_module.submit(output, 123, config_file, release, [task], split,
                                 repository=root, cpus=144) == {"submitted": True}
    assert json.loads((output / "configuration.json").read_text()) == config
    assert freezes == [((root, output / "snapshot/checkout"), {"extra_files": ("tools/slime_training_job.py",)})]
    (control, command), options = launches[0]
    assert control == output / "control" and options["cwd"] == root
    assert options["wall_seconds"] is None
    assert "--gpus=8" in command and "--cpus-per-task=144" in command
    # Import from the just-frozen source, including when that source is a legacy parent.
    assert command[-3:] == [str(output / "snapshot/checkout"), str(output / "snapshot/checkout/src"), str(output)]
    assert "from tools.slime_training_job import staged" in command[command.index("-c") + 1]


def test_shared_submit_rejects_staged_eval_membership_before_dispatch(tmp_path, monkeypatch, inputs):  # noqa: F811
    _, release, split, task = inputs
    root = repository(tmp_path, eval_task=task)
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("no dispatch"))
    with pytest.raises(ValueError, match="current evaluation tasks"):
        jobs_module.submit(tmp_path / "run", 123, tmp_path / "absent", release, [task], split,
                              repository=root, cpus=144)
    assert not (tmp_path / "run").exists()


def test_missing_node_stop_helper_fails_before_waiting_or_signalling(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "require_live_allocation", lambda *a: pytest.fail("must preflight first"))
    monkeypatch.setattr(jobs_module.subprocess, "run", lambda *a, **kw: pytest.fail("must not signal"))
    with pytest.raises(FileNotFoundError, match="owned stop helper is missing"):
        jobs_module.wait_and_pause(tmp_path / "run", 123, 18, poll_seconds=5, stop_script=tmp_path / "absent")
