"""Actual HDL scope tokens, including cocotb's empty model-instance name."""

from dataclasses import replace

import pytest

from .test_rtl_combinational_events import _trace
from .test_rtl_source_locations import _collector, _occurrence_meanings
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang", "verilator", "make")


@pytest.mark.parametrize("root", ["", "runner"])
def test_generated_helper_events_use_the_declared_model_root(tmp_path, root):
    source = """module Leaf(input [3:0] a,output logic [3:0] q);
      function automatic [3:0] identity(input [3:0] v); return v; endfunction
      if(1) begin: logic_scope always_comb q=identity(a); end
    endmodule
    module M(input [7:0] a,output wire [7:0] q);
      Leaf low(a[3:0],q[3:0]),high(a[7:4],q[7:4]);
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    collector = replace(collector, scope_root=root)
    assert collector.word_count == 1
    driver = """
      const unsigned values[]={0,1,1,17,16,16};
      for(unsigned i=0;i<6;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver, model_name=root)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector,
                    driver, native=True, model_name=root)
    assert [row[0] for row in native] == [row[0] for row in original] == [0, 1, 1, 17, 16, 16]
    expected = [
        {"M.low.logic_scope", "M.high.logic_scope"},
        {"M.low.logic_scope"}, set(), {"M.high.logic_scope"},
        {"M.low.logic_scope"}, set(),
    ]
    meanings = _occurrence_meanings(source, plan)
    for (_, bits, _), scopes in zip(native, expected, strict=True):
        seen = {index for index in range(collector.bindings.event_count) if bits >> index & 1}
        hits = collector.bindings.decode(seen).occurrences
        assert {scope for key in hits for scope, schema, text, _ in (meanings[key],)
                if schema == "location" and text == "return v;"} == scopes
