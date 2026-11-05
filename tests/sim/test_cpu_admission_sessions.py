"""Idle solver sessions keep their formulas, not their CPU permits."""

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from behave.sim import api, job_session
from behave.sim.core.failure import FailureRaised
from behave.sim.cpu_admission import reserve_cpus
from behave.sim.replay_parallel import ordered_map
from tests.sim.test_cpu_admission import eventually


def _transport(monkeypatch, replies=("prepared", "answer", "answer2")):
    child = SimpleNamespace(stdin=io.BytesIO(), stderr=io.BytesIO(), closed=0)
    child.stdout = io.BytesIO(b"".join(json.dumps({
        "version": "test-session/1", "id": i, "ok": True, "wall_ms": 1,
        "result": answer}).encode() + b"\n" for i, answer in enumerate(replies)))
    child.close = lambda: setattr(child, "closed", child.closed + 1)
    prepared = []
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda n, p: (p, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *a, **k: prepared.append(k))
    monkeypatch.setattr(job_session, "start", lambda spec: child)
    return child, prepared


def _open(**kwargs):
    return job_session.JobSession("rtl_source_generation_session", {}, module="test-worker",
        version="test-session/1", timeout_s=5, wall_s=30, exchanges=3, **kwargs)


def test_parallel_session_reserves_child_ceiling_per_exchange(monkeypatch):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    child, prepared = _transport(monkeypatch)
    with api.CpuPool(3) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        session = _open(cpus=8)
        try:
            assert prepared[0]['cpus'] == 3
            assert cpu.snapshot()['reserved'] == 0
            assert session._exchange('probe', {}, 5) == 'answer'
            assert cpu.snapshot()['reserved'] == 0
        finally:
            session.close()
    reservations = usage.as_dict()['reservations']
    assert [r['workers'] for r in reservations[:2]] == [3, 3]
    assert all(r['released'] for r in reservations)
    assert child.closed == 1


def test_idle_session_releases_cpu_and_never_rebuilds_formula(monkeypatch):
    child, prepared = _transport(monkeypatch)
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        session = _open()
        try:
            assert session.opened == "prepared"
            assert cpu.snapshot()["reserved"] == 0
            assert ordered_map(lambda x: x * 2, [3, 4], 1) == [6, 8]
            assert session._exchange("probe", {}, 5) == "answer"
            assert session._exchange("probe", {}, 5) == "answer2"
            assert cpu.snapshot()["reserved"] == 0
        finally:
            session.close()
        assert len(prepared) == 1  # one formula/child, not a restart per grant
        assert len(usage.as_dict()["reservations"]) == 4
        assert child.closed == 1
    frames = [json.loads(row) for row in child.stdin.getvalue().splitlines()]
    assert [row["op"] for row in frames] == ["open", "probe", "probe"]
    assert [row["id"] for row in frames] == [0, 1, 2]


def test_session_crash_keeps_bounded_stderr_and_exit_cause(monkeypatch):
    child, _ = _transport(monkeypatch, replies=())
    limit = job_session.defaults.PYTHON_STDERR_BYTES
    ending = b'Z3 diagnostic: array proof assertion failed\n'
    child.stderr = io.BytesIO(b'x' * (limit * 2) + ending)
    child.wait = lambda **kwargs: -6
    child.final_status = 'signaled'
    child.status_refusal = None
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint):
        with pytest.raises(job_session.IsolatedJobFailure) as caught:
            _open()
        assert cpu.snapshot()['reserved'] == 0
        assert not cpu.snapshot()['quarantined']
    detail = str(caught.value)
    assert 'array proof assertion failed' in detail
    assert '"returncode": -6' in detail
    assert '"truncated": true' in detail
    assert len(detail) < limit + 1000
    assert child.closed == 1


