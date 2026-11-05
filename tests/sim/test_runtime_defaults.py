"""Sim consumes central defaults without replacing explicit run policies."""

from behave.sim.symbolic import closure_proof

import subprocess
import sys
from importlib import import_module
from inspect import signature

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim import api


@pytest.mark.parametrize("module,target,parameter,name", [
    ("api", "BaselinePolicy", "case_limit", "SIM_BASELINE_CASES"),
    ("api", "BaselinePolicy", "max_queue_depth", "SIM_SEQUENCE_LENGTH"),
    ("api", "BaselinePolicy", "seed", "SIM_STIMULUS_SEED"),
    ("api", "SearchPolicy", "time_budget_s", "SIM_SEARCH_SECONDS"),
    ("api", "SearchPolicy", "debug", "SIM_SEARCH_DEBUG"),
    ("rtl.source_domains", "prove_source_domains", "timeout_ms", "SIM_RTL_SOURCE_DOMAIN_PROOF_MS"),
    ("api", "BehaviorConfig", "timeout_s", "SIM_BEHAVIOR_WORKER_SECONDS"),
    ("api", "RTLConfig", "timeout_s", "SIM_RTL_TOOL_SECONDS"),
    ("api", "realization_of_behavior_source", "timeout_s", "SIM_BEHAVIOR_WORKER_SECONDS"),
    ("api", "prepare_rtl_bundle", "timeout_s", "SIM_RTL_BUNDLE_SECONDS"),
    ("api", "prepare_behavior_generation", "timeout_s", "SIM_BEHAVIOR_WORKER_SECONDS"),
    ("api", "prepare_behavior_generation", "timeout_ms", "SIM_SEARCH_QUERY_MS"),
    ("behavior.realization", "realization_of_source", "timeout_s", "SIM_BEHAVIOR_WORKER_SECONDS"),
    ("rtl.artifact", "RTLArtifact", "timeout_s", "SIM_RTL_TOOL_SECONDS"),
    ("engines.behavior.generation", "BehaviorExactProvider", "timeout_ms", "SIM_SEARCH_QUERY_MS"),
    ("engines.behavior.generation", "BehaviorIsolatedGenerationProvider", "timeout_ms", "SIM_SEARCH_QUERY_MS"),
    ("engines.behavior.generation", "BehaviorIsolatedGenerationProvider", "timeout_s", "SIM_BEHAVIOR_WORKER_SECONDS"),
    ("engines.behavior.generation", "BehaviorIsolatedGenerationProvider.from_behavior_artifact", "timeout_ms", "SIM_SEARCH_QUERY_MS"),
    ("engines.behavior.generation", "BehaviorIsolatedGenerationProvider.from_behavior_artifact", "timeout_s", "SIM_BEHAVIOR_WORKER_SECONDS"),
    ("engines.rtl.generation", "RTLGenerationProvider", "timeout_ms", "SIM_SEARCH_QUERY_MS"),
    ("engines.rtl.generation", "RTLGenerationProvider.from_artifact", "timeout_ms", "SIM_SEARCH_QUERY_MS"),
    ("behavior.isolated", "Limits", "seconds", "SIM_SOURCE_CPU_SECONDS"),
    ("behavior.isolated", "Limits", "memory_bytes", "SIM_SOURCE_RSS_BYTES"),
    ("behavior.isolated", "Limits", "wall_seconds", "SIM_SOURCE_WALL_SECONDS"),
    ("behavior.isolated", "Limits", "processes", "SIM_SOURCE_PROCESSES"),
    ("rtl.tool_isolation", "ToolCommand", "timeout_s", "SIM_EDA_COMMAND_SECONDS"),
    ("rtl.tool_isolation", "default_limits", "wall_seconds", "SIM_EDA_WALL_SECONDS"),
    ("rtl.source_bundle", "prepare_rtl_bundle", "timeout_s", "SIM_RTL_BUNDLE_SECONDS"),
    ("job_isolation", "_limits", "rss_bytes", "SIM_JOB_RSS_BYTES"),
    ("job_isolation", "_limits", "wire_bytes", "SIM_JOB_WIRE_BYTES"),
    ("result_store", "ResultStore.timeout_if_undecided", "timeout", "SIM_RESULT_LOCK_SECONDS"),
])
def test_public_and_provider_defaults_use_one_runtime_entry(module, target, parameter, name):
    factory = import_module("behave.sim." + module)
    for component in target.split("."):
        factory = getattr(factory, component)
    assert signature(factory).parameters[parameter].default == getattr(defaults, name)


