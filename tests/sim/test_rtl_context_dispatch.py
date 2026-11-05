"""Shared observer maps retain every original callback ID and invocation."""

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_dispatch import ContextDispatch
from behave.sim.rtl.source_plan import plan_source
from tests.sim.test_rtl_combinational_events import _trace
from tests.sim.test_rtl_observation_frontend import _installed
from tests.sim.test_rtl_source_locations import _collector


def test_offset_map_is_shared_only_within_its_lexical_definition():
    dispatch = ContextDispatch()
    options = {"definition_start": 10, "context": "caller", "runtime_only": True}
    a = dispatch.select(((7, "41"), (2, "11"), (9, "65")), **options)
    b = dispatch.select(((9, "165"), (7, "141"), (2, "111")), **options)
    assert a == "__behave_native_event_dispatch_0(caller, 32'd11)"
    assert b == "__behave_native_event_dispatch_0(caller, 32'd111)"
    assert len(dispatch.declarations) == 1
    body = dispatch.declarations[0]
    assert body.offset == 10
    assert "2: __behave_native_event_dispatch_0 = base + 32'd0;" in body.text
    assert "7: __behave_native_event_dispatch_0 = base + 32'd30;" in body.text
    assert "9: __behave_native_event_dispatch_0 = base + 32'd54;" in body.text
    assert "default: __behave_native_event_dispatch_0 = 32'hffffffff;" in body.text
    dispatch.select(((7, "41"), (2, "11"), (9, "65")),
                    **(options | {"definition_start": 99}))
    assert len(dispatch.declarations) == 2
    assert dispatch.declarations[1].offset == 99


@pytest.mark.parametrize("alternatives", [((1, "37"),), ((1, "a+3"), (2, "a+5")),
                                         ((1, "0"), (2, "32'hffffffff"))])
def test_nonconstant_or_single_context_keeps_its_dispatch(alternatives):
    dispatch = ContextDispatch()
    assert dispatch.select(alternatives, definition_start=1, context="c", runtime_only=True) is None
    assert dispatch.declarations == []


def test_constant_evaluation_does_not_require_a_retained_runtime_frame():
    dispatch = ContextDispatch()
    dispatch.select(((0, "1"), (1, "2")), definition_start=1, context="c", runtime_only=False)
    assert "no_inline_task" not in dispatch.declarations[0].text


def _source(*, loop, process):
    entries = "\n".join(f"{i}: rom = 8'd{(i * i + 17) % 256};" for i in range(8))
    body = ("int i; fold=0; for(i=0;i<2;i++) fold ^= rom(v+i);" if loop else
            "if(v[0]) fold=rom(v)^rom(v+1); else fold=rom(v+2)+rom(v+3);")
    return f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      function automatic [7:0] rom(input [3:0] v);
        case(v) {entries} default: rom=8'd173; endcase
      endfunction
      function automatic [7:0] fold(input [3:0] v); {body} endfunction
      {process} q=fold(a)^fold(a+1);
    endmodule"""


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("loop,enumerate_iterations", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("process", ["always_comb", "always @(posedge clk)"])
def test_shared_and_legacy_dispatch_have_identical_outputs_bits_and_counts(
        tmp_path, monkeypatch, loop, enumerate_iterations, process):
    source = _source(loop=loop, process=process)
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    arguments = {"semantic_document": ast, "sensitivity_document": sensitivity,
                 "enumerate_iterations": enumerate_iterations}
    shared = plan_source(source, cst, **arguments)
    # Test-owned differential oracle: keep the old per-site dispatch. Production
    # has no alternate observer mode and still uses the same source/goal plan.
    with monkeypatch.context() as legacy:
        legacy.setattr(ContextDispatch, "select", lambda *args, **kwargs: None)
        original = plan_source(source, cst, **arguments)
    assert not shared.native_issues and not original.native_issues
    assert shared.events == original.events
    assert shared.invocations == original.invocations
    assert shared.declarations == original.declarations
    collector, _ = _collector(shared)
    words = collector.word_count
    driver = f"""
      for(unsigned i=0;i<32;i++) {{
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i/2; dut.clk=0; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits[{words}]={{}},calls=0;
        if(behave_native_events_end_v1(i,bits,{words},&calls)) return 2;
        std::cout<<unsigned(dut.q);
        for(unsigned j=0;j<{words};j++) std::cout<<" "<<bits[j];
        std::cout<<" "<<calls<<"\\n";
      }}
    """
    original_text = original.native(scoped=True).text
    shared_text = shared.native(scoped=True).text
    assert len(shared_text) < len(original_text)
    assert "function automatic int unsigned __behave_native_event_dispatch_" in shared_text
    plain = _trace(tmp_path / "plain", source, collector, driver, event_words=words)
    before = _trace(tmp_path / "legacy", original_text, collector, driver,
                    native=True, event_words=words)
    after = _trace(tmp_path / "shared", shared_text, collector, driver,
                   native=True, event_words=words)
    assert after == before
    assert [row[0] for row in after] == [row[0] for row in plain]
    assert any(bits and calls for _, bits, calls in after)
