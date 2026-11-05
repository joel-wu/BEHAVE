"""Fixed Yosys/Liberty mapping and OpenSTA; all EDA runs use existing isolation.

No model-authored Tcl/Yosys scripts or constraints enter the measurement flow.
The mapped design is flattened and checked for unmapped cells/memories before
area is accepted. Reports describe ideal-interconnect synthesis estimates.
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import asdict, replace
from pathlib import Path

from behave.core.artifacts import BlobRef, FileRef, SourceArtifact
from behave.core.paths import eda_root
from behave.core.records import parse_json
from behave.isolation import IsolationStatus, binary, executable_script, read_only
from behave.sim.api import (
    ToolCommand,
    ToolGroup,
    command_stream,
    prepare_tool_environment,
    tool_limits,
)

from . import power
from ._netlist_view import scope_metadata
from .config import EDABudget, MappingPolicy, PowerPolicy, TimingPolicy, identifier
from .records import write_document

FLOW_VERSION = "behave.ppa-yosys-tcl-sta/20"


def library_description(data: bytes):
    """Describe trusted technology bytes and reject implicit unit conversion.

    This reads metadata, not cells/timing: Yosys and STA remain the Liberty
    parsers. Native time must be ns; native capacitance may be pF or fF.
    """
    source = data.decode()
    name = re.search(r'^\s*library\s*\(\s*"?([\w.]+)"?\s*\)', source, re.MULTILINE)
    units = re.findall(r'^\s*time_unit\s*:\s*"([^"]+)"\s*;', source, re.MULTILINE)
    load = re.findall(r'^\s*capacitive_load_unit\s*\(\s*([\d.]+)\s*,\s*"?([a-zA-Z]+)"?\s*\)', source, re.MULTILINE)
    if name is None or units != ["1ns"] or len(load) != 1 or load[0][1] not in ("pf", "ff") or float(load[0][0]) != 1:
        raise ValueError("PPA requires one named Liberty in native 1ns and 1pF/1fF units")
    return {"library": name.group(1), "liberty_sha256": BlobRef.of(data).sha256,
            "time_unit": "ns", "capacitance_unit": {"pf": "pF", "ff": "fF"}[load[0][1]]}


def mapping_constraints(policy: TimingPolicy, mapping: MappingPolicy):
    # Yosys ABC uses fF, while the public timing/Liberty profile uses pF.
    return (f"set_driving_cell {mapping.driving_cell}\n"
            f"set_load {policy.output_load_pf * 1000:.12g}\n")


def validate_mapping_library(data: bytes, mapping: MappingPolicy):
    # ABC may only warn about an unknown driver and continue unconstrained.
    # Require the named cell in the trusted Liberty before invoking it.
    names = re.findall(r'^\s*cell\s*\(\s*"?([\w.]+)"?\s*\)\s*\{', data.decode(), re.MULTILINE)
    if names.count(mapping.driving_cell) != 1:
        raise ValueError(f"mapping driver is not a unique Liberty cell: {mapping.driving_cell}")
    if unknown := set(mapping.dont_use_cells) - set(names):
        raise ValueError(f"mapping exclusions absent from Liberty: {sorted(unknown)}")


def yosys_script(source, policy: TimingPolicy, liberty="/pdk/cells.lib", *, sat_resource_sharing=True,
                 checkpoint=False, repair_design=False, dont_use_cells=(), technology_name='sky130hd'):
    from behave.sim.api import bundle_for_entry
    from .mapping_repair import yosys_commands

    identifier(source.artifact.entry_symbol)
    exclusions = ''.join(' -dont_use ' + identifier(cell) for cell in dont_use_cells)
    bundle = bundle_for_entry({f.reference.path: f.data for f in source.files}, source.artifact.entry_file)
    # Invoke Yosys through Tcl's argument-vector bridge. The .ys parser keeps
    # quotes inside -I arguments instead of treating them as path delimiters.
    # Tcl quoting also keeps spaces, semicolons and substitution syntax inert.
    def quote(s):
        if any(c in s for c in '\r\n\x00'):
            raise ValueError("unsupported source path")
        for char in ('\\', '"', '$', '[', ']'):
            s = s.replace(char, '\\' + char)
        return '"' + s + '"'

    return "\n".join(command[1:] if command.startswith("@") else "yosys " + command for command in [
        f"read_liberty -lib {liberty}", "write_json library.json",
        # Use the source simulation's SYNTHESIS-undefined branch as well.
        "read_verilog -sv -nosynthesis " + " ".join("-I " + quote("sources/" + path) for path in bundle.include_dirs)
        + " " + " ".join(quote("sources/" + name) for name in bundle.compilation_units),
        f"hierarchy -check -top {source.artifact.entry_symbol}",
        f"synth -top {source.artifact.entry_symbol} -flatten -noabc" + ("" if sat_resource_sharing else " -noshare"),
        f"dfflibmap -liberty {liberty}" + exclusions,
        *(["write_rtlil pre_abc.il"] if checkpoint else []),
        # -constr enables ABC's default buffer/upsize/dnsize recipe. -D is ps.
        # FFs stay in Yosys (no -dff), preserving the cycle boundaries.
        f"abc -exe /tools/abc-trace -liberty {liberty} -constr abc.constr -D {policy.period_ns * 1000:.12g}" + exclusions,
        "clean", "check -assert",
        *(yosys_commands(source.artifact.entry_symbol, liberty, technology_name=technology_name)
          if repair_design else []),
        f"tee -o stats.json stat -json -liberty {liberty}",
        # Preserve the mapped authority, then serialize a connectivity-identical
        # type view: OpenSTA accepts neither signed declarations nor concat LHS.
        # No source RTL or cell expression is textually rewritten.
        "write_json mapped.json", "design -reset",
        "read_json [exec /tools/python -I -B netlist_view.py]",
        "write_verilog -noattr -noexpr -simple-lhs mapped.v",
        # Build an independent logical simulation view from this exact Liberty.
        # Ignored nonfunctional physical cells cannot silently pass hierarchy or
        # replay if they are actually used. Source SYNTHESIS branches are not reused.
        "design -reset", f"read_liberty -ignore_miss_func -ignore_miss_dir {liberty}",
        "read_verilog mapped.v", f"hierarchy -check -top {source.artifact.entry_symbol}",
        # Flatten the library's logical definitions and remove dead aliases before
        # simulation. Retaining every cell wrapper bloats public VPI/compiler state.
        # This is after the mapped area/STA exports; it cannot change those metrics.
        # Imported cell pins create many public aliases. Hide only internal
        # names so clean can remove them; rename -hide preserves every port.
        "flatten", "rename -hide", "clean -purge", "check -assert",
        "write_verilog -noattr simulation.v",
    ]) + "\n"


def sta_setup(top: str, policy: TimingPolicy, *, placement_repair=False, technology_name='sky130hd'):
    identifier(top)
    from .technology import technology
    profile = technology(technology_name)
    load = ('read_db physical.odb\n' if placement_repair else
            f'read_verilog mapped.v\nlink_design {top}\n')
    return 'read_liberty /pdk/cells.lib\n' + load + f'''create_clock -name clk -period {policy.period_ns} [get_ports {policy.clock}]
set_clock_uncertainty {policy.uncertainty_ns} [get_clocks clk]
set inputs [lsearch -all -inline -not -exact [all_inputs] [lindex [get_ports {policy.clock}] 0]]
set_input_delay {policy.input_delay_ns} -clock clk $inputs
set_input_transition {policy.input_transition_ns} $inputs
set_output_delay {policy.output_delay_ns} -clock clk [all_outputs]
set_load {profile.load_from_pf(policy.output_load_pf)} [all_outputs]
''' + (profile.rc + 'estimate_parasitics -placement\n' if placement_repair else '')


def sta_script(top: str, policy: TimingPolicy, *, placement_repair=False, technology_name='sky130hd'):
    from .sta_checks import SETUP_CHECKS
    return sta_setup(top, policy, placement_repair=placement_repair, technology_name=technology_name) + SETUP_CHECKS + '''
puts "BEHAVE_PPA_SETUP_NS [sta::format_time [sta::worst_slack_cmd max] 9]"
puts "BEHAVE_PPA_HOLD_NS [sta::format_time [sta::worst_slack_cmd min] 9]"
report_checks -path_delay min_max -format full_clock_expanded -fields {capacitance slew fanout} -digits 6 -no_line_splits
exit
'''


def mapped_area(data: bytes, stats: bytes, top: str, library: bytes) -> float:
    design = parse_json(data)
    modules = design["modules"]
    dut = modules[top]
    trusted = parse_json(library)["modules"]
    if dut.get("memories"):
        raise ValueError("unmapped memory")
    # Cell library modules are imported as blackboxes; every used leaf must have
    # a positive Liberty area. User blackboxes must not acquire free area.
    area = 0.
    for cell in dut.get("cells", {}).values():
        if scope_metadata(cell):
            continue
        kind = cell["type"]
        if kind not in trusted or modules.get(kind) != trusted[kind]:
            raise ValueError("unmapped or non-library cell")
        value = float(trusted[kind].get("attributes", {}).get("area", "nan"))
        if not math.isfinite(value) or value <= 0:
            raise ValueError("cell has no finite positive area")
        area += value
    report = parse_json(stats)
    # Yosys omits area when the design consists entirely of wires/constants.
    # This is a legitimate zero, but never a free unmapped cell (checked above).
    reported = report["modules"]["\\" + top].get("area", 0.)
    if type(reported) not in (int, float) or not math.isclose(area, reported, rel_tol=1e-5, abs_tol=.001):
        raise ValueError("mapped cell area and Yosys report disagree")
    return area


def timing_result(output: str):
    # OpenSTA can exit zero after Tcl errors: do not use exit code as closure.
    if re.search(r"(?im)^\s*(?:(?:error|warning)(?:\s+\d+)?:|\[(?:ERROR|WARNING) STA-)|unconstrained|no clock|combinational loop", output):
        return {"status": "unresolved", "setup_slack_ns": None, "hold_slack_ns": None}
    pattern = r"(?m)^BEHAVE_PPA_{}_NS\s+(-?\d+(?:\.\d+)?)$"
    slacks = [re.findall(pattern.format(kind), output) for kind in ("SETUP", "HOLD")]
    if any(len(values) != 1 for values in slacks):
        return {"status": "unresolved", "setup_slack_ns": None, "hold_slack_ns": None}
    setup, hold = (float(values[0]) for values in slacks)
    if not all(math.isfinite(value) for value in (setup, hold)):
        return {"status": "unresolved", "setup_slack_ns": None, "hold_slack_ns": None}
    return {"status": "met" if min(setup, hold) >= 0 else "violated",
            "setup_slack_ns": setup, "hold_slack_ns": hold}


def measure(source, policy: TimingPolicy, *, liberty: Path, sta: Path, directory: Path, objects,
            power_policy: PowerPolicy, constant_inputs: dict[str, int], budget: EDABudget,
            sat_resource_sharing: bool = True, mapping: MappingPolicy | None = None,
            checkpoint: bool = False):
    deadline = time.monotonic() + budget.service_seconds
    mapping = MappingPolicy() if mapping is None else mapping
    from .technology import technology
    profile = technology(mapping.technology)
    profile.validate_library(library_description(liberty.read_bytes()))
    validate_mapping_library(liberty.read_bytes(), mapping)
    from . import mapping_repair
    repair = mapping_repair.runtime(technology_name=mapping.technology) if mapping.repair_design else None
    environment = prepare_tool_environment("eda", binaries=(
        binary(str(sta.resolve()), "/tools/sta"),
        binary(str(eda_root().resolve() / "libexec/yosys-abc"), "/tools/abc"),
        executable_script(str(Path(__file__).with_name("_abc_trace.py")), "/tools/abc-trace"),
        *((repair.executable,) if repair else ())),
        mounts=(read_only("/usr/share/tcltk", "/usr/share/tcltk", allow_symlinks=True),
                *(repair.mounts if repair else ())))
    lib = read_only(str(liberty.resolve()), "/pdk/cells.lib")
    directory.mkdir()
    for file in source.files:
        path = directory / "sources" / file.reference.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.data)
    scripts = {"synth.tcl": yosys_script(source, policy, sat_resource_sharing=sat_resource_sharing,
                                        checkpoint=checkpoint, repair_design=mapping.repair_design,
                                        dont_use_cells=mapping.dont_use_cells, technology_name=mapping.technology),
               "abc.constr": mapping_constraints(policy, mapping),
               "netlist_view.py": Path(__file__).with_name("_netlist_view.py").read_text(),
               "timing.tcl": sta_script(source.artifact.entry_symbol, policy, placement_repair=mapping.placement_repair,
                                        technology_name=mapping.technology),
               "power.tcl": sta_setup(source.artifact.entry_symbol, policy, placement_repair=mapping.placement_repair,
                                       technology_name=mapping.technology)
                            + power.commands(power_policy, constant_inputs)}
    if repair:
        scripts["repair.tcl"] = mapping_repair.script(source.artifact.entry_symbol, policy,
            dont_use_cells=mapping.dont_use_cells, placement_repair=mapping.placement_repair,
            threads=len(os.sched_getaffinity(0)), technology_name=mapping.technology)
    for name, script in scripts.items():
        (directory / name).write_text(script)
    executions, stages, output_refs = {}, {}, {}

    def run_stage(name, argv, seconds, outputs=()):
        remaining = math.floor(deadline - time.monotonic())
        if remaining < 1:
            stages[name] = {"status": "timeout", "reason": "eda-deadline", "budget_seconds": seconds}
            return False
        began = time.monotonic()
        command_environment = (("TMPDIR", "/work"),)
        if name == "synthesis":
            # Yosys hardware_concurrency can see the whole node, not our CPU lease.
            command_environment += (("YOSYS_MAX_THREADS", str(len(os.sched_getaffinity(0)))),)
        execution = environment.execute(directory, (ToolGroup((ToolCommand(name, argv,
            timeout_s=min(seconds, remaining), environment=command_environment),)),),
            outputs=outputs, role="ppa-" + name, read_only_mounts=(lib,),
            limits=replace(tool_limits(wall_seconds=min(seconds + budget.overhead_seconds, remaining)),
                           rss_bytes=budget.rss_bytes))
        executions[name] = execution
        output_refs.update((key, objects.put(data)) for key, data in execution.outputs.items())
        status = stage_status(execution, name)
        stages[name] = {**status, "budget_seconds": seconds,
                        "elapsed_seconds": time.monotonic() - began}
        return status["status"] == "completed"

    outputs = ("mapped.json", "sta.json", "mapped.v", "stats.json", "library.json", "simulation.v")
    repair_outputs = ("pre_repair.v", "repaired.v", "repair.log") if repair else ()
    if mapping.placement_repair:
        repair_outputs += ('physical.odb', 'placed.def')
    synthesized = run_stage("synthesis", ("/tools/yosys", "-c", "synth.tcl"),
                            budget.synthesis_seconds, outputs + ("abc.log",) + repair_outputs +
                            (("pre_abc.il",) if checkpoint else ()))
    if synthesized and not set(outputs) <= output_refs.keys():
        stages["synthesis"].update(status="failed", reason="missing-mapped-output")
        synthesized = False
    if repair and synthesized and (not set(repair_outputs) <= output_refs.keys() or
            b"BEHAVE_MAPPING_REPAIR_COMPLETE" not in executions["synthesis"].outputs.get("repair.log", b"")):
        stages["synthesis"].update(status="failed", reason="mapping-repair-not-completed")
        synthesized = False
    if synthesized and mapping.dont_use_cells:
        mapped = parse_json(executions["synthesis"].outputs["mapped.json"])
        used = {cell['type'] for cell in mapped['modules'][source.artifact.entry_symbol]['cells'].values()}
        if forbidden := used & set(mapping.dont_use_cells):
            stages['synthesis'].update(status='failed', reason='excluded-mapped-cells', cells=sorted(forbidden))
            synthesized = False
    if synthesized:
        # Timing and power depend on synthesis, not on each other.
        for name in ("timing", "power"):
            run_stage(name, ("/tools/openroad" if mapping.placement_repair else "/tools/sta",
                            "-no_init", "-exit", name + ".tcl"),
                      getattr(budget, name + "_seconds"))
    else:
        for name in ("timing", "power"):
            stages[name] = {"status": "not-run", "reason": "synthesis-not-completed"}

    def logs(name):
        execution = executions.get(name)
        if execution is None:
            return ""
        return "\n".join(command_stream(execution, name, stream).decode(errors="replace")
                         for stream in ("stdout", "stderr")
                         if any(row["id"] == name for row in execution.commands))

    abc_log = (executions["synthesis"].outputs.get("abc.log", b"").decode(errors="replace")
               if "synthesis" in executions else "")
    operations = re.findall(r"(?m)^\[[\d.]+s\] \+ (.+)$", abc_log)
    if operations and stages["synthesis"]["status"] != "completed":
        stages["synthesis"]["last_operation"] = operations[-1].strip()
    repair_log = (executions["synthesis"].outputs.get("repair.log", b"").decode(errors="replace")
                  if "synthesis" in executions else "")
    log = "\n".join([*(logs(name) for name in stages), abc_log, repair_log])
    evidence, evidence_closure = write_document(objects, {"executions": {name: e.as_dict() for name, e in executions.items()},
                                           "budget": asdict(budget), "stages": stages, "scripts": scripts,
                                           "outputs": {name: ref.as_dict() for name, ref in output_refs.items()},
                                           "runtime": environment.identity, "liberty": lib.identity()})
    library_hash = BlobRef.of(liberty.read_bytes()).sha256
    result = {"stage": mapping.stage, "area_um2": None,
              "status": "failed", "timing": None, "log": log, "evidence": evidence.as_dict(),
              "stages": stages,
              "power": {**power.report("", power_policy, constant_inputs, completed=False),
                        "conditions": {"timing": asdict(policy), "liberty_sha256": library_hash}},
              "closure": [ref.as_dict() for ref in (*output_refs.values(), *evidence_closure)],
              "liberty_sha256": library_hash,
              "report_units": {"time": "ns", "capacitance": profile.capacitance_unit}}
    if repair:
        result["mapping_repair"] = {"runtime": repair.identity, "max_fanout": 32, "buffer_gain": 4,
                                    "completed": synthesized}
        if mapping.placement_repair:
            result['mapping_repair']['physical_profile'] = profile.profile
            result['mapping_repair']['hold_repair'] = 'placement-estimated-ideal-clock'
            result['power']['assumptions']['interconnect'] = 'placement-estimated'
    failed = next((name for name in ("synthesis", "timing") if stages[name]["status"] != "completed"), None)
    if failed:
        result.update(status=stages[failed]["status"], failed_stage=failed)
        return result
    try:
        result["area_um2"] = mapped_area((directory / "mapped.json").read_bytes(),
                                          (directory / "stats.json").read_bytes(), source.artifact.entry_symbol,
                                          (directory / "library.json").read_bytes())
    except (KeyError, ValueError, OSError):
        result["status"] = "unmapped-design"
        return result
    sta_log = logs("timing")
    result.update(status="measured", timing=timing_result(sta_log))
    result["power"].update(power.report(logs("power"), power_policy, constant_inputs,
        completed=stages["power"]["status"] == "completed"))
    if mapping.placement_repair:
        result['power']['assumptions']['interconnect'] = 'placement-estimated'
    if "simulation.v" in output_refs:
        result["simulation_source"] = SourceArtifact((FileRef("mapped.v", output_refs["simulation.v"]),),
                                                      "mapped.v", source.artifact.entry_symbol).as_dict()
    return result


def stage_status(execution, name):
    """A completed process supervisor does not imply a successful EDA command."""
    command = next((row for row in execution.commands if row["id"] == name), None)
    if command is not None:
        if command["timed_out"]:
            return {"status": "timeout", "reason": "tool-deadline"}
        if command.get("output_limited"):
            return {"status": "output-limit", "reason": "tool-output-limit"}
        if command["returncode"] != 0:
            return {"status": "failed", "reason": "tool-exit", "returncode": command["returncode"]}
    if execution.status is not IsolationStatus.COMPLETED:
        return {"status": "timeout" if execution.status is IsolationStatus.TIMED_OUT else "no-result",
                "reason": execution.status.value}
    if command is None:
        return {"status": "no-result", "reason": "tool-supervision"}
    return {"status": "completed", "reason": None}
