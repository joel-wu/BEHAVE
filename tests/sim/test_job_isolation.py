from __future__ import annotations

import math
import os
import subprocess
from types import SimpleNamespace

import pytest

from behave.sim.job_isolation import (
    IsolatedJobFailure,
    _freeze_inputs,
    _limits,
    _strict_json,
    run_isolated_job,
)


def test_host_reply_readers_use_the_shared_strict_decoder():
    from behave.sim import job_isolation
    from behave.sim.behavior import isolated
    from behave.sim.rtl import tool_protocol
    from behave.sim.strict_json import strict_json_loads

    for reader in (job_isolation, isolated, tool_protocol):
        assert reader.strict_json_loads is strict_json_loads
    assert not hasattr(isolated, "_strict_reply_json")


def test_named_job_policy_covers_all_dispatch_and_session_names():
    from behave.sim import job_isolation, worker

    sessions = {
        "rtl_source_generation_session", "behavior_generation_session", "rtl_analysis_session",
    }
    assert set(job_isolation._JOB_POLICIES) == set(worker.JOBS) | sessions
    # A policy declares paths and packages; no payload field selects either.
    assert job_isolation._JobPolicy._fields == ("path_fields", "packages")
    for retired in ("_PATH_FIELDS", "_JOB_PACKAGES", "_ENGINE_PACKAGES"):
        assert not hasattr(job_isolation, retired)


