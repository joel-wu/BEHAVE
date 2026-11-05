"""Prepared Sim tool environments, independent of command execution.

This module owns profile discovery, immutable dependency closures and batch
preparation. It does not launch tools, publish outputs or inspect replay state.
Protocol revisions remain part of the frozen runtime identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from behave.core.paths import eda_root
from behave.isolation import (
    Binary,
    MaskedPath,
    ReadOnlyMount,
    binary,
    content_digest,
    native_runtime,
    python_package,
    python_runtime,
    read_only,
    runtime_tree,
)

from .native_dependencies import (
    NATIVE_FRONTEND_EXECUTABLE,
    checked_native_frontend,
    checked_sim_verilator,
    eda_library_mounts,
)
from .runtime_dependencies import python_dependency_mounts
from .tool_protocol import EXECUTION_VERSION, JOB_VERSION

_WRAPPER_NAMES = (
    "cocotb-config", "python", "python3", "verilator",
    "verilator_coverage", "yosys",
)
RUNTIME_PROFILES = frozenset({
    "eda", "simulation", "source-events", "source-observation", "elaboration",
    "catalogue", "source-catalogue", "development", "testbench-build", "testbench", "iverilog",
})


def _compiler_version() -> str:
    return platform.machine() + "-linux-gnu"


def _compiler_runtime_paths() -> tuple[str, Path, Path]:
    """The selected GCC target, library tree and executable-helper tree."""

    machine = _compiler_version()
    gcc_root = Path("/usr/lib/gcc") / machine
    versions = sorted((path for path in gcc_root.iterdir() if path.is_dir()),
                      key=lambda path: path.name)
    if not versions:
        raise FileNotFoundError(f"no GCC runtime under {gcc_root}")
    gcc = versions[-1]
    libexec = Path("/usr/libexec/gcc") / machine / gcc.name
    if not libexec.is_dir():
        raise FileNotFoundError(f"no GCC executable runtime under {libexec}")
    return machine, gcc, libexec


def _without_redundant_mounts(
    mounts: tuple[ReadOnlyMount, ...] | list[ReadOnlyMount],
) -> tuple[ReadOnlyMount, ...]:
    """Remove a child mount already supplied byte-for-byte by a broader tree.

    A bind mount cannot be installed over a directory that already contains bind
    mounts and then recursively remounted on every supported kernel.  Python's exact
    library closure and the compiler's declared system-library tree overlap in just
    that way.  Keep the broader digest-bound tree when its *same relative path* is a
    same immutable object.  A symlink supplied by the broader tree is also
    redundant when the parent policy explicitly admits links, the link resolves
    *inside that same frozen tree*, and the resolved object is the exact leaf mount.
    The tree digest binds both the link text and its in-tree target bytes.  Links that
    leave the tree are never used as evidence for removing a leaf mount.
    """

    # Independent executable closures can request the identical loader. Keep
    # conflicting descriptions distinct so the isolation boundary rejects them.
    items = tuple(dict.fromkeys(mounts))
    kept = []
    for item in items:
        redundant = False
        target = PurePosixPath(item.target)
        for parent in items:
            if parent is item or not Path(parent.source).is_dir():
                continue
            try:
                relative = target.relative_to(PurePosixPath(parent.target))
            except ValueError:
                continue
            if not relative.parts:
                continue
            supplied = Path(parent.source).joinpath(*relative.parts)
            try:
                info = supplied.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                if not parent.allow_symlinks:
                    continue
                resolved = Path(os.path.realpath(supplied))
                try:
                    resolved.relative_to(Path(parent.source).resolve())
                except ValueError:
                    continue
            if os.path.realpath(supplied) == os.path.realpath(item.source) or (
                    supplied.is_file() and Path(item.source).is_file()
                    and content_digest(str(supplied.resolve())) == item.sha256):
                redundant = True
                break
        if not redundant:
            kept.append(item)
    return tuple(kept)


def _frontend_runtime(profile: str):
    """The closed tool dependencies of a frontend-only operation.

    Source declaration and observation parse resolved source; the legacy circuit
    catalogue additionally needs Yosys. None builds a simulator or imports
    site-packages. Build/replay dependencies belong to their own profiles.
    Every retained input has a byte identity fixed by the batch preparation.
    """
    from behave.isolation import executable_script

    if profile == "elaboration":
        programs = (("verilator", "verilator_bin"),)
        resources = ("verilator",)
    elif profile == "catalogue":
        programs = (("yosys", "yosys"), ("slang", "slang"))
        resources = ("yosys",)
    elif profile == "source-catalogue":
        programs = (("slang", "slang"),)
        resources = ()
    elif profile == "source-observation":
        programs = (("verilator", "verilator_bin"),)
        resources = ("verilator",)
    else:
        raise ValueError("unknown frontend runtime profile")
    py_binaries, py_mounts, masked = python_runtime()
    binaries, mounts = list(py_binaries), list(py_mounts)
    directory = Path(__file__).resolve().parent
    eda = eda_root().resolve()
    for name, executable in programs:
        binaries.append(executable_script(str(directory / "_tool_wrapper.py"), f"/tools/{name}"))
        binaries.append(checked_sim_verilator(eda) if executable == "verilator_bin" else
                        binary(str(eda / "libexec" / executable),
                               f"/opt/eda/libexec/{executable}", reuse_attestation=True))
    if programs:
        mounts.extend(eda_library_mounts(eda, tuple(executable for _, executable in programs)))
    if profile in {"catalogue", "source-catalogue", "source-observation"}:
        frontend = str(eda / "libexec" / NATIVE_FRONTEND_EXECUTABLE)
        binaries.append(checked_native_frontend(eda))
        mounts.extend(native_runtime((frontend,)))
    for name in resources:
        mounts.append(runtime_tree(str(eda / "share" / name),
                                   f"/opt/eda/share/{name}", allow_symlinks=True))
    mounts.append(read_only(str(directory / "_tool_job.py"), "/runtime/rtl-tool-job.py",
                            reuse_attestation=True))
    if profile in {"catalogue", "source-catalogue", "source-observation"}:
        # compiler_command executes the same package module as the full runtime;
        # the standalone output normalizer also retains its exact mounted bytes.
        import behave

        mounts.append(python_package(str(Path(behave.__file__).resolve().parent),
                                     "/runtime/behave/behave"))
        if profile == "catalogue":
            mounts.append(read_only(str(directory / "_rtlil_sites.py"), "/runtime/rtlil-sites.py",
                                    reuse_attestation=True))
    return tuple(binaries), _without_redundant_mounts(mounts), tuple(masked)


def _iverilog_runtime():
    """Icarus/Python tools for workflows whose own harness uses Icarus.

    This is an explicit profile, not a fallback from Verilator or Sim replay.
    The installation and its loader are shared read-only. Candidate programs
    still run in the same supervised tool namespace.
    """
    from behave.isolation import executable_script

    py_binaries, py_mounts, masked = python_runtime()
    binaries, mounts = list(py_binaries), list(py_mounts)
    eda = eda_root().resolve()
    wrapper = Path(__file__).with_name("_tool_wrapper.py")
    for name in ("python", "python3", "iverilog", "vvp"):
        binaries.append(executable_script(str(wrapper), f"/tools/{name}"))
    for name in ("iverilog", "vvp"):
        binaries.append(binary(str(eda / "libexec" / name), f"/opt/eda/libexec/{name}",
                               reuse_attestation=True))
    for name in ("ivl", "ivlpp"):
        binaries.append(binary(str(eda / "libexec" / name), f"/opt/eda/libexec/{name}",
                               reuse_attestation=True))
        binaries.append(executable_script(str(wrapper), f"/opt/eda/lib/ivl/{name}"))
    modules = []
    for path in sorted((eda / "lib/ivl").iterdir()):
        if path.name in {"ivl", "ivlpp"}:
            continue
        mounts.append(read_only(str(path), "/opt/eda/lib/ivl/" + path.name,
                                allow_symlinks=True, reuse_attestation=True))
        if path.suffix in {".vpi", ".tgt"}:
            modules.append("../lib/ivl/" + path.name)
    mounts.extend(eda_library_mounts(eda, ("iverilog", "vvp", "ivl", "ivlpp", *modules)))
    bash = shutil.which("bash", path=os.defpath)
    binaries.extend(binary(bash, target, reuse_attestation=True)
                    for target in ("/tools/bash", "/bin/sh"))
    mounts.extend(native_runtime((bash,)))
    mounts.append(read_only(str(Path(__file__).with_name("_tool_job.py")),
                            "/runtime/rtl-tool-job.py", reuse_attestation=True))
    return tuple(binaries), _without_redundant_mounts(mounts), tuple(masked)


def _runtime(profile: str = "eda"):
    """Freeze the managed EDA, Python, cocotb and host compiler runtime.

    Discovery happens only in this trusted policy builder.  Every discovered byte
    is represented by a digest in the resulting ``IsolationSpec``; none of the
    paths are searched again inside the guest.
    """

    if profile not in RUNTIME_PROFILES:
        raise ValueError("unknown RTL runtime profile")
    if profile == "iverilog":
        return _iverilog_runtime()
    if profile in {"elaboration", "catalogue", "source-catalogue", "source-observation"}:
        return _frontend_runtime(profile)
    simulation = profile in {"simulation", "testbench"}
    public = profile in {"development", "testbench-build", "testbench"}
    py_binaries, py_mounts, masked = python_runtime()
    wrapper = Path(__file__).with_name("_tool_wrapper.py").resolve()
    runner = Path(__file__).with_name("_tool_job.py").resolve()
    rtlil_sites = Path(__file__).with_name("_rtlil_sites.py").resolve()
    state_ownership = Path(__file__).with_name(
        "_yosys_state_ownership.py").resolve()
    binaries = list(py_binaries)
    if not simulation:
        from behave.isolation import executable_script

        binaries.extend(
            executable_script(str(wrapper), f"/tools/{name}")
            for name in (*_WRAPPER_NAMES, *(("slang",) if profile == "source-events" else ())))

    def host_binary(name: str, target: str):
        found = shutil.which(name)
        if found is None:
            raise FileNotFoundError(f"required RTL runtime binary {name!r}")
        binaries.append(binary(found, target, reuse_attestation=True))

    if not simulation:
        host_binary("make", "/tools/make")
        # GNU make uses /bin/sh regardless of the caller's SHELL environment.
        host_binary("dash", "/bin/sh")
        # Cocotb's trusted makefile invokes ``bash`` by name for its Python/tool
        # probes.  It therefore belongs in the explicit PATH closure as well; omitting
        # it makes a valid build report an empty Python architecture.
        host_binary("bash", "/tools/bash")
        # GCC derives its built-in include and helper search from the conventional
        # driver location.  Relocating the driver itself under ``/tools`` loses that
        # prefix (first ``cc1plus``, then GCC's own ``stddef.h``).  These remain exact
        # digest-bound files; only their guest path matches the compiler layout.
        host_binary("g++", "/usr/bin/g++")
        host_binary("gcc", "/usr/bin/gcc")
        for name in ("ar", "as", "ld", "nm", "ranlib", "strip"):
            host_binary(name, f"/tools/{name}")
        # Commands used by cocotb's and Verilator's trusted makefiles.  Exposing
        # individual digest-bound tools avoids an ambient /usr/bin mount while
        # keeping the build description relocatable.
        for name in (
            "basename", "cat", "cut", "dirname", "head", "mkdir", "rm",
            "sort", "tr", "uname", "xargs",
        ):
            host_binary(name, f"/tools/{name}")

        # Exact RTL publication validates Yosys' SMT-LIB output before it can
        # replace a previously usable relation.  Resolve the solver beside the
        # selected Python runtime rather than through ambient PATH: the virtual
        # environment is part of the frozen judge runtime and its binary bytes enter
        # the isolation-policy digest.
        if not public:
            z3 = Path(sys.executable).with_name("z3").resolve()
            if not z3.is_file():
                raise FileNotFoundError(
                    f"the RTL runtime has no trusted SMT-LIB validator at {z3}")
            binaries.append(binary(
                str(z3), "/tools/z3", reuse_attestation=True))

    eda = eda_root().resolve()
    if not simulation:
        if not (eda / "libexec" / "yosys").is_file():
            raise FileNotFoundError(
                f"the managed RTL toolchain is incomplete under {eda}")
        binaries.extend((
            checked_sim_verilator(eda),
            binary(str(eda / "libexec" / "verilator_coverage_bin_dbg"),
                   "/opt/eda/libexec/verilator_coverage_bin_dbg",
                   reuse_attestation=True),
            binary(
                str(eda / "libexec" / "yosys"),
                "/opt/eda/libexec/yosys",
                reuse_attestation=True,
            ),
        ))
        if profile == "source-events":
            binaries.append(binary(
                str(eda / "libexec" / "slang"), "/opt/eda/libexec/slang",
                reuse_attestation=True))
        if public:
            # With the managed ELF loader, Yosys locates ABC beside that loader.
            binaries.append(executable_script(str(wrapper), "/opt/eda/lib/yosys-abc"))
            binaries.append(binary(str(eda / "libexec/yosys-abc"),
                                   "/opt/eda/libexec/abc", reuse_attestation=True))

    mounts = list(py_mounts)
    if not simulation:
        # Public TB builds use the same one-CPU simulator entry point without
        # receiving Sim's private Python package or scoring implementation.
        mounts.append(read_only(
            str(Path(__file__).with_name("cocotb_main.cpp")),
            "/runtime/cocotb-main.cpp", reuse_attestation=True))
    if profile == "source-events":
        # This source-analysis executable is built at installation, not per
        # candidate. It links Slang statically and uses the host C++ runtime.
        frontend = str(eda / "libexec" / NATIVE_FRONTEND_EXECUTABLE)
        binaries.append(checked_native_frontend(eda))
        mounts.extend(native_runtime((frontend,)))
    if profile != "development":
        mounts.append(read_only(
            str(runner), "/runtime/rtl-tool-job.py", reuse_attestation=True))
    if not public:
        mounts.append(read_only(
            str(rtlil_sites), "/runtime/rtlil-sites.py", reuse_attestation=True))
        mounts.append(read_only(
            str(state_ownership),
            "/runtime/yosys-state-ownership.py",
            reuse_attestation=True,
        ))
    if not simulation:
        mounts.extend(eda_library_mounts(eda, (
            "verilator_bin", "verilator_coverage_bin_dbg", "yosys", "yosys-abc",
            *(("slang",) if profile == "source-events" else ()))))
        mounts.extend((
            runtime_tree(str(eda / "share" / "verilator"),
                         "/opt/eda/share/verilator", allow_symlinks=True),
            runtime_tree(str(eda / "share" / "yosys"),
                         "/opt/eda/share/yosys", allow_symlinks=True),
        ))

    # Only the stage's declared dependencies enter the guest. Development,
    # training and plotting packages installed beside Cocotb are not RTL inputs.
    mounts.extend(python_dependency_mounts(profile))
    if not public:
        import behave

        package = Path(behave.__file__).resolve().parent
        mounts.append(python_package(
            str(package), "/runtime/behave/behave"))

    machine = _compiler_version()
    system_mounts = [
        (f"/usr/lib/{machine}", f"/usr/lib/{machine}"),
        (f"/usr/lib/{machine}", f"/lib/{machine}"),
    ]
    if not simulation:
        machine, gcc, libexec = _compiler_runtime_paths()
        system_mounts[:0] = [
            ("/usr/include", "/usr/include"),
            (f"/usr/include/{machine}", f"/usr/include/{machine}"),
            (str(gcc), str(gcc)),
            (str(libexec), str(libexec)),
        ]
    for source, target in system_mounts:
        if os.path.exists(source):
            mounts.append(read_only(
                source,
                target,
                allow_symlinks=True,
                reuse_attestation=True,
            ))
    return tuple(binaries), _without_redundant_mounts(mounts), tuple(masked)


@dataclass(frozen=True, slots=True)
class FrozenToolRuntime:
    """One trusted runtime closure, shared by request identity and actual launch.

    This is a host-owned value, not a deserializable candidate manifest or cache.
    Prepared runtime objects stay fixed for the batch. Keeping this value avoids
    rediscovering a different runtime between labeling and execution. Submitted
    source and per-run inputs still receive their ordinary launch checks.
    """

    profile: str
    binaries: tuple[Binary, ...]
    mounts: tuple[ReadOnlyMount, ...]
    masked: tuple[MaskedPath, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.profile) is not str or self.profile not in RUNTIME_PROFILES:
            raise ValueError("unknown RTL runtime profile")
        for values, kind in ((self.binaries, Binary), (self.mounts, ReadOnlyMount),
                             (self.masked, MaskedPath)):
            if type(values) is not tuple or any(type(item) is not kind for item in values):
                raise TypeError("a frozen runtime contains exact immutable isolation records")
        if not self.binaries:
            raise ValueError("a frozen runtime needs its runner executable")
        image = {
            "version": JOB_VERSION,
            "execution_version": EXECUTION_VERSION,
            "profile": self.profile,
            "binaries": [item.identity() for item in self.binaries],
            "read_only": [item.identity() for item in self.mounts],
            "masked": [{"target": item.target} for item in self.masked],
        }
        object.__setattr__(self, "identity", hashlib.sha256(json.dumps(
            image, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest())


def freeze_runtime(profile: str = "eda") -> FrozenToolRuntime:
    """Get a profile of the batch runtime; installations are read only on preparation."""
    from behave.isolation.runtime_environment import runtime_environment

    if profile not in RUNTIME_PROFILES:
        raise ValueError("unknown RTL runtime profile")
    def encode(runtime):
        return {"binaries": [asdict(item) for item in runtime.binaries],
                "mounts": [asdict(item) for item in runtime.mounts],
                "masked": [asdict(item) for item in runtime.masked]}

    def decode(row):
        return FrozenToolRuntime(profile, tuple(Binary(**item) for item in row["binaries"]),
                                 tuple(ReadOnlyMount(**item) for item in row["mounts"]),
                                 tuple(MaskedPath(**item) for item in row["masked"]))

    return runtime_environment().prepare(
        ["rtl-runtime/4", JOB_VERSION, profile, str(eda_root()), sys.executable, str(Path(__file__).parent)],
        lambda: FrozenToolRuntime(profile, *_runtime(profile)), encode=encode, decode=decode)