def test_public_exports_consume_central_values():
    from behave.sim.rtl import artifact, elaborate

    assert api.DEFAULT_BASELINE_CASES == defaults.SIM_BASELINE_CASES
    assert api.DEFAULT_SEQUENCE_LENGTH == defaults.SIM_SEQUENCE_LENGTH
    assert api.DEFAULT_STIMULUS_SEED == defaults.SIM_STIMULUS_SEED
    assert api.DEFAULT_SEARCH_SECONDS == defaults.SIM_SEARCH_SECONDS
    assert elaborate.TIMEOUT_S == defaults.SIM_RTL_ELABORATION_SECONDS
    assert not hasattr(artifact, "MIN_PROBE_BUDGET_S")
    assert not hasattr(defaults, "SIM_RTL_DIAGNOSTIC_PROBE_MIN_SECONDS")


def test_new_process_imports_changed_defaults_without_a_second_config_source():
    # Set values before any Sim import, modelling an operator's edit followed
    # by process startup. Equal current numbers alone would not catch duplicates.
    script = """
from inspect import signature
from behave.core import runtime_defaults as defaults
defaults.SIM_BASELINE_CASES = 7
defaults.SIM_SEQUENCE_LENGTH = 29
defaults.SIM_STIMULUS_SEED = 'changed'
defaults.SIM_SEARCH_SECONDS = 139.0
defaults.SIM_SEARCH_QUERY_MS = 4321
defaults.SIM_BEHAVIOR_WORKER_SECONDS = 41
defaults.SIM_RTL_TOOL_SECONDS = 43
defaults.SIM_RTL_BUNDLE_SECONDS = 47
defaults.SIM_BASELINE_CLOCK_PERIODS = (3, 11)
defaults.SIM_BASELINE_MEMORY_READ_LATENCY = 7
defaults.SIM_BASELINE_MEMORY_STALL_RATE = 0.25
from behave.sim import api
recipe = api.BaselinePolicy().as_dict()
assert recipe['clock_periods'] == [3, 11]
assert recipe['memory_read_latency'] == 7
assert recipe['memory_stall_rate'] == 0.25
from behave.sim.engines.behavior.generation import BehaviorIsolatedGenerationProvider
from behave.sim.engines.rtl.generation import RTLGenerationProvider
assert api.BaselinePolicy().case_limit == 7
assert api.BaselinePolicy().max_queue_depth == 29
assert api.BaselinePolicy().seed == 'changed'
assert api.SearchPolicy().time_budget_s == 139.0
assert api.BehaviorConfig('model.py').timeout_s == 41
assert api.RTLConfig('model.sv', 'M', 'work').timeout_s == 43
assert signature(api.prepare_rtl_bundle).parameters['timeout_s'].default == 47
for factory in (api.prepare_behavior_generation, BehaviorIsolatedGenerationProvider,
                BehaviorIsolatedGenerationProvider.from_behavior_artifact,
                RTLGenerationProvider, RTLGenerationProvider.from_artifact):
    assert signature(factory).parameters['timeout_ms'].default == 4321
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_explicit_policies_and_recorded_values_do_not_follow_later_defaults(monkeypatch):
    baseline = api.BaselinePolicy(case_limit=3, max_queue_depth=17, seed="fixture")
    search = api.SearchPolicy(time_budget_s=137.5, max_probes=9)
    behavior = api.BehaviorConfig("fixture.py", timeout_s=23)
    rtl = api.RTLConfig("fixture.sv", "M", "work", timeout_s=31)
    saved = [item.as_dict() for item in (baseline, search, behavior, rtl)]
    baseline_identity = baseline.identity
    for name in (
            "SIM_BASELINE_CASES", "SIM_SEQUENCE_LENGTH", "SIM_SEARCH_SECONDS",
            "SIM_BEHAVIOR_WORKER_SECONDS", "SIM_RTL_TOOL_SECONDS"):
        monkeypatch.setattr(defaults, name, 2)
    monkeypatch.setattr(defaults, "SIM_STIMULUS_SEED", "later")
    assert [item.as_dict() for item in (baseline, search, behavior, rtl)] == saved
    assert baseline.identity == baseline_identity
    assert api.SearchPolicy.from_dict(saved[1]).as_dict() == saved[1]
    assert api.SearchPolicy(time_budget_s=None, max_probes=4).time_budget_s is None
    assert api.SearchPolicy(time_budget_s=0).time_budget_s == 0


def test_isolation_and_analysis_defaults_reach_the_resource_envelopes():
    from behave.sim import job_isolation, reference_isolated
    from behave.sim.behavior import budgets
    from behave.sim.rtl import tool_isolation

    reference = reference_isolated.LIMITS
    assert closure_proof._POSITION_CLOSURE_TIMEOUT_MS == defaults.SIM_BEHAVIOR_POSITION_CLOSURE_MS
    assert reference.as_dict() == {
        "cpu_seconds": defaults.SIM_REFERENCE_CPU_SECONDS,
        "memory_bytes": defaults.SIM_REFERENCE_RSS_BYTES,
        "wall_seconds": defaults.SIM_REFERENCE_WALL_SECONDS,
        "processes": defaults.SIM_REFERENCE_PROCESSES,
    }
    job = job_isolation._limits(7.2)
    assert job.wall_seconds == 8
    assert job.cpu_seconds == max(defaults.SIM_JOB_CPU_FLOOR_SECONDS,
                                  8 * defaults.SIM_JOB_PROCESSES)
    eda = tool_isolation.default_limits(wall_seconds=7)
    assert eda.wall_seconds == 7
    assert eda.cpu_seconds == max(defaults.SIM_EDA_CPU_FLOOR_SECONDS,
                                  7 * defaults.SIM_EDA_CPU_MULTIPLIER)
    for limits, prefix in ((job, "SIM_JOB_"), (eda, "SIM_EDA_")):
        for field in ("rss_bytes", "processes", "file_bytes", "open_files", "stderr_bytes"):
            assert getattr(limits, field) == getattr(defaults, prefix + field.upper())
    assert job.stdout_bytes == job.protocol_bytes == defaults.SIM_JOB_WIRE_BYTES
    assert eda.stdout_bytes == defaults.SIM_EDA_STDOUT_BYTES
    assert eda.protocol_bytes == defaults.SIM_EDA_PROTOCOL_BYTES
    for alias, name in (
        ("QUERY_MS", "SIM_BEHAVIOR_ANALYSIS_QUERY_MS"),
        ("ARTIFACT_MS", "SIM_BEHAVIOR_ANALYSIS_MS"),
        ("ARTIFACT_QUERIES", "SIM_BEHAVIOR_ANALYSIS_QUERIES"),
        ("ARTIFACT_MEMORY_MB", "SIM_BEHAVIOR_ANALYSIS_MEMORY_MB"),
        ("SOLVER_MEMORY_MB", "SIM_BEHAVIOR_SOLVER_MEMORY_MB"),
        ("ARTIFACT_WIRE_MB", "SIM_BEHAVIOR_ANALYSIS_WIRE_MB"),
        ("PARALLEL_WORKER_RESERVATION_MB", "SIM_ANALYSIS_WORKER_RESERVATION_MB"),
    ):
        assert alias in budgets.DECLARED
        assert getattr(budgets, alias) == getattr(defaults, name)
    for name in ("behavior_generation_select", "behavior_exact_analysis"):
        analysis = name == "behavior_exact_analysis"
        assert job_isolation._memory_limit_for(name) == (
            budgets.ARTIFACT_MEMORY_MB << 20 if analysis else defaults.SIM_JOB_RSS_BYTES)
        wire_mb = budgets.ARTIFACT_WIRE_MB
        assert job_isolation._wire_limit_for(name) == (
            (None if wire_mb is None else wire_mb << 20)
            if analysis else defaults.SIM_JOB_WIRE_BYTES)
    explicit = job_isolation._limits(3, rss_bytes=512 << 20, wire_bytes=4 << 20)
    assert explicit.rss_bytes == 512 << 20
    assert explicit.stdout_bytes == explicit.protocol_bytes == 4 << 20


def test_rtl_analysis_memory_is_separate_from_generation_and_scoring(monkeypatch):
    from behave.sim import job_isolation

    monkeypatch.setattr(defaults, "SIM_JOB_RSS_BYTES", 3 << 30)
    monkeypatch.setattr(defaults, "SIM_RTL_ANALYSIS_RSS_BYTES", 7 << 30)
    for name in ("rtl_exact_analysis", "rtl_analysis_session"):
        memory = job_isolation._memory_limit_for(name)
        assert memory == 7 << 30
        assert job_isolation._limits(3000, rss_bytes=memory).rss_bytes == memory
    for name in ("rtl_generation_layout", "rtl_source_generation_session",
                 "behavior_generation_select"):
        assert job_isolation._memory_limit_for(name) == 3 << 30


def test_changed_resource_defaults_propagate_on_process_start():
    script = """
