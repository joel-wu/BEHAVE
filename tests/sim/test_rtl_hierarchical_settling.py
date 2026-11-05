"""Small acyclic helper networks must settle with and without source probes."""

from dataclasses import replace

import pytest

from .test_rtl_combinational_events import _trace
from .test_rtl_source_activation_scheduling import _assert_entries
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare


pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


def test_unit_helper_local_initializers_in_an_acyclic_compare_network(tmp_path):
    source = """function automatic logic less(input logic [7:0] a, b);
      logic sa = a[7];
      logic sb = b[7];
      if (sa != sb) return sa;
      else return a <= b;
    endfunction
    module Compare(input logic [7:0] a, b, output wire [7:0] lo, hi);
      wire le;
      assign le=less(a,b);
      assign lo=le ? a : b;
      assign hi=le ? b : a;
    endmodule
    module M(input logic [7:0] a, output logic [31:0] q);
      wire [7:0] lo,hi,tail,head;
      Compare c0(a,8'd3,lo,hi);
      Compare c1(lo,8'd7,tail,head);
      always_comb q={8'd0,tail,head,hi};
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = """
      const unsigned values[]={0,1,1,3,7,255,128,0};
      for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver, native=True)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text,
                    collector, driver, native=True)
    # An independent arithmetic oracle avoids Icarus's failure on this
    # compilation-unit function/local-initializer combination. No event-count
    # equivalence is claimed by this settling/value regression.
    expected = []
    for value in (0,1,1,3,7,255,128,0):
        lo, hi = sorted((value, 3), key=lambda x: x if x < 128 else x - 256)
        tail, head = sorted((lo, 7), key=lambda x: x if x < 128 else x - 256)
        expected.append((tail << 16) | (head << 8) | hi)
    assert [value for value, _, _ in original] == expected
    assert [value for value, _, _ in native] == expected
    assert all(bits == calls == 0 for _, bits, calls in original)
    assert any(bits and calls for _, bits, calls in native)


def test_clocked_pipeline_with_combinational_helper_and_async_reset(tmp_path):
    source = """module M(input logic [7:0] a, output logic [31:0] q);
      logic [7:0] x=0, y=0;
      wire clk=a[0], rst=a[1];
      function automatic logic [7:0] add(input logic [7:0] v, w);
        return v+w;
      endfunction
      logic [7:0] total;
      always_comb total=add(x,a);
      always_ff @(posedge clk or posedge rst) begin
        if(rst) begin x<=0; y<=0; end
        else begin x<=a; y<=total; end
      end
      always_comb q={16'd0,x,y};
    endmodule"""
    _assert_entries(tmp_path, source, [0,2,3,0,1,4,5,8,9,2,0],
                    ("q={16'd0,x,y};",))
