"""Fixed-activity OpenSTA estimates, separate from timing and score authority.

OpenSTA reports internal/switching/leakage power in watts. Its Liberty models
and probabilistic propagation own the calculation; we never estimate power
from cell area or multiply a vectorless average by workload completion time.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict

from .config import PowerPolicy, identifier

METHOD = "liberty-vectorless-input-activity/1"
COMPONENTS = ("internal_w", "switching_w", "leakage_w", "total_w")


def assumptions(policy: PowerPolicy, constant_inputs: dict[str, int]) -> dict:
    for name, value in constant_inputs.items():
        identifier(name)
        if type(value) is not int or value not in (0, 1):
            raise ValueError("power constant input must be an exact bit")
    return {"method": METHOD, **asdict(policy), "activity_unit": "transitions-per-clock-period",
            "constant_inputs": dict(sorted(constant_inputs.items())),
            "clock_activity": "declared-clock", "interconnect": "ideal",
            "workload_activity": False}


def commands(policy: PowerPolicy, constant_inputs: dict[str, int]) -> str:
    """Append to the same fixed library/netlist/clock/load setup as timing."""
    assumptions(policy, constant_inputs)
    lines = [f"set_power_activity -input -activity {policy.input_activity} -duty {policy.input_duty}"]
    for name, value in sorted(constant_inputs.items()):
        # Setting activity is NOT set_case_analysis: it must not change the
        # synthesized circuit or hide reset paths from the timing checks.
        lines.append(f"set_power_activity -input_ports [get_ports {name}] -activity 0 -duty {value}")
    lines += ['puts "BEHAVE_POWER_BEGIN"', "report_power -digits 9", 'puts "BEHAVE_POWER_END"', "exit"]
    return "\n".join(lines) + "\n"


def report(output: str, policy: PowerPolicy, constant_inputs: dict[str, int], *,
           completed: bool = True) -> dict:
    result = {"status": "unavailable", "reason": "power-report-invalid", "units": "W",
              "assumptions": assumptions(policy, constant_inputs), **dict.fromkeys(COMPONENTS)}
    if not completed:
        return {**result, "reason": "power-tool-failed"}
    # OpenSTA can exit zero after a Tcl error. Missing/partial/duplicate reports,
    # warnings, NaNs and negative values must never masquerade as zero power.
    if re.search(r"(?im)^\s*(?:(?:error|warning)(?:\s+\d+)?:|\[(?:ERROR|WARNING) STA-)", output):
        return result
    parts = re.findall(r"(?ms)^BEHAVE_POWER_BEGIN\s*\n(.*?)^BEHAVE_POWER_END\s*$", output)
    if len(parts) != 1:
        return result
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    rows = re.findall(r"(?m)^Total\s+(" + number + r")\s+(" + number + r")\s+("
                      + number + r")\s+(" + number + r")\s+" + number + r"%\s*$", parts[0])
    if len(rows) != 1:
        return result
    values = tuple(float(value) for value in rows[0])
    if not all(math.isfinite(value) and value >= 0 for value in values):
        return result
    if not math.isclose(sum(values[:3]), values[3], rel_tol=1e-7, abs_tol=1e-30):
        return result
    return {**result, "status": "estimated", "reason": None, **dict(zip(COMPONENTS, values, strict=True))}
