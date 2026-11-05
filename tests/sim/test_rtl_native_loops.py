"""Literal-loop native events: compare complete occurrences, not shared goals."""

import subprocess
from dataclasses import replace

import pytest

from behave.sim.rtl.catalogue import occurrence_key_of, sites_of
from behave.sim.rtl.native_events import prepare_native_events
from behave.sim.rtl.site_target import site_hits
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_plan import EventInvocation, plan_source

from .test_rtl_source_bindings import _documents
from .test_rtl_source_plan import FIXTURES, _parse, _run

pytestmark = pytest.mark.eda("slang", "yosys")


def _source(loop, body="if(a[i]) q[i]<=1'b1; else q[i]<=1'b0;"):
    return ("module M(input clk,input [3:0] a,output reg [3:0] q); "
            f"integer i; integer j; always @(posedge clk) begin q<=0; {loop} {body} end endmodule")


LOOPS = {
    "ascending": _source("for(i=0;i<4;i=i+1)"),
    "descending": _source("for(i=3;i>=0;i=i-1)"),
    "strided": _source("for(i=1;i<=3;i=i+2)"),
    "nested": _source("for(i=0;i<2;i=i+1) for(j=0;j<2;j=j+1)",
                      "if(a[2*i+j]) q[2*i+j]<=1'b1; else q[2*i+j]<=1'b0;"),
    "zero": _source("for(i=0;i<0;i=i+1)"),
    "local": _source("for(int i=0;i<4;++i)").replace("integer i; ", ""),
    "compound": _source("for(i=3;i>=0;i-=1)"),
    "negative": _source("for(i=-1;i<3;i=i+1)",
                        "if(a[i+1]) q[i+1]<=1'b1; else q[i+1]<=1'b0;"),
}


def _expected_invocations(name, a=None):
    """Handwritten source iterations, independent of RTLIL and its predicates."""
    if name == "nested":
        iterations = tuple((i, j) for i in (0, 1) for j in (0, 1))
    else:
        values = {"ascending": (0, 1, 2, 3), "descending": (3, 2, 1, 0),
                  "strided": (1, 3), "zero": (), "local": (0, 1, 2, 3),
                  "compound": (3, 2, 1, 0), "negative": (-1, 0, 1, 2)}[name]
        iterations = tuple((i,) for i in values)
    expected = {EventInvocation(0, ())}  # one process entry
    # The loop's entry events have only their OUTER iteration coordinates.
    # Both outcomes stay declared, including the statically impossible one.
    outcomes = (1, 2) if a is None else (1 if name == "zero" else 2,)
    expected.update(EventInvocation(event, ()) for event in outcomes)
    if name == "nested":
        inner = (3, 4) if a is None else (4,)
        expected.update(EventInvocation(event, (i,)) for i in (0, 1) for event in inner)
    true_event, false_event = (5, 6) if name == "nested" else (3, 4)
    for coordinate in iterations:
        bit = (2 * coordinate[0] + coordinate[1] if name == "nested"
               else coordinate[0] + (name == "negative"))
        arms = ((true_event, false_event) if a is None else
                (true_event if a & (1 << bit) else false_event,))
        expected.update(EventInvocation(arm, coordinate) for arm in arms)
    return expected


@pytest.mark.parametrize("name", LOOPS)
def test_loop_coordinates_are_explicit_and_catalogue_is_unchanged(tmp_path, name):
    source = LOOPS[name]
    plan, original, annotated = _documents(tmp_path, source)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert not plan.native_issues
    assert {b.occurrence for b in bound.occurrences} == {occurrence_key_of(s) for s in sites_of(original)}
    assert bound.event_count == len(plan.invocations)
    assert len(set(plan.invocations)) == len(plan.invocations)
    assert [event.role for event in plan.events] == [
        "process", "loop-zero", "loop-nonzero",
        *(["loop-zero", "loop-nonzero"] if name == "nested" else []),
        "if-true", "if-false"]
    assert set(plan.invocations) == _expected_invocations(name)
    # Each retained branch occurrence gets its own invocation, never broadcast
    # from one source ordinal to every elaborated copy.
    arm_keys = {occurrence_key_of(site) for site in sites_of(original) if site.kind == "case-arm"}
    arms = [b for b in bound.occurrences if b.occurrence in arm_keys]
    assert arms or "i<0" in source
    assert len({b.event for b in arms}) == len(arms)


