"""Observer arguments preserve native task argument binding and copy-back."""

import pytest

from .test_rtl_combinational_events import _trace
from .test_rtl_helper_boundaries import HINT
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang", "verilator", "make")


@pytest.mark.parametrize("ports", [
    "(input logic [3:0] x, output logic [3:0] y, inout logic [3:0] acc);",
    "; input logic [3:0] x; output logic [3:0] y; inout logic [3:0] acc;",
])
@pytest.mark.parametrize("aliased", [False, True])
def test_task_output_and_inout_keep_values_and_branch_hits(tmp_path, ports, aliased):
    call = "choose(a,total,total); result=total;" if aliased else "choose(a,result,total);"
    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      task automatic choose{ports}
        if(x[0]) y=x+1; else y=x-1;
        acc=acc+y;
      endtask
      always @(posedge clk) begin
        logic [3:0] result,total;
        total=3;
        {call}
        q={{total,result}};
      end
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    assert len(plan.declarations.retained_frame_calls) == 1
    assert not plan.declarations.closed_value_calls
    assert not plan.declarations.frame_local_calls
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = """
      for(unsigned i=0;i<16;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i; dut.clk=0; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    plain = _trace(tmp_path / "plain", source, collector, driver)
    retained_source = plan.native(scoped=True).text
    assert retained_source.count(HINT) == 1
    native = _trace(tmp_path / "native", retained_source, collector, driver, native=True)
    inline = _trace(tmp_path / "inline", retained_source.replace(HINT, ""),
                    collector, driver, native=True)
    assert native == inline
    for value, (actual, bits, calls), original in zip(range(16), native, plain, strict=True):
        selected = (value + (1 if value & 1 else -1)) & 15
        total = (3 + selected) & 15
        assert actual == original[0] == (total << 4) | (total if aliased else selected)
        assert calls > 0
        roles = {plan.events[item.source_event].role for index, item in enumerate(plan.invocations)
                 if bits & (1 << index)}
        assert ("if-true" if value & 1 else "if-false") in roles
        assert ("if-false" if value & 1 else "if-true") not in roles


@pytest.mark.parametrize("body,expected", [
    ("choose(v,t); result=t;", 2),
    ("choose(v,state); result=state;", 1),
    ("choose(v,t); result=t+state;", 1),
])
def test_copy_output_certificate_is_transitive_and_frame_local(tmp_path, body, expected):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      logic [3:0] state;
      task automatic choose(input logic [3:0] x, output logic [3:0] y);
        if(x[0]) y=x+1; else y=x-1;
      endtask
      task automatic outer(input logic [3:0] v, output logic [3:0] result);
        logic [3:0] t; {body}
      endtask
      always @(posedge clk) outer(a,q);
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    assert plan.native().text.count(HINT) == expected
    # Output formals must not acquire the value-call certificate consumed by
    # deferred-write analysis or the loop-counter's no-outward-writes proof.
    assert not plan.declarations.closed_value_calls
    assert not plan.declarations.frame_local_calls
