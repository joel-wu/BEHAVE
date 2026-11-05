"""Batch runtime reuse does not waive byte checks on ordinary input declarations."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from behave.isolation import IsolationRefusal, IsolationResult, IsolationStatus
from behave.sim.rtl import replay_build, tool_protocol, tool_runtime
from behave.sim.rtl import tool_isolation as tools
from tests.sim.test_rtl_tool_isolation import _needs_namespace, _runtime


@pytest.fixture(autouse=True)
def runtime_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime-batch"))


def test_runtime_preparation_has_no_execution_or_replay_dependency():
    code = """
import sys
from behave.sim.rtl import tool_runtime
assert callable(tool_runtime.freeze_runtime)
for name in ('tool_isolation', 'replay_build', 'artifact'):
    assert 'behave.sim.rtl.' + name not in sys.modules, name
assert 'behave.sim.evaluation' not in sys.modules
"""
    subprocess.run([sys.executable, "-B", "-c", code], check=True)
    assert not hasattr(tools, "freeze_runtime")
    assert not hasattr(tools, "FrozenToolRuntime")
    assert not hasattr(replay_build, "invocation_runtime")
    assert not hasattr(replay_build.ReplayBuildScope, "runtime")


def test_frozen_runtime_preserves_the_existing_identity_format(tmp_path):
    import hashlib
    import json

    from behave.isolation import MaskedPath

    binaries, mounts, _ = _runtime(tmp_path)
    masks = (MaskedPath("/runtime/private"),)
    frozen = tool_runtime.FrozenToolRuntime("eda", binaries, mounts, masks)
    # The owner moves; persisted batch identities keep their original wire shape.
    image = {"version": "rtl-tool-job/7", "execution_version": "rtl-tool-execution/5",
             "profile": "eda", "binaries": [item.identity() for item in binaries],
             "read_only": [item.identity() for item in mounts],
             "masked": [{"target": item.target} for item in masks]}
    assert frozen.identity == hashlib.sha256(json.dumps(
        image, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_batch_runtime_survives_build_scopes_and_threads(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    discoveries = []

    def discover(profile):
        discoveries.append(profile)
        return runtime

    monkeypatch.setattr(tool_runtime, "_runtime", discover)
    owner = object()
    with replay_build.replay_build_scope(owner):
        compiler = tool_runtime.freeze_runtime("eda")
        assert tool_runtime.freeze_runtime().identity == compiler.identity
        assert tool_runtime.freeze_runtime("eda") is compiler
        assert tool_runtime.freeze_runtime("simulation") is not compiler
        with replay_build.replay_build_scope(owner):
            assert tool_runtime.freeze_runtime("eda") is compiler
        # The runtime is batch-owned, unlike this command's mutable build state.
        with ThreadPoolExecutor(max_workers=1) as pool:
            other = pool.submit(tool_runtime.freeze_runtime, "eda").result()
        assert other is compiler
        assert discoveries == ["eda", "simulation"]
    assert tool_runtime.freeze_runtime("eda") is compiler
    assert discoveries == ["eda", "simulation"]


def test_unannotated_tool_jobs_use_the_same_declared_runtime(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda profile: runtime)
    seen = []

    def refuse(spec):
        seen.append(spec)
        return IsolationResult(
            IsolationStatus.REFUSED,
            spec.digest,
            "test",
            refusal=IsolationRefusal("test-stop", "inspect policy, do not execute"),
        )

    monkeypatch.setattr(tools, "run", refuse)
    with replay_build.replay_build_scope(object()):
        frozen = tool_runtime.freeze_runtime("eda")

        def rediscovery(profile):
            pytest.fail("a command's relation job must reuse runtime discovery")

        monkeypatch.setattr(tool_runtime, "_runtime", rediscovery)
        assert tool_runtime.freeze_runtime().identity == frozen.identity
        for _ in range(2):
            tools.run_workspace(
                tmp_path, (tool_protocol.ToolGroup((tool_protocol.ToolCommand("probe", ("/runtime/python",)),)),), role="probe"
            )
        assert len(seen) == 2
        assert all(spec.binaries is frozen.binaries for spec in seen)


def test_public_preparation_is_reused_by_separate_host_workers(tmp_path, monkeypatch):
    from behave.sim import api

    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(tmp_path))
    batch = api.prepare_runtime(profiles=("simulation",), directory=tmp_path / "explicit-batch")
    expected = tool_runtime.freeze_runtime("simulation").identity
    assert os.environ["BEHAVE_RUNTIME_DIR"] == str(batch.directory)
    code = """
from behave.sim.rtl import tool_runtime
def forbidden(*args, **kwargs):
    raise AssertionError('host worker rediscovered the installation')
tool_runtime._runtime = forbidden
print(tool_runtime.freeze_runtime('simulation').identity)
"""

    def worker(_):
        completed = subprocess.run([sys.executable, "-B", "-c", code],
                                   capture_output=True, text=True, check=True,
                                   env=dict(os.environ))
        return completed.stdout.strip()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(worker, range(8))) == [expected] * 8


@pytest.mark.isolation
def test_implicit_scoped_runtime_rejects_changed_bytes(tmp_path, monkeypatch):
    _needs_namespace()
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda profile: runtime)
    with replay_build.replay_build_scope(object()):
        frozen = tool_runtime.freeze_runtime("eda")
        Path(frozen.binaries[0].source).write_text("#!/bin/sh\nprintf changed\n")
        execution = tools.run_workspace(
            tmp_path, (tool_protocol.ToolGroup((tool_protocol.ToolCommand("probe", ("/runtime/python",)),)),), role="probe"
        )
    assert execution.status is IsolationStatus.REFUSED
    assert execution.refusal.code == "isolation-input-changed"
    assert not execution.commands