@pytest.mark.solver
@pytest.mark.parametrize("name", LOOPS)
def test_symbolic_occurrences_match_the_independent_source_loop_oracle(tmp_path, name):
    """Same handwritten events as native replay, including copies sharing a goal.

    These fixtures' branch operands are input bits; no settled internal signal is
    substituted for event history. This checks the retained branch/action slice,
    not the still-missing zero/nonzero loop-activation schema.
    """
    import z3

    from behave.sim.rtl.site_target import coverage_of, predicate_for

    plan, original, annotated = _documents(tmp_path, LOOPS[name])
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    sites = sites_of(original)
    a = z3.BitVec("source_loop_a", 4)

    class Inputs:
        @staticmethod
        def value_var(signal):
            return {"a": (a, 4), "clk": (z3.BoolVal(True), 1)}.get(signal, (None, None))

    occurrence_terms = {occurrence_key_of(site): predicate_for(Inputs(), site) for site in sites}
    assert all(z3.is_bool(term) for term in occurrence_terms.values())
    public_terms, refused = coverage_of(Inputs(), sites)
    assert not refused
    choices = [site for site in sites if site.kind == "case-arm"]
    assert len({site.goal for site in choices}) == (0 if name == "zero" else 2)

    def holds(term, value):
        return z3.is_true(z3.simplify(z3.substitute(term, (a, z3.BitVecVal(value, 4)))))

    for value in range(16):
        expected_events = _expected_invocations(name, value)
        expected_occurrences = {entry.occurrence for entry in bound.occurrences
                                if plan.invocations[entry.event] in expected_events}
        expected_goals = {entry.goal for entry in bound.occurrences
                          if entry.occurrence in expected_occurrences}

        assert {key for key, term in occurrence_terms.items() if holds(term, value)} == expected_occurrences
        assert {key for key, term in public_terms.items() if holds(term, value)} == expected_goals


@pytest.mark.parametrize("before,after", [
    ("i<4", "i<a"), ("i=i+1", "i=i+0"),
    ("integer i", "integer unsigned i"),
    ("if (a[i])", "if (i++)"),
    ("if (a[i])", "begin i[0]=1; end if(a[i])"),
    ("if (a[i])", "begin i=2; end if(a[i])"),
    ("if (a[i])", "if ($random)"),
    ("if (a[i])", "#1 if(a[i])"),
    ("i<4", "i<2147483647"),
    ("i=0; i<4; i=i+1", "i=2147483646; i<2147483647; i=i+2"),
])
def test_unproved_or_effectful_iteration_is_refused_before_binding(tmp_path, before, after):
    source = FIXTURES["unrolled"].replace(before, after)
    plan = plan_source(source, _parse(tmp_path, source))
    with pytest.raises(ValueError, match="loop occurrence contexts|storage bound"):
        plan.native()


def test_control_expansion_cannot_lose_or_reorder_a_source_control(tmp_path):
    source = _source("for(i=0;i<4;i=i+1)",
                     "begin if(a[i]) q[i]<=1; else q[i]<=0; if(a[0]) q[0]<=1; end")
    plan, original, annotated = _documents(tmp_path, source)
    bind_source_events(plan, original, annotated, filename="design.sv")
    for visits in (plan.visits[:-1], (plan.visits[0], *reversed(plan.visits[1:]))):
        with pytest.raises(ValueError, match="control expansion"):
            bind_source_events(replace(plan, visits=visits), original, annotated, filename="design.sv")


@pytest.mark.parametrize("shape", ["sequential", "block", "helper"])
def test_counter_binding_is_lexical_and_per_invocation(tmp_path, shape):
    loop = "for(i=0;i<4;i=i+1) if(a[i]) q[i]=1; else q[i]=0;"
    if shape == "sequential":
        source = _source("", f"begin {loop} {loop} end")
    elif shape == "block":
        source = _source("", "begin integer i; " + loop + " end")
    else:
        source = """module M(input clk,input [3:0] a,output reg [3:0] q);
          integer i;
          function automatic [3:0] f(input [3:0] a);
            integer i; begin for(i=0;i<4;i=i+1) if(a[i]) f[i]=1; else f[i]=0; end
          endfunction
          always @(posedge clk) q<=f(a);
        endmodule"""
    plan, original, annotated = _documents(tmp_path, source)
    assert not plan.native_issues, plan.native_issues
    assert [tuple(loop.values) for loop in plan.loops] == [(0, 1, 2, 3)] * (2 if shape == "sequential" else 1)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert {entry.occurrence for entry in bound.occurrences} == {occurrence_key_of(s) for s in sites_of(original)}


