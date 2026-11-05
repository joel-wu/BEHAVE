"""An empty assertion action remains empty and still has a source entry."""

from dataclasses import replace

import pytest

from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_declarations import _documents
from .test_rtl_source_expressions import _driver

pytestmark = pytest.mark.eda("slang", "verilator", "make")


@pytest.mark.parametrize("shape", ["direct", "loop", "helper"])
def test_null_pass_action_hits_its_actual_call_and_iteration(tmp_path, shape):
    helpers = ""
    if shape == "direct":
        body = "q=0; if(a[0]) begin assert(1); end else q+=3;"
    elif shape == "loop":
        body = "q=0; for(int i=0;i<2;i++) if(a[i]) begin assert(1); end else q+=3;"
    else:
        helpers = ("task automatic f(input [3:0] v); "
                   "if(v[0]) begin assert(1); end else q+=3; endtask")
        body = "q=0; f(a); f(a^1);"
    source = f"""module M(input clk,rst,input [3:0] a,output reg [7:0] q);
      {helpers}
      always @(posedge clk) begin {body} end
    endmodule"""
    cst, ast = _documents(tmp_path / "parse", source)
    plan = plan_source(source, cst, semantic_document=ast)
    collector = prepare_declared_native_events(
        plan, filename="design.sv", toolchain_sha256="1" * 64,
        execution_sha256="2" * 64, callback_limit=100)
    transport = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, transport, _driver())
    native = _trace(tmp_path / "native", plan.native(scoped=True).text,
                    collector, _driver(), native=True)

    # The expectation is the original null statement's byte span, not an
    # extractor-selected event ordinal or a final-value heuristic.
    start = source.index("assert(1);") + len("assert(1)")
    entries = [(index, invocation, plan.events[invocation.source_event])
               for index, invocation in enumerate(plan.invocations)
               if plan.events[invocation.source_event].role == "location"
               and plan.events[invocation.source_event].start == start]
    assert len(entries) == (1 if shape == "direct" else 2)
    if shape == "helper":
        first_call, second_call = source.index("f(a);"), source.index("f(a^1);")
        assert {event.calls[-1][0] for _, _, event in entries} == {first_call, second_call}
    for a, ((plain_q, _, _), (q, bits, _)) in enumerate(zip(plain, native, strict=True)):
        expected_q = 3 if shape == "helper" else 3 * sum(
            not (a & (1 << bit)) for bit in range(2 if shape == "loop" else 1))
        assert q == plain_q == expected_q
        for index, invocation, event in entries:
            if shape == "helper":
                expected = {first_call: bool(a & 1), second_call: not bool(a & 1)}[
                    event.calls[-1][0]]
            else:
                bit = invocation.iterations[0] if shape == "loop" else 0
                expected = bool(a & (1 << bit))
            assert bool(bits & (1 << index)) == expected, (shape, a, event, invocation)


@pytest.mark.parametrize("statement", [
    "assert(i++ >= 0);", "assert(a) else i=0;",
    "assert #0(a);", "assert final(a);",
])
def test_assertion_loop_coordinates_exclude_counter_writes_and_deferred_actions(tmp_path, statement):
    source = f"""module M(input clk,a,output reg q);
      always @(posedge clk) for(int i=0;i<2;i++) begin {statement} q=a; end
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    with pytest.raises(ValueError, match="nonwrapping counter|deferred assertions"):
        plan.native()
