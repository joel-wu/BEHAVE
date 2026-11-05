"""All serving subprocesses own cleanup, including commands before readiness."""

import hashlib
import shutil
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest
from test_native_chat_tokens import native
from test_serving import lifecycle, load_tool, spec  # noqa: F401
from test_serving_weights_job import weight_input
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401
from test_weight_publication import trained  # noqa: F401

from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import parse_json
from behave.isolation.service import ServiceResult
from behave.serving import ServingFailure
from behave.serving import environment as provision
from behave.serving import session as control
from behave.serving.session import probe as real_probe


@pytest.fixture(autouse=True)
def listening_port(monkeypatch):
    # These fixtures exercise the HTTP subprocess's cleanup, after TCP listen.
    monkeypatch.setattr(control.socket, "create_connection", lambda *a, **kw: nullcontext())


def uncertain(status):
    if status == "raise":
        raise RuntimeError("private supervisor diagnostic")
    if status == "interrupt":
        raise KeyboardInterrupt("private supervisor diagnostic")
    return ServiceResult(status, 0, b"untrusted response", b"private stderr", .01)


@pytest.mark.parametrize("phase", ["installation", "headers", "readiness"])
@pytest.mark.parametrize("status", ["cleanup-incomplete", "unknown-result", "raise", "interrupt"])
def test_preparation_cannot_hide_unconfirmed_cleanup(tmp_path, monkeypatch, phase, status):
    def run(*args, **kwargs):
        return uncertain(status)

    monkeypatch.setattr(provision, "run_service", run)
    monkeypatch.setattr(control, "run_service", run)
    payload = b"authenticated package fixture"
    package = tmp_path / "cached.deb"
    package.write_bytes(payload)
    binding = {"path": str(package), "sha256": hashlib.sha256(payload).hexdigest(),
               "size": len(payload), "include_paths": ["usr/include/python"]}
    with pytest.raises(ServingFailure) as error:
        if phase == "installation":
            provision.inspect_installation(spec(), tmp_path, {}, deadline=time.monotonic() + 10)
        elif phase == "headers":
            provision.headers(binding, tmp_path, {}, deadline=time.monotonic() + 10)
        else:
            real_probe(spec(), scratch=tmp_path, deadline=time.monotonic() + 10)
    expected = CleanupState.INCOMPLETE if status == "cleanup-incomplete" else CleanupState.UNKNOWN
    assert error.value.cleanup_state is expected
    assert "private" not in str(error.value)


@pytest.mark.parametrize("status", ["timeout", "launch-failed"])
def test_clean_probe_failure_remains_retryable(tmp_path, monkeypatch, status):
    monkeypatch.setattr(control, "run_service", lambda *a, **kw: ServiceResult(status, None, b"", b"", .01))
    assert real_probe(spec(), scratch=tmp_path, deadline=time.monotonic() + 10) is None


@pytest.mark.parametrize("seconds", [None, 37])
def test_readiness_uses_the_same_runtime_cleanup_budget(tmp_path, monkeypatch, seconds):
    stopped = lambda: False

    def run(*args, **kwargs):
        assert kwargs["deadline"] == seconds
        assert kwargs["cleanup_seconds"] == seconds
        assert kwargs["cancelled"] is stopped
        return ServiceResult("exited", 0, b"ready", b"", .01)

    monkeypatch.setattr(control, "run_service", run)
    monkeypatch.setattr(control.time, "monotonic", lambda: 0.)
    assert real_probe(spec(cleanup_seconds=seconds), scratch=tmp_path,
                      deadline=seconds, cancelled=stopped) == b"ready"


@pytest.mark.parametrize("status", ["cleanup-incomplete", "unknown-result", "raise", "interrupt"])
def test_clean_server_shutdown_cannot_clear_probe_uncertainty(lifecycle, monkeypatch, status):  # noqa: F811
    make, active = lifecycle
    serve = control.run_service

    def run(*args, **kwargs):
        return uncertain(status) if "behave.serving.probe" in " ".join(args[0]) else serve(*args, **kwargs)

    monkeypatch.setattr(control, "run_service", run)
    monkeypatch.setattr(control, "probe", real_probe)
    session = make()
    with pytest.raises(ServingFailure):
        session.start()
    assert not active
    with pytest.raises(ServingFailure):
        session.close()
    stopped = parse_json((session.records / "stopped.json").read_bytes())
    assert stopped["service_status"] == "cancelled"  # The model itself exited cleanly.
    assert stopped["cleanup_complete"] is False  # The probe did not.


def test_unknown_server_status_is_not_a_cleanup_witness(lifecycle, monkeypatch):  # noqa: F811
    make, _ = lifecycle
    monkeypatch.setattr(control, "run_service", lambda *a, **kw: uncertain("unknown-result"))
    monkeypatch.setattr(control, "probe", lambda *a, **kw: None)
    session = make()
    with pytest.raises(ServingFailure):
        session.start()
    stopped = parse_json((session.records / "stopped.json").read_bytes())
    assert stopped["cleanup_complete"] is False
    with pytest.raises(ServingFailure):
        session.close()


