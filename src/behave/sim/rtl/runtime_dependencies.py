"""Python dependencies of EDA, internal replay and public development/TB runtimes.

These are selected package roots and public libraries, not a snapshot of
the caller's virtual environment.
Keep complete package resources (including Cocotb's native libraries and build
files), but never discover dependencies by walking site-packages. The locked
Cocotb installation and the production build/replay tests validate this closure.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

from behave.isolation import ReadOnlyMount, python_package
from behave.isolation.package_image import runtime_file

from ..behavior import LIBRARY_DIR

# pygpi's default startup imports cocotb_tools._coverage even when coverage is
# disabled. All three roots belong to the Cocotb distribution and are needed
# for replay. Builds also run cocotb-config, which imports find_libpython.
_REPLAY_PACKAGES = ("cocotb", "pygpi", "cocotb_tools")
_BUILD_PACKAGES = (*_REPLAY_PACKAGES, "find_libpython")
_PACKAGES_BY_PROFILE = {
    "development": _BUILD_PACKAGES,
    "testbench-build": _BUILD_PACKAGES,
    "testbench": _REPLAY_PACKAGES,
    "simulation": _REPLAY_PACKAGES,
    "eda": _BUILD_PACKAGES,
    "source-events": _BUILD_PACKAGES,
    "elaboration": (),
    "catalogue": (),
    "source-catalogue": (),
    "source-observation": (),
}


def python_dependency_mounts(profile: str) -> tuple[ReadOnlyMount, ...]:
    """Freeze declared packages and, for public runtimes, FP and memory helpers.

    Each root is resolved independently, so split installations work too. An
    absent or ambiguous dependency fails here; it cannot widen the mount to an
    ambient Python directory. Every selected package still uses the existing
    local image, byte-verified at batch preparation. Candidate input checks are
    independent of this fixed runtime.
    """
    if profile not in _PACKAGES_BY_PROFILE:
        raise ValueError("unknown RTL runtime profile")
    mounts = []
    for name in _PACKAGES_BY_PROFILE[profile]:
        spec = find_spec(name)
        locations = () if spec is None else tuple(spec.submodule_search_locations or ())
        if len(locations) != 1 or spec.origin is None:
            raise FileNotFoundError(
                f"RTL {profile} requires one installed regular package {name!r}")
        source = Path(locations[0]).resolve()
        if not source.is_dir() or Path(spec.origin).resolve() != source / "__init__.py":
            raise FileNotFoundError(
                f"RTL {profile} package {name!r} has no unique source directory")
        mounts.append(python_package(str(source), f"/runtime/site/{name}"))
    if profile in {"development", "testbench-build", "testbench"}:
        # Publish only the approved module, from the same source used by the
        # Behavior linker. The existing runtime image is shared across rollouts.
        mounts.append(runtime_file(str(LIBRARY_DIR / "fp_lib.py"), "/runtime/site/fp_lib.py"))
        # The public responder includes the same pure transition used by Sim,
        # but no task loader, hidden reference, observation or scoring modules.
        mounts.append(python_package(
            str(Path(__file__).resolve().parents[1] / "memory_driver"),
            "/runtime/site/memory_driver"))
    return tuple(mounts)
