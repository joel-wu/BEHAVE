import resource

import pytest

from behave.core.process_limits import configure_worker_process_limit, process_limit


@pytest.mark.parametrize("hard", [resource.RLIM_INFINITY, 600])
def test_no_soft_cap_uses_existing_hard_limit(monkeypatch, hard):
    monkeypatch.setattr(resource, "getrlimit", lambda kind: (300, hard))
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda *args: calls.append(args))
    assert process_limit(None) == hard
    assert calls == []  # The supervisor changes only its child, before unshare.


def test_explicit_runtime_limit_is_not_clamped(monkeypatch):
    monkeypatch.setattr(resource, "getrlimit", lambda kind: (300, resource.RLIM_INFINITY))
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda *args: calls.append(args))
    assert process_limit(4096) == 4096
    assert calls == []


def test_runtime_rejects_invalid_process_limit():
    with pytest.raises(ValueError):
        process_limit(0)


def test_node_worker_applies_runtime_before_forking_preparation(monkeypatch):
    monkeypatch.setattr(resource, "getrlimit", lambda kind: (300, resource.RLIM_INFINITY))
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda *args: calls.append(args))
    assert configure_worker_process_limit(None) == {"previous_soft": 300, "soft": -1, "hard": -1}
    assert calls == [(resource.RLIMIT_NPROC, (-1, -1))]


@pytest.mark.parametrize("recorded", [False, True])
def test_job_entry_sets_runtime_limit_before_installation(tmp_path, monkeypatch, recorded):
    import json
    from contextlib import contextmanager

    from behave.runtime.jobs import node_heartbeat
    from behave.train import jobs
    from tools import slime_recorded_job

    module = slime_recorded_job if recorded else jobs
    output, local, source = (tmp_path / name for name in ("job", "local", "source"))
    for directory in (output, local, source):
        directory.mkdir()
    config = {"native_python": "/native/python", "process_limit": 4096}
    (source / "configuration.json").write_text(json.dumps(config))
    (output / "configuration.json").write_text(json.dumps(config))
    (output / "source_configuration.json").write_text(json.dumps(config))
    (output / "selection.json").write_text(json.dumps({"original_job": str(source), "cpus": 2}))
    monkeypatch.setattr(module, "mkdtemp", lambda **kwargs: str(local))
    monkeypatch.setattr(module.os, "sched_setaffinity", lambda *args: None)
    monkeypatch.setattr("behave.core.resources.allocated_cpus", lambda cpus: {0, 1})
    monkeypatch.setattr("behave.runtime.snapshots.stage", lambda snapshot, checkout: checkout.mkdir())
    order = []

    def configure(limit):
        order.append(("limit", limit))
        return {"previous_soft": 300, "soft": limit, "hard": -1}

    def install(*args):
        assert order == [("limit", 4096)]
        order.append(("install",))
        raise RuntimeError("probe stops before installing or training")

    @contextmanager
    def after_limit(*args, **kwargs):
        assert order == [("limit", 4096)]
        with node_heartbeat(*args, **kwargs):
            yield

    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", configure)
    monkeypatch.setattr("behave.runtime.snapshots.install_runtime", install)
    monkeypatch.setattr("behave.runtime.jobs.node_heartbeat", after_limit)
    with pytest.raises(RuntimeError, match="probe stops"):
        module.staged(str(output))
    assert order == [("limit", 4096), ("install",)]
    assert json.loads((output / "runtime.json").read_text())["process_limit"]["soft"] == 4096


def test_service_sets_limit_before_namespace_without_changing_parent(tmp_path, monkeypatch):
    import sys

    from behave.isolation import service

    calls = []
    before = resource.getrlimit(resource.RLIMIT_NPROC)

    def unavailable(argv, **kwargs):
        calls.append(argv)
        raise FileNotFoundError("fixture launcher")

    monkeypatch.setattr(service.subprocess, "Popen", unavailable)
    service.run_service((sys.executable, "-c", "pass"), cwd=tmp_path,
                        environment={}, deadline=None, process_limit=-1)
    assert calls[0][:4] == ("/usr/bin/prlimit", "--nproc=-1:", "--", "/usr/bin/unshare")
    assert resource.getrlimit(resource.RLIMIT_NPROC) == before
