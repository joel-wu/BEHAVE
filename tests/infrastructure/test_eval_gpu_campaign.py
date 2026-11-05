"""Targeted GPU recovery reuses the existing eight-engine rollout driver."""

import json
import os
import socket
from unittest.mock import patch

import pytest

from tools.eval_review_trial import write_json


@pytest.fixture(autouse=True)
def worker_environment():
    # Production runs this worker in its own process. Direct unit calls must
    # not leak its scratch/replay environment into later judge/TB tests.
    with patch.dict(os.environ):
        yield


def test_submit_requires_explicit_allocation(tmp_path, monkeypatch, capsys):
    from tools import eval_gpu_campaign as campaign

    monkeypatch.setattr("sys.argv", ["eval_gpu_campaign", "submit", str(tmp_path)])
    monkeypatch.setattr(campaign, "submit", lambda *a, **kw: pytest.fail("must not submit"))
    with pytest.raises(SystemExit) as error:
        campaign.main()
    assert error.value.code == 2
    assert "submit requires --allocation" in capsys.readouterr().err


def test_submit_uses_requested_allocation(tmp_path, monkeypatch):
    from tools import eval_gpu_campaign as campaign

    calls = []
    monkeypatch.setattr("sys.argv", ["eval_gpu_campaign", "submit", str(tmp_path), "--allocation", "123"])
    monkeypatch.setattr(campaign, "submit", lambda root, allocation, **kw: calls.append((root, allocation)))
    campaign.main()
    assert calls == [(tmp_path.resolve(), 123)]


def test_prepare_does_not_require_allocation(tmp_path, monkeypatch):
    from tools import eval_gpu_campaign as campaign

    calls = []
    configuration = tmp_path / "config.json"
    monkeypatch.setattr("sys.argv", ["eval_gpu_campaign", "prepare", str(tmp_path),
                                     "--configuration", str(configuration)])
    monkeypatch.setattr(campaign, "prepare", lambda root, config: calls.append((root, config)))
    campaign.main()
    assert calls == [(tmp_path.resolve(), configuration)]


def test_gpu_repair_selects_only_explicit_unsubmitted_infra_failures(tmp_path, monkeypatch):
    from tools.eval_gpu_campaign import repair_targets

    manifest = {"runs": [{"name": "gpu_joint", "model": "gpu", "provider": "sglang"},
                         {"name": "gpu_rtl_only", "model": "gpu", "provider": "sglang"},
                         {"name": "hosted_joint", "model": "hosted", "provider": "openai"}]}
    code = "model-unknown:chat-response-invalid"
    write_json(tmp_path / "gpu_runtime.json", {"state": "running", "host": socket.gethostname()})
    with pytest.raises(ValueError, match="engines exit"):
        repair_targets(tmp_path, manifest, (code,))
    write_json(tmp_path / "gpu_runtime.json", {"state": "archived", "host": socket.gethostname()})
    outcomes = {"broken_transport": {"result": {"submission": None, "judgment": None, "failure_code": code}},
                "no_submit": {"result": {"submission": None, "judgment": None, "failure_code": None}},
                "submitted": {"result": {"submission": {"rtl": "fixture"}, "judgment": None,
                                          "failure_code": code}}}
    monkeypatch.setattr("behave.eval.followup.load_outcomes",
                        lambda paths: outcomes if paths[0].name == "gpu_joint" else {})
    prepared = []
    monkeypatch.setattr("behave.eval.followup.prepare", lambda *a, **kw: prepared.append((a, kw)))
    targets = repair_targets(tmp_path, manifest, (code,))
    assert len(targets) == len(prepared) == 1
    assert targets[0]["name"] == "gpu_joint" and targets[0]["tasks"] == 1
    assert targets[0]["directory"] == tmp_path / "runs/gpu_joint/gpu_joint_infra_retry"
    assert prepared[0][1] == {"failure_codes": (code,)}


