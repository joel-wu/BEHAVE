"""Attribute an observed-build rejection using the untouched source compiler.

This is a diagnostic check after a failed instrumented build, not another replay
or an alternate successful-score path. No generated observer or C++ harness is
input to the language checker. Its ordinary rejection status is source evidence;
signals, limits, missing outputs and tool failures are not candidate failures.
"""

import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from ..core.failure import FailureDisposition, RunFailure
from ..core.refusal import Refusal

if TYPE_CHECKING:
    from .tool_protocol import ToolExecution

VERSION = "rtl-original-build-attribution/1"


def source_compile_failure(source: str, top: str, *, timeout_s: int) -> tuple[RunFailure | None, "ToolExecution"]:
    """Return (failure or None, exact tool execution), without running RTL."""
    from .frontend_error import tool_failure
    from .tool_isolation import default_limits, run_workspace
    from .tool_protocol import ToolCommand, ToolGroup, ToolIsolationError, command_result, require_completed

    if (type(source) is not str or type(top) is not str
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top) is None
            or type(timeout_s) is not int or timeout_s < 1):
        raise ValueError("source compilation needs exact source, top and a positive budget")
    with tempfile.TemporaryDirectory(prefix="behave-source-check-", dir=scratch_root()) as temporary:
        root = Path(temporary)
        (root / "rtl.sv").write_text(source, encoding="utf-8")
        try:
            execution = require_completed(run_workspace(
                root, (ToolGroup((ToolCommand("verilator-source-check", (
                    "/tools/verilator", "--cc", "--Mdir", "build", "--prefix", "Vtop",
                    "--top-module", top, "--timescale", "1ns/1ps", "--vpi", "--public-flat-rw",
                    "-Wno-WIDTH", "-Wno-fatal", "rtl.sv"), timeout_s=timeout_s),)),),
                outputs=("build/Vtop.mk",), role="rtl-source-check",
                runtime_profile="elaboration",
                limits=default_limits(wall_seconds=timeout_s + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS)))
        except ToolIsolationError as error:
            return tool_failure(error), error.execution
    row = command_result(execution, "verilator-source-check")
    detail = {"version": VERSION, "tool_execution": execution.as_dict()}
    if row.get("timed_out") or row.get("output_limited"):
        failure = RunFailure.resource("rtl-build-attribution", Refusal(
            "rtl-source-check-resource-limit", "original-source compilation exceeded its diagnostic budget",
            **detail), policy=VERSION, disposition=FailureDisposition.NO_RESULT)
    elif row.get("returncode") == 1:
        failure = RunFailure.candidate("candidate-build", Refusal(
            "rtl-source-build-rejected", "Verilator rejected the untouched source without an observer",
            **detail))
    elif row.get("returncode") != 0:
        failure = RunFailure.infrastructure("rtl-build-attribution", Refusal(
            "rtl-source-check-process-failed", "the original-source compiler did not complete normally",
            **detail))
    elif "build/Vtop.mk" not in execution.outputs:
        failure = RunFailure.verifier("rtl-build-attribution", Refusal(
            "rtl-source-check-output-missing", "the original-source compiler returned no build description",
            **detail))
    else:
        failure = None
    return failure, execution
