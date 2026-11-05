"""Dynamic loop-entry semantics, independent of the catalogue and RTLIL.

The rewrite is exercised on general loops, including forms that the current
finite occurrence binder does not yet admit. Those tests do not widen public
replay or symbolic capabilities.
"""

import subprocess
from collections import Counter

import pytest

from behave.sim.rtl.source_edits import SourceEdits
from behave.sim.rtl.source_loop_events import loop_entry_edits
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_source_declarations import _documents
from .test_rtl_source_plan import _run

CASES = {
    "for": ("for(i=0;cond(i);i++)", "q++;", "", "", ""),
    "while-break": ("while(cond(i))", "begin i++; q++; if(i==2) break; end", "", "", ""),
    "while-continue": ("while(cond(i))", "begin i++; if(i==1) continue; q++; end", "", "", ""),
    "do": ("do", "begin i++; q++; end", " while(cond(i));", "", ""),
    "repeat": ("repeat(count(n))", "q++;", "", "", ""),
    "forever-break": ("forever", "begin q++; break; end", "", "", ""),
    "body-return": ("for(i=0;cond(i);i++)", "begin q++; return; end", "", "", ""),
    "reentry": ("repeat(j)", "q++;", "", "for(j=1;j>=0;j--) begin ", " end"),
}

HELPER_SOURCE = """module M(input clk,input [3:0] a,output reg [3:0] q);
  function automatic [3:0] f(input [3:0] v);
    f=0; for(int i=0;i<2;i++) f=f+v;
  endfunction
  always @(posedge clk) if(a[0]) q<=f(a); else q<=f(a+1);
endmodule"""


def _sources(name):
    head, body, tail, outer, outer_end = CASES[name]
    prefix = """module M(input clk,input [2:0] n,
        output integer q,checks,zero_hits,nonzero_hits);
      function automatic logic cond(input integer i);
        checks++; cond=i<n;
      endfunction
      function automatic integer count(input integer n);
        checks++; count=n;
      endfunction
      task automatic run();
        integer i,j;
        begin i=0;
    """ + outer
    begin = len(prefix.encode())
    body_begin = begin + len((head + " ").encode())
    body_end = body_begin + len(body.encode())
    end = body_end + len(tail.encode())
    source = prefix + head + " " + body + tail + outer_end + """
        end
      endtask
      always @(posedge clk) begin
        q=0; checks=0; zero_hits=0; nonzero_hits=0; run();
      end
    endmodule"""
    observed = SourceEdits(source, loop_entry_edits(
        (begin, end), (body_begin, body_end),
        zero="zero_hits++;", nonzero="nonzero_hits++;"))
    return source, observed.text


def _expected(name, n):
    if name == "reentry":
        return (1, 0, 1, 1)
    if name == "forever-break":
        return (1, 0, 0, 1)
    if name == "do":
        return (max(1, n), max(1, n), 0, 1)
    zero, nonzero = int(n == 0), int(n != 0)
    if name == "while-break":
        return (min(n, 2), min(n+1, 2), zero, nonzero)
    if name == "body-return":
        return (min(n, 1), 1, zero, nonzero)
    if name == "while-continue":
        return (max(0, n-1), n+1, zero, nonzero)
    return (n, 1 if name == "repeat" else n+1, zero, nonzero)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("name", CASES)
