"""Concurrent callers and incomplete builds cannot cross the reuse boundary."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from behave.sim.rtl import replay_build, tool_isolation, tool_runtime
from tests.sim.test_rtl_replay_build import _compiler
from tests.sim.test_rtl_tool_isolation import _command_row, _completed


def test_same_artifact_in_another_thread_has_a_private_scope(monkeypatch):
    _compiler(monkeypatch)
    artifact = object()
    inputs = {"rtl.sv": b"module M; endmodule", "run0/Makefile": b"compile"}

    def other_thread():
        with pytest.raises(RuntimeError, match="no owning"):
            replay_build.current_replay_build(artifact)
        with replay_build.replay_build_scope(artifact) as scope:
            build, reused = scope.compile(inputs, timeout_s=1)
            assert not reused
            return scope, build

    with replay_build.replay_build_scope(artifact) as main:
        first, _ = main.compile(inputs, timeout_s=1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            separate, second = pool.submit(other_thread).result(timeout=5)
        assert separate is not main and second is not first
        assert replay_build.current_replay_build(artifact) is main
        same, reused = main.compile(inputs, timeout_s=1)
        assert same is first and reused


@pytest.mark.parametrize("defect", ["missing-file", "missing-payload", "changed-bytes", "symlink", "output-limit"])
def test_incomplete_export_never_becomes_executable_or_recompiles_in_the_same_scope(monkeypatch, defect):
    calls = []
    monkeypatch.setattr(
        tool_runtime, "freeze_runtime", lambda profile: SimpleNamespace(identity="runtime/" + profile)
    )

    def incomplete(root, groups, **kwargs):
        calls.append(root)
        if defect != "missing-file":
            (root / "build").mkdir()
            path = root / "build/Vtop"
            if defect == "symlink":
                (root / "outside").write_bytes(b"executable")
                path.symlink_to(root / "outside")
            else:
                path.write_bytes(b"changed" if defect == "changed-bytes" else b"executable")
        row = _command_row("verilator-build", returncode=0)
        row["output_limited"] = defect == "output-limit"
        return _completed(
            commands=(row,), outputs={} if defect == "missing-payload" else {"build/Vtop": b"executable"}
        )

    monkeypatch.setattr(tool_isolation, "run_workspace", incomplete)
    with replay_build.replay_build_scope(object()) as scope:
        for repeat in range(2):
            result, reused = scope.compile({"rtl.sv": b"source", "run0/Makefile": b"compile"}, timeout_s=1)
            # The failed attempt is retained, not promoted to an executable.
            # Equal-budget callers must not silently compile it again per batch.
            assert result.mount is None and reused is bool(repeat)
    assert len(calls) == 1
    with replay_build.replay_build_scope(object()) as scope:
        result, reused = scope.compile({"rtl.sv": b"source", "run0/Makefile": b"compile"}, timeout_s=1)
        assert result.mount is None and not reused
    assert len(calls) == 2


@pytest.mark.cosim
def test_reused_executable_still_starts_every_stimulus_from_initial_state(tmp_path):
    from behave.sim import api
    from behave.sim.core.stimulus import AuditedTrace
    from tests.sim.test_rtl_initial_state import initial_fixture

    artifact, contract, _, initial = initial_fixture(tmp_path, False, False, False, "rising")
    # No reset prefix: a shared mutable simulator would carry 7 into the next run.
    stimulus = api.Stimulus(
        queues={"in": []}, slots=[api.EventSlot(controls={"rst": 0, "out_ready": 1}) for _ in range(2)]
    )

    def replay():
        (trace,) = artifact.traces_per_process(
            contract, [stimulus], workers=1, state_names=("value",), state_widths={"value": 3}
        )
        assert type(trace) is AuditedTrace, trace.as_dict()
        assert dict(trace.initial_state) == initial
        assert [dict(row.state)["value"] for row in trace.records] == [6, 7]
        return trace

    with replay_build.replay_build_scope(artifact):
        first, second = replay(), replay()
        assert first.records == second.records
    roles = [row["role"] for row in artifact.tool_lineage()]
    assert roles.count("verilator-build") == 1
    assert sum(role.startswith("verilator-simulate:") for role in roles) == 2
    # A later command owns a new build, even when artifact and bytes are unchanged.
    with replay_build.replay_build_scope(artifact):
        third = replay()
        assert first.records == third.records
    assert [row["role"] for row in artifact.tool_lineage()].count("verilator-build") == 2


@pytest.mark.cosim
def test_reused_analysis_build_reads_each_runs_own_initial_choice(tmp_path):
    from behave.sim import api
    from behave.sim.core.stimulus import AuditedTrace
    from behave.sim.rtl.initial_state import RTLInitialState
    from tests.sim.test_rtl_initial_state import initial_fixture

    artifact, contract, _, _ = initial_fixture(tmp_path, False, False, False, "rising", declared_initial=False)
    stimulus = api.Stimulus(queues={"in": []}, slots=[api.EventSlot(controls={"rst": 0, "out_ready": 1})])
    with replay_build.replay_build_scope(artifact):
        for value in (2, 6):
            initial = RTLInitialState.from_values(artifact.identity, {"value": 3}, {"value": value})
            (trace,) = artifact.traces_per_process(
                contract,
                [stimulus],
                workers=1,
                state_names=("value",),
                state_widths={"value": 3},
                _initial_state=initial,
            )
            assert type(trace) is AuditedTrace, trace.as_dict()
            assert dict(trace.initial_state) == {"value": value}
            assert dict(trace.records[0].state) == {"value": value + 1}
    assert [row["role"] for row in artifact.tool_lineage()].count("verilator-build") == 1
