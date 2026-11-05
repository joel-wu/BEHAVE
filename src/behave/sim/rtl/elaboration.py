"""One pre-process compiler universe for catalogue, observation and relations.

Flatten before collecting occurrences or selecting their wires. Parameterized
modules are instantiated here; later synthesis may simplify the relation but
cannot redefine which source occurrences the frontend declared.
"""

import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from ..core.refusal import Refusal, Refused

SOURCE_FILENAME = "design.sv"
SOURCE_IMAGE_VERSION = "rtl-exact-source-image/2"

# The admitted source remains unchanged. Yosys' always_comb attribute asserts
# latch-free synthesis before dead internal state can be eliminated (for example
# a conditionally executed loop counter). Remove only that synthesis assertion:
# proc still infers real latches, and requested observations/keep wires and the
# existing state-capability checks prevent live storage from disappearing. This
# is deliberately not read_verilog -nolatches, which replaces hold values by X.
STORAGE_INFERENCE_SCRIPT = "setattr -unset always_comb p:*\n"


def source_documents(artifact, *, expected_identity, timeout_s=None):
    """Compile frozen source for any consumer under its caller-owned allowance."""
    from .tool_isolation import default_limits, run_workspace
    from .tool_protocol import ToolCommand, ToolGroup, command_result, require_completed

    source = Path(artifact.rtl_path).read_text()
    if hashlib.sha256((str(artifact.top_module) + "\0" + source).encode()).hexdigest() != expected_identity:
        raise Refused(Refusal("rtl-source-query-artifact-changed",
            "query preparation needs the same source bytes as the completed native baseline"))
    allowance = artifact.timeout_s if timeout_s is None else timeout_s
    if type(allowance) not in (int, float) or not math.isfinite(allowance) or allowance <= 0:
        raise ValueError("source compilation needs a positive finite time allowance")
    filenames = tuple(f"frontend.{kind}.json" for kind in ("cst", "ast", "sensitivity"))
    timeout = max(1, math.ceil(allowance))
    with tempfile.TemporaryDirectory(prefix="behave-source-query-", dir=scratch_root()) as temporary:
        root = Path(temporary)
        (root / SOURCE_FILENAME).write_text(source)
        execution = run_workspace(
            root, (ToolGroup((ToolCommand("source-query-documents",
                ("/runtime/python/bin/python", "-S", "-m", "behave.sim.rtl.shared_frontend",
                 artifact.top_module, "--observe-source"), timeout_s=timeout),)),),
            outputs=filenames, role="rtl-source-query", runtime_profile="source-observation",
            limits=default_limits(wall_seconds=timeout + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS))
    artifact._record_tool_execution(execution)
    require_completed(execution)
    row = command_result(execution, "source-query-documents")
    if row["returncode"] != 0 or row["timed_out"]:
        raise Refused(Refusal("rtl-source-query-frontend-unavailable",
            "the shared source compiler did not complete query preparation",
            tool_execution=execution.as_dict()))
    return {"source": source, **{kind: json.loads(execution.outputs[filename])
            for kind, filename in zip(("cst", "ast", "sensitivity"), filenames)}}


def compiler_command(top: str, script: str) -> tuple[str, ...]:
    """Shared, judge-owned frontend, never a direct per-consumer Yosys parser."""
    elaboration_script(top)  # Same top validation as the trusted script builder.
    return ("/runtime/python/bin/python", "-S", "-m", "behave.sim.rtl.shared_frontend", top, script)


def elaboration_script(top: str, *, scoped: bool = False) -> str:
    """Trusted script prefix; scope attributes are an optional side witness."""
    if type(top) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top) is None:
        raise ValueError("the RTL top module is not a SystemVerilog identifier")
    if type(scoped) is not bool:
        raise TypeError("the compiler context option must be Boolean")
    return (f"read_verilog -sv {SOURCE_FILENAME}\n"
            f"hierarchy -top {top}\n"
            + ("flatten -scopename\n" if scoped else "flatten\n"))
