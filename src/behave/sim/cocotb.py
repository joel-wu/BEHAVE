"""Ordinary submitted cocotb tests, separate from authoritative replay.

The compiler, runtime and isolation are shared with RTL replay. Submitted tests
keep their source and imports; no Driver, golden, or comparison oracle is added.
Their XML and coverage files are untrusted diagnostics, never score evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import FileData, SourceArtifact, relative_paths
from behave.core.records import canonical_json

COCOTB_RUN_VERSION = "submitted-cocotb/5"


def development_limits():
    """Use the same configured compiler/simulator budget for interactive builds."""
    from .rtl.tool_isolation import default_limits

    return default_limits()


def prepare_development_environment(shell):
    """Add the public EDA/Python toolchain to a caller-owned shell template.

    This shares installation preparation with Sim, not Sim's implementation or
    any task/reference inputs. Mutable workspaces and documentation belong to
    the caller. The returned IsolationSpec still uses the caller's limits and
    the ordinary process-tree supervisor.
    """
    from behave.isolation import Device

    from .rtl.tool_runtime import _without_redundant_mounts, freeze_runtime

    runtime = freeze_runtime("development")
    binaries = {item.target: item for item in (*runtime.binaries, *shell.binaries)}
    mounts = {item.target: item for item in (*shell.read_only, *runtime.mounts)}
    environment = dict(shell.environment)
    environment.update({
        "PATH": "/tools:/usr/bin:/bin",
        "PYTHONPATH": "/workspace:/runtime/site",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYGPI_PYTHON_BIN": "/runtime/python/bin/python",
        "LD_LIBRARY_PATH": "/runtime/python/lib:/runtime/site/cocotb/libs",
        "CC": "/usr/bin/gcc", "CXX": "/usr/bin/g++", "AR": "/tools/ar",
        "SHELL": "/bin/sh", "VERILATOR_ROOT": "/opt/eda/share/verilator",
    })
    return replace(shell, binaries=tuple(binaries.values()),
                   read_only=_without_redundant_mounts(tuple(mounts.values())),
                   masked=runtime.masked, environment=tuple(environment.items()),
                   devices=(*shell.devices, Device("/dev/null", "/dev/null", writable=True),
                            Device("/dev/urandom", "/dev/urandom")))


@dataclass(frozen=True, slots=True)
class CocotbResult:
    status: str
    code: str
    comparison: str
    # Named bytes, not paths into a disappearing workspace. All output files
    # remain explicitly labelled as submitted-process diagnostics in the receipt.
    evidence: tuple[tuple[str, bytes], ...]
    # Covered / instrumented source lines from Verilator's LCOV export.
    line_coverage: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.status not in ("completed", "failed", "unavailable") or self.comparison not in (
            "passed", "failed", "not_reported"
        ):
            raise ValueError("invalid submitted cocotb status")
        if type(self.code) is not str or not self.code:
            raise ValueError("cocotb result requires a reason code")
        if type(self.evidence) is not tuple or any(
            type(item) is not tuple or len(item) != 2 or type(item[0]) is not str or type(item[1]) is not bytes
            for item in self.evidence
        ):
            raise TypeError("cocotb evidence requires named immutable bytes")
        relative_paths(tuple(name for name, _ in self.evidence))
        if self.line_coverage is not None and (
            type(self.line_coverage) is not tuple or len(self.line_coverage) != 2
            or any(type(value) is not int for value in self.line_coverage)
            or not 0 <= self.line_coverage[0] <= self.line_coverage[1]
        ):
            raise ValueError("invalid cocotb line coverage counts")


def _line_counts(report: bytes) -> tuple[int, int]:
    """Count DA rows; Verilator owns source mapping and point/instance merging."""
    covered = total = 0
    for row in report.decode("utf-8").splitlines():
        if row.startswith("DA:"):
            line, hits, *_ = row[3:].split(",")
            if int(line) < 1 or int(hits) < 0:
                raise ValueError("invalid LCOV line count")
            covered += int(int(hits) > 0)
            total += 1
    return covered, total


def _export_line_coverage(workdir: Path, raw: bytes, log_bytes: int):
    """Use the installed converter, without recompiling or replaying the TB."""
    from behave.isolation import IsolationStatus

    from .rtl.tool_isolation import default_limits, run_workspace
    from .rtl.tool_protocol import ToolCommand, ToolGroup, command_result

    workdir.mkdir()
    (workdir / "coverage.dat").write_bytes(raw)
    execution = run_workspace(
        workdir, (ToolGroup((ToolCommand(
            "submitted-tb:coverage", ("/tools/verilator_coverage", "--write-info", "coverage.info", "coverage.dat"),
            timeout_s=defaults.TB_WORKER_OVERHEAD_SECONDS,
        ),)),),
        outputs=("coverage.info",), role="submitted-tb:coverage",
        limits=default_limits(wall_seconds=defaults.TB_WORKER_OVERHEAD_SECONDS),
    )
    counts = None
    report = execution.outputs.get("coverage.info")
    if execution.status is IsolationStatus.COMPLETED:
        row = command_result(execution, "submitted-tb:coverage")
        if row["returncode"] == 0 and not row["timed_out"] and not row.get("output_limited") and report:
            counts = _line_counts(report)
    return execution, counts


def _reported_comparison(data: bytes) -> str:
    """Read bounded UTF-8 JUnit as a submitted claim, not an attestation."""
    if len(data) > defaults.SIM_COCOTB_REPORT_BYTES:
        return "not_reported"
    try:
        text = data.decode("utf-8")
        if "<!" in text:
            return "not_reported"
        root = ElementTree.fromstring(text)
    except (UnicodeError, ElementTree.ParseError):
        return "not_reported"
    cases = list(root.iter("testcase"))
    if not cases:
        return "not_reported"
    if any(case.find("failure") is not None or case.find("error") is not None for case in cases):
        return "failed"
    return "passed" if any(case.find("skipped") is None for case in cases) else "not_reported"


def _evidence(executions, log_bytes: int) -> tuple[tuple[str, bytes], ...]:
    """Expose a bounded console without making callers decode RTL internals.

    Complete command streams are separate evidence files. The
    console is their labelled tail, so a compile banner cannot hide a later
    assertion failure from the development tool.
    """
    from .rtl.tool_protocol import command_stream

    console = bytearray()
    records = []
    for index, execution in enumerate(executions):
        records.append((f"execution-{index}.json", canonical_json(execution.as_dict())))
        for command_index, row in enumerate(execution.commands):
            if "skipped" in row:
                continue
            for stream in ("stdout", "stderr"):
                data = command_stream(execution, row["id"], stream)
                records.append((f"execution-{index}-command-{command_index}.{stream}.log", data))
                if data:
                    console.extend(f"[{row['id']} {stream}]\n".encode())
                    console.extend(data)
                    del console[:-log_bytes]
        if execution.stderr_tail:
            console.extend(execution.stderr_tail.encode())
            del console[:-log_bytes]
    return (*records, ("console.txt", bytes(console)))


def run_cocotb_test(
    files: tuple[FileData, ...], *, rtl: SourceArtifact, testbench: SourceArtifact,
    workdir: Path, timeout_seconds: int = defaults.TB_RUN_SECONDS,
    log_bytes: int = defaults.LOG_RETAIN_BYTES, seed: int = defaults.TB_SEED,
    compile_timeout_seconds: int = defaults.TB_COMPILE_SECONDS,
) -> CocotbResult:
    """Run declared sources with Verilator and normal cocotb discovery.

    ``testbench.entry_symbol`` is a test function, or ``*`` for every decorated
    test in ``entry_file``. All selected .v/.sv files are compiled; headers and
    Python/data files retain their relative paths. Submitted Makefiles are data,
    not build authority. Compilation and simulation have separate command
    deadlines. The caller must also supervise the complete service, including
    runtime preparation and evidence export. No host execution fallback.
    """
    from .harness import generate_makefile
    from .rtl.tool_isolation import run_verilator_workspace
    from .rtl.tool_protocol import ToolIsolationError, command_result

    if type(files) is not tuple or any(type(file) is not FileData for file in files):
        raise TypeError("cocotb sources require immutable FileData values")
    relative_paths(tuple(file.reference.path for file in files))
    if type(rtl) is not SourceArtifact or type(testbench) is not SourceArtifact:
        raise TypeError("cocotb entries require SourceArtifact")
    for value in (timeout_seconds, compile_timeout_seconds, log_bytes):
        if type(value) is not int or value < 1:
            raise ValueError("cocotb limits must be positive integers")
    if type(seed) is not int or not 1 <= seed < (1 << 31):
        raise ValueError("cocotb seed must be a positive signed 32-bit integer")
    by_path = {file.reference.path: file.reference for file in files}
    if any(by_path.get(file.path) != file for artifact in (rtl, testbench) for file in artifact.files):
        raise ValueError("cocotb source bytes do not match their declared artifacts")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", rtl.entry_symbol) is None:
        return CocotbResult("failed", "tb-top-unsupported", "not_reported", ())
    entry = PurePosixPath(testbench.entry_file)
    if entry.suffix != ".py" or not all(part.isidentifier() for part in entry.with_suffix("").parts):
        return CocotbResult("failed", "tb-module-path-unsupported", "not_reported", ())
    if testbench.entry_symbol != "*" and not testbench.entry_symbol.isidentifier():
        return CocotbResult("failed", "tb-test-selector-unsupported", "not_reported", ())
    # Make expands source/include arguments. This profile never interpolates
    # arbitrary path bytes into a build program.
    if any(re.fullmatch(r"[A-Za-z0-9_./-]+", file.path) is None for file in rtl.files):
        return CocotbResult("failed", "tb-rtl-path-unsupported", "not_reported", ())
    verilog = tuple("submitted/" + file.path for file in rtl.files
                    if PurePosixPath(file.path).suffix in (".v", ".sv"))
    if "submitted/" + rtl.entry_file not in verilog:
        return CocotbResult("failed", "tb-rtl-language-unsupported", "not_reported", ())
    module = ".".join(entry.with_suffix("").parts)
    # Discover the submitted module itself, including normal package imports.
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=False)
    for file in files:
        path = workdir / "submitted" / file.reference.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.data)
    # No generated Python wrapper: normal cocotb discovers the actual module.
    generate_makefile(workdir, rtl.entry_symbol, tb_module=module, coverage=False)
    include_dirs = sorted({"/work/submitted/" + str(PurePosixPath(file.path).parent) for file in rtl.files})
    with (workdir / "Makefile").open("a") as stream:
        stream.write("\nEXTRA_ARGS += --coverage-line " + " ".join("-I" + name for name in include_dirs) + "\n")
    roots = ("submitted", *sorted({"submitted/" + str(PurePosixPath(file.reference.path).parent)
                                  for file in files if file.reference.path.endswith(".py")}
                                 - {"submitted/."}))
    test_filter = "" if testbench.entry_symbol == "*" else "^" + re.escape(module + "." + testbench.entry_symbol) + "$"
    try:
        executions = run_verilator_workspace(
            workdir, top_module=rtl.entry_symbol, test_module=module,
            role="submitted-tb", outputs=("submitted/results.xml", "submitted/coverage.dat"),
            compile_timeout_s=compile_timeout_seconds, simulate_timeout_s=timeout_seconds,
            verilog_sources=verilog,
            python_paths=roots, test_filter=test_filter, simulation_cwd="submitted",
            environment=(("COCOTB_RANDOM_SEED", str(seed)),), simulation_args=(f"+verilator+seed+{seed}",),
        )
    except ToolIsolationError as error:
        evidence = _evidence(error.executions, log_bytes)
        if error.code == "rtl-tool-compile-failed":
            row = command_result(error.execution, "submitted-tb:compile")
            code = ("tb-compile-output-limit" if row.get("output_limited") else
                    "tb-compile-timeout" if row.get("timed_out") else error.code)
            return CocotbResult("failed", code, "not_reported", evidence)
        return CocotbResult("unavailable", error.code, "not_reported", evidence)
    simulation = executions[-1]
    row = command_result(simulation, "submitted-tb:simulate")
    line_coverage = None
    outputs = dict(simulation.outputs)
    raw_coverage = outputs.get("submitted/coverage.dat")
    if raw_coverage is not None:
        export, line_coverage = _export_line_coverage(workdir / "coverage-report", raw_coverage, log_bytes)
        executions = (*executions, export)
        outputs.update(export.outputs)
    evidence = _evidence(executions, log_bytes)
    evidence += tuple(("submitted-output/" + PurePosixPath(name).name, data) for name, data in sorted(outputs.items()))
    comparison = _reported_comparison(simulation.outputs.get("submitted/results.xml", b""))
    if row.get("output_limited"):
        return CocotbResult("failed", "tb-output-limit", comparison, evidence, line_coverage)
    if row.get("timed_out"):
        return CocotbResult("failed", "tb-timeout", comparison, evidence, line_coverage)
    if row.get("returncode") != 0 or comparison == "failed":
        return CocotbResult("failed", "tb-execution-failed", comparison, evidence, line_coverage)
    if comparison == "not_reported":
        return CocotbResult("failed", "tb-no-test-result", comparison, evidence, line_coverage)
    return CocotbResult("completed", "tb-completed", comparison, evidence, line_coverage)
