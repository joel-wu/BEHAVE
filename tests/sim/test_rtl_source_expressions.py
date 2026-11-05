"""Conditional-expression values, native entries and compiler-bound selectors."""

from dataclasses import replace

import pytest

from behave.sim.core.refusal import Refusal
from behave.sim.rtl.native_events import prepare_native_events
from behave.sim.rtl.source_goals import goal_of
from behave.sim.rtl.source_projection import project_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_encoding import _prepared

pytestmark = [pytest.mark.eda("slang", "yosys", "verilator", "make"), pytest.mark.solver]

CASES = {
    "vector": ("q = a ? 8'h91 : 8'h27;", lambda a: (145 if a else 39, {"true" if a else "false"})),
    "context-width": ("q = a ? 1'sb1 : 4'sb0011;", lambda a: (255 if a else 3, {"true" if a else "false"})),
    "guarded": ("if(a[3]) q = a[0] ? 8'h91 : 8'h27; else q=0;",
                lambda a: ((145 if a&1 else 39) if a&8 else 0,
                           {"true" if a&1 else "false"} if a&8 else set())),
    "nested": ("q = a[0] ? 8'h91 : (a[1] ? 8'h27 : 8'h43);",
               lambda a: (145 if a&1 else 39 if a&2 else 67,
                          {"true"} if a&1 else {"false", "true"} if a&2 else {"false"})),
    "program-point": ("q=a+1; q=q[0] ? 8'h91 : 8'h27;",
                      lambda a: (145 if not a&1 else 39, {"false" if a&1 else "true"})),
    "predicate-nesting": ("q=(a[0] ? a[1] : a[2]) ? 8'h91 : 8'h27;",
                          lambda a: (145 if a & (2 if a&1 else 4) else 39,
                                     {"true" if a&1 else "false",
                                      "true" if a & (2 if a&1 else 4) else "false"})),
    "user-attribute": ("q=a ? (* note=1 *) 8'h91 : 8'h27;",
                       lambda a: (145 if a else 39, {"true" if a else "false"})),
}


def _driver():
    return '''
      dut.rst=0;
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''


def test_helper_expression_calls_keep_distinct_native_events(tmp_path):
    source = """module M(input clk,rst,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        f=v[0] ? 8'h91 : 8'h27;
      endfunction
      always @(posedge clk) if(a[3]) q=f(a); else q=f(~a);
    endmodule"""
    plan, original, annotated, execution = _prepared(tmp_path, source)
    projection = project_source(plan, execution.native, filename="design.sv")
    projection.require_complete()
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100, source_goals=True)
    plain = _trace(tmp_path / "plain", source, collector, _driver())
    native = _trace(tmp_path / "native", plan.native().text, collector, _driver(), native=True)
    call_spans = sorted({event.calls[-1] for event in plan.events if event.role.startswith("expression-")})
    assert len(call_spans) == 2
    for a, ((plain_q, _, _), (q, bits, calls)) in enumerate(zip(plain, native, strict=True)):
        truth = bool(a&1) if a&8 else not bool(a&1)
        assert plain_q == q == (145 if truth else 39)
        assert calls == 6  # process, branch, choice, and three entered statements
        for event, invocation in enumerate(plan.invocations):
            entry = plan.events[invocation.source_event]
            if entry.role.startswith("expression-"):
                active = entry.calls[-1] == call_spans[0 if a&8 else 1]
                assert bool(bits & (1 << event)) == (active and (entry.choice == "true") == truth)
                # Two muxes share the helper's source range. No ordinal guess.
                assert isinstance(execution.encoding.predicate_for(event, object()), Refusal)


@pytest.mark.parametrize("attribute", ["src", "scopename"])
def test_source_attributes_cannot_impersonate_compiler_expression_origins(tmp_path, attribute):
    from behave.sim.rtl.source_plan import plan_source

    from .test_rtl_source_plan import _parse

    source = f'''module M(input clk,a,output reg q);
      always @(posedge clk) q=a ? (* {attribute}="forged" *) 1'b1 : 1'b0;
    endmodule'''
    with pytest.raises(ValueError, match="source attributes cannot override compiler provenance"):
        plan_source(source, _parse(tmp_path, source))


def test_constant_pruned_expression_stays_declared_and_unencoded(tmp_path):
    source = "module M(input clk,output reg q); always @(posedge clk) q=1'b0 ? 1'b1 : 1'b0; endmodule"
    plan, _, _, execution = _prepared(tmp_path, source)
    projection = project_source(plan, execution.native, filename="design.sv")
    projection.require_complete()
    assert {goal.event for goal in projection.catalogue.values()
            if goal.family == "control-choice"} == {"true", "false"}
    assert [goal.event for goal in projection.catalogue.values() if goal.family == "location"] == ["entry"]
    assert all(isinstance(term, Refusal) for term in projection.predicates(execution.encoding, object()).values())


def test_pruned_helper_copy_does_not_borrow_the_surviving_mux(tmp_path):
    from behave.sim.rtl.source_expression_binding import expression_cells

    source = """module M(input clk,rst,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v); f=v ? 8'h91 : 8'h27; endfunction
      always @(posedge clk) if(a[3]) q=f(a); else q=f(0);
    endmodule"""
    plan, original, _, execution = _prepared(tmp_path, source)
    # Constant propagation removes one physical mux before proc. A unique
    # surviving source span is therefore not proof of a unique call context.
    assert len(expression_cells(original, {})) == 1
    expressions = [event for event, invocation in enumerate(plan.invocations)
                   if plan.events[invocation.source_event].role.startswith("expression-")]
    assert len(expressions) == 4
    assert all(isinstance(execution.encoding.predicate_for(event, object()), Refusal) for event in expressions)


def test_unrolled_expression_records_each_iteration_without_guessing_mux_copies(tmp_path):
    source = """module M(input clk,rst,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0; for(int i=0;i<4;i++) q=q+(a[i] ? 1 : 2);
      end
    endmodule"""
    plan, original, annotated, execution = _prepared(tmp_path, source)
    projection = project_source(plan, execution.native, filename="design.sv")
    projection.require_complete()
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100, source_goals=True)
    rows = _trace(tmp_path / "native", plan.native().text, collector, _driver(), native=True)
    for a, (q, bits, calls) in enumerate(rows):
        # Process + loop entry + four choices, plus q initialization, loop
        # statement, counter initialization and four entered body statements.
        assert q == 8-a.bit_count() and calls == 13
        for event in range(collector.bindings.event_count):
            invocation = plan.invocations[event]
            source_event = plan.events[invocation.source_event]
            if source_event.role.startswith("expression-"):
                expected = bool(a & (1 << invocation.iterations[0])) == (source_event.choice == "true")
                assert bool(bits & (1 << event)) == expected
    # Native occurrence completeness is not a license to choose arbitrary mux
    # copies for exact analysis. This profile has not bound iteration selectors.
    for event in range(execution.native.event_count):
        source_event = plan.events[plan.invocations[event].source_event]
        if source_event.role.startswith("expression-"):
            assert isinstance(execution.encoding.predicate_for(event, object()), Refusal)
