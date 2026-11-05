"""Build reuse is a resource scope, not a trace/result cache."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.isolation import IsolationStatus
from behave.sim.rtl import replay_build, tool_isolation, tool_protocol, tool_runtime
from tests.sim.test_rtl_tool_isolation import _command_row, _completed


def _compiler(monkeypatch, *, failed=False, timed_out=False, observe=None):
    calls = []
    monkeypatch.setattr(tool_runtime, "freeze_runtime",
                        lambda profile: SimpleNamespace(identity="runtime/" + profile))

    def compile(root, groups, **kwargs):
        if observe is not None:
            observe(groups)
        calls.append((root, kwargs))
        assert kwargs["outputs"] == ("build/Vtop",)
        assert not kwargs.get("output_trees")
        assert not list(root.rglob("plan.pickle"))
        assert not list(root.rglob("driver_tb.py"))
        (root / "build").mkdir()
        (root / "build/Vtop").write_bytes(b"compiled bytes")
        return _completed(commands=(_command_row("verilator-build",
                          returncode=-9 if timed_out else int(failed), timed_out=timed_out),),
                          outputs={"build/Vtop": b"compiled bytes"})

    monkeypatch.setattr(tool_isolation, "run_workspace", compile)
    return calls


def test_compile_uses_the_granted_width_and_cache_hits_reserve_nothing(monkeypatch):
    from behave.sim import cpu_admission

    events = []

    @contextmanager
    def reserve(workers):
        events.append(("reserve", workers))
        try:
            yield 2
        finally:
            events.append(("release", 2))

    def observe(groups):
        assert events[-1] == ("reserve", 8)
        assert groups[0].parallel == 1  # One make tree owns the complete grant.
        assert groups[0].commands[0].argv[1] == "-j2"
        events.append(("compiled", 2))

    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "8")
    monkeypatch.setattr(cpu_admission, "reserve_cpus", reserve)
    _compiler(monkeypatch, observe=observe)
    with replay_build.replay_build_scope(object()) as scope:
        inputs = {"rtl.sv": b"x", "run0/Makefile": b"m"}
        scope.compile(inputs, timeout_s=1)
        assert scope.compile(inputs, timeout_s=1)[1]
    assert events == [("reserve", 8), ("compiled", 2), ("release", 2)]


def test_nested_compiler_cannot_multiply_the_outer_grant(monkeypatch):
    from behave.sim.cpu_admission import reserve_cpus

    seen = []
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "8")
    _compiler(monkeypatch, observe=lambda groups: seen.append(groups[0].commands[0].argv))
    with reserve_cpus(2), replay_build.replay_build_scope(object()) as scope:
        scope.compile({"rtl.sv": b"x", "run0/Makefile": b"m"}, timeout_s=1)
    assert seen[0][1] == "-j1"


@pytest.mark.parametrize("rss_bytes", [12 << 30, 32 << 30])
def test_compiler_memory_budget_does_not_raise_simulator_limits(monkeypatch, rss_bytes):
    from dataclasses import replace

    from behave.core import runtime_defaults as defaults

    ordinary = tool_isolation.default_limits(wall_seconds=91)
    monkeypatch.setattr(defaults, "SIM_RTL_BUILD_RSS_BYTES", rss_bytes)
    calls = _compiler(monkeypatch)
    with replay_build.replay_build_scope(object()) as scope:
        scope.compile({"rtl.sv": b"x", "run0/Makefile": b"m"},
                      timeout_s=91 - defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS)
    assert calls[0][1]["limits"] == replace(ordinary, rss_bytes=rss_bytes)
    assert tool_isolation.default_limits(wall_seconds=91) == ordinary


def test_all_completed_variants_are_reused_until_scope_exit(monkeypatch):
    calls = _compiler(monkeypatch)
    inputs = [{"rtl.sv": str(index).encode(), "run0/Makefile": b"make"} for index in range(12)]
    with replay_build.replay_build_scope(object()) as scope:
        first = [scope.compile(item, timeout_s=1)[0] for item in inputs]
        for item, built in zip(inputs, first):
            repeated, reused = scope.compile(item, timeout_s=1)
            assert reused and repeated is built
        assert len(calls) == len(inputs)
        assert all("command_output_bytes" not in options for _, options in calls)
    assert all(not root.exists() for root, _ in calls)


def test_visibility_manifest_is_an_owned_compiler_input(monkeypatch):
    calls = _compiler(monkeypatch)
    base = {"rtl.sv": b"x", "run0/Makefile": b"make"}
    with replay_build.replay_build_scope(object()) as scope:
        for signals in (b"a", b"b"):
            inputs = base | {"run0/visibility.vlt": signals}
            built, reused = scope.compile(inputs, timeout_s=1)
            assert not reused
            assert (calls[-1][0] / "run0/visibility.vlt").read_bytes() == signals
            assert scope.compile(inputs, timeout_s=1) == (built, True)
    assert len(calls) == 2


@pytest.mark.parametrize("failed", [False, True])
def test_fifty_compilers_queue_on_one_pool_and_release_on_failure(monkeypatch, failed):
    from behave.sim import api
    from behave.sim.cpu_admission import reserve_cpus
    from tests.sim.test_cpu_admission import eventually

    release = threading.Event()
    entered = []
    receipts = []
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "2")
    with api.CpuPool(8) as cpu, ThreadPoolExecutor(max_workers=50) as callers:
        def observe(groups):
            assert groups[0].commands[0].argv[1] == "-j2"
            # run_workspace's nested admission must borrow, not queue again.
            with reserve_cpus(1) as width:
                assert width == 1
                entered.append(1)
                assert release.wait(10)

        calls = _compiler(monkeypatch, failed=failed, observe=observe)

        def invoke():
            with api.cpu_scope(cpu.endpoint) as usage, replay_build.replay_build_scope(object()) as scope:
                result, reused = scope.compile(
                    {"rtl.sv": b"x", "run0/Makefile": b"m"}, timeout_s=1)
                assert not reused
                assert (result.mount is None) is failed
            receipts.extend(usage.as_dict()["reservations"])

        futures = [callers.submit(invoke) for _ in range(50)]
        try:
            eventually(lambda: cpu.snapshot()["reserved"] == 8
                       and cpu.snapshot()["queued"] >= 40)
            assert len(entered) == 4
        finally:
            release.set()
        for future in futures:
            future.result(10)
        eventually(lambda: cpu.snapshot()["reserved"] == 0)
        assert cpu.snapshot()["peak_reserved"] == 8
        assert not cpu.snapshot()["quarantined"]
    assert len(calls) == len(receipts) == 50
    assert all(row["workers"] == 2 and row["released"] for row in receipts)


def test_scope_reuses_only_matching_compiler_inputs_and_removes_builds(monkeypatch):
    calls = _compiler(monkeypatch)
    artifact = object()
    inputs = {"rtl.sv": b"module m; endmodule", "run0/Makefile": b"compile"}
    with replay_build.replay_build_scope(artifact) as scope:
        first, reused = scope.compile(inputs, timeout_s=1)
        assert not reused
        assert first.mount.target == "/build/Vtop"
        assert Path(first.mount.source).read_bytes() == b"compiled bytes"
        assert Path(first.mount.source).stat().st_mode & 0o777 == 0o500
        with replay_build.replay_build_scope(artifact) as nested:
            assert nested is scope
            same, reused = nested.compile(dict(inputs), timeout_s=1)
            assert same is first and reused
        for name in ("rtl.sv", "run0/Makefile", "collector.cpp", "initial_state.cpp"):
            _, reused = scope.compile({**inputs, name: b"different"}, timeout_s=1)
            assert not reused
        assert len(calls) == 5
        roots = [root for root, _ in calls]
        assert all(root.is_dir() for root in roots)
    assert all(not root.exists() for root in roots)
    with pytest.raises(RuntimeError, match="no owning"):
        replay_build.current_replay_build(artifact)
    with replay_build.replay_build_scope(artifact) as fresh:
        _, reused = fresh.compile(inputs, timeout_s=1)
        assert not reused
    assert len(calls) == 6


@pytest.mark.parametrize("timed_out", [False, True])
def test_failed_attempt_is_not_repeated_by_later_stimuli(monkeypatch, timed_out):
    calls = _compiler(monkeypatch, failed=True, timed_out=timed_out)
    inputs = {"rtl.sv": b"invalid", "run0/Makefile": b"compile"}
    with replay_build.replay_build_scope(object()) as scope:
        failed, reused = scope.compile(inputs, timeout_s=2)
        assert failed.mount is None and not reused
        for seconds in (2, 1, 2):
            repeated, reused = scope.compile(inputs, timeout_s=seconds)
            assert repeated is failed and reused
            assert repeated.mount is None
        assert len(calls) == 1
        retried, reused = scope.compile(inputs, timeout_s=3)
        assert retried.mount is None and not reused
        with pytest.raises(ValueError, match="closed source"):
            scope.compile({**inputs, "run0/plan.pickle": b"stimulus"}, timeout_s=1)
    assert len(calls) == 2
    with replay_build.replay_build_scope(object()) as fresh:
        _, reused = fresh.compile(inputs, timeout_s=2)
        assert not reused
    assert len(calls) == 3


def test_other_artifacts_and_exception_exit_cannot_borrow_a_scope(monkeypatch):
    _compiler(monkeypatch)
    left, right = object(), object()
    with replay_build.replay_build_scope(left) as outer:
        with pytest.raises(LookupError), replay_build.replay_build_scope(right) as inner:
            assert inner is not outer
            inner.compile({"rtl.sv": b"x", "run0/Makefile": b"m"}, timeout_s=1)
            path = Path(inner._temporary.name)
            raise LookupError("stop")
        assert not path.exists()
        assert replay_build.current_replay_build(left) is outer


@pytest.mark.parametrize("status", [IsolationStatus.FILE_LIMIT, IsolationStatus.MEMORY_LIMIT,
                                  IsolationStatus.TIMED_OUT, IsolationStatus.EXITED])
def test_tool_tree_failure_is_retained_without_rebuilding_for_each_stimulus(monkeypatch, status):
    from dataclasses import replace

    _compiler(monkeypatch)
    failed = replace(_completed(), status=status)
    calls = []

    def compile(*args, **kwargs):
        calls.append(args)
        return failed

    monkeypatch.setattr(tool_isolation, "run_workspace", compile)
    inputs = {"rtl.sv": b"x", "run0/Makefile": b"m"}
    with replay_build.replay_build_scope(object()) as scope:
        for seconds, count in ((2, 1), (2, 1), (1, 1), (3, 2)):
            with pytest.raises(tool_protocol.ToolIsolationError) as caught:
                scope.compile(inputs, timeout_s=seconds)
            assert caught.value.execution is failed
            assert caught.value.code == "rtl-tool-" + status.value
            assert len(calls) == count
    with replay_build.replay_build_scope(object()) as fresh, pytest.raises(tool_protocol.ToolIsolationError):
        fresh.compile(inputs, timeout_s=2)
    assert len(calls) == 3
