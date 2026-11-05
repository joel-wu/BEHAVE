"""One allocation across shell, Sim tool, final judge and deferred judging."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.agent.cpu_resources import CpuResources, cpu_job, current_cpu_lease
from behave.agent.local_rpc import exchange
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, parse_json


def until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "resource operation did not progress"
        time.sleep(.005)


@pytest.fixture
def resources(monkeypatch):
    if len(os.sched_getaffinity(0)) < 2:
        pytest.skip("two allocated CPUs needed for concurrent resource checks")
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "2")
    return CpuResources(2)


def test_partition_uses_all_cpus_without_overlap(monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda _: set(range(11)))
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "11")
    pool = CpuResources(3)
    leases = [pool.acquire(lambda: False) for _ in range(3)]
    assert sorted(len(lease.cpus) for lease in leases) == [3, 4, 4]
    assert sorted(cpu for lease in leases for cpu in lease.cpus) == list(range(11))
    for lease in leases:
        pool.release(lease, CleanupState.COMPLETE)
    assert pool.snapshot()["available_cpus"] == 11


def test_snapshot_replaces_readonly_restore_without_changing_archive(tmp_path, resources):
    archived = tmp_path / "archived.json"
    archived.write_bytes(b'{"old":true}')
    archived.chmod(0o444)
    restored = tmp_path / "resources.json"
    os.link(archived, restored)
    resources.write_snapshot(restored)
    assert parse_json(restored.read_bytes()) == resources.snapshot()
    assert archived.read_bytes() == b'{"old":true}'
    assert archived.stat().st_mode & 0o777 == 0o444
    assert archived.stat().st_ino != restored.stat().st_ino
    resources.write_snapshot(restored)
    assert not list(tmp_path.glob(".resources.json.*.tmp"))


def test_cpu_jobs_queue_without_limiting_idle_rollout_count(resources):
    before = os.sched_getaffinity(0)
    release, third = Event(), Event()
    seen = []

    def job(waiting):
        with cpu_job(resources) as lease:
            seen.append(set(os.sched_getaffinity(0)))
            assert set(lease.cpus) == seen[-1]
            if waiting:
                assert release.wait(5)
            else:
                third.set()
            lease.cleanup = CleanupState.COMPLETE
        assert os.sched_getaffinity(0) == before

    assert resources.snapshot()["active"] == 0
    with ThreadPoolExecutor(3) as threads:
        first = [threads.submit(job, True) for _ in range(2)]
        try:
            until(lambda: len(seen) == 2)
            assert not seen[0] & seen[1]
            last = threads.submit(job, False)
            until(lambda: resources.snapshot()["queued"] == 1)
            assert not third.is_set()
        finally:
            release.set()
        for future in [*first, last]:
            future.result(timeout=5)
    assert resources.snapshot()["active"] == resources.snapshot()["queued"] == 0
    assert os.sched_getaffinity(0) == before


def test_cancelled_wait_never_consumes_a_cpu(resources):
    held = [resources.acquire(lambda: False) for _ in range(2)]
    cancel = Event()
    with ThreadPoolExecutor(1) as thread:
        waiting = thread.submit(resources.acquire, cancel.is_set)
        until(lambda: resources.snapshot()["queued"] == 1)
        cancel.set()
        with pytest.raises(AttemptInterrupted, match="resource-cancelled"):
            waiting.result(timeout=5)
    for lease in held:
        resources.release(lease, CleanupState.COMPLETE)
    assert resources.snapshot()["available_cpus"] == 2


def test_unknown_cleanup_retires_only_its_cpu_set(resources):
    lost = resources.acquire(lambda: False)
    resources.release(lost, CleanupState.UNKNOWN)
    with cpu_job(resources) as live:
        assert not set(lost.cpus) & set(live.cpus)
        live.cleanup = CleanupState.COMPLETE
    assert resources.snapshot()["retired"] == 1
    assert resources.snapshot()["active"] == 0
    second = resources.acquire(lambda: False)
    resources.release(second, CleanupState.INCOMPLETE)
    with pytest.raises(AttemptInterrupted, match="resource-cleanup"):
        resources.acquire(lambda: False)


def test_child_process_inherits_only_its_granted_cpus(resources):
    before = os.sched_getaffinity(0)
    with cpu_job(resources) as lease:
        output = subprocess.check_output([sys.executable, "-c",
            "import json, os; print(json.dumps(sorted(os.sched_getaffinity(0))))"])
        assert json.loads(output) == list(lease.cpus)
        with cpu_job(resources) as nested:
            assert nested is lease and resources.snapshot()["active"] == 1
        lease.cleanup = CleanupState.COMPLETE
    assert os.sched_getaffinity(0) == before


def test_failed_affinity_setup_does_not_retire_unused_cpus(resources, monkeypatch):
    before = os.sched_getaffinity(0)
    set_affinity = os.sched_setaffinity

    def bind(pid, cpus):
        if set(cpus) != before:
            raise OSError("binding failed before executor start")
        set_affinity(pid, cpus)

    monkeypatch.setattr(os, "sched_setaffinity", bind)
    with pytest.raises(OSError, match="before executor"):
        with cpu_job(resources):
            pytest.fail("no executor should run after a failed binding")
    assert resources.snapshot()["active"] == resources.snapshot()["retired"] == 0
    assert resources.snapshot()["available_cpus"] == 2


def test_remote_worker_releases_between_tool_calls(resources):
    with resources.relay() as relay:
        for _ in range(2):
            with cpu_job(relay.endpoint) as lease:
                assert resources.snapshot()["active"] == 1
                lease.cleanup = CleanupState.COMPLETE
            assert resources.snapshot()["active"] == 0
        relay.worker_cleanup = CleanupState.COMPLETE


@pytest.mark.parametrize("cleanup,retired", [(CleanupState.COMPLETE, 0), (CleanupState.UNKNOWN, 1)])
def test_lost_worker_reply_released_only_after_supervisor_cleanup(resources, cleanup, retired):
    with resources.relay() as relay:
        reply = parse_json(exchange(relay.endpoint, canonical_json({"op": "acquire"}),
                                    deadline=None, cancelled=lambda: False))
        assert reply["cpus"] and resources.snapshot()["active"] == 1
        # No release message, e.g. worker killed while executing the command.
        relay.worker_cleanup = cleanup
    assert resources.snapshot()["retired"] == retired
    assert resources.snapshot()["active"] == 0


def test_judge_and_tb_share_resource_owner_and_preserve_result(resources):
    from behave.task_execution.resources import ScheduledJudge, ScheduledTestbench

    receipt = SimpleNamespace(cleanup=CleanupState.COMPLETE, score=0)
    request = SimpleNamespace(cancelled=lambda: False)

    def execute(*_):
        assert current_cpu_lease() is not None
        assert resources.snapshot()["active"] == 1
        return receipt

    backend = SimpleNamespace(identity="fixture", evaluate=execute, observe=execute)
    assert ScheduledJudge(backend, resources).evaluate(request, None) is receipt
    assert ScheduledTestbench(backend, resources).observe(request, None) is receipt
    assert resources.snapshot()["active"] == 0