@pytest.mark.parametrize("startup_failure", [False, True])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_gpu_repair_reuses_engines_and_does_not_overwrite_original_evidence(
        tmp_path, monkeypatch, startup_failure, cleanup_failure):
    from threading import Barrier

    from test_serving_startup_cancellation import wait_cancelled

    from tools import eval_gpu_campaign as campaign

    code = "model-unknown:chat-response-invalid"
    original_state = {"state": "archived", "host": socket.gethostname(), "local_directory": "original"}
    write_json(tmp_path / "gpu_runtime.json", original_state)
    write_json(tmp_path / "campaign.json", {"configuration": {"attempt": {"context_tokens": 131072},
        "models": [{"id": "model", "model": "fixture", "provider": "sglang"},
                   {"id": "not_selected", "model": "absent", "provider": "sglang"}]}, "runs": []})
    write_json(tmp_path / "release/objects/fixture.json", {})
    write_json(tmp_path / "weights/model.json", {"path": str(tmp_path / "input_weights"),
                                               "model": "fixture", "revision": "original-revision"})
    write_json(tmp_path / "input_weights/config.json", {})
    targets = []
    for setting in ("joint", "rtl_only"):
        name = "model_" + setting
        directory = tmp_path / "runs" / name / (name + "_infra_retry")
        write_json(directory / "settings.json", {"tasks": [setting], "score_on_submit": False,
                                                "retry": {"selection": "original-selection"}})
        write_json(directory / "selection.json", {"sources": [str(directory.parent)], "rollout": [setting]})
        targets.append({"name": name, "model": "model", "directory": directory})
    monkeypatch.setattr(campaign, "repair_targets", lambda *_: targets)
    local = tmp_path / "scratch"
    local.mkdir()
    monkeypatch.setattr(campaign, "mkdtemp", lambda **_: str(local))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3,4,5,6,7")
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    monkeypatch.setattr("behave.serving.environment.environment", lambda *_a, **_k: {})
    monkeypatch.setattr("behave.serving.environment.inspect_installation", lambda *_a, **_k: {})
    monkeypatch.setattr("behave.serving.environment.headers", lambda *_a, **_k: ())
    started, closed, dispatched = [], [], []
    barrier = Barrier(8, timeout=3)

    class Session:
        def __init__(self, spec, **_):
            self.spec = spec

        def start(self, *, cancelled=None):
            started.append(self.spec.port)
            if startup_failure:
                barrier.wait()
                if self.spec.port == 32007:
                    raise RuntimeError("last engine startup failed")
                wait_cancelled(cancelled)
                raise RuntimeError("peer startup cancelled")

        def close(self):
            closed.append(self.spec.port)
            if cleanup_failure and self.spec.port == 32000:
                raise RuntimeError("first engine cleanup failed")
            return {"state": "closed", "cleanup_complete": True}

    monkeypatch.setattr("behave.serving.session.ServingSession", Session)

    def child(argv, log, stopped):
        settings = json.loads((log.parent / "settings.json").read_text())
        assert not settings["score_on_submit"]
        assert settings["endpoint"]["routing"] == "attempt"
        assert len(settings["endpoint"]["worker_urls"]) == 8
        assert settings["retry"]["selection"] == str(log.parent / "selection.json")
        dispatched.append(settings["tasks"])
        return 0

    monkeypatch.setattr(campaign, "_child", child)
    if startup_failure or cleanup_failure:
        with pytest.raises(RuntimeError, match="last engine startup failed" if startup_failure
                           else "first engine cleanup failed"):
            campaign.run(tmp_path, failure_codes=(code,))
        assert dispatched == ([] if startup_failure else [["joint"], ["rtl_only"]])
        assert sorted(started) == sorted(closed) == list(range(32000, 32008))
        assert json.loads((tmp_path / "gpu_runtime.json").read_text()) == original_state
        assert (tmp_path / "repair_engines/model/cleanup.json").exists()
        receipts = json.loads((tmp_path / "repair_engines/model/cleanup.json").read_text())
        assert len(receipts) == 8 and receipts[0]["cleanup_complete"] is not cleanup_failure
        assert all(receipt["cleanup_complete"] for receipt in receipts[1:])
        assert json.loads((tmp_path / "gpu_repair_runtime.json").read_text())["state"] == "failed"
        return
    assert campaign.run(tmp_path, failure_codes=(code,)) == 0
    assert dispatched == [["joint"], ["rtl_only"]]
    assert sorted(started) == sorted(closed) == list(range(32000, 32008))
    assert json.loads((tmp_path / "gpu_runtime.json").read_text()) == original_state
    assert json.loads((tmp_path / "gpu_repair_runtime.json").read_text())["state"] == "archived"
    assert (tmp_path / "repair_engines/model/cleanup.json").exists()
    assert not (tmp_path / "engines").exists()


