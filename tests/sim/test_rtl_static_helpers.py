"""Native callbacks preserve static helper storage, not just ROM output values."""
from dataclasses import replace

import pytest

from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_continuous_events import _prepare
from .test_rtl_source_declarations import _documents

pytestmark = pytest.mark.eda("slang", "verilator", "make")


@pytest.mark.parametrize("lifetime", ["", "static ", "automatic "])
@pytest.mark.parametrize("ansi", [False, True])
def test_rom_function_lifetime_and_formals_keep_values_and_real_case_hits(tmp_path, lifetime, ansi):
    prototype = ("(input [3:0] v);" if ansi else "; input [3:0] v;")
    source = f"""module M(input [3:0] a,input noise,output [3:0] q);
      function {lifetime}[3:0] lookup{prototype}
        case(v) 0:lookup=9; 1:lookup=7; 2:lookup=5; default:lookup=0; endcase
      endfunction
      assign q=lookup(a);
    endmodule"""
    plan, collector, _ = _prepare(tmp_path, source)
    native_source = plan.native(scoped=True).text
    assert native_source.count(f"function {lifetime}[3:0] lookup") == 1
    driver = '''
      for(unsigned i=0;i<32;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i/2; dut.noise=i%2; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", native_source, collector, driver, native=True)
    expected = [({0: 9, 1: 7, 2: 5}.get(i // 2, 0)) for i in range(32)]
    assert [r[0] for r in plain] == [r[0] for r in native] == expected
    assert all(bits == count == 0 for _, bits, count in plain)
    for i, (_, bits, count) in enumerate(native):
        roles = [plan.events[item.source_event].role
                 for j, item in enumerate(plan.invocations) if bits & (1 << j)]
        if i % 2:
            assert bits == count == 0  # unrelated input is not a function activation
        else:
            assert roles.count("case-arm") == 1
            assert count > 0


@pytest.mark.parametrize("lifetime", ["", "static "])
@pytest.mark.parametrize("kind", ["function", "task"])
def test_static_helper_calls_share_original_persistent_storage(tmp_path, lifetime, kind):
    if kind == "function":
        helper = f"""function {lifetime}[7:0] f(input [3:0] v);
          integer n=0;
          n=n+1; if(v[0]) f=n+v; else f=n-v;
        endfunction"""
        call = "q=f(a); q=q+f(a^1);"
    else:
        helper = f"""task {lifetime}f(input [3:0] v, output [7:0] y);
          integer n=0;
          n=n+1; if(v[0]) y=n+v; else y=n-v;
        endtask"""
        call = "f(a,t); q=t; f(a^1,t); q=q+t;"
    source = ("module M(input clk,input [3:0] a,output reg [7:0] q); "
              "reg [7:0] t; " + helper + " always @(posedge clk) begin " + call + " end endmodule")
    plan, collector, _ = _prepare(tmp_path, source)
    native_source = plan.native(scoped=True).text
    assert f"{kind} {lifetime}" in native_source
    assert "automatic" not in native_source.split("integer n=0;")[0].split(kind)[-1]
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
    native = _trace(tmp_path / "native", native_source, collector, driver, native=True)
    expected = [(4*a+3 + (a if a & 1 else -a)
                 + ((a^1) if (a^1) & 1 else -(a^1))) & 255 for a in range(16)]
    assert [r[0] for r in plain] == [r[0] for r in native] == expected
    for _, bits, count in native:
        roles = [plan.events[item.source_event].role
                 for j, item in enumerate(plan.invocations) if bits & (1 << j)]
        assert roles.count("if-true") == roles.count("if-false") == 1
        assert count > 0


@pytest.mark.parametrize("lifetime", ["", "static "])
def test_static_recursion_and_suspension_still_refuse(tmp_path, lifetime):
    sources = [
        f"function {lifetime}f(input v); if(v) f=f(v-1); else f=0; endfunction",
        f"task {lifetime}f(input v); #1 if(v) q=1; endtask",
        f"task pause_task(); #1; endtask "
        f"task {lifetime}f(input v); if(v) pause_task(); endtask",
    ]
    for index, helper in enumerate(sources):
        call = "q=f(a);" if index == 0 else "f(a);"
        source = f"module M(input clk,a,output reg q); {helper} always @(posedge clk) {call} endmodule"
        cst, ast = _documents(tmp_path / str(index), source)
        with pytest.raises(ValueError, match="recursive|non-suspending"):
            plan = plan_source(source, cst, semantic_document=ast)
            plan.native()