def test_job_policy_import_does_not_load_engines():
    import sys

    script = """
import sys
from behave.sim import job_isolation
assert len(job_isolation._JOB_POLICIES) == 13
for prefix in ('z3', 'cocotb', 'hypothesis', 'crosshair',
               'behave.sim.worker', 'behave.sim.engines'):
    assert not any(name == prefix or name.startswith(prefix + '.')
                   for name in sys.modules), prefix
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name,field", [
    ("classify_seeds", "behavior_path"),
    ("rtl_generation_layout", "relation_path"), ("echo", None),
    ("unknown-job", None),
])
def test_job_input_policy_ignores_undeclared_path_strings(tmp_path, name, field):
    source = tmp_path / "input.txt"
    source.write_text("frozen input")
    payload = {key: "/must/not/be/mounted" for key in (
        "behavior_path", "smt2_path", "relation_path", "arbitrary_path")}
    payload["nested"] = {"file": str(source)}
    if field is not None:
        payload[field] = str(source)
    expected = dict(payload)
    frozen, mounts = _freeze_inputs(name, payload)
    assert payload == expected
    assert frozen is not payload and frozen["nested"] is not payload["nested"]
    if field is not None:
        expected[field] = "/inputs/0/input.txt"
        assert [(mount.source, mount.target) for mount in mounts] == [
            (str(source.resolve()), expected[field])]
    else:
        assert mounts == ()
    assert frozen == expected


@pytest.mark.parametrize("name,spec,packages", [
    ("classify_seeds", {"name": "dual"}, ()),
    ("echo", {"name": "crosshair"}, ()),
    ("unknown-job", {"name": "dual"}, ()),
    ("behavior_source_facts", {"name": "dual"}, ()),
    ("rtl_analysis_session", {"name": "crosshair"}, ("z3",)),
])
def test_job_dependencies_are_selected_only_by_trusted_policy(
    monkeypatch, name, spec, packages,
):
    from behave.sim import job_isolation

    selected = []
    monkeypatch.setattr(job_isolation, "_package_mount", selected.append)
    mounts = job_isolation._dependency_mounts(
        name, {"spec": spec, "packages": ["arbitrary.module"]})
    assert mounts == ()
    assert selected == list(packages)


def test_job_inputs_are_exact_files_with_guest_paths(tmp_path):
    source = tmp_path / "behavior.py"
    source.write_text("class M:\n    pass\n")

    payload, mounts = _freeze_inputs("classify_seeds", {"behavior_path": str(source)})

    assert payload["behavior_path"] == "/inputs/0/behavior.py"
    assert len(mounts) == 1
    assert mounts[0].source == str(source.resolve())
    assert mounts[0].target == "/inputs/0/behavior.py"
    assert len(mounts[0].sha256) == 64


def test_job_path_policy_does_not_mount_a_directory(tmp_path):
    with pytest.raises(IsolatedJobFailure, match="not a regular file"):
        _freeze_inputs("classify_seeds", {"behavior_path": str(tmp_path)})


def test_job_path_policy_does_not_follow_a_submitted_symlink(tmp_path):
    source = tmp_path / "behavior.py"
    source.write_text("class M:\n    pass\n")
    linked = tmp_path / "candidate.py"
    linked.symlink_to(source)

    with pytest.raises(IsolatedJobFailure, match="is a symlink"):
        _freeze_inputs("classify_seeds", {"behavior_path": str(linked)})


@pytest.mark.parametrize(
    "payload",
    [
        b'{"ok":true,"ok":false}',
        b'{"ok":NaN}',
        b'[]',
        b'\xff',
    ],
)
def test_job_reply_is_one_strict_finite_json_object(payload):
    with pytest.raises(IsolatedJobFailure):
        _strict_json(payload)


@pytest.mark.parametrize("payload,position", [
    (b"", 0), (b'{"ok":', 6), (b'{}\n{"private": "hidden-reference"}', 3),
])
def test_job_malformed_json_preserves_position_without_contents(payload, position):
    with pytest.raises(IsolatedJobFailure) as found:
        _strict_json(payload)
    assert found.value.how == "no-answer"
    assert f"(char {position})" in found.value.detail
    assert "private" not in found.value.detail and "hidden-reference" not in found.value.detail


@pytest.mark.parametrize("recursion_limit", [1000, 20000])
def test_job_reply_nesting_cannot_escape_as_a_parser_exception(recursion_limit):
    import sys

    payload = b'{"ok":' + b'[' * 4000 + b'0' + b']' * 4000 + b'}'
    original = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(recursion_limit)
        try:
            got = _strict_json(payload)
        except IsolatedJobFailure as exc:
            assert exc.how == "no-answer"
            return
        # Finite JSON may be supported when a previous frontend raised the
        # interpreter recursion limit. That is not a malformed response here;
        # the caller separately checks its operation's reply schema.
        value = got["ok"]
        for _ in range(4000):
            assert type(value) is list and len(value) == 1
            value = value[0]
        assert value == 0
    finally:
        sys.setrecursionlimit(original)


def test_json_parser_recursion_failure_is_typed(monkeypatch):
    from behave.sim import job_isolation

    def exhausted(*args, **kwargs):
        raise RecursionError("parser recursion exhausted")

    monkeypatch.setattr(job_isolation.json, "loads", exhausted)
    with pytest.raises(IsolatedJobFailure, match="no-answer"):
        _strict_json(b'{"ok":true,"result":0}')


def test_job_resource_policy_has_one_finite_owner():
    limits = _limits(3.2)
    assert limits.wall_seconds == 4
    assert limits.processes == 64
    assert limits.cpu_seconds >= limits.wall_seconds * limits.processes

    for bad in (0, -1, math.inf, math.nan, True):
        with pytest.raises(IsolatedJobFailure):
            _limits(bad)


def test_parallel_job_policy_has_private_shared_memory(monkeypatch):
    """A bounded process tree gets private IPC storage, never the host's ``/dev/shm``."""
    from behave.isolation import IsolationStatus
    from behave.sim import job_isolation

    captured = []

    def inspect_policy(spec, *, stdin):
        captured.append((spec, stdin))
        return SimpleNamespace(
            status=IsolationStatus.COMPLETED,
            stdout=b'{"ok":true,"result":null}',
        )

    monkeypatch.setattr(job_isolation, "run", inspect_policy)
    assert run_isolated_job("echo", {"answer": None}, timeout_s=1) is None

    spec, _stdin = captured[0]
    writable = {item.target: item.bytes for item in spec.writable}
    assert writable == {"/work": 240 << 20, "/dev/shm": 16 << 20}
    assert sum(writable.values()) == spec.limits.file_bytes
    assert dict(spec.environment)["BEHAVE_JOB_CPU_LIMIT"] == "1"
    assert dict(spec.environment)["BEHAVE_SCRATCH_DIR"] == "/work"