from behave.core import runtime_defaults as d
d.SIM_SOURCE_CPU_SECONDS = 17
d.SIM_SOURCE_WALL_SECONDS = 19
d.SIM_SOURCE_RSS_BYTES = 768 << 20
d.SIM_SOURCE_PROCESSES = 2
d.SIM_REFERENCE_CPU_SECONDS = 23
d.SIM_REFERENCE_WALL_SECONDS = 29
d.SIM_REFERENCE_RSS_BYTES = 512 << 20
d.SIM_REFERENCE_PROCESSES = 3
d.SIM_JOB_RSS_BYTES = 2 << 30
d.SIM_RTL_ANALYSIS_RSS_BYTES = 5 << 30
d.SIM_JOB_WIRE_BYTES = 6 << 20
d.SIM_EDA_WALL_SECONDS = 41
d.SIM_EDA_COMMAND_SECONDS = 37
d.SIM_EDA_RSS_BYTES = 3 << 30
d.SIM_BEHAVIOR_ANALYSIS_MEMORY_MB = 2048
d.SIM_BEHAVIOR_ANALYSIS_WIRE_MB = 16
d.SIM_BEHAVIOR_ANALYSIS_MS = 54321
from behave.sim.behavior.isolated import Limits
from behave.sim.reference_isolated import LIMITS
from behave.sim import job_isolation
from behave.sim.behavior import budgets
from behave.sim.rtl.tool_isolation import ToolCommand, default_limits
assert Limits().as_dict() == dict(cpu_seconds=17, wall_seconds=19,
    memory_bytes=768 << 20, processes=2)
