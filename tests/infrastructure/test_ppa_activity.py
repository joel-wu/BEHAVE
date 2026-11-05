"""Installation acceptance, not workload-power integration or a model rollout."""

import os
from pathlib import Path

import pytest

from behave.isolation import IsolationStatus, binary, read_only
from behave.ppa import power
from behave.ppa.config import PowerPolicy
from behave.sim.api import (
    ToolCommand,
    ToolGroup,
    command_stream,
    prepare_tool_environment,
    tool_limits,
)


@pytest.mark.eda
@pytest.mark.isolation
def test_installed_sta_imports_vcd_and_distinguishes_idle_from_switching(tmp_path, pytestconfig):
    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-activity"))
    if not (root / "receipt.json").is_file():
        pytest.skip("explicit activity-capable OpenSTA installation required")
    sta = root / "opensta/usr/bin/sta"
    environment = prepare_tool_environment("eda", binaries=(binary(str(sta), "/tools/sta"),),
        mounts=(read_only("/usr/share/tcltk", "/usr/share/tcltk", allow_symlinks=True),))
    lib = read_only(str(root / "sky130.lib"), "/pdk/cells.lib")
    (tmp_path / "mapped.v").write_text("""module M(input clk, input a, output y);
sky130_fd_sc_hd__inv_1 inv (.A(a), .Y(y));
endmodule
""")
    header = """$timescale 1ns $end
$scope module tb $end
$scope module dut $end
$var wire 1 ! a $end
$var wire 1 \" y $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
$dumpvars
0!
1\"
$end
"""
    # Hand-built VCD is a reader fixture only. Production workload waveforms
    # must come from actual mapped simulation, never from this test generator.
    for name, events in (("idle", "#2000\n"), ("active", "".join(
            f'#{i * 20}\n{i % 2}!\n{1 - i % 2}"\n' for i in range(1, 101)))):
        (tmp_path / f"{name}.vcd").write_text(header + events)
        (tmp_path / f"{name}.tcl").write_text(f"""read_liberty /pdk/cells.lib
read_verilog mapped.v
link_design M
create_clock -period 20 [get_ports clk]
set_input_transition 0.1 [get_ports a]
set_load 0.01 [get_ports y]
read_vcd -scope tb/dut {name}.vcd
report_activity_annotation -report_annotated
puts "BEHAVE_POWER_BEGIN"
report_power -digits 9
puts "BEHAVE_POWER_END"
exit
""")
    result = environment.execute(tmp_path, tuple(ToolGroup((ToolCommand(name,
        ("/tools/sta", "-no_init", "-exit", f"{name}.tcl"), timeout_s=30),)) for name in ("idle", "active")),
        outputs=(), role="ppa-activity-install-acceptance",
        read_only_mounts=(lib,), limits=tool_limits(wall_seconds=90))
    assert result.status is IsolationStatus.COMPLETED, result
    reports, logs = {}, {}
    for name in ("idle", "active"):
        logs[name] = "\n".join(command_stream(result, name, stream).decode() for stream in ("stdout", "stderr"))
        reports[name] = power.report(logs[name], PowerPolicy(), {})
        assert reports[name]["status"] == "estimated", logs[name]
        # The fixture records top-level ports. Cell pin activities are propagated,
        # not explicitly VCD-annotated; do not confuse the two coverage claims.
        assert "vcd a" in logs[name] and "vcd y" in logs[name], logs[name]
    idle, active = reports["idle"], reports["active"]
    assert idle["internal_w"] + idle["switching_w"] == 0
    assert active["internal_w"] + active["switching_w"] > 0
    # Leakage can depend on state occupancy, which differs between these VCDs.
    assert idle["leakage_w"] >= 0 and active["leakage_w"] >= 0
    report = pytestconfig.getoption("xmlpath")
    if report:
        from behave.core.records import canonical_json

        Path(report).parent.mkdir(parents=True, exist_ok=True)
        # Store raw reports; do not relabel power.report's vectorless assumptions
        # as a production workload-power API, which this fixture does not test.
        (Path(report).parent / "activity-install.json").write_bytes(canonical_json({
            "fixture_only": True, "kind": "direct-vcd-reader-test", "logs": logs,
            "execution": result.as_dict()}))
