import pytest

from behave.sim.core import resources


def test_worker_lifetime_registers_parent_death_signal(monkeypatch):
    import ctypes
    import signal
    from types import SimpleNamespace

    calls = []

    def library(name, **kwargs):
        calls.append((name, kwargs))
        return SimpleNamespace(prctl=lambda *args: calls.append(args))

    monkeypatch.setattr(ctypes, "CDLL", library)
    monkeypatch.setattr(resources.os, "getppid", lambda: 123)
    resources.bind_worker_to_parent()
    assert calls == [("libc.so.6", {"use_errno": True}),
                     (1, signal.SIGKILL, 0, 0, 0)]


def test_worker_lifetime_exits_if_parent_died_before_registration(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    def exit_process(code):
        raise SystemExit(code)

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs:
                        SimpleNamespace(prctl=lambda *args: 0))
    monkeypatch.setattr(resources.os, "getppid", lambda: 1)
    monkeypatch.setattr(resources.os, "_exit", exit_process)
    with pytest.raises(SystemExit) as caught:
        resources.bind_worker_to_parent()
    assert caught.value.code == 0


def test_worker_lifetime_retains_portable_fallback(monkeypatch):
    import ctypes

    def unavailable(*args, **kwargs):
        raise OSError("no Linux libc")

    def not_registered():
        raise AssertionError("registration did not finish")

    monkeypatch.setattr(ctypes, "CDLL", unavailable)
    monkeypatch.setattr(resources.os, "getppid", not_registered)
    assert resources.bind_worker_to_parent() is None


