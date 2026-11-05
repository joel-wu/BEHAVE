"""Source workload timing, independent EDA, and shared CPU ownership."""

from contextlib import nullcontext
from threading import Barrier
from types import SimpleNamespace

import pytest

from test_ppa import FakeNative
from test_ppa_review import archive_with_candidate

from behave.agent.attempt import AttemptInterrupted
from behave.agent.cpu_resources import CpuResources, current_cpu_lease
from behave.core.lifecycle import CleanupState
from behave.core.resources import allocated_cpus
from behave.ppa.worker import NativePPA
from behave.ppa.workflow import SearchArchive, frontier
from behave.sim import api


def test_source_workload_needs_no_eda_or_mapped_image(tmp_path):
    archive, key = archive_with_candidate(tmp_path)
    archive.action(key, "performance")
    row = archive.candidates[key]
    assert "eda" not in row and "mapped_score" not in row
    assert row["performance"] == {"score": 1, "time_ns": 80., "source": "rtl"}
    assert frontier([row]) == []  # Timing/area are still required.
    archive.action(key, "check")
    archive.action(key, "eda")
    assert frontier([row]) == [key]


def test_pair_captures_one_source_and_settles_once(tmp_path):
    calls = []

    class Native(FakeNative):
        def run_many(self, inputs, exp, modes, pool, objects):
            calls.append((inputs.source.artifact.as_dict(), modes))
            return super().run_many(inputs, exp, modes, pool, objects)

    archive, key = archive_with_candidate(tmp_path, Native())
    results = archive.actions(key, ("eda", "performance"))
    assert calls == [(archive.candidates[key]["source"], ("eda", "performance"))]
    assert list(results) == ["eda", "performance"]
    assert [event.kind for event in archive.events] == ["requested", "settled"]
    assert [row["mode"] for row in archive.candidates[key]["history"]] == ["eda", "performance"]
    restored = SearchArchive(archive.run, archive.task, archive.experiment, Native())
    assert restored.candidates == archive.candidates and restored.calls == 2


def test_interrupted_pair_cannot_be_replayed_as_settled(tmp_path):
    class Broken(FakeNative):
        def run_many(self, *args):
            raise RuntimeError("lost worker")

    archive, key = archive_with_candidate(tmp_path, Broken())
    with pytest.raises(RuntimeError, match="lost worker"):
        archive.actions(key, ("eda", "performance"))
    with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
        SearchArchive(archive.run, archive.task, archive.experiment, Broken())


def test_optional_mapped_failure_does_not_replace_source_measurement(tmp_path):
    class Diagnostic(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            if mode == "mapped":
                return {"score": None, "code": "diagnostic-unavailable"}
            return super().run(inputs, exp, mode, pool, objects)

    archive, key = archive_with_candidate(tmp_path, Diagnostic())
    archive.action(key, "check")
    archive.actions(key, ("eda", "performance"))
    before = archive.candidates[key]["performance"].copy()
    archive.action(key, "mapped")
    assert archive.candidates[key]["performance"] == before
    assert frontier(archive.candidates.values()) == [key]


def test_historical_mapped_failure_is_not_reinterpreted_as_source_success():
    old = {"candidate": "old", "score": 1, "mapped_score": None,
           "performance": {"score": 1, "time_ns": 80.},
           "eda": {"status": "measured", "timing": {"status": "met"}, "area_um2": 10.}}
    assert frontier([old]) == []


@pytest.mark.parametrize("failure", [None, "clean", "unknown"])
@pytest.mark.parametrize("remote", [False, True])
def test_native_pair_partitions_one_lease_and_joins_before_release(monkeypatch, failure, remote):
    if len(allocated_cpus()) < 2:
        pytest.skip("two allocated CPUs needed to demonstrate overlapping branches")
    resources = CpuResources(1, cpus=2)
    barrier = Barrier(2, timeout=5)
    leases = {}

    class Pool:
        def __init__(self, *, capacity):
            self.endpoint = SimpleNamespace(as_dict=lambda: {"capacity": capacity})
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    class Native(NativePPA):
        def _run(self, inputs, experiment, mode, pool, objects, *, lease, cpu_pool):
            assert current_cpu_lease() is lease
            assert cpu_pool == {"capacity": 1}
            assert resources.snapshot()["active"] == 1
            leases[mode] = lease.cpus
            barrier.wait()
            lease.cleanup = CleanupState.COMPLETE
            if mode == "eda" and failure:
                if failure == "unknown":
                    lease.cleanup = CleanupState.UNKNOWN
                raise RuntimeError("EDA failure")
            return {"mode": mode}

    monkeypatch.setattr(api, "CpuPool", Pool)
    native = object.__new__(Native)
    with (resources.relay() if remote else nullcontext()) as relay:
        native.cpu_resources = relay.endpoint if remote else resources
        if failure:
            with pytest.raises(RuntimeError, match="EDA failure"):
                native.run_many(None, None, ("eda", "performance"), (), None)
        else:
            result = native.run_many(None, None, ("eda", "performance"), (), None)
            assert {mode: row[0] for mode, row in result.items()} == {
                "eda": {"mode": "eda"}, "performance": {"mode": "performance"}}
    assert set(leases["eda"]).isdisjoint(leases["performance"])
    assert set(leases["eda"]) | set(leases["performance"]) == set(resources.cpus)
    assert resources.snapshot()["retired"] == int(failure == "unknown")
    assert resources.snapshot()["active"] == 0


def test_single_cpu_pair_queues_without_deadlocking(monkeypatch):
    resources = CpuResources(1, cpus=1)
    calls = []

    class Native(NativePPA):
        def run(self, inputs, experiment, mode, pool, objects):
            from behave.agent.cpu_resources import cpu_job

            with cpu_job(self.cpu_resources) as lease:
                assert len(lease.cpus) == 1
                calls.append(mode)
                lease.cleanup = CleanupState.COMPLETE
            return {"mode": mode}

    native = object.__new__(Native)
    native.cpu_resources = resources
    result = native.run_many(None, None, ("eda", "performance"), (), None)
    assert set(calls) == set(result) == {"eda", "performance"}
    assert resources.snapshot()["available_cpus"] == 1
