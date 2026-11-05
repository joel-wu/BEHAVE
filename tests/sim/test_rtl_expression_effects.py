"""Value simplification must retain executed source-expression observations."""

from dataclasses import replace

import pytest

from behave.sim.rtl.source_ir import SourceCompiler

from .test_rtl_combinational_events import _trace
from .test_rtl_source_ir import _encode, _run_activation
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare

pytestmark = [pytest.mark.eda("slang", "verilator", "make"), pytest.mark.solver]


@pytest.mark.parametrize("body,expected,depends_on_a", [
    ("q=a ? 5 : 5;", lambda a: 5, True),
    ("q=a[0] ? a : a;", lambda a: a, True),
    ("q=(a ? 1 : 1) ? 7 : 9;", lambda a: 7, True),
    ("q=(a ? 5 : 9) & 0;", lambda a: 0, True),
    ("q=a && 1'b0;", lambda a: 0, True),
    ("q=1'b0 ? (a ? 5 : 5) : 7;", lambda a: 7, False),
    ("q=f(a);", lambda a: 5, True),
], ids=["equal-constant", "equal-data", "nested", "masked-value",
        "boolean", "unexecuted-arm", "helper"])
def test_optimized_value_keeps_exact_executed_occurrences(tmp_path, body, expected, depends_on_a):
    source = f"""module M(input [3:0] a,output logic [7:0] q);
      function automatic int f(input int v); return v ? 5 : 5; endfunction
      always_comb begin {body} end
    endmodule"""
    plan, ast, sensitivity = _prepare(tmp_path, source)
    assert any(read["symbol"] == "M.a" for row in sensitivity for read in row["reads"]) == depends_on_a
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    context, = plan.declarations.contexts
    lowered = compiler.lower(context)
    names = {compiler.symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = """
      for(unsigned a=0;a<4;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.a=a; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    observed = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    for value, before, after in zip(range(4), original, observed, strict=True):
        assert before == (expected(value), 0, 0)
        assert after[0] == before[0]
        seen = {bit for bit in range(collector.bindings.event_count) if after[1] >> bit & 1}
        assert after[2] == len(seen)
        # A constant-false outer condition never reads its inactive arm's a.
        # That process has one startup activation, not an activation per input.
        if value and not depends_on_a:
            assert seen == set()
            continue
        post, _, ir_hits = _run_activation(lowered, names, {"a": value, "q": 0}, encoded)
        assert post[names["q"]] == before[0]
        assert collector.bindings.decode(seen).occurrences == ir_hits
