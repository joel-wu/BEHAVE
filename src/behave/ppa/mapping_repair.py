"""Shared post-mapping buffering/sizing and placement-estimated hold repair."""

from dataclasses import dataclass

from behave.core.artifacts import BlobRef
from behave.core.paths import RepositoryUnavailable, repo_root
from behave.core.records import canonical_json
from behave.isolation import Binary, ReadOnlyMount, binary, native_runtime

# Optimize short data paths under the SAME ideal-clock/placement-RC model used
# for acceptance. This is not post-CTS signoff. Do not allow setup violations to
# be introduced merely to fix hold. Legalization can change parasitics, so export
# only after re-estimation; the independent final STA remains authoritative.
PLACED_HOLD_REPAIR = '''puts "BEHAVE_HOLD_REPAIR_BEFORE_NS [sta::format_time [sta::worst_slack_cmd min] 9]"
repair_timing -hold
detailed_placement
check_placement -verbose
estimate_parasitics -placement
puts "BEHAVE_HOLD_REPAIR_AFTER_NS [sta::format_time [sta::worst_slack_cmd min] 9]"
write_db physical.odb
write_def placed.def
'''


@dataclass(frozen=True)
class RepairRuntime:
    executable: Binary
    mounts: tuple[ReadOnlyMount, ...]

    @property
    def identity(self):
        return BlobRef.of(canonical_json({
            "binary": self.executable.identity(),
            "mounts": [m.identity() for m in self.mounts],
        })).sha256


def runtime(*, required=True, technology_name='sky130hd'):
    from .technology import technology

    try:
        root = repo_root() / "external/ppa-netlist-repair"
    except RepositoryUnavailable:
        if required:
            raise
        return None
    exe = root / "openroad/usr/bin/openroad"
    if not exe.is_file() and not required:
        return None
    # Declare the complete closure. Sim owns merging it with its tool profile;
    # matching a mount's path alone does not establish matching library bytes.
    dependencies = native_runtime((str(exe),))
    return RepairRuntime(binary(str(exe), "/tools/openroad"), (
        *dependencies,
        *technology(technology_name).lef_mounts,
    ))


def script(top, policy, *, dont_use_cells=(), placement_repair=False, threads=1,
           technology_name='sky130hd'):
    from .config import identifier
    from .eda import sta_setup
    from .technology import technology
    profile = technology(technology_name)

    if type(threads) is not int or threads < 1:
        raise ValueError('repair threads must be positive')

    body = (f"set_thread_count {threads}\nread_lef /pdk/technology.lef\n"
            "read_lef /pdk/cells.lef\n"
            + sta_setup(top, policy, technology_name=technology_name).replace("read_verilog mapped.v", "read_verilog pre_repair.v")
            + ("set_dont_use {" + ' '.join(identifier(cell) for cell in dont_use_cells) + "}\n"
               if dont_use_cells else "")
            + "set_max_fanout 32 [current_design]\n"
            # OpenROAD 2.0-17598: gain-based pre-placement buffering. Zero wire
            # parasitics; Liberty pin capacitance and the shared SDC still apply.
            + "repair_design -buffer_gain 4\n"
            + (profile.rc + profile.placement + PLACED_HOLD_REPAIR if placement_repair else '')
            + "write_verilog repaired.v\n"
            + 'puts "BEHAVE_MAPPING_REPAIR_COMPLETE"\n')
    # OpenROAD otherwise may report a Tcl error but exit zero. Never accept it.
    return ('if {[catch {\n' + body + '} message]} {\n'
            '  puts stderr $message\n  exit 1\n}\nexit 0\n')


def yosys_commands(top, liberty, *, technology_name='sky130hd'):
    """Continue the same synthesis process/budget, then export the repaired netlist."""
    from .technology import technology
    profile = technology(technology_name)
    return [
        # OpenDB's Verilog export does not preserve bare constant output aliases.
        # Materialize real library tie drivers BEFORE crossing that boundary.
        # Default hilomap creates a separate driver per constant bit (no global
        # high-fanout tie net); the cells count toward area and physical placement.
        # This maps only defined 0/1 constants, never undriven or unknown signals.
        f"hilomap -hicell {' '.join(profile.tie_high)} -locell {' '.join(profile.tie_low)}",
        "check -assert",
        "write_json mapped.json", "design -reset",
        "read_json [exec /tools/python -I -B netlist_view.py]",
        "write_verilog -noattr -noexpr -simple-lhs pre_repair.v",
        # Prefix is omitted by eda.yosys_script for this trusted Tcl command.
        "@exec /tools/openroad -no_init -exit repair.tcl > repair.log 2>@1",
        "design -reset", f"read_liberty -lib {liberty}",
        "read_verilog repaired.v", f"hierarchy -check -top {top}", "check -assert",
    ]