assert LIMITS.as_dict() == dict(cpu_seconds=23, wall_seconds=29,
    memory_bytes=512 << 20, processes=3)
assert job_isolation._limits(1).rss_bytes == 2 << 30
assert job_isolation._limits(1).protocol_bytes == 6 << 20
assert job_isolation._memory_limit_for('behavior_generation_select') == 2 << 30
assert job_isolation._memory_limit_for('rtl_exact_analysis') == 5 << 30
assert job_isolation._memory_limit_for('rtl_analysis_session') == 5 << 30
assert job_isolation._memory_limit_for('rtl_source_generation_session') == 2 << 30
assert job_isolation._wire_limit_for('behavior_generation_select') == 6 << 20
assert job_isolation._memory_limit_for('behavior_exact_analysis') == 2048 << 20
assert job_isolation._wire_limit_for('behavior_exact_analysis') == 16 << 20
assert budgets.ARTIFACT_MS == 54321
assert default_limits().wall_seconds == 41
assert default_limits().rss_bytes == 3 << 30
assert ToolCommand('compile', ('/runtime/compiler',)).timeout_s == 37
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_replay_auto_default_and_both_override_paths(monkeypatch):
    from behave.sim import replay_parallel

    monkeypatch.delenv("BEHAVE_REPLAY_WORKERS", raising=False)
    monkeypatch.setattr(defaults, "SIM_REPLAY_AUTO_WORKERS", 6)
    monkeypatch.setattr(replay_parallel.os, "sched_getaffinity", lambda _pid: set(range(32)))
    assert replay_parallel.replay_workers() == 6
    monkeypatch.setattr(replay_parallel.os, "sched_getaffinity", lambda _pid: {0, 1})
    assert replay_parallel.replay_workers() == 2
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "7")
    assert replay_parallel.replay_workers() == 7
    assert replay_parallel.replay_workers(3) == 3
    monkeypatch.delenv("BEHAVE_REPLAY_WORKERS")

    def no_affinity(_pid):
        raise OSError("affinity is unavailable")

    monkeypatch.setattr(replay_parallel.os, "sched_getaffinity", no_affinity)
    monkeypatch.setattr(replay_parallel.os, "cpu_count", lambda: None)
    assert replay_parallel.replay_workers() == 1