def test_loop_entry_preserves_header_effects_and_body_exits(tmp_path, name):
    traces = []
    for label, source in zip(("plain", "observed"), _sources(name), strict=True):
        work = tmp_path / label
        work.mkdir()
        (work / "design.sv").write_text(source)
        (work / "probe.cpp").write_text('''#include "VM.h"
#include "verilated.h"
#include <iostream>
int main() {
  VM dut;
  for (unsigned n: {0,1,3,0,2}) {
    dut.clk=0; dut.n=n; dut.eval(); dut.clk=1; dut.eval();
    std::cout<<dut.q<<" "<<dut.checks<<" "<<dut.zero_hits<<" "<<dut.nonzero_hits<<"\\n";
  }
  dut.final();
}''')
        _run(work, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal",
             "--top-module", "M", "design.sv", "probe.cpp", timeout_s=90)
        result = subprocess.run([str(work / "obj_dir/VM")], cwd=work, capture_output=True,
                                text=True, check=True, timeout=10)
        traces.append([tuple(map(int, line.split())) for line in result.stdout.splitlines()])
    expected = [_expected(name, n) for n in (0, 1, 3, 0, 2)]
    assert traces[0] == [(q, checks, 0, 0) for q, checks, _, _ in expected]
    assert traces[1] == expected


@pytest.mark.eda("slang")
def test_source_plan_binds_loop_and_case_producers_to_declared_outcomes(tmp_path):
    source = """module M(input clk,a,output reg q);
      always @(posedge clk) begin
        case(a) default: q<=0; 1'b1: q<=1; endcase
        for(int i=0;i<0;i++) q<=1;
        for(int j=0;j<2;j++) q<=0;
      end
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert plan.native()
    by_site = {}
    for binding in plan.declared_events:
        by_site.setdefault(binding.site, set()).add(binding.outcome)
        event = plan.events[binding.event]
        site = plan.declarations.sites[binding.site]
        if site.schema == "loop-activation":
            assert event.contexts == ()  # not one entry goal per body copy
            assert event.role == "loop-" + binding.outcome
    assert by_site == {index: set(site.outcomes)
                       for index, site in enumerate(plan.declarations.sites)}
    assert Counter(site.schema for site in plan.declarations.sites) == {
        "control-choice": 1, "loop-activation": 2, "location": 9}


@pytest.mark.eda("slang")
def test_loop_only_helper_uses_the_source_plans_event_owner(tmp_path):
    source = HELPER_SOURCE
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert plan.native()
    assert len(plan.calls) == 2
    loop, = [i for i, site in enumerate(plan.declarations.sites)
             if site.schema == "loop-activation"]
    assert [(row.outcome, plan.events[row.event].contexts)
            for row in plan.declared_events if row.site == loop] == [
                ("zero", ()), ("nonzero", ()), ("zero", ()), ("nonzero", ())]


@pytest.mark.eda("slang", "yosys", "verilator", "make")
def test_loop_only_helper_records_its_actual_call_context(tmp_path):
    from behave.sim.rtl.native_events import prepare_native_events

    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_plan import _compile

    cst, ast = _documents(tmp_path, HELPER_SOURCE)
    plan = plan_source(HELPER_SOURCE, cst, semantic_document=ast)
    original = _compile(tmp_path / "original", HELPER_SOURCE)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    collector = prepare_native_events(
        plan, original, annotated, filename="design.sv", toolchain_sha256="1"*64,
        execution_sha256="2"*64, callback_limit=100)
    trace = _trace(tmp_path / "native", plan.native().text, collector, '''
      for (unsigned a=0; a<16; a++) {
        if (behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if (behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    ''', native=True)
    for a, (q, bits, calls) in enumerate(trace):
        assert q == (2 * (a if a & 1 else a + 1)) % 16
        selected = "f(a)" if a & 1 else "f(a+1)"
        observed = set()
        for index, invocation in enumerate(plan.invocations):
            if bits & (1 << index):
                event = plan.events[invocation.source_event]
                if event.role != "location":
                    path = tuple(HELPER_SOURCE[start:end] for start, end in event.calls)
                    observed.add((event.role, path))
        assert observed == {("process", ()),
                            ("if-true" if a & 1 else "if-false", ()),
                            ("loop-nonzero", (selected,))}
        assert calls == bits.bit_count()


def test_loop_rewrite_does_not_replace_source_tokens():
    for name, (head, body, _, _, _) in CASES.items():
        plain, observed = _sources(name)
        assert "__behave_native_event_loop_seen_" in observed
        assert head in observed and body in observed
        assert plain != observed
