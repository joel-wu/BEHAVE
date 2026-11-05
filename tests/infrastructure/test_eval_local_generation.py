"""Saved checkpoint generation uses shared serving, cleanup and Eval contracts."""

import json

import pytest

from behave.eval import generation as generation_module
from behave.runtime.records import write_json
from behave.serving.spec import ServingSpec


@pytest.fixture
def served_job(tmp_path, monkeypatch):
    import behave.serving
    from behave.core import process_limits
    from behave.eval import generation as generation_module
    from behave.serving import environment as serving_environment

    root = tmp_path / "measurement"
    root.mkdir()
    events, sessions, probes, limits = [], [], [], []
    cpu_ids = [2, 3, 4, 5]
    spec = ServingSpec(
        runtime="sglang",
        python="/fixture/python",
        package_version="0.5.18",
        torch_version="2.13.0",
        model_path="/cold/original",
        model_revision="original",
        served_model="fixture",
        reasoning_parser="qwen3",
        tool_parser="qwen3_coder",
        cuda_root="/fixture/cuda",
        seed=42,
        tensor_parallel=4,
        context_length=131072,
        lifetime_seconds=None,
        native_arguments=("--watchdog-timeout", "1800"),
    )
    write_json(root / "serving.json", {"spec": spec.as_dict()})
    write_json(root / "trained/configuration.json", {"model_path": "/cold/checkpoint", "model_revision": "hf-identity"})
    write_json(root / "binding.json", {"headers": {"fixture": True}})
    runtime = {
        "output": str(root),
        "source": str(root),
        "original_launch": str(root / "serving.json"),
        "serving_binding": str(root / "binding.json"),
        "scratch_root": str(tmp_path),
        "staged_weights": "/local/verified-weights",
        "cpu_ids": cpu_ids,
        "devices": ["0,1,2,3", "4,5,6,7"],
        "cpu_threads": 6,
        "port_first": 24000,
        "nccl_port_first": 25000,
        "process_limit": None,
        "headers_seconds": 180,
        "serving_limits": {
            "startup_seconds": 1800,
            "lifetime_seconds": None,
            "cleanup_seconds": 300,
            "log_bytes": None,
        },
        "serving_probe_request": str(root / "probe.json"),
    }
    write_json(root / "runtime_settings.json", runtime)
    sampling = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "chat_template_kwargs": {"enable_thinking": True}}
    settings = {
        "tasks": ["one", "two"],
        "setting": "joint",
        "score_on_submit": False,
        "attempt": {"max_turns": 60, "context_tokens": 131072},
        "generation": {"source": "/fenced/prior"},
        "attempt_namespace": "original",
    }
    write_json(root / "sampling.json", sampling)
    write_json(root / "settings.json", settings)
    state = {
        "root": root,
        "events": events,
        "sessions": sessions,
        "probes": probes,
        "runtime": runtime,
        "sampling": sampling,
        "settings": settings,
        "limits": limits,
    }

    class Service:
        def __init__(self, spec, *, scratch, environment, records):
            self.spec, self.environment, self.records = spec, environment, records
            self.index = len(sessions)
            sessions.append(self)

        def start(self, *, cancelled=None):
            events.append(("start", self.index))
            if "startup_barrier" in state:
                state["startup_barrier"].wait()
            if state.get("wait_start") == self.index:
                from test_serving_startup_cancellation import wait_cancelled

                wait_cancelled(cancelled)
                raise RuntimeError("fixture peer cancelled")
            if state.get("fail_start") == self.index:
                raise RuntimeError("fixture startup failed")

        def close(self):
            events.append(("close", self.index))
            if state.get("fail_close") == self.index:
                raise RuntimeError("fixture cleanup failed")
            return {"cleanup_complete": state.get("unclean") != self.index}

    def probe(spec, request, parameters, output):
        probes.append((spec, request, parameters, output))
        return not state.get("fail_probe", False)

    # The worker intentionally owns these process-local settings. Never alter
    # the test runner's real CPU affinity, process limits or scratch selection.
    monkeypatch.setattr(generation_module.os, "sched_setaffinity", lambda pid, cpus: events.append(("cpus", cpus)))
    monkeypatch.setattr(generation_module.os, "sched_getaffinity", lambda pid: set(cpu_ids))
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(process_limits, "configure_worker_process_limit", limits.append)
    monkeypatch.setattr(serving_environment, "environment", lambda spec, scratch, values, **kw: values)
    monkeypatch.setattr(serving_environment, "headers", lambda *a, **kw: ["fixture-includes"])
    monkeypatch.setattr(behave.serving, "ServingSession", Service)
    monkeypatch.setattr(generation_module, "check_running_session", probe)
    return state


def test_served_generation_preserves_sampler_topology_and_recovery(served_job):
    state = served_job
    root = state["root"]

    def evaluate(output):
        assert output == root
        assert {event for event in state["events"] if event[0] == "start"} == {("start", 0), ("start", 1)}
        assert len(state["probes"]) == 2
        updated = json.loads((root / "settings.json").read_text())
        endpoint = updated.pop("endpoint")
        assert updated.pop("isolation_startup_slots") is None
        assert updated == state["settings"]
        assert endpoint["parameters"] == state["sampling"]
        assert endpoint["routing"] == "attempt"
        assert endpoint["worker_urls"] == [
            "http://127.0.0.1:24000/v1/chat/completions",
            "http://127.0.0.1:24001/v1/chat/completions",
        ]
        return 0

    assert generation_module.run_local(root, evaluate=evaluate) == 0
    assert state["limits"] == [None]
    for index, service in enumerate(state["sessions"]):
        assert service.spec.model_path == "/local/verified-weights"
        assert service.spec.model_revision == "hf-identity"
        assert service.spec.seed == 42 and service.spec.tensor_parallel == 4
        assert service.spec.context_length == 131072
        assert service.spec.native_arguments == ("--watchdog-timeout", "1800", "--nccl-port", str(25000 + index))
        assert service.environment["CUDA_VISIBLE_DEVICES"] == state["runtime"]["devices"][index]
    assert all(probe[2] == state["sampling"] for probe in state["probes"])
    assert json.loads((root / "cleanup.json").read_text())["cleanup_complete"] is True
    assert json.loads((root / "runtime.json").read_text())["state"] == "completed"
    assert {event for event in state["events"] if event[0] == "close"} == {("close", 0), ("close", 1)}


