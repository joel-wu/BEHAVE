"""Managed ELF dependencies, resolved by the loader used in the guest.

This is installation preparation, never execution of submitted RTL. The fixed
tool profiles do not load arbitrary plugins. Their data files are declared
separately; Python/Qt distributions beside the ELF libraries are not inputs.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.isolation import Binary, ReadOnlyMount, binary, read_only
from behave.isolation.runtime_environment import runtime_environment

NATIVE_FRONTEND_REVISION = "behave-rtl-frontend/23 slang-source=8acc660a2"
NATIVE_FRONTEND_EXECUTABLE = "behave-rtl-frontend-23"
SIM_VERILATOR_EXECUTABLE = "behave-verilator-schedule-refs-1"
SIM_VERILATOR_MARKER = "(behave-schedule-refs-1)"


def _eda_loader(eda):
    libraries = eda / "lib"
    loaders = tuple(libraries.glob("ld-linux*.so.*"))
    if len(loaders) != 1:
        raise FileNotFoundError("managed EDA installation needs one ELF loader")
    return libraries, loaders[0]


def checked_sim_verilator(eda: Path) -> Binary:
    """Bind the qualified compiler once per batch, never silently use stock bits."""
    executable = str(eda / "libexec" / SIM_VERILATOR_EXECUTABLE)

    def prepare():
        from ..core.refusal import Refusal, Refused

        libraries, loader = _eda_loader(eda)
        result = subprocess.run(
            [str(loader), "--library-path", str(libraries), executable, "--version"],
            capture_output=True, text=True, check=False,
            timeout=defaults.ISOLATION_DISCOVERY_SECONDS)
        actual = result.stdout.strip()
        if (result.returncode or not actual.startswith("Verilator 5.051 ")
                or SIM_VERILATOR_MARKER not in actual):
            raise Refused(Refusal(
                "rtl-verilator-version-mismatch",
                "Sim requires the compiler that preserves source evaluation effects; "
                "run scripts/setup_sim_verilator.sh before preparing tasks",
                expected=SIM_VERILATOR_MARKER, actual=actual,
                executable=executable, returncode=result.returncode))
        return binary(executable, "/opt/eda/libexec/verilator_bin", reuse_attestation=True)

    return runtime_environment().prepare(
        ["sim-verilator", executable, SIM_VERILATOR_MARKER], prepare,
        encode=asdict, decode=lambda row: Binary(**row))


def checked_native_frontend(eda: Path) -> Binary:
    """Check the installed producer once per prepared runtime, before parsing RTL."""
    executable = str(eda / "libexec" / NATIVE_FRONTEND_EXECUTABLE)

    def prepare():
        from ..core.refusal import Refusal, Refused

        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            check=False, timeout=defaults.ISOLATION_DISCOVERY_SECONDS)
        actual = result.stdout.strip()
        if result.returncode or actual != NATIVE_FRONTEND_REVISION:
            raise Refused(Refusal(
                "rtl-native-frontend-version-mismatch",
                "the installed native frontend is incompatible with this Sim runtime; "
                "run scripts/setup_rtl_frontend.sh before preparing tasks",
                expected=NATIVE_FRONTEND_REVISION, actual=actual,
                executable=executable, returncode=result.returncode))
        return binary(executable, "/tools/behave-rtl-frontend", reuse_attestation=True)

    return runtime_environment().prepare(
        ["native-frontend", executable, NATIVE_FRONTEND_REVISION], prepare,
        encode=asdict, decode=lambda row: Binary(**row))


def eda_library_mounts(eda: Path, programs: tuple[str, ...]) -> tuple[ReadOnlyMount, ...]:
    programs = tuple(SIM_VERILATOR_EXECUTABLE if name == "verilator_bin" else name
                     for name in programs)

    def prepare():
        libraries, loader = _eda_loader(eda)
        paths = {str(loader)}
        for program in programs:
            try:
                completed = subprocess.run(
                    [str(loader), "--inhibit-cache", "--inhibit-rpath", "",
                     "--library-path", str(libraries), "--list", str(eda / "libexec" / program)],
                    capture_output=True, text=True, check=True,
                    env={"PATH": os.defpath}, timeout=defaults.ISOLATION_DISCOVERY_SECONDS)
            except subprocess.SubprocessError as error:
                raise RuntimeError(f"cannot resolve managed EDA dependencies of {program}: {error}") from error
            for line in completed.stdout.splitlines():
                fields = line.strip().split()
                if not fields:
                    continue
                path = fields[fields.index("=>") + 1] if "=>" in fields else fields[0]
                if path.startswith("/"):
                    paths.add(os.path.normpath(path))
        mounts = []
        for path in sorted(paths):
            source = Path(path)
            target = ("/opt/eda/lib/" + source.name if source.parent == libraries
                      else str(source))
            mounts.append(read_only(str(source), target, reuse_attestation=True))
        return tuple(mounts)

    return runtime_environment().prepare(
        ["eda-libraries", str(eda), programs], prepare,
        encode=lambda rows: [asdict(row) for row in rows],
        decode=lambda rows: tuple(ReadOnlyMount(**row) for row in rows))