@pytest.mark.parametrize("operation", ["open", "probe"])
def test_session_queues_before_start_or_sending_request(monkeypatch, operation):
    child, prepared = _transport(monkeypatch)
    release = threading.Event()
    with api.CpuPool(1) as cpu, ThreadPoolExecutor(max_workers=2) as threads:
        with api.cpu_scope(cpu.endpoint):
            session = _open() if operation == "probe" else None
        before = child.stdin.getvalue()

        def hold():
            with api.cpu_scope(cpu.endpoint), reserve_cpus(1):
                assert release.wait(10)

        def call():
            with api.cpu_scope(cpu.endpoint):
                return _open() if operation == "open" else session._exchange("probe", {}, 5)

        holder = threads.submit(hold)
        try:
            eventually(lambda: cpu.snapshot()["reserved"] == 1)
            waiter = threads.submit(call)
            eventually(lambda: cpu.snapshot()["queued"] == 1)
            assert child.stdin.getvalue() == before
            assert len(prepared) == (1 if operation == "probe" else 0)
        finally:
            release.set()
            holder.result(10)
        result = waiter.result(10)
        if session is None:
            session = result
        else:
            assert result == "answer"
        session.close()
        assert not cpu.snapshot()["quarantined"]


def test_cancelled_queue_closes_idle_session_without_sending_probe(monkeypatch):
    child, _ = _transport(monkeypatch)
    cancelled = threading.Event()
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint, cancelled=cancelled.is_set):
        session = _open()
        before = child.stdin.getvalue()
        cancelled.set()
        with pytest.raises(FailureRaised):
            session._exchange("probe", {}, 5)
        assert child.stdin.getvalue() == before
        assert child.closed == 1
        assert not cpu.snapshot()["quarantined"]


def test_session_nested_in_replay_reuses_its_lane(monkeypatch):
    child, _ = _transport(monkeypatch)
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        def run(_):
            session = _open()
            try:
                return session._exchange("probe", {}, 5)
            finally:
                session.close()

        assert ordered_map(run, [0], 1) == ["answer"]
        assert len(usage.as_dict()["reservations"]) == 1
        assert child.closed == 1


def test_session_cleanup_exception_does_not_release_active_permit(monkeypatch):
    child, _ = _transport(monkeypatch)
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint):
        session = _open()
        child.stdout = io.BytesIO(b"malformed\n")
        child.close = lambda: (_ for _ in ()).throw(RuntimeError("cleanup failed"))
        with pytest.raises(RuntimeError, match="cleanup failed"):
            session._exchange("probe", {}, 5)
        eventually(lambda: cpu.snapshot()["quarantined"])


def test_session_start_without_a_process_receipt_quarantines(monkeypatch):
    _transport(monkeypatch)
    monkeypatch.setattr(job_session, "start", lambda spec: (
        _ for _ in ()).throw(KeyboardInterrupt()))
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint):
        with pytest.raises(KeyboardInterrupt):
            _open()
        eventually(lambda: cpu.snapshot()["quarantined"])


@pytest.mark.isolation
@pytest.mark.parametrize("wrong", [False, True])
def test_real_pair_search_and_score_share_one_cpu(tmp_path, monkeypatch, wrong):
    from .test_behavior_generation_session import (
        test_behavior_pair_searches_keep_independent_workers_and_public_score as exercise,
    )

    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        exercise(tmp_path, monkeypatch, wrong)
        receipts = usage.as_dict()["reservations"]
        assert len(receipts) > 4  # open, Select/Probe and replay, not one held session
        assert all(row["workers"] == 1 and row["released"] for row in receipts)
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
def test_real_rtl_search_and_score_share_one_cpu(tmp_path, wrong):
    from .test_settled_protocol import (
        test_clockless_source_search_reaches_rare_branch_and_public_score as exercise,
    )

    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint) as usage:
        exercise(tmp_path, wrong)
        receipts = usage.as_dict()["reservations"]
        assert len(receipts) > 4
        assert all(row["workers"] == 1 and row["released"] for row in receipts)
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]