@pytest.mark.isolation
def test_worker_lifetime_ends_a_real_worker_after_parent_exit():
    import os
    import select
    import signal
    import subprocess
    import sys

    if sys.platform != "linux":
        pytest.skip("the parent-death hook is Linux-only")
    worker = """
import time
from behave.sim.core.resources import bind_worker_to_parent
bind_worker_to_parent()
print('ready', flush=True)
time.sleep(60)
"""
    launcher = f"""
import os, subprocess, sys
subprocess.Popen([sys.executable, '-c', {worker!r}], stdin=subprocess.DEVNULL)
sys.stdin.readline()
os._exit(0)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", launcher], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        assert select.select([parent.stdout], [], [], 10)[0], "worker never registered"
        assert parent.stdout.readline() == b"ready\n"
        # The worker holds both output pipes open while sleeping. EOF after its
        # parent's exit proves it stopped too, rather than becoming an orphan.
        out, err = parent.communicate(b"exit\n", timeout=10)
        assert parent.returncode == 0 and out == b"" and err == b""
    finally:
        # This group was created by this test, including any surviving worker.
        try:
            os.killpg(parent.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        parent.wait(timeout=10)
        for stream in (parent.stdin, parent.stdout, parent.stderr):
            stream.close()


def test_cpu_affinity_wins_over_host_and_scheduler_counts(monkeypatch):
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _pid: {3, 9})
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 192)
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "32")

    assert resources.available_cpus() == 2


def test_slurm_task_allocation_is_the_first_affinity_fallback(monkeypatch):
    def unavailable(_pid):
        raise OSError("this platform has no readable process affinity")

    monkeypatch.setattr(resources.os, "sched_getaffinity", unavailable)
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 192)
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "4")
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "16")

    assert resources.available_cpus() == 4


def test_scheduler_compact_counts_and_hardware_fallback(monkeypatch):
    monkeypatch.delattr(resources.os, "sched_getaffinity", raising=False)
    for variable in (
        "SLURM_CPUS_PER_TASK",
        "SLURM_CPUS_ON_NODE",
        "SLURM_JOB_CPUS_PER_NODE",
        "PBS_NP",
        "NSLOTS",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SLURM_JOB_CPUS_PER_NODE", "12(x2)")
    assert resources.available_cpus() == 12

    monkeypatch.delenv("SLURM_JOB_CPUS_PER_NODE")
    monkeypatch.setattr(resources.os, "cpu_count", lambda: None)
    assert resources.available_cpus() == 1


def test_worker_limit_is_separate_from_cpu_entitlement(monkeypatch):
    monkeypatch.setenv("BEHAVE_ANALYSIS_WORKERS", "3")
    assert resources.worker_limit("BEHAVE_ANALYSIS_WORKERS", 12) == 3
    assert resources.worker_limit("BEHAVE_ANALYSIS_WORKERS", 2) == 2

    monkeypatch.setenv("BEHAVE_ANALYSIS_WORKERS", "auto")
    with pytest.raises(ValueError, match="positive integer"):
        resources.worker_limit("BEHAVE_ANALYSIS_WORKERS", 12)


def test_job_allocation_caps_explicit_analysis_pool_and_rejects_bad_limit(monkeypatch):
    monkeypatch.setenv("BEHAVE_ANALYSIS_WORKERS", "32")
    monkeypatch.setenv("BEHAVE_JOB_CPU_LIMIT", "2")
    assert resources.worker_limit("BEHAVE_ANALYSIS_WORKERS", 16) == 2
    assert resources.job_cpu_limit(1) == 1
    for value in ("0", "-1", "auto", "2.5"):
        monkeypatch.setenv("BEHAVE_JOB_CPU_LIMIT", value)
        with pytest.raises(ValueError, match="BEHAVE_JOB_CPU_LIMIT"):
            resources.job_cpu_limit(8)


def test_budget_worker_default_uses_the_shared_cpu_authority(monkeypatch):
    from behave.sim.behavior import budgets

    monkeypatch.delenv("BEHAVE_ANALYSIS_WORKERS", raising=False)
    monkeypatch.setattr(budgets, "available_cpus", lambda: 8)
    assert budgets.workers_for(range(20), reservation_mb=None) == 6

    monkeypatch.setattr(budgets, "available_cpus", lambda: 1)
    assert budgets.workers_for(range(20), reservation_mb=None) == 1

    monkeypatch.setenv("BEHAVE_ANALYSIS_WORKERS", "2")
    monkeypatch.setattr(budgets, "available_cpus", lambda: 32)
    assert budgets.workers_for(range(20), reservation_mb=None) == 2


def test_parallel_audit_default_uses_the_shared_cpu_authority(monkeypatch):
    import concurrent.futures

    from behave.sim.behavior import audit

    monkeypatch.delenv("BEHAVE_ANALYSIS_WORKERS", raising=False)
    opened = []

    class Pool:
        def __init__(self, max_workers):
            opened.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def map(self, _function, work):
            return [[] for _ in work]

    monkeypatch.setattr(audit, "available_cpus", lambda: 6)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Pool)

    assert audit._parallel_step_audit(None, None, list(range(12)), None, None) == []
    assert opened == [4]

    monkeypatch.setenv("BEHAVE_ANALYSIS_WORKERS", "2")
    assert audit._parallel_step_audit(None, None, list(range(12)), None, None) == []
    assert opened[-1] == 2


def test_complete_analysis_uses_one_wall_without_a_hidden_phase_fraction(monkeypatch):
    from behave.sim.behavior import analysis_job

    monkeypatch.setattr(analysis_job.time, "monotonic_ns", lambda: 41_000_000_000)
    policy = analysis_job._analysis_execution_policy(
        300_000, 1_000_000_000, None, 17_000)

    assert policy == {
        "artifact_timeout_ms": 300_000,
        "artifact_started": 1.0,
        "feasibility_timeout_ms": 260_000,
        "query_timeout_ms": 17_000,
    }


def test_complete_analysis_honours_a_named_wall_longer_than_the_default(
        monkeypatch):
    from behave.sim.behavior import analysis_job

    monkeypatch.setattr(analysis_job.time, "monotonic_ns", lambda: 1_000_000_000)
    policy = analysis_job._analysis_execution_policy(
        1_800_000, 1_000_000_000, None, None)

    # Neither the artifact nor per-query legacy default shortens a named wall.
    assert policy == {
        "artifact_timeout_ms": 1_800_000,
        "artifact_started": 1.0,
        "feasibility_timeout_ms": 1_800_000,
        "query_timeout_ms": 1_800_000,
    }


@pytest.mark.parametrize("cap", [None, 17_000, 400_000])
def test_complete_analysis_query_caps_are_only_caller_owned(monkeypatch, cap):
    from behave.sim.behavior import analysis_job, budgets

    monkeypatch.setattr(budgets, "QUERY_MS", 5)
    monkeypatch.setattr(analysis_job.time, "monotonic_ns", lambda: 41_000_000_000)
    policy = analysis_job._analysis_execution_policy(
        300_000, 1_000_000_000, cap, cap)
    expected = 260_000 if cap is None else min(cap, 260_000)
    assert policy["feasibility_timeout_ms"] == expected
    assert policy["query_timeout_ms"] == expected


def test_complete_analysis_reports_a_wall_spent_before_queries(monkeypatch):
    from behave.sim.behavior import analysis_job
    from behave.sim.core.refusal import Refused

    monkeypatch.setattr(analysis_job.time, "monotonic_ns", lambda: 10_000_000_000)
    with pytest.raises(Refused, match="wall envelope was spent"):
        analysis_job._analysis_execution_policy(
            10_000, 0, None, None)


def test_behavior_worker_installs_owner_before_build_and_closes_on_deadline(monkeypatch):
    from behave.isolation.preparation_control import check_preparation
    from behave.sim import analysis_budget
    from behave.sim.behavior import analysis_job, budgets

    now, closed = [1.0], []
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    binding = {"artifact": "artifact", "task_contract": "contract", "horizon": 2,
               "domain": None, "analysis_identity": "source", "catalogue": None,
               "admission": "admission"}

    def build(*_args, **_kwargs):
        owner = analysis_budget.active_analysis()
        assert owner.wall_seconds == 10
        owner.on_close(closed.append, "released")
        now[0] += 2
        assert budgets.source_audit_limits().seconds == 8
        now[0] += 8
        check_preparation()
        pytest.fail("an expired construction cannot return a bound instance")

    monkeypatch.setattr(analysis_job, "_build", build)
    reply = analysis_job.run_analysis_job({
        "operation": "analyse", "binding": binding, "analysis_wall_ms": 10_000,
        "feasibility_timeout_ms": None, "query_timeout_ms": None})
    assert reply["status"] == "refused"
    assert reply["binding"] == binding
    assert reply["refusal"]["kind"] == "analysis-budget-exhausted"
    assert reply["refusal"]["detail"]["elapsed_seconds"] == 10
    assert closed == ["released"]
    assert analysis_budget.active_analysis() is None


@pytest.mark.parametrize("late", [False, True])
def test_fixed_input_audit_spends_the_owner_wall_not_a_fresh_limit(monkeypatch, late):
    from types import SimpleNamespace

    import z3

    from behave.sim import analysis_budget
    from behave.sim.behavior.audit import _audit_model

    now, settings = [1.0], []
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])

    def check():
        if late:
            now[0] += 2
        return z3.unsat

    solver = SimpleNamespace(set=lambda **kwargs: settings.append(kwargs), check=check)
    with analysis_budget.analysis_scope(5):
        now[0] += 3
        if late:
            with pytest.raises(analysis_budget.AnalysisStopped):
                _audit_model(solver)
        else:
            assert _audit_model(solver) is None
    assert settings == [{"timeout": 2000}]


def test_complete_analysis_leaves_worker_selection_to_the_residual_scheduler():
    from behave.sim.behavior import analysis_job, budgets

    execution = analysis_job._analysis_query_execution({
        "artifact_timeout_ms": 180_000,
        "artifact_started": 7.5,
    })

    assert execution == {
        "artifact_ms": 180_000,
        "artifact_started": 7.5,
        "memory_mb": budgets.ARTIFACT_MEMORY_MB,
    }
    assert "workers" not in execution
