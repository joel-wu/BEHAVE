"""Unique helper invocation proof and native full-occurrence differential."""

import re
import subprocess
from dataclasses import replace

import pytest

from behave.sim.rtl.catalogue import occurrence_key_of, sites_of
from behave.sim.rtl.native_events import prepare_native_events
from behave.sim.rtl.site_target import rtlil_name, site_hits
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_source_bindings import _documents
from .test_rtl_source_plan import _parse, _run

pytestmark = pytest.mark.eda("slang", "yosys")

FUNCTION = """function automatic [3:0] adjust(input [3:0] v);
    if(v[0]) adjust = v+1; else adjust = v-1;
endfunction
"""


def _source(functions=FUNCTION, body="q<=adjust(a);", declarations=""):
    return ("module M(input clk,input [3:0] a,output reg [3:0] q); "
            + declarations + functions + " always @(posedge clk) begin " + body + " end endmodule")


SOURCES = {
    "direct": _source(),
    "casts": _source(FUNCTION.replace("v+1", "$signed(v) >>> 1")
                              .replace("v-1", "$unsigned(v) >> 1")),
    "constant_queries": _source(FUNCTION.replace("v+1", "v+$clog2($bits(v))")
                                         .replace("v-1", "v-$clog2($bits(v))")),
    "guarded": _source(body="if(a[3]) q<=adjust(a); else q<=0;"),
    "nested": _source(FUNCTION + """function automatic [3:0] outer(input [3:0] v);
        outer=adjust(v);
    endfunction""", "q<=outer(a);"),
    "two_distinct": _source(FUNCTION + FUNCTION.replace("adjust", "other").replace("v[0]", "v[1]"),
                             "q<=adjust(a)+other(a);"),
    "predicate": _source(body="if(adjust(a)) q<=1; else q<=0;"),
    "case": _source(FUNCTION.replace("if(v[0]) adjust = v+1; else adjust = v-1;",
                    "case(v[0]) 1'b1: adjust=v+1; default: adjust=v-1; endcase")),
    "local_loop": _source(FUNCTION.replace("if(v[0]) adjust = v+1; else adjust = v-1;",
                "begin adjust=0; for(int j=0;j<4;j++) if(v[j]) adjust[j]=1; else adjust[j]=0; end")),
    "repeated_branches": _source(body="if(a[3]) q<=adjust(a); else q<=adjust(a);"),
    "repeated_nested": _source(FUNCTION + """function automatic [3:0] outer(input [3:0] v);
        outer=adjust(v);
    endfunction""", "if(a[3]) q<=outer(a); else q<=outer(a);"),
    "task_write": _source("""task automatic put(input [3:0] v);
        if(v[0]) q<=v+1; else q<=v-1;
    endtask""", "put(a);"),
    "task_repeated": _source("""task automatic put(input [3:0] v);
        if(v[0]) q<=v+1; else q<=v-1;
    endtask""", "if(a[3]) put(a); else put(a);"),
    "two_guards": _source(body="if(a[3]) q<=adjust(a); if(a[2]) q<=adjust(a);"),
    "no_arguments": _source("""function automatic [3:0] adjust();
        if(a[0]) adjust=a+1; else adjust=a-1;
    endfunction""", "q<=adjust();"),
}


@pytest.mark.parametrize("name", SOURCES)
def test_unique_helper_chain_keeps_complete_original_catalogue(tmp_path, name):
    plan, original, annotated = _documents(tmp_path, SOURCES[name])
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert plan.calls and not plan.native_issues
    assert {b.occurrence for b in bound.occurrences} == {occurrence_key_of(s) for s in sites_of(original)}
    assert all(call.start < call.end and call.definition_start < call.definition_end for call in plan.calls)


@pytest.mark.parametrize("source", [
    _source(body="for(i=0;i<4;i++) q<=adjust(a);", declarations="integer i;"),
    _source(FUNCTION.replace("adjust = v+1", "adjust = adjust(v-1)")),
    _source(declarations="initial q=adjust(0);"),
    _source("task automatic put(input [3:0] v); #1 if(v[0]) q<=v; endtask", "put(a);"),
    _source(declarations="integer adjust;"),
])
def test_unproved_helper_context_is_not_broadcast(tmp_path, source):
    plan = plan_source(source, _parse(tmp_path, source))
    with pytest.raises(ValueError, match="helper occurrence contexts|loop occurrence contexts|outside an observed"):
        plan.native()


def test_module_writes_do_not_prevent_native_call_contexts(tmp_path):
    source = _source("""function automatic [3:0] adjust(input [3:0] v);
        q=v;
        if(v[0]) return q+1;
        return q-1;
    endfunction""")
    plan = plan_source(source, _parse(tmp_path, source))
    assert plan.calls and not plan.native_issues
    plan.native()
    # Whole-function definedness is a symbolic obligation. The old refusal
    # fixture left the return undefined after its module write; that is not a
    # reason to refuse observation of all functions which write module state.


@pytest.mark.parametrize("source", [
    _source(FUNCTION.replace("input [3:0] v", "inout [3:0] v")),
    _source("task automatic put(inout [3:0] v); if(v[0]) v=0; endtask", "put(q);"),
])
def test_native_helper_context_preserves_supported_inout_formals(tmp_path, source):
    # These signatures are supported by the shared helper binding (including
    # copy-back); they are not unresolved-call refusal fixtures anymore.
    plan = plan_source(source, _parse(tmp_path, source))
    assert plan.calls and not plan.native_issues
    plan.native()


