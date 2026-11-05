"""Command-scoped RTL build reuse; each replay still owns a new namespace.

Only successful compiler outputs become executable products. Completed failed
attempts are also retained so later stimuli cannot implicitly retry that build.
The key includes every compiler input and the frozen tool runtime. Stimuli,
traces and scores are never cached.
All files are private to this scope and removed on exit; concurrent commands do
not share mutable build state.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

if TYPE_CHECKING:
    from behave.isolation.model import ReadOnlyMount

    from .tool_protocol import ToolExecution

_CURRENT = ContextVar("behave_rtl_replay_build", default=None)
_INPUTS = frozenset({"rtl.sv", "run0/Makefile", "run0/visibility.vlt",
                     "collector.cpp", "initial_state.cpp"})


@dataclass(frozen=True)
class PreparedReplayBuild:
    execution: ToolExecution
    mount: ReadOnlyMount | None


class ReplayBuildScope:
    def __init__(self, artifact):
        self.artifact = artifact
        self._temporary = None
        self._builds = {}
        self._failed_builds = {}

    def directory(self, prefix):
        """Private local staging, owned until all replay workers have joined.

        The outer scope also removes partially prepared invocations when an
        exception occurs before the replay scheduler takes ownership.
        """
        if self._temporary is None:
            self._temporary = tempfile.TemporaryDirectory(
                prefix="behave-replay-build-", dir=scratch_root())
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self._temporary.name))

    def compile(self, inputs, *, timeout_s):
        from ..cpu_admission import reserve_cpus
        from ..replay_parallel import replay_workers
        from .tool_isolation import default_limits, run_workspace, verilator_product_mount
        from .tool_protocol import ToolCommand, ToolGroup, ToolIsolationError, require_completed

        if type(inputs) is not dict or not {"rtl.sv", "run0/Makefile"} <= inputs.keys() \
                or not inputs.keys() <= _INPUTS or any(type(v) is not bytes for v in inputs.values()):
            raise ValueError("replay compiler inputs are the closed source/Makefile bundle")
        from .tool_runtime import freeze_runtime

        runtime = freeze_runtime("eda")
        key = (runtime.identity, tuple((name, hashlib.sha256(data).hexdigest())
                                      for name, data in sorted(inputs.items())))
        if key in self._builds:
            return self._builds[key], True
        previous = self._failed_builds.get(key)
        if previous is not None and timeout_s <= previous[0]:
            require_completed(previous[1].execution)
            return previous[1], True
        # A larger explicit budget can retry a failed attempt. Equal or smaller
        # remaining budgets cannot turn each baseline chunk into a fresh build.
        # Starting another command also starts a new scope and permits retry.
        root = self.directory("compile-")
        for name, data in inputs.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # Compilation and replay are successive CPU stages, not competing pools.
        # GNU make passes this jobserver budget to its recursive C++ build. A
        # nested caller receives one slot; run_workspace borrows the held grant
        # and completes process-tree cleanup before this reservation is released.
        with reserve_cpus(replay_workers()) as admitted:
            built = run_workspace(
                root, (ToolGroup((ToolCommand(
                    "verilator-build", ("/tools/make", f"-j{admitted}",
                                        "SIM=verilator", "SIM_BUILD=/work/build",
                                        "VERILOG_SOURCES=/work/rtl.sv", "/work/build/Vtop"),
                    cwd="run0", timeout_s=timeout_s),)),),
                outputs=("build/Vtop",), role="verilator-build",
                limits=replace(default_limits(
                    wall_seconds=timeout_s + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS),
                    rss_bytes=defaults.SIM_RTL_BUILD_RSS_BYTES),
                frozen_runtime=runtime)
            try:
                require_completed(built)
            except ToolIsolationError:
                self._failed_builds[key] = (timeout_s, PreparedReplayBuild(built, None))
                raise
        mount = verilator_product_mount(root, built, "verilator-build")
        result = PreparedReplayBuild(built, mount)
        # These outputs already live until scope exit. Refusing reuse after an
        # arbitrary variant count only recompiles them into more directories.
        if mount is not None:
            self._builds[key] = result
            self._failed_builds.pop(key, None)
        else:
            self._failed_builds[key] = (timeout_s, result)
        return result, False

    def close(self):
        self._builds.clear()
        self._failed_builds.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def retain_interruption(self, error):
        """Keep cancelled tool logs before this scope releases scratch storage."""
        note = error.interrupted_tool
        if not note or not note.get("log_directory"):
            return
        try:
            directory = (Path(self.artifact.workdir) / "tool-logs").resolve()
            if Path(note["log_directory"]).resolve().is_relative_to(directory):
                return
            directory.mkdir(parents=True, exist_ok=True)
            retained = tempfile.mkdtemp(prefix="interrupted-", dir=directory)
            shutil.copytree(note["log_directory"], retained, dirs_exist_ok=True)
            error.interrupted_tool = {**note, "log_directory": str(Path(retained).resolve())}
        except OSError as failure:
            # Diagnostic publication must not replace the original cancellation.
            error.interrupted_tool = {**note, "log_retention_error": type(failure).__name__}


@contextmanager
def replay_build_scope(artifact):
    from ..scoring_policy import ScoringStopped

    active = _CURRENT.get()
    if active is not None and active.artifact is artifact:
        try:
            yield active
        except ScoringStopped as error:
            # Evaluation may catch this interruption inside the outer command's
            # build scope. Retain now, before final assembly consumes the error.
            active.retain_interruption(error)
            raise
        return
    scope = ReplayBuildScope(artifact)
    token = _CURRENT.set(scope)
    try:
        yield scope
    except ScoringStopped as error:
        scope.retain_interruption(error)
        raise
    finally:
        _CURRENT.reset(token)
        scope.close()


def current_replay_build(artifact):
    scope = _CURRENT.get()
    if scope is None or scope.artifact is not artifact:
        raise RuntimeError("RTL replay has no owning build scope")
    return scope
