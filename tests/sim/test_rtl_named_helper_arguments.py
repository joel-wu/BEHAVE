"""Observe named/mixed helper calls without changing binding or copy-back."""

from dataclasses import replace

import pytest

from .test_rtl_combinational_events import _trace
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang", "verilator", "make")


@pytest.mark.parametrize("call", [
    "adjust(.v(a), .result(value))",
    "adjust(.result(value), .v(a))",
    "adjust(a, .result(value))",
])
def test_named_output_arguments_keep_native_values_and_branch_hits(tmp_path, call):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      logic [3:0] value;
      function automatic void adjust(input [3:0] v, output logic [3:0] result);
        if(v[0]) result=v+1; else result=v-1;
      endfunction
      always @(posedge clk) begin {call}; q<=value; end
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, manifest = _collector(plan)
    assert collector.word_count == 1
    driver = '''
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0;dut.a=a;dut.eval();dut.clk=1;dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    branch = {goal.id: goal.event for goal in manifest.values() if goal.family == "control-choice"}
    assert branch
    for a, (before, after) in enumerate(zip(plain, native, strict=True)):
        assert before == (((a + (1 if a & 1 else -1)) & 15), 0, 0)
        assert after[0] == before[0]
        hits = collector.bindings.decode({i for i in range(collector.bindings.event_count)
                                         if after[1] >> i & 1}).goals
        assert {branch[goal] for goal in hits if goal in branch} == {"true" if a & 1 else "false"}