def test_control_free_helpers_keep_the_existing_admission(tmp_path):
    source = _source("function automatic [3:0] f(input [3:0] v); f=v+1; endfunction",
                     "q<=f(a)+f(a+1);")
    plan, original, annotated = _documents(tmp_path, source)
    assert not plan.calls and not plan.native_issues
    bind_source_events(plan, original, annotated, filename="design.sv")


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("name", list(SOURCES))
def test_native_helper_full_occurrence_oracle(tmp_path, name):
    source = SOURCES[name]
    plan, original, annotated = _documents(tmp_path, source)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100)
    traces = {}
    for label, text in (("plain", source), ("native", plan.native().text)):
        work = tmp_path/label
        work.mkdir()
        (work/"design.sv").write_text(text)
        (work/"collector.cpp").write_text(collector.collector_source())
        (work/"probe.cpp").write_text('''#include "VM.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
extern "C" int behave_native_events_begin_v1(uint64_t);
extern "C" int behave_native_events_end_v1(uint64_t,uint64_t*,uint64_t,uint64_t*);
extern "C" int behave_native_events_finish_v1();
int main(int argc,char** argv) {
 Verilated::commandArgs(argc,argv); VM dut;
 for(unsigned a=0;a<16;++a) {
  if(behave_native_events_begin_v1(a)) return 1;
  dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
  uint64_t bits=0,calls=0;
  if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
  std::cout<<unsigned(dut.q)<<" "<<bits<<"\\n";
 }
 dut.final(); return behave_native_events_finish_v1();
}''')
        _run(work, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal",
             "--top-module", "M", "design.sv", "probe.cpp", "collector.cpp", timeout_s=90)
        done = subprocess.run([str(work/"obj_dir/VM")], cwd=work, capture_output=True,
                              text=True, timeout=10, check=True)
        traces[label] = [tuple(map(int, row.split())) for row in done.stdout.splitlines()]
    def output(a):
        adjusted = (a + (1 if a & 1 else -1)) & 15
        if name == "casts":
            value = a - 16 if a & 1 and a & 8 else a
            return (value >> 1) & 15
        if name == "constant_queries":
            return (a + (2 if a & 1 else -2)) & 15
        if name == "guarded":
            return adjusted if a & 8 else 0
        if name == "two_distinct":
            return (adjusted + a + (1 if a & 2 else -1)) & 15
        if name == "predicate":
            return int(adjusted != 0)
        if name == "two_guards":
            return adjusted if a & 12 else 0
        return a if name == "local_loop" else adjusted

    assert [q for q, _ in traces["plain"]] == [output(a) for a in range(16)]
    assert [q for q, _ in traces["native"]] == [output(a) for a in range(16)]
    assert all(not bits for _, bits in traces["plain"])
    # Independent literal fixture values supply the original catalogue, without
    # using the native event plan to manufacture the expected occurrence set.
    # Only the predicate fixture has a generated switch value. Check its exact
    # reduce_bool wiring and the two return assignments before giving it the
    # literal arithmetic result, rather than inferring meaning from its name.
    sites = sites_of(original)
    generated = {rtlil_name(s.signal) for s in sites if s.kind == "case-arm" and s.signal.startswith("$")}
    if name == "predicate":
        signal, = generated
        matches = re.findall(r"^  cell \$reduce_bool [^\n]+\n(.*?)^  end$", original, re.MULTILINE | re.DOTALL)
        cell, = matches
        returned, = re.findall(r"^    connect \\A (\S+)\n", cell, re.MULTILINE)
        assert f"    connect \\Y {signal}\n" in cell
        results = re.findall(r"^        assign " + re.escape(returned) + r" (\S+) \[3:0\]$", original, re.MULTILINE)
        assert len(results) == 2
        for operation, result in zip(("add", "sub"), results, strict=True):
            bodies = re.findall(r"^  cell \$"+operation+r" [^\n]+\n(.*?)^  end$", original, re.MULTILINE | re.DOTALL)
            body, = bodies
            assert "    connect \\A \\a\n" in body and "    connect \\B 1\n" in body
            assert f"    connect \\Y {result}\n" in body
    else:
        assert not generated
    detected = False
    for a, (_, bits) in enumerate(traces["native"]):
        seen = {i for i in range(collector.bindings.event_count) if bits & (1<<i)}
        values = {"a": a, "clk": 1, **{s: output(a) for s in generated}}
        widths = {"a": 4, "clk": 1, **{s: 1 for s in generated}}
        expected_occurrences, expected_goals = set(), set()
        for site in sites:
            goals, issues = site_hits([site], values, widths=widths, active_events={("rising", "clk")})
            assert not issues, issues
            if goals:
                expected_occurrences.add(occurrence_key_of(site))
                expected_goals.update(goals)
        hits = collector.bindings.decode(seen)
        assert hits.occurrences == expected_occurrences
        assert hits.goals == expected_goals
        if len(seen) > 1:
            damaged = replace(collector.bindings, occurrences=collector.bindings.occurrences[:-1])
            detected |= damaged.decode(seen).occurrences != collector.bindings.decode(seen).occurrences
    assert sites and detected


def test_helper_must_keep_the_callers_control_ancestry(tmp_path):
    plan, original, annotated = _documents(tmp_path, SOURCES["guarded"])
    helper_event = next(e for e in plan.events if e.choice == "true" and len(e.ancestors) == 2)
    changed = replace(plan, events=tuple(replace(e, ancestors=e.ancestors[:1])
                                         if e == helper_event else e for e in plan.events))
    with pytest.raises(ValueError, match="control ancestry"):
        bind_source_events(changed, original, annotated, filename="design.sv")
