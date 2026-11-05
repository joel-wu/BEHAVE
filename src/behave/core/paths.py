"""Filesystem locations used across the package.

Source checkouts supply default repository paths; installed wheels require
explicit resource paths. Host scratch uses a separate node-local root.
No module outside this one is expected to construct these roots itself.

Environment variables:
    BEHAVE_REPO_ROOT  explicit checkout-layout assets (also supplies defaults below)
    BEHAVE_DATA_DIR   problem sets and benchmarks (default: <repo>/data)
    BEHAVE_WORK_DIR   run outputs, checkpoints, logs, cache (default: <repo>/work)
    BEHAVE_EDA_ROOT   EDA toolchain prefix (default: <repo>/external/oss-cad-suite)
    BEHAVE_SCRATCH_DIR existing node-local temporary root (default: /tmp)
    BEHAVE_RUNTIME_DIR shared host runtime batch (default: private dir in scratch)
    BEHAVE_ISOLATION_STARTUP_DIR node-local startup admission, relayed to trusted services

Scratch selection ignores ambient TMPDIR, which may point at NFS. Configure a
local path before starting workers; relay sockets use a separate short parent.
Explicit run-directory arguments still select durable journals and artifacts.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from .records import RecordError

__all__ = [
    "RepositoryUnavailable",
    "benchmarks_root",
    "data_root",
    "eda_root",
    "isolation_startup_root",
    "local_rpc_root",
    "problem_set",
    "problems_root",
    "repo_root",
    "scratch_root",
    "tool_resource_environment",
    "work_root",
]


class RepositoryUnavailable(RecordError):
    """An installed package has no implicit checkout resources."""


def _env_path(name: str, default: Callable[[], Path]) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default()


def repo_root() -> Path:
    """Explicit asset root or source checkout, never a guessed wheel ancestor."""
    value = os.environ.get("BEHAVE_REPO_ROOT")
    if value:
        return Path(value).expanduser().resolve()
    source = Path(__file__).resolve()
    # Frozen source snapshots need no .git directory or parent-directory scan.
    if source.parts[-4:] == ("src", "behave", "core", "paths.py"):
        return source.parents[3]
    raise RepositoryUnavailable(
        "repository-root-unavailable",
        "No source checkout default for this installed package. Set BEHAVE_REPO_ROOT "
        "to checkout-layout assets, or configure BEHAVE_DATA_DIR, BEHAVE_WORK_DIR "
        "and BEHAVE_EDA_ROOT separately for the resources you use.",
    )


def data_root() -> Path:
    return _env_path("BEHAVE_DATA_DIR", lambda: repo_root() / "data")


def problems_root() -> Path:
    return data_root() / "problems"


def benchmarks_root() -> Path:
    return data_root() / "benchmarks"


def work_root() -> Path:
    return _env_path("BEHAVE_WORK_DIR", lambda: repo_root() / "work")


def scratch_root(environment: Mapping[str, str] | None = None) -> Path:
    """Host temporary storage, not a candidate-visible sandbox mount."""
    if environment is not None and environment.get("BEHAVE_SCRATCH_DIR"):
        return Path(environment["BEHAVE_SCRATCH_DIR"]).expanduser().resolve()
    return _env_path("BEHAVE_SCRATCH_DIR", lambda: Path("/tmp"))


def eda_root() -> Path:
    return _env_path("BEHAVE_EDA_ROOT", lambda: repo_root() / "external" / "oss-cad-suite")


def tool_resource_environment() -> dict[str, str]:
    """Absolute resource paths for trusted services, without requiring EDA.

    Source checkouts retain their managed-tool default. An installed API-only
    worker needs neither a checkout nor EDA; actual tool users resolve and
    validate their required resources. Caller data/work roots are not relayed.
    """
    environment = {}
    if os.environ.get("BEHAVE_REPO_ROOT"):
        environment["BEHAVE_REPO_ROOT"] = str(repo_root())
    try:
        environment["BEHAVE_EDA_ROOT"] = str(eda_root())
    except RepositoryUnavailable:
        pass
    return environment


def local_rpc_root() -> Path:
    """Short node-local parent for owner-scoped Unix sockets, never archives.

    Execution scratch may be nested arbitrarily. Unix socket names cannot be;
    each relay owns and cleans a private directory directly under /tmp instead.
    Deliberately ignore TMPDIR, which can point at NFS or another long path.
    """
    return Path("/tmp")


def isolation_startup_root() -> Path:
    # Preserve the original host's coordination directory through trusted user
    # namespaces, where getuid() becomes 0. Candidates do not receive this path.
    value = os.environ.get("BEHAVE_ISOLATION_STARTUP_DIR")
    return (Path(value).absolute() if value else
            scratch_root() / f"behave-isolation-startup-{os.getuid()}")


def problem_set(name: str) -> Path:
    """Directory of one problem set, for example ``problem_set("cvdp")``."""
    return problems_root() / name
