"""Closed multi-file RTL input, normalized by the real compiler preprocessor.

Compilation-unit order and include directories are explicit and hashed. The
result is an ordinary RTL source for the existing admission/replay/analysis path;
its goal locations refer to that retained normalized source. No Python-side
macro expansion, source concatenation or ambient include search is performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import TYPE_CHECKING

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, relative_paths
from behave.core.paths import scratch_root
from behave.core.records import RecordError, canonical_json

from ..core.failure import FailureRaised, RunFailure
from ..core.refusal import Refusal
from .frontend_error import SourceRejected

if TYPE_CHECKING:
    from .tool_protocol import ToolExecution


def _invalid_bundle(why):
    return FailureRaised(RunFailure.candidate(
        "candidate-source", Refusal("candidate-rtl-build-invalid", why)))


@dataclass(frozen=True, slots=True)
class RTLSourceBundle:
    files: MappingProxyType
    compilation_units: tuple[str, ...]
    include_dirs: tuple[str, ...] = (".",)

    def __post_init__(self):
        files = dict(self.files)
        try:
            relative_paths(tuple(files))
            relative_paths(self.compilation_units)
        except RecordError as error:
            raise _invalid_bundle(str(error)) from error
        if not 1 <= len(files) <= defaults.SIM_SOURCE_BUNDLE_FILES or any(
                type(data) is not bytes for data in files.values()):
            raise _invalid_bundle(
                f"RTL bundle requires 1 to {defaults.SIM_SOURCE_BUNDLE_FILES} byte sources")
        if sum(map(len, files.values())) > defaults.SIM_SOURCE_BYTES:
            raise _invalid_bundle(
                f"RTL source bundle exceeds {defaults.SIM_SOURCE_BYTES} bytes")
        if not self.compilation_units or not set(self.compilation_units) <= set(files):
            raise _invalid_bundle("compilation units must be explicit captured files")
        if type(self.include_dirs) is not tuple or len(set(self.include_dirs)) != len(self.include_dirs):
            raise _invalid_bundle("include directories must be a distinct tuple")
        for directory in self.include_dirs:
            if directory != ".":
                try:
                    relative_paths((directory,))
                except RecordError as error:
                    raise _invalid_bundle(str(error)) from error
            if not any(directory == "." or path.startswith(directory + "/") for path in files):
                raise _invalid_bundle("include directories must belong to the captured bundle")
        object.__setattr__(self, "files", MappingProxyType(files))

    @property
    def identity(self) -> BlobRef:
        return BlobRef.of(canonical_json({
            "schema": "behave.rtl-source-bundle/1",
            "files": {path: BlobRef.of(data).as_dict() for path, data in self.files.items()},
            "compilation_units": list(self.compilation_units), "include_dirs": list(self.include_dirs),
        }))


@dataclass(frozen=True, slots=True)
class PreparedRTLSource:
    source: str
    bundle: BlobRef
    execution: ToolExecution

    def as_dict(self):
        return {"schema": "behave.prepared-rtl-source/1", "bundle": self.bundle.as_dict(),
                "source": BlobRef.of(self.source.encode()).as_dict(), "execution": self.execution.as_dict()}


def prepare_rtl_bundle(bundle: RTLSourceBundle, *,
                       timeout_s: int = defaults.SIM_RTL_BUNDLE_SECONDS) -> PreparedRTLSource:
    """Preprocess all named units inside the same isolated EDA boundary."""
    from .tool_isolation import default_limits, run_workspace
    from .tool_protocol import (
        ToolCommand,
        ToolGroup,
        ToolIsolationError,
        command_result,
        command_stream,
        require_completed,
    )

    if type(bundle) is not RTLSourceBundle or type(timeout_s) is not int or timeout_s < 1:
        raise TypeError("preprocessing requires an RTLSourceBundle and a positive timeout")
    with TemporaryDirectory(prefix="behave-rtl-sources-", dir=scratch_root()) as temporary:
        workspace = Path(temporary)
        for name, data in bundle.files.items():
            path = workspace / "sources" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        command = ToolCommand("rtl-preprocess", (
            "/tools/verilator", "-E", "-P", "-sv", "-Wno-fatal",
            *("-I./" + directory for directory in bundle.include_dirs),
            *("./" + unit for unit in bundle.compilation_units),
        ), cwd="sources", timeout_s=timeout_s)
        execution = require_completed(run_workspace(workspace, (ToolGroup((command,)),),
            runtime_profile="elaboration",
            role="rtl-source-bundle", limits=default_limits(
                wall_seconds=timeout_s + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS)))
    outcome = command_result(execution, "rtl-preprocess")
    if outcome.get("timed_out") or outcome.get("output_limited"):
        raise ToolIsolationError("rtl-source-bundle-resource-limit", "preprocessing exceeded its budget", execution)
    if outcome.get("returncode") == 1:
        raise SourceRejected(Refusal("rtl-source-rejected", "the compiler rejected the source bundle",
            diagnostic=command_stream(execution, "rtl-preprocess", "stderr").decode(errors="replace"),
            execution=execution.as_dict()))
    if outcome.get("returncode") != 0:
        raise ToolIsolationError("rtl-source-bundle-process-failed", "preprocessing did not complete", execution)
    source = command_stream(execution, "rtl-preprocess", "stdout").decode("utf-8")
    if not source.strip():
        raise SourceRejected(Refusal("rtl-source-rejected", "the source bundle contains no compilation output"))
    return PreparedRTLSource(source, bundle.identity, execution)


def bundle_for_entry(files: dict[str, bytes], entry: str) -> RTLSourceBundle:
    """Default judge build: sorted HDL helpers, then entry; optional rtl.f order.

    rtl.f is a closed newline-separated list of captured compilation units, not
    a compiler command/options file. Headers remain includes. It permits package
    ordering without interpreting arbitrary simulator flags from a submission.
    """
    if entry not in files:
        raise _invalid_bundle("RTL entry is not captured")
    if "rtl.f" in files:
        try:
            manifest = files["rtl.f"].decode("utf-8")
        except UnicodeDecodeError as error:
            raise _invalid_bundle("RTL compilation manifest must be UTF-8") from error
        units = tuple(line.strip() for line in manifest.splitlines()
                      if line.strip() and not line.lstrip().startswith("#"))
    else:
        units = (*sorted(name for name in files if name != entry and PurePosixPath(name).suffix in (".v", ".sv")), entry)
    if entry not in units:
        raise _invalid_bundle("RTL compilation order must include the entry file")
    directories = tuple(sorted({".", *(str(PurePosixPath(name).parent) for name in files)}))
    return RTLSourceBundle(files, units, directories)
