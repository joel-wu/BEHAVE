"""Host EDA discovery for tests and diagnostic commands.

This does not prepare candidate sandbox mounts or configure Sim execution.
Candidate cocotb runs use the Sim public API and its isolated tool runtime.
``tool_env`` returns a child environment; ``ensure_toolchain`` explicitly
updates the current process. Both retain a complete toolchain already on PATH
and otherwise try the managed installation. Neither installs tools or adds
bundled libraries to LD_LIBRARY_PATH.

Installation is performed by ``scripts/setup_eda.sh``; the prefix is reported by
:func:`behave.core.paths.eda_root`.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import MutableMapping
from pathlib import Path

from .paths import RepositoryUnavailable, eda_root

__all__ = [
    "REQUIRED_TOOLS",
    "ensure_toolchain",
    "missing_tools",
    "tool_env",
    "toolchain_bin",
]

REQUIRED_TOOLS = ("verilator", "yosys", "iverilog")


def toolchain_bin() -> Path | None:
    """Locate managed ``bin`` by its verilator file; not a full toolchain check."""
    try:
        candidate = eda_root() / "bin"
    except RepositoryUnavailable:
        return None
    return candidate if (candidate / "verilator").is_file() else None


def tool_env() -> dict[str, str]:
    """Discover tools in a copy, then prefer this interpreter's console scripts."""
    env = os.environ.copy()
    _discover(env)
    venv_bin = str(Path(sys.executable).parent)
    parts = [p for p in env.get("PATH", os.defpath).split(os.pathsep) if p and p != venv_bin]
    env["PATH"] = os.pathsep.join([venv_bin, *parts])
    env.setdefault("VIRTUAL_ENV", sys.prefix)
    return env


def ensure_toolchain() -> Path | None:
    """Make the managed toolchain visible to this process.

    Prepends the toolchain's ``bin`` to ``PATH``, mirroring the distribution's
    environment script. A toolchain already reachable through ``PATH`` is left
    untouched, so a system-wide or module-provided installation continues to take
    effect. Returns the directory that was added, or None if no change was made.
    """
    return _discover(os.environ)


def missing_tools() -> tuple[str, ...]:
    """Required tools that are not reachable after discovery."""
    ensure_toolchain()
    return tuple(tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None)


def _discover(env: MutableMapping[str, str]) -> Path | None:
    """Apply the shared fallback rule to the requested environment only."""
    current = env.get("PATH", os.defpath)
    if all(shutil.which(tool, path=current) for tool in REQUIRED_TOOLS):
        return None
    directory = toolchain_bin()
    if directory is None:
        return None
    entries = current.split(os.pathsep) if current else []
    if str(directory) in entries:
        return None
    env["PATH"] = os.pathsep.join([str(directory), *entries])
    return directory
