"""Tool groups consume the enclosing allocation, never another nested pool."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from behave.isolation import IsolationRefusal, IsolationResult, IsolationStatus
from behave.sim import api
from behave.sim.cpu_admission import reserve_cpus
from behave.sim.replay_parallel import ordered_map
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_protocol, tool_runtime
from tests.sim.test_cpu_admission import eventually
from tests.sim.test_rtl_tool_isolation import _runtime


def _groups():
    return (tool_protocol.ToolGroup(tuple(tool_protocol.ToolCommand(str(i), ("/runtime/python",))
                                  for i in range(4)), parallel=4),)


def _launcher(tmp_path, monkeypatch, observe):
    # Frozen profiles outlive a call. Never publish a fake runner into another
    # test's implicit batch, even after monkeypatch restores the factory.
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime-batch"))
    monkeypatch.setattr(tool_runtime, "_runtime", lambda profile: _runtime(tmp_path))

    def run(spec):
        job_mount = next(x for x in spec.read_only if x.target == "/input/job.json")
        observe(json.loads(Path(job_mount.source).read_bytes()), spec)
        return IsolationResult(
            IsolationStatus.REFUSED, spec.digest, "test-backend",
            refusal=IsolationRefusal("test-stop", "boundary inspected"))

    monkeypatch.setattr(tools, "run", run)


def test_nested_tool_groups_serialize_under_existing_permit(tmp_path, monkeypatch):
    seen = []
    _launcher(tmp_path, monkeypatch, lambda job, spec: seen.append(job))
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        results = ordered_map(
            lambda _: tools.run_workspace(tmp_path, _groups(), role="compile"), [0, 1], 1)
        assert all(x.refusal.code == "test-stop" for x in results)
        assert [job["groups"][0]["parallel"] for job in seen] == [1, 1]
        assert all(len(job["groups"][0]["commands"]) == 4 for job in seen)
        assert len(usage.as_dict()["reservations"]) == 1
        assert not cpu.snapshot()["quarantined"]


def test_tool_waits_before_preparation_and_keeps_command_timeout(tmp_path, monkeypatch):
    release = threading.Event()
    prepared = threading.Event()
    seen = []
    _launcher(tmp_path, monkeypatch, lambda job, spec: seen.append((job, spec)))
    original = tools._run_workspace

    def prepare(*args, **kwargs):
        prepared.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(tools, "_run_workspace", prepare)
    with api.CpuPool(4) as cpu, ThreadPoolExecutor(max_workers=2) as callers:
        def hold():
            with api.cpu_scope(cpu.endpoint), reserve_cpus(1):
                assert release.wait(10)

        def invoke():
            with api.cpu_scope(cpu.endpoint) as usage:
                result = tools.run_workspace(tmp_path, _groups(), role="simulate",
                                             limits=tools.default_limits(wall_seconds=7))
            return result, usage.as_dict()

        first = callers.submit(hold)
        try:
            eventually(lambda: cpu.snapshot()["reserved"] == 1)
            second = callers.submit(invoke)
            eventually(lambda: cpu.snapshot()["queued"] == 1)
            assert not prepared.is_set()
            assert not seen
        finally:
            release.set()
            first.result(10)
        result, usage = second.result(10)
        assert result.refusal.code == "test-stop"
        assert usage["reservations"][0]["workers"] == 4
        assert usage["reservations"][0]["released"]
        assert seen[0][0]["groups"][0]["parallel"] == 4
        assert seen[0][1].limits.wall_seconds == 7
        assert seen[0][0]["groups"][0]["commands"][0]["timeout_s"] == _groups()[0].commands[0].timeout_s


def test_tool_group_maximum_fits_smaller_allocation(tmp_path, monkeypatch):
    seen = []
    _launcher(tmp_path, monkeypatch, lambda job, spec: seen.append(job))
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        tools.run_workspace(tmp_path, _groups(), role="compile")
    assert seen[0]["groups"][0]["parallel"] == 2
    assert len(seen[0]["groups"][0]["commands"]) == 4
    assert usage.as_dict()["reservations"][0]["workers"] == 2


def test_missing_backend_cleanup_receipt_quarantines(tmp_path, monkeypatch):
    _launcher(tmp_path, monkeypatch, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint):
        with pytest.raises(KeyboardInterrupt):
            ordered_map(lambda _: tools.run_workspace(tmp_path, _groups(), role="compile"), [0], 1)
        eventually(lambda: cpu.snapshot()["quarantined"])
