"""Condition evaluation includes the final failed test, not only body entries."""

from dataclasses import replace

import pytest

from behave.sim.rtl.source_ir import SourceCompiler

from .test_rtl_combinational_events import _trace
from .test_rtl_source_ir import _encode, _run_activation
from .test_rtl_source_locations import _collector, _occurrence_meanings
from .test_rtl_source_net_drivers import _prepare

pytestmark = [pytest.mark.eda("slang", "verilator", "make"), pytest.mark.solver]


def _compare(tmp_path, source, condition, values, expected, *, condition_values):
    plan, ast, _ = _prepare(tmp_path, source)
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    context, = plan.declarations.contexts
    lowered = compiler.lower(context)
    names = {compiler.symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = f"""
      const unsigned values[]={{{','.join(map(str, values))}}};
      for(unsigned i=0;i<{len(values)};i++) {{
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }}
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    observed = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    start = source.index(condition)
    span = start, start + len(condition)
    for value, before, after in zip(values, original, observed, strict=True):
        assert before == (expected(value), 0, 0)
        assert after[0] == before[0]
        seen = {bit for bit in range(collector.bindings.event_count) if after[1] >> bit & 1}
        assert after[2] == len(seen)  # Each coordinate fires once, not a duplicate evaluation.
        post, _, ir_hits = _run_activation(lowered, names, {"a": value, "q": 0}, encoded)
        assert post[names["q"]] == before[0]
        assert collector.bindings.decode(seen).occurrences == ir_hits
        tests = set()
        for bit in seen:
            invocation = plan.invocations[collector.bindings.coordinates[bit].source_event]
            event = plan.events[invocation.source_event]
            if span in event.calls or span[0] <= event.start < event.end <= span[1]:
                tests.add(invocation.iterations)
        assert tests == {(counter,) for counter in condition_values(value)}
    with pytest.raises(ValueError, match="loop-condition"):
        plan.require_compiler_binding()
    return plan


@pytest.mark.parametrize("first,bound,step,operator,counters", [
    (0, 3, 1, "<", (0, 1, 2, 3)),
    (0, 0, 1, "<", (0,)),
    (0, 5, 2, "<", (0, 2, 4, 6)),
    (4, 0, -2, ">", (4, 2, 0)),
])
def test_helper_condition_observes_every_test_including_termination(
        tmp_path, first, bound, step, operator, counters):
    source = f"""module M(input [3:0] a,output logic [7:0] q);
      parameter int FIRST={first}, N={bound}, STEP={step};
      function automatic int limit(input int n); return n; endfunction
      always_comb begin
        q=a;
        for(int i=FIRST;i{operator}limit(N);i=i+STEP) q=q+1;
      end
    endmodule"""
    _compare(tmp_path, source, "limit(N)", [0, 1, 2],
             lambda a: a+len(counters)-1, condition_values=lambda _: counters)


@pytest.mark.parametrize("count", [0, 2])
def test_conditional_bound_keeps_zero_trip_and_final_test_events(tmp_path, count):
    source = f"""module M(input [3:0] a,output logic [7:0] q);
      parameter int N={count};
      always_comb begin q=a; for(int i=0;i<(N>0 ? N : 0);i++) q=q+1; end
    endmodule"""
    _compare(tmp_path, source, "N>0 ? N : 0", [0, 1, 2], lambda a: a+count,
             condition_values=lambda _: range(count+1))


def test_body_return_does_not_invent_a_terminating_condition_test(tmp_path):
    source = """module M(input [3:0] a,output logic [7:0] q);
      parameter int N=3;
      function automatic int limit(input int n); return n; endfunction
      function automatic int f(input int x);
        for(int i=0;i<limit(N);i++) if(i==x) return i+10;
        return 20;
      endfunction
      always_comb q=f(a);
    endmodule"""
    _compare(tmp_path, source, "limit(N)", [0, 1, 3], lambda a: a+10 if a<3 else 20,
             condition_values=lambda a: range(min(a, 3)+1))


def test_condition_grid_does_not_merge_instance_execution_counts(tmp_path):
    source = """module Part #(parameter int N=3)(input [3:0] a,output logic [7:0] q);
      function automatic int limit(input int n); return n; endfunction
      always_comb begin q=a; for(int i=0;i<limit(N);i++) q=q+1; end
    endmodule
    module M(input [3:0] a,output wire [15:0] q);
      Part #(.N(0)) empty(a,q[7:0]);
      Part #(.N(3)) full(a,q[15:8]);
    endmodule"""
    plan, ast, _ = _prepare(tmp_path, source)
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    collector, _ = _collector(plan)
    meanings = _occurrence_meanings(source, plan)
    driver = """
      if(behave_native_events_begin_v1(0)) return 1;
      dut.a=2; dut.eval();
      uint64_t bits=0,calls=0;
      if(behave_native_events_end_v1(0,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original, = _trace(tmp_path / "original", source, flat, driver)
    observed, = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert original == (1282, 0, 0)
    assert observed[0] == original[0]
    seen = {bit for bit in range(collector.bindings.event_count) if observed[1] >> bit & 1}
    assert observed[2] == len(seen)
    native_hits = collector.bindings.decode(seen).occurrences
    call = source.index("limit(N)"), source.index("limit(N)")+len("limit(N)")
    for instance, count in (("empty", 0), ("full", 3)):
        context, = [row for row in plan.declarations.contexts if row.scope == ("M", instance)]
        lowered = compiler.lower(context)
        names = {compiler.symbols[identity]["name"]: name for name, identity in lowered.storage}
        post, _, ir_hits = _run_activation(lowered, names, {"a": 2, "q": 0}, _encode(lowered))
        assert post[names["q"]] == 2+count
        assert {hit for hit in native_hits if meanings[hit][0] == "M."+instance} == ir_hits
        tests = set()
        for bit in seen:
            coordinate = collector.bindings.coordinates[bit]
            invocation = plan.invocations[coordinate.source_event]
            if coordinate.scope == "TOP.M."+instance and call in plan.events[invocation.source_event].calls:
                tests.add(invocation.iterations)
        assert tests == {(i,) for i in range(count+1)}