@pytest.mark.parametrize("parallel_models", [1, 2])
def test_extra_models_get_declared_device_groups_and_sticky_routes(tmp_path, monkeypatch, parallel_models):
    from behave.core.resources import allocated_cpus
    from tools import eval_gpu_campaign as campaign

    cpus = set(allocated_cpus())
    if len(cpus) < parallel_models:
        pytest.skip("one CPU per concurrent model lane required for this check")
    initial_affinity = os.sched_getaffinity(0)

    models = [{"id": key, "model": key, "provider": "sglang"} for key in ("small", "smaller")]
    runs = [{"name": model["id"] + "_joint", "model": model["id"], "provider": "sglang"}
            for model in models]
    write_json(tmp_path / "campaign.json", {"configuration": {"models": models,
               "attempt": {"context_tokens": 131072}}, "runs": runs})
    write_json(tmp_path / "gpu_handoff.json", {"delegated_models": ["small", "smaller"]})
    write_json(tmp_path / "gpu_runtime.json", {"state": "running", "local_directory": "original"})
    write_json(tmp_path / "release/objects/fixture.json", {})
    for model, row in zip(models, runs):
        write_json(tmp_path / "weights" / (model["id"] + ".json"),
                   {"path": str(tmp_path / "weights_source" / model["id"]),
                    "model": model["model"], "revision": "original"})
        write_json(tmp_path / "weights_source" / model["id"] / "config.json", {})
        write_json(tmp_path / "runs" / row["name"] / "settings.json", {"score_on_submit": False})
    local = tmp_path / "scratch"
    local.mkdir()
    monkeypatch.setattr(campaign, "mkdtemp", lambda **_: str(local))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3,4,5,6,7")
    monkeypatch.setattr(campaign.signal, "signal", lambda *_: None)
    monkeypatch.setattr("behave.serving.environment.environment", lambda _s, _d, env, **_: env)
    monkeypatch.setattr("behave.serving.environment.inspect_installation", lambda *_a, **_k: {})
    monkeypatch.setattr("behave.serving.environment.headers", lambda *_a, **_k: ())
    devices, routes, lifecycle, cpu_routes = {}, [], [], []

    class Session:
        def __init__(self, spec, *, environment, **_):
            self.spec = spec
            devices[spec.port] = environment["CUDA_VISIBLE_DEVICES"]

        def start(self, *, cancelled=None):
            lifecycle.append(("start", self.spec.served_model))

        def close(self):
            lifecycle.append(("close", self.spec.served_model))
            return {"state": "closed", "cleanup_complete": True}

    monkeypatch.setattr("behave.serving.session.ServingSession", Session)

    def child(argv, log, stopped):
        settings = json.loads((log.parent / "settings.json").read_text())
        assert not settings["score_on_submit"] and settings["endpoint"]["routing"] == "attempt"
        routes.append(set(settings["endpoint"]["worker_urls"]))
        cpu_routes.append(set(os.sched_getaffinity(0)))
        return 0

    monkeypatch.setattr(campaign, "_child", child)
    assert campaign.run(tmp_path, models=("small", "smaller"), parallel_models=parallel_models) == 0
    assert len(routes) == 2 and len(routes[0]) == len(routes[1]) == 8 // parallel_models
    if parallel_models == 2:
        assert routes[0].isdisjoint(routes[1])
        assert cpu_routes[0].isdisjoint(cpu_routes[1])
        assert cpu_routes[0] | cpu_routes[1] == cpus
    else:
        assert routes[0] == routes[1]
        assert cpu_routes == [cpus, cpus]
        assert lifecycle == ([('start', 'small')] * 8 + [('close', 'small')] * 8
                             + [('start', 'smaller')] * 8 + [('close', 'smaller')] * 8)
    assert len(set(devices.values())) == len(devices) == 8
    assert json.loads((tmp_path / "gpu_runtime.json").read_text())["state"] == "running"
    assert json.loads((tmp_path / "gpu_extra_runtime.json").read_text())["state"] == "archived"
    assert os.sched_getaffinity(0) == initial_affinity
