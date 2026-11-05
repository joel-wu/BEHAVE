"""Typed helper arguments retain their language types and evaluation order."""

from dataclasses import replace

import pytest

from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_native_helpers import _source
from .test_rtl_source_declarations import _documents
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang", "verilator", "make")

CASES = {
    "implicit_scalar": (
        "v", "if(v) adjust=$bits(v); else adjust=0;", "a", lambda a: a & 1),
    "inherited_input": (
        "input [3:0] v, w", "if(v[0]) adjust=w; else adjust=v;",
        "a,a+1", lambda a: (a + (a & 1)) & 15),
    "implicit_typed_input": (
        "logic [3:0] v, logic [3:0] w", "if(v[0]) adjust=w; else adjust=v;",
        "a,a+1", lambda a: (a + (a & 1)) & 15),
}


@pytest.mark.parametrize("name", CASES)
def test_native_context_preserves_implicit_and_inherited_input_types(tmp_path, name):
    ports, body, arguments, expected = CASES[name]
    source = _source(
        f"function automatic [3:0] adjust({ports}); {body} endfunction",
        body=f"q<=adjust({arguments});")
    plan, _, _ = _prepare(tmp_path, source)
    assert not plan.native_issues, plan.native_issues
    assert [(event.role, event.choice) for event in plan.events
            if event.role in {"process", "if-true", "if-false"}] == [
        ("process", None), ("if-true", "true"), ("if-false", "false")]
    collector, _ = _collector(plan)
    driver = '''
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0;dut.a=a;dut.eval();dut.clk=1;dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    plain = _trace(tmp_path / "plain", source,
                   replace(collector, bindings=replace(collector.bindings, coordinates=())), driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [value for value, _, _ in plain] == [expected(a) for a in range(16)]
    assert [value for value, _, _ in native] == [expected(a) for a in range(16)]
    assert all(bits == calls == 0 for _, bits, calls in plain)
    for a, (_, bits, calls) in enumerate(native):
        roles = {plan.events[item.source_event].role for index, item in enumerate(plan.invocations)
                 if bits & (1 << index)}
        assert {"process", "if-true" if a & 1 else "if-false"} <= roles
        assert ("if-false" if a & 1 else "if-true") not in roles
        assert calls == bits.bit_count()


@pytest.mark.parametrize("ports", [
    "input [3:0] v, output [3:0] w, z",
    "input [3:0] v, inout [3:0] w, z",
    "input [3:0] v, ref logic [3:0] w, z",
])
def test_typed_noninput_roles_do_not_acquire_a_pure_call_certificate(tmp_path, ports):
    source = _source(
        f"function automatic [3:0] adjust({ports}); if(v[0]) adjust=w; else adjust=z; endfunction",
        body="q<=adjust(a,t,t);", declarations="logic [3:0] t;")
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    plan.native()
    assert not plan.declarations.frame_local_calls
    assert not plan.declarations.closed_value_calls
