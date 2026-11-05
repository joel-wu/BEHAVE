"""One-shot jobs queue before preparation; child fan-out stays within their grant."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from behave.isolation import IsolationStatus
from behave.sim import api, job_isolation
from behave.sim.cpu_admission import reserve_cpus
from behave.sim.replay_parallel import ordered_map
from tests.sim.test_cpu_admission import eventually


def _launcher(monkeypatch):
    seen = []
    monkeypatch.setattr(job_isolation, "_freeze_inputs", lambda n, p: (p, ()))
    monkeypatch.setattr(job_isolation, "_job_spec",
                        lambda *args, **kwargs: seen.append(kwargs) or kwargs)

    def run(spec, *, stdin):
        seen[-1]["payload"] = json.loads(stdin)["payload"]
        return SimpleNamespace(status=IsolationStatus.COMPLETED,
                               stdout=b'{"ok":true,"result":42}')

    monkeypatch.setattr(job_isolation, "run", run)
    return seen


@pytest.mark.parametrize("name, payload", [
    ("echo", {"value": 42}),
    ("behavior_generation_select", {"spec": {"name": "dual"}}),
])
def test_named_job_reuses_enclosing_lane_and_preserves_payload(monkeypatch, name, payload):
    seen = _launcher(monkeypatch)
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        result = ordered_map(lambda _: job_isolation.run_isolated_job(
            name, payload, timeout_s=5), [0], 1)
        assert result == [42]
        assert len(usage.as_dict()["reservations"]) == 1
        assert seen[0]["cpus"] == 1
        assert seen[0]["payload"] == payload
        assert not cpu.snapshot()["quarantined"]


def test_named_job_queues_before_freeze_and_keeps_execution_timeout(monkeypatch):
    seen = _launcher(monkeypatch)
    release = threading.Event()
    with api.CpuPool(1) as cpu, ThreadPoolExecutor(max_workers=2) as callers:
        def hold():
            with api.cpu_scope(cpu.endpoint), reserve_cpus(1):
                assert release.wait(10)

        def invoke():
            with api.cpu_scope(cpu.endpoint):
                return job_isolation.run_isolated_job("echo", {}, timeout_s=7)

        holder = callers.submit(hold)
        try:
            eventually(lambda: cpu.snapshot()["reserved"] == 1)
            waiter = callers.submit(invoke)
            eventually(lambda: cpu.snapshot()["queued"] == 1)
            assert seen == []
        finally:
            release.set()
            holder.result(10)
        assert waiter.result(10) == 42
        assert seen[0]["timeout_s"] == 7


def test_source_analysis_job_admits_parallel_proof_width(monkeypatch):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    seen = _launcher(monkeypatch)
    payload = {'source_layout': True, 'operation': 'bind'}
    with api.CpuPool(3) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        assert job_isolation.run_isolated_job('rtl_exact_analysis', payload, timeout_s=7) == 42
    assert seen[0]['cpus'] == 3 and seen[0]['payload'] == payload
    assert usage.as_dict()['reservations'][0]['workers'] == 3
    assert usage.as_dict()['reservations'][0]['released']


def test_named_job_has_no_cleanup_receipt_on_launcher_interrupt(monkeypatch):
    _launcher(monkeypatch)
    monkeypatch.setattr(job_isolation, "run", lambda *a, **k: (
        _ for _ in ()).throw(KeyboardInterrupt()))
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint):
        with pytest.raises(KeyboardInterrupt):
            job_isolation.run_isolated_job("echo", {}, timeout_s=5)
        eventually(lambda: cpu.snapshot()["quarantined"])


def test_a_payload_spec_does_not_widen_a_job_reservation(monkeypatch):
    seen = _launcher(monkeypatch)
    payload = {"spec": {"name": "dual"}, "seeds": [[1], [2]]}
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        job_isolation.run_isolated_job("behavior_generation_select", payload, timeout_s=5)
    assert seen[0]["cpus"] == 1
    assert seen[0]["payload"] == payload
    assert usage.as_dict()["reservations"][0]["workers"] == 1


def test_sandbox_budget_caps_inner_maps_without_another_broker(monkeypatch):
    monkeypatch.setenv("BEHAVE_JOB_CPU_LIMIT", "1")
    identities = ordered_map(lambda _: threading.get_ident(), range(4), 8)
    assert identities == [threading.get_ident()] * 4


def test_nested_audit_keeps_every_trace_without_starting_a_pool(monkeypatch):
    import concurrent.futures

    from behave.sim.behavior import audit

    monkeypatch.setenv("BEHAVE_JOB_CPU_LIMIT", "1")
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor",
        lambda **kw: pytest.fail("one admitted CPU must not open another audit pool"))
    seen = []
    def check(proc, env, traces, **kwargs):
        seen.append((traces, kwargs))
        return ["same audit result"]
    monkeypatch.setattr(audit, "step_audit", check)
    traces = [[1], [2], [3]]
    assert audit._parallel_step_audit(None, None, traces, None, 8) == ["same audit result"]
    assert seen == [(traces, {"state": None, "workers": 1, "position_plan": None})]
