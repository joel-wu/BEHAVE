"""Native observers retain generated ownership and assignment carrier types."""

from dataclasses import replace

import pytest

from .test_rtl_combinational_events import _trace
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang", "verilator", "make")


@pytest.mark.parametrize("body", [
    "assign w[k] = a[k];",
    "begin assign w[k] = a[k]; end",
    "begin: lane assign w[k] = a[k]; end",
    "if(k%2) assign w[k] = a[k]; else assign w[k] = a[k];",
    "case(k%2) 0: assign w[k] = a[k]; default: assign w[k] = a[k]; endcase",
])
def test_generated_single_statement_callbacks_keep_instance_owner(tmp_path, body):
    source = f"""module M(input [7:0] a, output wire [31:0] q);
      wire [7:0] w;
      for(genvar k=0;k<8;k=k+1) {body}
      assign q={{24'b0,w}};
    endmodule"""
    _compare(tmp_path, source)


def test_enum_nba_observer_masks_are_bit_carriers_not_implicit_enum_values(tmp_path):
    source = """module M(input [7:0] a, output logic [31:0] q);
      typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
      state_t state=IDLE;
      always @(posedge a[0]) begin
        if(a[1]) state<=RUN; else state<=DONE;
      end
      always_comb begin
        case(state)
          IDLE: q=10;
          RUN: q=20;
          default: q=30;
        endcase
      end
    endmodule"""
    _compare(tmp_path, source)


def _compare(tmp_path, source):
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = """
      const unsigned values[]={0,1,2,3,0,255,254,1};
      for(unsigned i=0;i<8;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver, native=True)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in native] == [row[0] for row in original]
    assert any(bits for _, bits, _ in native)