def test_behavior_exact_job_owns_the_artifact_memory_ceiling(monkeypatch):
    """The outer named worker bounds the whole exact operation.

    Formula construction used to fork a second process solely to install this
    limit.  If that child died, the named worker saw only ``BrokenProcessPool``
    and could not report which resource was exhausted.
    """
    from behave.isolation import IsolationStatus
    from behave.sim import job_isolation
    from behave.sim.behavior.budgets import (
        ARTIFACT_MEMORY_MB,
        ARTIFACT_WIRE_MB,
    )

    captured = []

    def inspect_policy(spec, *, stdin):
        captured.append((spec, stdin))
        return SimpleNamespace(
            status=IsolationStatus.COMPLETED,
            stdout=b'{"ok":true,"result":null}',
        )

    monkeypatch.setattr(job_isolation, "run", inspect_policy)
    assert run_isolated_job(
        "behavior_exact_analysis", {}, timeout_s=1) is None
    assert captured[0][0].limits.rss_bytes == ARTIFACT_MEMORY_MB << 20
    wire_bytes = None if ARTIFACT_WIRE_MB is None else ARTIFACT_WIRE_MB << 20
    assert captured[0][0].limits.protocol_bytes == wire_bytes
    assert captured[0][0].limits.stdout_bytes == wire_bytes


def test_private_unit_launchers_are_scoped_and_nest_without_leaking():
    """The restricted-host test seam is process-local but never sticky.

    Pytest owns the outer launcher for the session.  A nested test override must restore
    that exact value, and leaving the outer context in production restores ``None``.
    This checks the context-manager invariant without pretending the weaker launcher is
    a production capability.
    """
    from behave.sim import worker
    from behave.sim.behavior import isolated

    baseline_job = worker._TEST_JOB_RUNNER
    baseline_model = isolated._TEST_WORKER_LAUNCHER
    job_one, job_two = object(), object()
    model_one, model_two = object(), object()

    with worker._testing_job_runner(job_one):
        assert worker._TEST_JOB_RUNNER is job_one
        with worker._testing_job_runner(job_two):
            assert worker._TEST_JOB_RUNNER is job_two
        assert worker._TEST_JOB_RUNNER is job_one
    assert worker._TEST_JOB_RUNNER is baseline_job

    with isolated._testing_worker_launcher(model_one):
        assert isolated._TEST_WORKER_LAUNCHER is model_one
        with isolated._testing_worker_launcher(model_two):
            assert isolated._TEST_WORKER_LAUNCHER is model_two
        assert isolated._TEST_WORKER_LAUNCHER is model_one
    assert isolated._TEST_WORKER_LAUNCHER is baseline_model


def test_nested_model_launcher_starts_a_new_image_without_forking_solver_heap(
    monkeypatch,
):
    """The inner source audit must use posix_spawn from a large analysis job.

    A fork is semantically harmless but duplicates the resident Z3 image until
    exec.  The outer judge measures aggregate RSS, so that transient copy can
    turn an otherwise in-budget query into a process-tree memory refusal.
    """
    from behave.sim.behavior.isolated import _nested_job_launcher

    used = []
    original = subprocess.Popen._posix_spawn

    def observed(self, *args, **kwargs):
        used.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "_posix_spawn", observed)
    child = _nested_job_launcher("pass")
    try:
        assert child.wait(timeout=5) == 0
        assert used == [True]
    finally:
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def test_nested_model_launcher_seals_an_ambient_inheritable_descriptor():
    from behave.sim.behavior.isolated import _nested_job_launcher

    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(read_fd, True)
        child = _nested_job_launcher("pass")
        try:
            assert child.wait(timeout=5) == 0
            assert not os.get_inheritable(read_fd)
        finally:
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.isolation
def test_a_named_job_refuses_an_input_replaced_after_it_was_frozen(
    tmp_path, monkeypatch,
):
    from behave.isolation import IsolationRefusal, capabilities
    from behave.sim import job_isolation

    available = capabilities()
    if type(available) is IsolationRefusal:
        pytest.skip(
            f"namespace isolation unavailable: {available.code}: {available.why}")

    source = tmp_path / "behavior.py"
    source.write_text("class M:\n    pass\n")
    real_run = job_isolation.run

    def replace_then_run(spec, *, stdin):
        replacement = tmp_path / "replacement.py"
        replacement.write_text("class M:\n    changed = True\n")
        replacement.replace(source)
        return real_run(spec, stdin=stdin)

    monkeypatch.setattr(job_isolation, "run", replace_then_run)
    with pytest.raises(IsolatedJobFailure, match="isolation-input-changed"):
        run_isolated_job(
            "classify_seeds",
            {"behavior_path": str(source), "seeds": []},
            timeout_s=10,
        )
