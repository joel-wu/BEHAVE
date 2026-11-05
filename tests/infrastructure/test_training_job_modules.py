"""Package ownership must not alter frozen resumes or storage destinations."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_slime_training_job import job  # noqa: F401 -- shared execution fixture
from test_slime_training_plan import inputs  # noqa: F401 -- fixture dependency
from test_training_release_inputs import repository

from behave.train import jobs as jobs_module

REPO = Path(__file__).resolve().parents[2]
WRAPPER = "tools/slime_training_job.py"
EXECUTION = "src/behave/train/jobs.py"
STATE = "src/behave/train/jobs.py"


def test_installed_execution_requires_an_explicit_checkout():
    with pytest.raises(TypeError, match="repository"):
        jobs_module.execute("run", "scratch")


def test_node_uses_explicit_checkout_for_split_and_eda(job, tmp_path, monkeypatch):  # noqa: F811
    output, local, calls = job
    root = repository(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert jobs_module.execute(str(output), str(local), repository=root) == 0
    assert len(calls) == 3
    assert all(kwargs["environment"]["BEHAVE_EDA_ROOT"] == str(root / "external/oss-cad-suite")
               for _, kwargs in calls)


def test_node_checks_the_staged_eval_membership_before_training(job, tmp_path):  # noqa: F811
    output, local, calls = job
    task = json.loads((output / "selection.json").read_text())["tasks"][0]
    root = repository(tmp_path, eval_task=task)
    with pytest.raises(ValueError, match="current evaluation tasks"):
        jobs_module.execute(str(output), str(local), repository=root)
    assert not calls
    assert not (output / "artifacts/plan/binding.json").exists()
    assert json.loads((output / "runtime.json").read_text())["state"] == "interrupted"
    assert (local / "weights").is_dir()  # Failed jobs retain diagnostic scratch.


@pytest.mark.parametrize("failure", [OSError("fixture cleanup failure"), KeyError("fixture cleanup failure")])
def test_model_cleanup_failure_does_not_erase_training_completion(job, monkeypatch, failure):  # noqa: F811
    output, local, calls = job

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("behave.runtime.models.release_model", fail)
    assert jobs_module.execute(str(output), str(local), repository=REPO) == 0
    runtime = json.loads((output / "runtime.json").read_text())
    assert runtime["state"] == "completed" and len(calls) == 3
    assert runtime["scratch_release"]["state"] == "retained"
    assert "fixture cleanup failure" in runtime["scratch_release"]["error"]
    assert (local / "weights").is_dir()
    assert json.loads((output / "artifacts/plan/result.json").read_text())["native_cycle_completed"]


def test_unconfirmed_weight_checker_keeps_the_model_copy(job, monkeypatch):  # noqa: F811
    from behave.core.lifecycle import CleanupState

    output, local, calls = job
    monkeypatch.setattr("behave.isolation.service.run_service", lambda *a, **kw: SimpleNamespace(
        status="exited", returncode=0, cleanup_state=CleanupState.UNKNOWN, stdout=b"[]", stderr=b""))
    monkeypatch.setattr("behave.runtime.models.release_model", lambda *a, **kw:
                        pytest.fail("unconfirmed consumer cannot release its weights"))
    with pytest.raises(RuntimeError, match="weight update check failed"):
        jobs_module.execute(str(output), str(local), repository=REPO)
    assert len(calls) == 2 and (local / "weights").is_dir()
    assert json.loads((output / "runtime.json").read_text())["state"] == "interrupted"


def test_staged_bootstrap_uses_installed_package_not_repository_tools(tmp_path, monkeypatch, inputs):  # noqa: F811
    config, _, _, _ = inputs
    output, local = tmp_path / "run", tmp_path / "local"
    output.mkdir()
    local.mkdir()
    monkeypatch.setattr(jobs_module, "mkdtemp", lambda **kw: str(local))
    monkeypatch.setattr("behave.runtime.snapshots.stage", lambda source, target: target.mkdir())
    monkeypatch.setattr("behave.runtime.snapshots.install_runtime", lambda *args: {
        "evidence": {"control_python": "/installed/python"}, "environment": {"fixture": "installed"},
    })
    calls = []
    monkeypatch.setattr(jobs_module.subprocess, "run",
                        lambda *args, **kw: calls.append((args, kw)) or SimpleNamespace(returncode=13))
    assert jobs_module._staged(output, config, {}) == 13
    (command,), kwargs = calls[0]
    assert command[:5] == ("/installed/python", "-I", "-B", "-u", "-c")
    bootstrap = command[5]
    assert "behave.train.jobs" in bootstrap
    assert "sys.path" not in bootstrap and "tools." not in bootstrap
    assert command[6:] == (str(local / "checkout"), str(output), str(local))
    assert kwargs == {"cwd": local, "env": {"fixture": "installed"}, "check": False}


