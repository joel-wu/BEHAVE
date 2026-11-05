"""Typed helper-boundary optimization preserves original values and every hit."""

import pytest

from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_declarations import _documents
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang")
HINT = " /* verilator no_inline_task */ "


@pytest.mark.eda("verilator", "make")
def test_nested_mixed_width_predicates_compile_and_preserve_outputs(tmp_path):
    source = """module M(input clk,input integer n,m,output logic q);
      function automatic logic quant(input integer k,v);
        if(k>=8 || (k>=0 ? ((v<<k)>=3) : (k < -7 ? 0 : (v >= (3<<(-k))))))
          quant=1;
        else quant=0;
      endfunction
      always @(posedge clk) q=quant(n,m);
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = """
      unsigned i=0;
      for(int n=-10;n<=10;n++) for(int m=0;m<32;m++,i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.n=n; dut.m=m; dut.clk=0; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    plain = _trace(tmp_path / "plain", source, collector, driver)
    observed = _trace(tmp_path / "native", plan.native(scoped=True).text,
                      collector, driver, native=True)
    expected = [int(n >= 8 or (m << n >= 3 if n >= 0 else
                              False if n < -7 else m >= 3 << -n))
                for n in range(-10, 11) for m in range(32)]
    assert [row[0] for row in plain] == expected
    assert [row[0] for row in observed] == expected
    assert all(bits and calls for _, bits, calls in observed)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("lifetime", ["automatic ", ""])
def test_deferred_helper_inlining_preserves_values_events_and_callback_counts(tmp_path, lifetime):
    from behave.sim.rtl.native_events import VERILATOR_FLAGS

    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      logic [3:0] state=0;
      function {lifetime}[3:0] inner(input [3:0] v);
        if(v[0]) return v+1; else return v-1;
      endfunction
      function {lifetime}[3:0] outer(input [3:0] v);
        return inner(v)^inner(v+1);
      endfunction
      always @(posedge clk) state<=outer(a);
      always_comb q=outer(state);
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    native = plan.native(scoped=True).text
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
    eager = _trace(tmp_path / "eager", native, collector, driver, native=True,
                   compiler_flags=tuple(flag for flag in VERILATOR_FLAGS
                                        if flag != "-fno-inline-funcs-eager"))
    deferred = _trace(tmp_path / "deferred", native, collector, driver, native=True)
    assert deferred == eager
    assert [row[0] for row in deferred] == [row[0] for row in plain]
    assert all(bits and calls for _, bits, calls in deferred)


@pytest.mark.parametrize("body,expected", [
    ("return v+1;", True),
    ("v=v+1; return v;", True),
    ("return v+K;", True),
    ("int x=v; return x;", True),
    ("return $unsigned(v);", True),
    ("return $realtobits($sin($itor(v)));", True),
    ("return $rtoi($sqrt($itor(v)));", True),
    ("return a;", False),
    ("return state;", False),
    ("state=v; return v;", False),
    ("state<=v; return v;", False),
    ("static int x=0; x++; return x;", False),
    ("return $urandom;", False),
    ("return $time;", False),
])
def test_only_closed_transitive_frames_get_native_hint(tmp_path, body, expected):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      localparam K=3; int state=0;
      function automatic [3:0] inner(input [3:0] v); {body} endfunction
      function automatic [3:0] outer(input [3:0] v); return inner(v); endfunction
      always @(posedge clk) q<=outer(a);
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    # Hints are not an admission rule. All bodies still have the same native
    # caller/context path, including stateful helpers and nonlocal reads.
    assert plan.native().text.count(HINT) == (2 if expected else 0)
    assert bool(plan.declarations.closed_value_calls) is expected
    assert plan.declarations.occurrences


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("constant", ["inner(3)", "outer(3)"])
def test_constant_and_runtime_helpers_keep_one_body_and_native_hits(tmp_path, constant):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      function automatic [3:0] inner(input [3:0] v);
        if(v[0]) return v+1; else return v-1;
      endfunction
      function automatic [3:0] outer(input [3:0] v); return inner(v); endfunction
      localparam [3:0] K={constant};
      always @(posedge clk) q=outer(a)^K;
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    native_source = plan.native(scoped=True).text
    assert native_source.count(HINT) == (1 if constant == "inner(3)" else 0)
    assert native_source.count("function automatic [3:0] inner") == 1
    driver = """
      for(unsigned i=0;i<16;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i; dut.clk=0; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    original = _trace(tmp_path / "original", source, collector, driver)
    native = _trace(tmp_path / "native", native_source, collector, driver, native=True)
    for value, ((actual, bits, calls), plain) in enumerate(zip(native, original, strict=True)):
        assert actual == plain[0] == (((value + (1 if value & 1 else -1)) & 15) ^ 4)
        assert calls > 0
        roles = {plan.events[item.source_event].role for index, item in enumerate(plan.invocations)
                 if bits & (1 << index)}
        assert ("if-true" if value & 1 else "if-false") in roles
        assert ("if-false" if value & 1 else "if-true") not in roles


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("process", ["always @(posedge clk)", "always_comb"])
@pytest.mark.parametrize("compact", [False, True])
def test_retained_and_inline_helpers_have_identical_outputs_bits_and_counts(tmp_path, process, compact):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      function automatic [3:0] inner(input [3:0] v);
        if(v[0]) return v+1; else return v-1;
      endfunction
      function automatic [3:0] outer(input [3:0] v);
        return inner(v)^inner(v+1);
      endfunction
      {process} q=outer(a);
    endmodule"""
    if compact:
        source = source.replace("\n", "").replace(";        ", ";").replace(";      ", ";")
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    native = plan.native(scoped=True).text
    assert native.count(HINT) == 2
    # Unknown helper context must still report the caller's explicit scope,
    # not introduce a module read via the callback's default argument.
    assert "default: __behave_native_event_hit(32'hffffffff, __behave_native_event_call_scope)" in native
    driver = """
      for(unsigned i=0;i<16;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i; dut.clk=0; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    original = _trace(tmp_path / "original", source, collector, driver)
    inline = _trace(tmp_path / "inline", native.replace(HINT, ""), collector, driver, native=True)
    retained = _trace(tmp_path / "retained", native, collector, driver, native=True)
    assert retained == inline
    assert [row[0] for row in retained] == [row[0] for row in original]
    assert all(bits and calls for _, bits, calls in retained)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("process", ["always @(posedge clk)", "always_comb"])
def test_concatenated_local_swap_keeps_outputs_and_native_hits(tmp_path, process):
    # Verilator lowers a simultaneous concat write through __Vconcswap module
    # storage. Retaining the caller's frame must not make that write impure.
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      function automatic [3:0] inner(input [3:0] v);
        logic [1:0] x,y;
        x=v[3:2]; y=v[1:0];
        if(v[0]) {{x,y}}={{y,x}};
        return {{x,y}};
      endfunction
      function automatic [3:0] outer(input [3:0] v); return inner(v); endfunction
      {process} q=outer(a);
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    native = plan.native(scoped=True).text
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
    observed = _trace(tmp_path / "observed", native, collector, driver, native=True)
    assert [row[0] for row in observed] == [row[0] for row in plain]
    for value, (_, bits, calls) in enumerate(observed):
        assert calls > 0
        roles = {plan.events[item.source_event].role for index, item in enumerate(plan.invocations)
                 if bits & (1 << index)}
        assert ("if-true" if value & 1 else "if-false") in roles
        assert ("if-false" if value & 1 else "if-true") not in roles
