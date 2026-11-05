"""Exact hierarchy binding and real native events, not public-hit-count parity."""

import json
import subprocess
from dataclasses import replace

import pytest

from behave.sim.rtl.catalogue import occurrence_key_of, sites_of
from behave.sim.rtl.native_events import NativeEventPlan, prepare_native_events
from behave.sim.rtl.site_target import site_hits
from behave.sim.rtl.source_bindings import EventCoordinate, bind_source_events
from behave.sim.rtl.source_contexts import process_contexts
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_source_plan import FIXTURES, _parse, _run

pytestmark = pytest.mark.eda("slang", "yosys")

HIERARCHY = '''module Inner #(parameter K=1)(input clk, input [3:0] a, output reg [3:0] q);
    always @(posedge clk) if(a[0]) q<=a+K; else q<=a-K;
endmodule
module M(input clk, input [3:0] a, output [15:0] q);
    Inner #(1) left(clk,a,q[3:0]);
    Inner #(2) right(clk,~a,q[7:4]);
    for(genvar i=0;i<2;i=i+1) begin: g
        Inner #(3) nested(clk,a^i,q[8+4*i+:4]);
    end
endmodule'''


def _documents(tmp_path, source):
    plan = plan_source(source, _parse(tmp_path / "parse", source))
    documents = []
    for name, text in (("original", source), ("annotated", plan.compiler.text)):
        work = tmp_path / name
        work.mkdir()
        (work / "design.sv").write_text(text)
        _run(work, "yosys", "-Q", "-T", "-p",
             "read_verilog -sv design.sv; hierarchy -top M; flatten; write_rtlil design.rtlil")
        documents.append((work / "design.rtlil").read_text())
    _run(work, "yosys", "-Q", "-T", "-p",
         "read_verilog -sv design.sv; hierarchy -top M; flatten -scopename; write_rtlil scoped.rtlil")
    return plan, *documents, (work / "scoped.rtlil").read_text()


def test_context_document_cannot_change_the_relation_or_instance_identity(tmp_path):
    plan, original, annotated, scoped = _documents(tmp_path, HIERARCHY)
    contexts = process_contexts(annotated, scoped)
    assert set(contexts.values()) == {"left", "right", "g[0].nested", "g[1].nested"}
    bound = bind_source_events(plan, original, annotated, filename="design.sv", scoped=scoped)
    assert len(bound.coordinates) == 12
    assert {b.occurrence for b in bound.occurrences} == {occurrence_key_of(s) for s in sites_of(original)}
    for mutant in (scoped.replace('\\scopename "left"', '\\scopename "right"'),
                   scoped.replace('  attribute \\scopename "left"\n', ''),
                   scoped.replace("sync posedge", "sync negedge")):
        with pytest.raises(ValueError):
            bind_source_events(plan, original, annotated, filename="design.sv", scoped=mutant)


@pytest.mark.parametrize("source", [FIXTURES["helper"], '''
module M(input clk, input [1:0] a, output reg [1:0] q);
    for(genvar i=0;i<2;i=i+1) begin: g
        always @(posedge clk) if(a[i]) q[i]<=1; else q[i]<=0;
    end
endmodule'''])
def test_module_scope_is_not_a_loop_or_helper_occurrence_context(tmp_path, source):
    plan, original, annotated, scoped = _documents(tmp_path, source)
    with pytest.raises(ValueError, match="occurrence contexts"):
        bind_source_events(plan, original, annotated, filename="design.sv", scoped=scoped)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("root", ["TOP", ""])