@pytest.mark.parametrize("typed", [True, False])
def test_startup_error_survives_later_unconfirmed_server_cleanup(lifecycle, monkeypatch, typed):  # noqa: F811
    make, active = lifecycle
    serve = control.run_service
    original = ServingFailure("readiness-model-mismatch") if typed else ValueError("invalid readiness response")

    def uncertain_shutdown(*args, **kwargs):
        result = serve(*args, **kwargs)
        # The fake server is stopped; only the cleanup acknowledgement is lost.
        return replace(result, status="cleanup-incomplete")

    def invalid_response(*args, **kwargs):
        raise original

    monkeypatch.setattr(control, "run_service", uncertain_shutdown)
    monkeypatch.setattr(control, "probe", invalid_response)
    session = make()
    with pytest.raises(type(original)) as caught:
        session.start()
    assert caught.value is original
    assert not active
    if typed:
        assert original.cleanup_state is CleanupState.INCOMPLETE
    assert any("cleanup-incomplete" in note for note in original.__notes__)
    stopped = parse_json((session.records / "stopped.json").read_bytes())
    assert stopped["failure"] == (original.code if typed else "ValueError")
    assert stopped["execution_status"] == "cancelled" and not stopped["cleanup_complete"]


@pytest.mark.parametrize("status", ["cleanup-incomplete", "unknown-result", "raise", "interrupt", "timeout"])
def test_worker_retains_staged_weights_after_unconfirmed_preparation(
    tmp_path, trained, monkeypatch, status,  # noqa: F811
):
    job, _, path = weight_input(tmp_path, trained, monkeypatch)
    from model_weight_input import WeightInput

    from behave import serving

    binding = Path(job.__file__).resolve().parents[1] / job.BINDING
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(job, "mkdtemp", lambda **kw: str(scratch))
    staged = []
    stage_weights = WeightInput.stage

    def weights(self, policy):
        policy = stage_weights(self, policy)
        staged.append(Path(policy.model_path))
        return policy

    def stage(snapshot, source):
        (source / job.BINDING).parent.mkdir(parents=True)
        shutil.copyfile(binding, source / job.BINDING)
        return {"sha256": "fixture"}

    monkeypatch.setattr(WeightInput, "stage", weights)
    monkeypatch.setattr('behave.runtime.snapshots.stage', stage)
    monkeypatch.setattr(provision, "environment", lambda *a, **kw: {})
    monkeypatch.setattr(provision, "run_service", lambda *a, **kw: uncertain(status))
    monkeypatch.setattr(serving, "ServingSession", lambda *a, **kw: pytest.fail("failed preparation launched model"))
    output = tmp_path / "evidence"
    try:
        assert job.run(tmp_path / "snapshot", output, "sglang", weight_input=path,
                       weight_reference=BlobRef.of(path.read_bytes()).as_dict()) == 1
        result = parse_json((output / "result.json").read_bytes())
        assert result["passed"] is False and result["failure_phase"] == "installation"
        assert "private" not in str(result)
        if status != "timeout":
            assert result["cleanup_complete"] is False
            assert Path(result["retained_scratch"]) == scratch and scratch.is_dir()
            assert Path(result["retained_weights"]) == staged[0] and staged[0].is_dir()
        else:
            assert not scratch.exists() and not staged[0].exists()
            assert "retained_scratch" not in result
    finally:
        # No subprocess was created; the fixture owns the retained directories.
        for directory in (scratch, *staged):
            if directory.exists():
                shutil.rmtree(directory)


@pytest.mark.parametrize("cleanup", [CleanupState.INCOMPLETE, CleanupState.UNKNOWN])
def test_smoke_client_uncertainty_reaches_the_serving_owner(tmp_path, monkeypatch, cleanup):
    from behave.agent.model import ModelFailure
    from behave.agent.process_model import ProcessModelBackend

    job = load_tool(monkeypatch, "model_service_job")

    def generate(self, request):
        self.cleanup_state = cleanup
        return ModelFailure("unknown", "fixture-client-incomplete")

    monkeypatch.setattr(ProcessModelBackend, "generate", generate)
    with pytest.raises(ServingFailure) as error:
        job.tool_smoke(native("vllm")[0], tmp_path / "attempt")
    assert error.value.cleanup_state is cleanup


@pytest.mark.parametrize("history", ["pending", "malformed", "clean"])
def test_scored_smoke_checks_owner_history_even_on_failure(tmp_path, monkeypatch, history):
    from behave.task_execution.run_store import RunStore
    from behave.train import rollouts

    job = load_tool(monkeypatch, "model_task_smoke")

    def run(**kwargs):
        candidate, = kwargs["plan"].execution.jobs
        key = BlobRef.of(candidate.to_bytes()).sha256
        run = RunStore(kwargs["run"].directory / "execution" / "candidates" / key)
        journal = run.journal("supervision")
        first = journal.append((), "solver_requested" if history != "malformed" else "unrecognized", {})
        if history == "clean":
            journal.append((first,), "solver_settled", {
                "schema": "behave.solver-settlement/1", "cleanup": "complete", "status": "exited",
                "returncode": 0, "elapsed_seconds": .1, "interruption": None, "supervision_errors": []})
        raise RuntimeError("fixture rollout failed after dispatch")

    monkeypatch.setattr(rollouts, "run_rollouts", run)
    if history == "clean":
        with pytest.raises(RuntimeError, match="fixture rollout failed"):
            job.scored_rollout(native("vllm")[0], tmp_path / "rollout", mask=17)
    else:
        with pytest.raises(ServingFailure) as error:
            job.scored_rollout(native("vllm")[0], tmp_path / "rollout", mask=17)
        assert error.value.cleanup_state is CleanupState.UNKNOWN