def test_formal_counter_cannot_borrow_the_module_counter_type(tmp_path):
    source = """module M(input clk, a, output reg q); integer i;
      function automatic f(input logic i);
        begin for(i=0;i<2;i=i+1) if(i) f=1; else f=0; end
      endfunction
      always @(posedge clk) q<=f(a);
    endmodule"""
    plan = plan_source(source, _parse(tmp_path, source))
    with pytest.raises(ValueError, match="constant bounds and a nonwrapping counter"):
        plan.native()


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("name", LOOPS)
def test_real_native_loop_hits_match_every_original_occurrence(tmp_path, name):
    source = LOOPS[name]
    plan, original, annotated = _documents(tmp_path, source)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=100)
    traces = {}
    for label, text in (("plain", source), ("native", plan.native().text)):
        work = tmp_path / label
        work.mkdir()
        (work / "design.sv").write_text(text)
        (work / "collector.cpp").write_text(collector.collector_source())
        (work / "probe.cpp").write_text('''#include "VM.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
extern "C" int behave_native_events_begin_v1(uint64_t);
extern "C" int behave_native_events_end_v1(uint64_t,uint64_t*,uint64_t,uint64_t*);
extern "C" int behave_native_events_finish_v1();
int main(int argc, char** argv) {
    Verilated::commandArgs(argc,argv); VM dut;
    for (unsigned a=0;a<16;++a) {
        if (behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0, calls=0;
        if (behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
    dut.final(); return behave_native_events_finish_v1();
}''')
        _run(work, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal",
             "--top-module", "M", "design.sv", "probe.cpp", "collector.cpp", timeout_s=90)
        run = subprocess.run([str(work / "obj_dir/VM")], cwd=work, capture_output=True,
                             text=True, timeout=10, check=True)
        traces[label] = [tuple(map(int, row.split())) for row in run.stdout.splitlines()]
    mask = 0 if name == "zero" else 10 if name == "strided" else 15
    assert [q for q, _, _ in traces["plain"]] == [a & mask for a in range(16)]
    assert [q for q, _, _ in traces["native"]] == [a & mask for a in range(16)]
    assert all(bits == calls == 0 for _, bits, calls in traces["plain"])
    mutation_detected = False
    for a, (_, bits, calls) in enumerate(traces["native"]):
        observed = {i for i in range(collector.bindings.event_count) if bits & (1 << i)}
        # Actual callbacks must equal the source event oracle before any
        # projection through the extracted catalogue is allowed to agree.
        assert {plan.invocations[i] for i in observed} == _expected_invocations(name, a)
        hits = collector.bindings.decode(observed)
        expected_occurrences, expected_goals = set(), set()
        for site in sites_of(original):
            goals, issues = site_hits([site], {"a": a, "clk": 1}, widths={"a": 4, "clk": 1},
                                     active_events={("rising", "clk")})
            assert not issues, issues
            if goals:
                expected_occurrences.add(occurrence_key_of(site))
                expected_goals.update(goals)
        assert hits.occurrences == expected_occurrences
        assert hits.goals == expected_goals
        assert calls == 1 + mask.bit_count() + (3 if name == "nested" else 1)
        # A routing permutation preserves output values and catalogue counts.
        # The full occurrence oracle, unlike a public-goal OR, must detect it.
        true_event = 5 if name == "nested" else 3
        same_arm = [i for i, invocation in enumerate(plan.invocations)
                    if invocation.source_event == true_event]
        if len(same_arm) > 1:
            left, right = same_arm[:2]
            swapped = {right if i == left else left if i == right else i for i in observed}
            mutation_detected |= collector.bindings.decode(swapped).occurrences != expected_occurrences
    assert mutation_detected == (name != "zero")