def test_native_hierarchy_keeps_each_instances_own_events(tmp_path, root):
    # This is the retained RTLIL differential oracle. The public source-owned
    # catalogue additionally declares location entries; it is not equal to this
    # legacy catalogue and must not be checked against its numeric event IDs.
    plan, original, annotated, scoped = _documents(tmp_path, HIERARCHY)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=100,
        scoped=scoped, scope_root=root)
    native = plan.native(scoped=True).text
    assert NativeEventPlan.from_dict(json.loads(json.dumps(collector.as_dict()))) == collector
    coordinates = collector.bindings.coordinates
    paths = {"TOP.M.left", "TOP.M.right", "TOP.M.g[0].nested", "TOP.M.g[1].nested"}
    assert {c.scope for c in coordinates} == paths
    # All calls use one source ordinal family, but disjoint instance event bits.
    assert {c.source_event for c in coordinates} == {0, 1, 2}
    traces = {}
    for name, text in (("plain", HIERARCHY), ("native", native)):
        work = tmp_path / name
        work.mkdir()
        (work / "design.sv").write_text(text)
        # The original HDL has no DPI import; its idle transport needs no scope
        # lookup and must not add an observer to the noninterference baseline.
        transport = (replace(collector, bindings=replace(collector.bindings, coordinates=()))
                     if name == "plain" else collector)
        (work / "collector.cpp").write_text(transport.collector_source())
        (work / "probe.cpp").write_text('''#include "VM.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
extern "C" int behave_native_events_begin_v1(uint64_t);
extern "C" int behave_native_events_end_v1(uint64_t,uint64_t*,uint64_t,uint64_t*);
extern "C" int behave_native_events_finish_v1();
int main(int argc, char** argv) {
    Verilated::commandArgs(argc,argv); VM dut(argv[1]);
    for (unsigned i=0;i<2;++i) {
        if (behave_native_events_begin_v1(i)) return 1;
        dut.clk=0; dut.a=i+1; dut.eval(); dut.clk=1; dut.eval();
        uint64_t hits=0, calls=0;
        if (behave_native_events_end_v1(i,&hits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<hits<<" "<<calls<<"\\n";
    }
    dut.final(); return behave_native_events_finish_v1();
}''')
        _run(work, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal",
             "--top-module", "M", "design.sv", "probe.cpp", "collector.cpp", timeout_s=90)
        out = subprocess.run([str(work / "obj_dir/VM"), root], cwd=work, capture_output=True,
                             text=True, timeout=10, check=True).stdout
        traces[name] = [tuple(map(int, line.split())) for line in out.splitlines()]
    assert [q for q, _, _ in traces["plain"]] == [0xD4C2, 0x6FF1]
    assert [q for q, _, _ in traces["native"]] == [0xD4C2, 0x6FF1]
    assert all(bits == calls == 0 for _, bits, calls in traces["plain"])
    for index, (_, bits, calls) in enumerate(traces["native"]):
        expected_true = ({"TOP.M.left", "TOP.M.g[0].nested"} if index == 0 else
                         {"TOP.M.right", "TOP.M.g[1].nested"})
        expected = {EventCoordinate(path, 0) for path in paths}
        expected |= {EventCoordinate(path, 1 if path in expected_true else 2) for path in paths}
        observed = {i for i in range(len(coordinates)) if bits & (1 << i)}
        assert {coordinates[i] for i in observed} == expected
        assert calls == 8
        hits = collector.bindings.decode(observed)
        a = index + 1
        inputs = {"left": a, "right": (~a) & 15, "g[0].nested": a, "g[1].nested": a ^ 1}
        values = {path + ".a": value for path, value in inputs.items()}
        values.update({path + ".clk": 1 for path in inputs})
        widths = {path + ".a": 4 for path in inputs}
        widths.update({path + ".clk": 1 for path in inputs})
        # Only the task clock is scheduled. Child trigger activation comes
        # from the compiler's wiring proof, not test-supplied child events.
        edges = {("rising", "clk")}
        expected_occurrences, expected_goals = set(), set()
        for site in sites_of(original):
            goals, issues = site_hits([site], values, widths=widths, active_events=edges)
            assert not issues, issues
            if goals:
                expected_occurrences.add(occurrence_key_of(site))
                expected_goals.update(goals)
        assert hits.occurrences == expected_occurrences
        assert hits.goals == expected_goals
        # Swapping instance coordinates changes complete occurrences even if
        # shared public goal ORs or outputs happen to stay equal.
        swapped = tuple(replace(c, scope=("TOP.M.right" if c.scope == "TOP.M.left" else
                                         "TOP.M.left" if c.scope == "TOP.M.right" else c.scope))
                        for c in coordinates)
        assert {swapped[i] for i in observed} != expected
