"""Public, host-owned EDA environments and tool execution.

Consumers declare extra binaries/mounts, not changes to a private runtime record.
Sim owns its profiles; isolation owns only process/resource/cache mechanics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from behave.core import runtime_defaults as defaults
from behave.isolation import Binary, ReadOnlyMount, ResourceLimits
from behave.isolation.runtime_environment import runtime_environment

if TYPE_CHECKING:
    from .rtl.tool_protocol import ToolCommand, ToolExecution, ToolGroup
    from .rtl.tool_runtime import FrozenToolRuntime


def __getattr__(name):
    # Merely importing the score API must not load EDA implementation modules.
    if name in {"ToolCommand", "ToolExecution", "ToolGroup"}:
        from .rtl import tool_protocol

        return getattr(tool_protocol, name)
    raise AttributeError(name)


def command_stream(execution, identifier, stream):
    """Read the complete retained command stream, not its display preview."""
    from .rtl.tool_protocol import command_stream as read

    return read(execution, identifier, stream)


def command_log(execution, identifier, stream):
    """Return a log path for streaming/copying, without loading it into memory."""
    from .rtl.tool_protocol import command_log as locate

    return locate(execution, identifier, stream)


def default_limits(*, wall_seconds: int = defaults.SIM_EDA_WALL_SECONDS) -> ResourceLimits:
    from .rtl.tool_isolation import default_limits as limits

    return limits(wall_seconds=wall_seconds)


@dataclass(frozen=True, slots=True)
class ToolEnvironment:
    """One prepared environment; reuse it for every stage of an invocation."""

    _runtime: FrozenToolRuntime

    @property
    def identity(self) -> str:
        return self._runtime.identity

    def execute(self, workspace, groups, *, outputs=(), read_only_mounts=(),
                role="eda", limits=None, output_root=None) -> ToolExecution:
        from .rtl.tool_isolation import run_workspace

        return run_workspace(
            workspace, groups, outputs=outputs, read_only_mounts=read_only_mounts,
            role=role, limits=limits, output_root=output_root,
            runtime_profile=self._runtime.profile, frozen_runtime=self._runtime)


def prepare_tool_environment(profile: str = "eda", *, binaries: tuple[Binary, ...] = (),
                             mounts: tuple[ReadOnlyMount, ...] = ()) -> ToolEnvironment:
    """Prepare installed tools, optionally extended by explicit host resources.

    This neither installs software nor gives candidates a resource-selection API.
    Extra resources use the public ``behave.isolation`` Binary/ReadOnlyMount types.
    """
    from .rtl.tool_runtime import _without_redundant_mounts, freeze_runtime

    runtime = freeze_runtime(profile)
    if binaries or mounts:
        runtime = replace(runtime, binaries=runtime.binaries + tuple(binaries),
                          mounts=_without_redundant_mounts(runtime.mounts + tuple(mounts)))
    return ToolEnvironment(runtime)


def prepare_runtime(*, profiles=("eda", "simulation"), directory=None):
    """Optional Sim-profile warm-up; ordinary calls use the same batch runtime."""
    from .rtl.tool_runtime import freeze_runtime

    environment = runtime_environment(directory)
    os.environ["BEHAVE_RUNTIME_DIR"] = str(environment.directory)
    with environment.activate():
        for profile in profiles:
            freeze_runtime(profile)
    return environment


__all__ = ["ToolCommand", "ToolEnvironment", "ToolExecution", "ToolGroup",
           "command_log", "command_stream", "default_limits", "prepare_runtime", "prepare_tool_environment"]
