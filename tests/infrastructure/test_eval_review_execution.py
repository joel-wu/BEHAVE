"""Resume one retained review; archive-only retries never repeat generation."""

import json
import signal
import socket
from dataclasses import replace
from pathlib import Path

import pytest
from test_rejudge import local_rollout  # noqa: F401 -- shared local solver fixture

from behave.agent.attempt import AttemptInterrupted
from behave.core.records import RecordError
from behave.eval import followup as execution
from behave.eval import generation as generation_module
from behave.runtime.records import write_json


def read(path):
    return json.loads(path.read_text())


@pytest.fixture
def review(tmp_path, monkeypatch):
    output, node = tmp_path / "archive", tmp_path / "node"
    node.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(node))
    write_json(output / "settings.json", {"tasks": ["fixture"], "generation_parameters": {"seed": 17}})
    return output, node


@pytest.mark.parametrize("tasks", [[], ["fixture"]])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_cancelled_rejudging_does_not_start_generation_or_mark_review_complete(
        review, monkeypatch, tasks, signum):
    from behave.eval import followup as followup_module

    output, node = review
    write_json(output / "settings.json", {"tasks": tasks, "retry": {"selection": "fixture"}})
    calls = []
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def rejudge(local, *, status_output, cancelled):
        assert status_output == output and not cancelled()
        calls.append(("rejudge", local))
        write_json(local / "rejudged.json", {"fixture": "committed result"})
        if len(calls) == 1:
            signal.getsignal(signum)(signum, None)
            assert cancelled()
        return {"fixture": "committed result"}

    def generate(local, *, status_output):
        assert len(calls) == 2, "cancelled scoring must not dispatch model work"
        assert status_output == output
        calls.append(("generation", local))
        return 0

    monkeypatch.setattr(followup_module, "run_rejudgments", rejudge)
    monkeypatch.setattr(generation_module, "run", generate)
    assert execution.run_review(output) == 1
    runtime = read(output / "runtime.json")
    local = Path(runtime["local_directory"])
    assert runtime["state"] == "failed-retained" and runtime["exit_code"] == 1
    assert local.is_dir() and not (local / "review_finished.json").exists()
    assert read(output / "rejudged.json") == {"fixture": "committed result"}
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    assert execution.run_review(output, resume=True) == 0
    assert calls == [("rejudge", local), ("rejudge", local)] + (
        [("generation", local)] if tasks else [])
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    assert read(output / "runtime.json")["state"] == "archived"
    assert list(node.iterdir()) == []


@pytest.mark.parametrize("state", ["failed-retained", "running"])
def test_resume_reuses_the_same_settings_and_partial_journal(review, monkeypatch, state):
    output, node = review
    calls = []

    def run(local, *, status_output):
        assert status_output == output
        calls.append(local)
        if len(calls) == 1:
            write_json(local / "run/partial.json", {"completed": "first"})
            raise OSError("interrupted worker")
        assert read(local / "run/partial.json") == {"completed": "first"}
        assert (local / "settings.json").read_bytes() == (output / "settings.json").read_bytes()
        write_json(local / "result.json", {"completed": "second"})
        return 0

    monkeypatch.setattr(generation_module, "run", run)
    with pytest.raises(OSError, match="interrupted worker"):
        execution.run_review(output)
    runtime = read(output / "runtime.json")
    assert runtime["state"] == "failed-retained"
    write_json(output / "runtime.json", {**runtime, "state": state})
    with pytest.raises(RuntimeError, match="existing node-local run"):
        execution.run_review(output)
    assert execution.run_review(output, resume=True) == 0
    assert len(calls) == 2 and calls[0] == calls[1]
    assert read(output / "run/partial.json") == {"completed": "first"}
    assert read(output / "result.json") == {"completed": "second"}
    assert read(output / "runtime.json")["state"] == "archived"
    assert list(node.iterdir()) == []
    assert execution.run_review(output, resume=True) == 0 and len(calls) == 2


@pytest.mark.parametrize("boundary", ["archive_before", "archive_after", "terminal_status", "terminal_after", "finish_receipt"])
def test_publication_retry_does_not_execute_again(review, monkeypatch, boundary):
    output, node = review
    calls = []

    def run(local, **_):
        calls.append(local)
        write_json(local / "result.json", {"score": 1})
        return 0

    monkeypatch.setattr(generation_module, "run", run)
    archive, write, publish = execution.archive_run, execution.write_json, execution.publish_json

    def fail_archive(*args):
        if boundary == "archive_before":
            raise OSError("publication interrupted")
        archive(*args)
        if boundary == "archive_after":
            raise OSError("publication interrupted")

    def fail_status(path, value, **kwargs):
        if boundary == "terminal_status" and value.get("state") == "archived":
            raise OSError("publication interrupted")
        write(path, value, **kwargs)
        if boundary == "terminal_after" and value.get("state") == "archived":
            raise OSError("publication interrupted")

    def fail_receipt(path, value):
        publish(path, value)
        if boundary == "finish_receipt" and path.name == "review_finished.json":
            raise OSError("publication interrupted")

    with monkeypatch.context() as fault:
        fault.setattr(execution, "archive_run", fail_archive)
        fault.setattr(execution, "write_json", fail_status)
        fault.setattr(execution, "publish_json", fail_receipt)
        with pytest.raises(OSError, match="publication interrupted"):
            execution.run_review(output)
    assert calls[0].is_dir()
    assert (read(output / "runtime.json")["state"] == "archived") is (boundary == "terminal_after")
    monkeypatch.setattr(generation_module, "run", lambda *_a, **_k: pytest.fail("must only finish archive"))
    assert execution.run_review(output, resume=True) == 0
    assert read(output / "result.json") == {"score": 1}
    assert list(node.iterdir()) == []


