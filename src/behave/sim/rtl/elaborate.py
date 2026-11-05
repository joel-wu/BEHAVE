"""A module's interface as the elaborator resolves it.

The reason this exists is a sentence that has been in `interface.py` since it was
written: the authority should be the elaborated design. A regular expression over
a header cannot be one. It refuses a width that is not a literal, which is right
as far as it goes, and 91 of the designs in the population have one: `N-1`,
`N*DATA_W-1`, `NUM_TOKENS*TOP_K*$clog2(NUM_EXPERTS)-1`. Substituting the
parameters the module declares would read 85 of them, and was nearly written
here, and would have been a second SystemVerilog evaluator for a language whose
evaluator this repository already ships.

It would also have been quietly wrong in a way the reader it replaced was not.
Parameters have defaults and instantiations override them, so an evaluator over
the declared defaults answers about the module as declared and not as built. That
is the same shape of fault as reading `input logic [7:0] a, b` and calling `b` one
bit: a width read wrongly is not reported, it just moves the input space every
verdict is relative to.

So the widths come from `verilator`, which resolves parameters, `$clog2`,
packages, typedefs and generate blocks because that is its job. What is asked of
it is one thing: dump the elaborated syntax tree and read the ports out of it.

This is not the RTL frontend proving anything about a candidate. It is the
elaborator, and the SystemVerilog it is pointed at may be a candidate's or the
task's own declaration. Which it is decides what the answer means, and the caller
owns that: a schema read from a candidate is a claim about the candidate, and one
read from the task is the contract both artifacts owe.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from ...core.paths import eda_root
from ..core.schema import Authority, Endpoint, InterfaceError, Reading, TransactionSchema
from ..strict_json import strict_json_loads

__all__ = ["available", "schema_and_execution_of_module", "schema_of_module"]

#: How long the elaborator is given. A header is elaborated in tens of
#: milliseconds; a design that takes longer than this is not a header this can
#: read, and waiting for it would turn a reader into a build step.
TIMEOUT_S = defaults.SIM_RTL_ELABORATION_SECONDS


def _verilator():
    got = eda_root() / "bin" / "verilator"
    return got if got.exists() else None


def available():
    """Whether the elaborator can be asked at all.

    Callers that must work without the toolchain fall back to reading the header
    themselves, and say so: an artifact whose contract came from a regular
    expression carries that among its blockers, and the point of this module is
    that an artifact elaborated properly does not.
    """
    return _verilator() is not None


def schema_of_module(source, name="the design", source_note=None, top=None,
                     authority=None):
    """The ports of the module in ``source``, with the widths elaborated.

    ``source_note`` is what the returned schema will say about where it came
    from, which is the caller's business: this reads SystemVerilog and does not
    know whether it was handed a candidate or a task's declaration.

    ``authority`` is that same question asked so an answer can be *used*. The note is
    prose for a reader and nothing decides anything by reading it, which is B5: two
    facts were carried in one sentence and rewording the sentence moved a blocker. The
    reading is this function's own answer, since elaborating is what it does.

    ``top`` names which module, for a file that holds more than one top. A design
    built from helpers is ordinary -- one in the population is an adder written as
    `add1`, `add2`, `add4`, `add8` and then the sixteen-bit one -- and reading the
    ports of every module in the file gives the ports of whichever came first,
    which is a helper. Without a name the module nothing instantiates is used,
    which is what a top module is; with one, that module, since the caller knows
    what contract it is asking about and this does not.

    It is not a way into the hierarchy. The elaborator inlines what it can, so a
    helper it dissolved is not a module with ports any more and naming it gets
    nothing: what is elaborated is the design, and the pieces it was written from
    are gone by then.
    """
    schema, _execution = schema_and_execution_of_module(
        source, name, source_note, top, authority)
    return schema


def schema_and_execution_of_module(
    source,
    name="the design",
    source_note=None,
    top=None,
    authority=None,
):
    """Return the elaborated schema and its exact isolated-tool lineage."""

    from ..core.refusal import Refusal, Refused
    from .frontend_error import SourceRejected
    from .tool_isolation import default_limits, run_workspace
    from .tool_protocol import (
        OutputNormalizer,
        ToolCommand,
        ToolGroup,
        ToolIsolationError,
        command_result,
        command_stream,
        require_completed,
    )

    if top is not None:
        if type(top) is not str:
            raise TypeError("the requested top module must be an exact string")
        if not top.isidentifier():
            # These legal SV names are outside the current name transport. They
            # remain capability refusals, not evidence against a submission.
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*|\\[!-~]+\s*", top):
                raise Refused(Refusal(
                    "rtl-top-identifier-unsupported",
                    "the selected SystemVerilog top name is not supported by this frontend interface",
                    top=top))
            raise SourceRejected(Refusal(
                "rtl-entry-invalid", "the selected RTL entry is not a module identifier", top=top))
    if _verilator() is None:
        raise FileNotFoundError("no elaborator: verilator was not found under "
                                f"{eda_root()}")
    with tempfile.TemporaryDirectory(prefix="behave-elaborate-", dir=scratch_root()) as where:
        workspace = Path(where)
        (workspace / "design.sv").write_text(source)
        argv = ["/tools/verilator", "--json-only", "-Wno-fatal",
                "--Mdir", "obj", "--json-only-output", "tree.json"]
        if top is not None:
            argv.extend(("--top-module", top))
        argv.append("design.sv")
        execution = require_completed(run_workspace(
            workspace,
            (ToolGroup((ToolCommand(
                "verilator-elaborate", tuple(argv),
                timeout_s=int(TIMEOUT_S)),)),),
            outputs=("tree.json",),
            output_normalizers=(OutputNormalizer(
                "tree.json", "verilator-ports/1", (("top", top),)),),
            role="verilator-elaborate",
            runtime_profile="elaboration",
            limits=default_limits(wall_seconds=int(TIMEOUT_S) + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS),
        ))
        row = command_result(execution, "verilator-elaborate")
        if row.get("timed_out") or row.get("output_limited"):
            raise ToolIsolationError(
                "rtl-frontend-resource-limit",
                "the elaborator exceeded its declared time or output limit", execution)
        diagnostic = (
            command_stream(execution, "verilator-elaborate", "stderr")
            or command_stream(execution, "verilator-elaborate", "stdout")
        ).decode("utf-8", errors="replace")
        # The wrapper reserves 126 for launch failures. Only the language checker's
        # ordinary rejection status is source evidence; a signal or unexpected exit
        # is a tool failure. Diagnostic text is evidence, never a classifier.
        if row.get("returncode") == 1:
            raise SourceRejected(Refusal(
                "rtl-source-rejected",
                "the declared Verilator frontend rejected the source",
                diagnostic=diagnostic, execution=execution.as_dict()))
        if row.get("returncode") != 0:
            raise ToolIsolationError(
                "rtl-frontend-process-failed",
                "the elaborator did not complete its source check", execution)
        try:
            payload = execution.outputs.get("tree.json")
            if payload is None:
                raise ValueError("no normalized port record")
            ports = strict_json_loads(
                payload, what="the normalized Verilator port record")
        except (OSError, ValueError, RecursionError):
            raise ToolIsolationError(
                "rtl-frontend-output-invalid",
                "the completed elaborator produced no closed port record",
                execution) from None
    return _schema_of_ports(ports, name, source_note, authority), execution


def _schema_of_ports(document, name, source_note, authority=None):
    """Validate the small port record normalized inside the RTL judge."""

    if type(document) is not dict or set(document) != {"version", "top", "ports"} \
            or document["version"] != "verilator-ports/1" \
            or type(document["top"]) is not str \
            or type(document["ports"]) is not list:
        raise InterfaceError("the isolated elaborator returned an invalid port record")
    endpoints = []
    seen = set()
    for row in document["ports"]:
        if type(row) is not dict or set(row) != {
                "name", "direction", "width", "signed"}:
            raise InterfaceError("one elaborated port record has an invalid shape")
        port_name = row["name"]
        direction = row["direction"]
        width = row["width"]
        signed = row["signed"]
        if type(port_name) is not str or not port_name or port_name in seen \
                or direction not in {"input", "output", "inout"} \
                or type(width) is not int or isinstance(width, bool) or width < 1 \
                or type(signed) is not bool:
            raise InterfaceError("one elaborated port record has invalid fields")
        seen.add(port_name)
        endpoints.append(Endpoint(port_name, direction, width, signed))
    # An empty selected module is a readable interface. Admission compares it
    # with the task; missing task ports are not corrupt compiler output.
    return TransactionSchema(
        endpoints, source=source_note or f"elaborated from {name}",
        authority=authority or Authority.UNKNOWN,
        reading=Reading.ELABORATED,
        top=document["top"])