@pytest.mark.parametrize("setting", [{}, {"isolation_startup_slots": None}, {"isolation_startup_slots": 4}])
def test_local_worker_preserves_startup_policy_into_saved_generation(served_job, monkeypatch, setting):
    from behave.isolation.startup import startup_slots

    state = served_job
    root = state["root"]
    runtime = json.loads((root / "runtime_settings.json").read_text())
    runtime.update(setting)
    write_json(root / "runtime_settings.json", runtime)
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "1")

    def evaluate(root):
        expected = setting.get("isolation_startup_slots")
        assert startup_slots() == expected
        assert json.loads((root / "settings.json").read_text())["isolation_startup_slots"] == expected
        return 0

    assert generation_module.run_local(root, evaluate=evaluate) == 0
    assert json.loads((root / "runtime_options.json").read_text()) == {
        "isolation_startup_slots": setting.get("isolation_startup_slots")}


@pytest.mark.parametrize("failure", ["start", "probe", "evaluate"])
def test_failure_closes_every_session_without_admitting_early_candidates(served_job, failure):
    state = served_job
    if failure == "start":
        state["fail_start"] = 0
    elif failure == "probe":
        state["fail_probe"] = True
    evaluated = []

    def evaluate(output):
        evaluated.append(output)
        raise RuntimeError("fixture evaluation failed")

    with pytest.raises(RuntimeError):
        generation_module.run_local(state["root"], evaluate=evaluate)
    assert len(evaluated) == (1 if failure == "evaluate" else 0)
    assert {event for event in state["events"] if event[0] == "close"} == {("close", 0), ("close", 1)}
    assert json.loads((state["root"] / "cleanup.json").read_text())["cleanup_complete"] is True


def test_uncertain_cleanup_is_not_reported_as_complete(served_job):
    from behave.serving import ServingFailure

    served_job["unclean"] = 1
    with pytest.raises(ServingFailure, match="cleanup-unknown"):
        generation_module.run_local(served_job["root"], evaluate=lambda _: 7)
    cleanup = json.loads((served_job["root"] / "cleanup.json").read_text())
    assert cleanup["cleanup_complete"] is False
    assert json.loads((served_job["root"] / "runtime.json").read_text())["state"] == "failed"


def test_completion_is_not_published_before_cleanup_receipt(served_job, monkeypatch):
    original = generation_module.write_json
    root = served_job["root"]
    completed = []

    def write(path, value):
        if path == root / "runtime.json" and value["state"] == "completed":
            assert json.loads((root / "cleanup.json").read_text())["cleanup_complete"] is True
            completed.append(value)
        return original(path, value)

    monkeypatch.setattr(generation_module, "write_json", write)
    assert generation_module.run_local(root, evaluate=lambda _: 0) == 0
    assert len(completed) == 1


@pytest.mark.parametrize("primary", [None, "start", "evaluate"])
def test_failed_close_is_recorded_without_hiding_primary_error(served_job, primary):
    state = served_job
    state["fail_close"] = 1
    if primary == "start":
        state["fail_start"] = 0

    def evaluate(_):
        if primary == "evaluate":
            raise RuntimeError("fixture evaluation failed")
        return 0

    reason = {None: "fixture cleanup failed", "start": "fixture startup failed",
              "evaluate": "fixture evaluation failed"}[primary]
    with pytest.raises(RuntimeError, match=reason):
        generation_module.run_local(state["root"], evaluate=evaluate)
    assert {event for event in state["events"] if event[0] == "close"} == {("close", 0), ("close", 1)}
    report = json.loads((state["root"] / "cleanup.json").read_text())
    assert report["cleanup_complete"] is False and len(report["services"]) == 2
    assert report["services"][0]["cleanup_complete"] is True
    assert report["services"][1]["cleanup_state"] == "unknown"
    assert json.loads((state["root"] / "runtime.json").read_text())["state"] == "failed"


def test_later_engine_failure_cancels_earlier_waiter_and_still_records_cleanup(served_job):
    from threading import Barrier

    state = served_job
    state.update(startup_barrier=Barrier(2, timeout=3), wait_start=0, fail_start=1)
    with pytest.raises(RuntimeError, match="fixture startup failed"):
        generation_module.run_local(state["root"], evaluate=lambda _: pytest.fail("must not admit candidates"))
    assert {row for row in state["events"] if row[0] == "close"} == {("close", 0), ("close", 1)}
    assert json.loads((state["root"] / "cleanup.json").read_text())["cleanup_complete"] is True


def test_repository_entry_supplies_existing_eval_runner(tmp_path, monkeypatch):
    from behave.eval import generation as generation_module
    from tools import eval_local_generation

    calls = []
    monkeypatch.setattr(generation_module, "run", lambda root, *, status_output: calls.append((root, status_output)) or 13
    )
    monkeypatch.setattr(generation_module, "run_local", lambda root, *, evaluate: evaluate(root))
    assert eval_local_generation.worker is generation_module.worker
    assert eval_local_generation.worker(str(tmp_path)) == 13
    assert calls == [(tmp_path, tmp_path)]