def test_nonzero_exit_is_not_success_and_keeps_recovery_state(review, monkeypatch):
    output, node = review
    monkeypatch.setattr(generation_module, "run", lambda *_a, **_k: 7)
    assert execution.run_review(output) == 7
    runtime = read(output / "runtime.json")
    assert runtime["state"] == "failed-retained" and runtime["exit_code"] == 7
    local = Path(runtime["local_directory"])
    assert local.exists() and not (local / "review_finished.json").exists()
    monkeypatch.setattr(generation_module, "run", lambda *_a, **_k: 0)
    assert execution.run_review(output, resume=True) == 0 and list(node.iterdir()) == []


def test_unknown_worker_cleanup_blocks_resume_and_scratch_release(review, monkeypatch):
    from behave.task_execution.run_store import RunStore

    output, _ = review

    def run(local, **_):
        RunStore(local / "run/candidates/c").journal("supervision").append((), "solver_requested", {})
        return 0  # A function result is not an old worker cleanup witness.

    monkeypatch.setattr(generation_module, "run", run)
    with pytest.raises(RecordError, match="old execution"):
        execution.run_review(output)
    runtime = read(output / "runtime.json")
    assert runtime["state"] == "failed-retained"
    local = Path(runtime["local_directory"])
    assert local.is_dir() and not (local / "review_finished.json").exists()
    monkeypatch.setattr(generation_module, "run", lambda *_a, **_k: pytest.fail("unknown cleanup cannot dispatch"))
    with pytest.raises(RecordError, match="old execution"):
        execution.run_review(output, resume=True)
    assert read(output / "runtime.json") == runtime and local.is_dir()


@pytest.mark.parametrize("change", ["host", "protocol", "settings", "local_settings", "owner", "missing", "symlink"])
def test_changed_or_missing_recovery_evidence_never_creates_another_attempt(review, monkeypatch, change):
    output, node = review
    monkeypatch.setattr(generation_module, "run", lambda *_a, **_k: (_ for _ in ()).throw(OSError("stopped")))
    with pytest.raises(OSError):
        execution.run_review(output)
    runtime = read(output / "runtime.json")
    local = Path(runtime["local_directory"])
    if change == "host":
        write_json(output / "runtime.json", {**runtime, "host": "another-node"})
    elif change == "protocol":
        write_json(output / "runtime.json", {**runtime, "owner_protocol": "legacy"})
    elif change in {"settings", "local_settings"}:
        write_json((output if change == "settings" else local) / "settings.json", {"changed": True})
    elif change == "owner":
        write_json(local / "review_owner.json", {"output": "another-run"})
    else:
        moved = node / "retained-fixture"
        local.rename(moved)
        if change == "symlink":
            local.symlink_to(moved, target_is_directory=True)
    before = set(node.iterdir())
    monkeypatch.setattr(generation_module, "run", lambda *_a, **_k: pytest.fail("must not redispatch"))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        execution.run_review(output, resume=True)
    assert set(node.iterdir()) == before


def test_live_coordinator_excludes_a_second_resume(review, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    output, _ = review
    entered, finish = Event(), Event()

    def run(local, **_):
        entered.set()
        assert finish.wait(10)
        return 0

    monkeypatch.setattr(generation_module, "run", run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execution.run_review, output)
        try:
            assert entered.wait(10)
            with pytest.raises(AttemptInterrupted, match="run-already-owned"):
                execution.run_review(output, resume=True)
        finally:
            finish.set()
        assert future.result() == 0


def test_real_batch_reuses_committed_candidates_after_summary_failure(
        review, publication, local_rollout, monkeypatch):  # noqa: F811 -- fixture
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.task_execution import batch as batch_module
    from behave.task_execution.batch_plan import BatchPlan

    output, node = review
    write_json(output / "settings.json", {
        "provider": "sglang", "endpoint": LocalChatEndpoint(
            "http://127.0.0.1:31000/v1/chat/completions", "fixture", "fixture").as_dict(),
        "tasks": [publication.task.task_id], "task_objects": str(publication.root),
        "release": publication.ref.as_dict(), "solver_settings": ["joint", "rtl_only"],
        "score_on_submit": False, "attempt": {"max_turns": 3},
        "workers": 2, "model_workers": 2, "judge_workers": 1})
    monkeypatch.setattr("behave.sim.api.prepare_runtime", lambda **_: None)
    monkeypatch.setattr(generation_module, "check_development_shell", lambda *_: None)
    monkeypatch.setattr("behave.runtime.records.signal.signal", lambda *_: None)
    monkeypatch.setattr("behave.eval.plan.EvaluationPlan",
                        lambda *a, **kw: replace(BatchPlan(*a, **kw), model_admission=None))
    summarize = batch_module.summarize_recorded
    with monkeypatch.context() as fault:
        fault.setattr(batch_module, "summarize_recorded", lambda *_a, **_k: (_ for _ in ()).throw(OSError("summary failed")))
        with pytest.raises(OSError, match="summary failed"):
            execution.run_review(output)
    assert len(local_rollout) == 2
    assert read(output / "runtime.json")["host"] == socket.gethostname()
    monkeypatch.setattr(batch_module, "summarize_recorded", summarize)
    assert execution.run_review(output, resume=True) == 0
    assert len(local_rollout) == 2  # Both originally committed candidates are replayed, not regenerated.
    assert read(output / "report.json")["requested"] == 2
    assert list(node.iterdir()) == []
