"""One helper body and state, with observation scoped to runtime invocations."""

from dataclasses import replace

import pytest

from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.source_calls import HelperContexts
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_declarations import _documents

pytestmark = pytest.mark.eda("slang")

HELPER = """function automatic [3:0] f(input [3:0] v);
  if(v[0]) f=v+1; else f=v-1;
endfunction
function automatic [3:0] outer(input [3:0] v); outer=f(v); endfunction
"""

UNOBSERVED = {
    "unused": "function automatic [3:0] unused(input [3:0] v); unused=f(v); endfunction",
    "unused_named": "function automatic [3:0] unused(input [3:0] v); unused=f(.v(v)); endfunction",
    "initial": "initial q=outer(3);",
    "final": "final q=outer(3);",
    "final_loop": "final begin int i; i=0; while(i<3) begin q=outer(i); i++; end end",
    "type_query": "localparam WIDTH=$bits(f(3));",
    "constant_call": "localparam [3:0] K=outer(3); initial q=K;",
}


def _source(extra, helper=HELPER):
    return ("module M(input clk,input [3:0] a,output reg [3:0] q);\n"
            + helper + extra + "\nalways @(posedge clk) q=outer(a); endmodule")


@pytest.mark.parametrize("extra", UNOBSERVED.values(), ids=UNOBSERVED)
def test_only_selected_runtime_calls_require_event_contexts(tmp_path, extra):
    source = _source(extra)
    cst, ast = _documents(tmp_path, source)
    # Qualify native call ownership, not a symbolic iteration grid for a final
    # block. Production native observation uses source occurrences as well.
    plan = plan_source(source, cst, semantic_document=ast, enumerate_iterations=False)
    assert not plan.native_issues, plan.native_issues
    assert len(plan.calls) == 2
    assert [source[call.start:call.end] for call in plan.calls] == ["outer(a)", "f(v)"]
    # The root call statement has its own location entry; only events inside
    # the helper need an invocation context.
    assert all(event.calls or event.role in {"process", "location"} for event in plan.events)
    plan.native()


def test_missing_selected_runtime_call_is_still_an_error(tmp_path, monkeypatch):
    source = _source(UNOBSERVED["unused"])
    cst, ast = _documents(tmp_path, source)
    apply = HelperContexts.apply

    def missing_call(self, edits, issues):
        # Simulate an incomplete rewrite, not a construction-only invocation.
        key = next(key for key, (_node, contexts) in self._calls.items()
                   if contexts[0][0] is None)
        del self._calls[key]
        return apply(self, edits, issues)

    monkeypatch.setattr(HelperContexts, "apply", missing_call)
    plan = plan_source(source, cst, semantic_document=ast)
    with pytest.raises(ValueError, match="helper call outside an observed event body: outer"):
        plan.native()


@pytest.mark.parametrize("scope", ["helper", "process"])
def test_automatic_initializer_is_a_runtime_call_not_construction(tmp_path, scope):
    helper = (HELPER.replace("outer=f(v);", "logic [3:0] t=f(v); outer=t;")
              if scope == "helper" else HELPER)
    source = _source(UNOBSERVED["initial"], helper)
    if scope == "process":
        source = source.replace("q=outer(a);",
                                "begin automatic logic [3:0] t=outer(a); q=t; end")
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues, plan.native_issues
    assert [source[call.start:call.end] for call in plan.calls] == ["outer(a)", "f(v)"]
    assert len(plan.declarations.sites_for("control-choice")) == 1


def test_static_local_initializer_does_not_acquire_runtime_goals(tmp_path):
    helper = HELPER.replace("outer=f(v);", "static logic [3:0] t=f(3); outer=f(v)+t;")
    source = _source("", helper)
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues, plan.native_issues
    assert [source[call.start:call.end] for call in plan.calls] == ["outer(a)", "f(v)"]
    assert len(plan.declarations.sites_for("control-choice")) == 1


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("shape", ["initial", "final", "constant_call", "static_local",
                                   "expression", "assertion", "initial_assertion_failure",
                                   "automatic_initializer", "unused_named"])
def test_construction_and_runtime_share_original_values_and_state(tmp_path, shape):
    helper = HELPER
    extra = UNOBSERVED.get(shape, UNOBSERVED["initial"])
    if shape == "static_local":
        helper = helper.replace("if(v[0]) f=v+1; else f=v-1;",
                                "static int n=0; n=n+1; if(v[0]) f=v+n; else f=v-n;")
    elif shape == "automatic_initializer":
        helper = helper.replace("outer=f(v);", "logic [3:0] t=f(v); outer=t;")
    elif shape == "expression":
        helper = helper.replace("if(v[0]) f=v+1; else f=v-1;", "f=v[0] ? v+1 : v-1;")
    elif shape in {"assertion", "initial_assertion_failure"}:
        # A recovering assertion must still run both original action arms.
        helper = helper.replace("if(v[0]) f=v+1; else f=v-1;",
                                "assert(v[0]) f=v+1; else f=v-1;")
        if shape == "initial_assertion_failure":
            extra = "initial q=outer(2);"
    source = _source(extra, helper)
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues, plan.native_issues
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=100)
    driver = '''
      if(behave_native_events_begin_v1(0)) return 1;
      dut.clk=0; dut.a=0; dut.eval();
      uint64_t bits=0,calls=0;
      if(behave_native_events_end_v1(0,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a+1)) return 3;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        if(behave_native_events_end_v1(a+1,&bits,1,&calls)) return 4;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector,
                    driver, native=True)
    assert len(plain) == len(native) == 17
    assert [row[0] for row in plain] == [row[0] for row in native]
    assert native[0][0] == (0 if shape in {"final", "unused_named"} else
                            1 if shape == "initial_assertion_failure" else 4)
    assert native[0][1:] == (0, 0)  # construction is not a runtime goal hit
    assert all(bits == calls == 0 for _, bits, calls in plain)
    for a, (value, bits, calls) in enumerate(native[1:]):
        delta = a + 2 if shape == "static_local" else 1
        assert value == (a + (delta if a & 1 else -delta)) & 15
        roles = {plan.events[item.source_event].role for i, item in enumerate(plan.invocations)
                 if bits & (1 << i) and plan.events[item.source_event].role != "location"}
        if shape in {"assertion", "initial_assertion_failure"}:
            expected = {"process"} | ({"failure"} if not a & 1 else set())
        else:
            prefix = "expression" if shape == "expression" else "if"
            expected = {"process", prefix + ("-true" if a & 1 else "-false")}
        assert roles == expected
        assert calls == bits.bit_count()


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("scope", ["helper", "process", "lazy_helper"])
def test_compiler_scheduling_tracks_runtime_initializer_reads(tmp_path, scope):
    """Use production compiler dependencies, including a helper's captured read.

    ``b`` is read only by an automatic initializer. Changing it must activate
    the process; construction-only calls and unrelated/identical input updates
    must not manufacture runtime events. The lazy variant must call only the
    selected helper, not both compiler-expanded alternatives.
    """
    from .test_rtl_continuous_events import _prepare

    expression = "v[0] ? f(b) : f(~b)" if scope == "lazy_helper" else "f(v ^ b)"
    helper = HELPER.replace("outer=f(v);", f"logic [3:0] t={expression}; outer=t;")
    process = "q=outer(a);"
    if scope == "process":
        helper = HELPER
        process = "begin automatic logic [3:0] t=outer(a ^ b); q=t; end"
    source = ("module M(input [3:0] a,b,input noise,output logic [3:0] q,seed);\n"
              + helper + "initial seed=f(3);\nalways_comb " + process + " endmodule")
    plan, collector, sensitivity = _prepare(tmp_path, source)
    assert {read["symbol"].split(".")[-1] for row in sensitivity
            for read in row["reads"]} == {"a", "b"}
    inputs = [(0, 0, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1),
              (0, 1, 0), (1, 1, 0), (1, 3, 0), (1, 3, 0)]
    rows = ",".join("{" + ",".join(map(str, values)) + "}" for values in inputs)
    driver = f"const unsigned inputs[][3]={{{rows}}};" + '''
      for(unsigned i=0;i<sizeof(inputs)/sizeof(inputs[0]);i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=inputs[i][0]; dut.b=inputs[i][1]; dut.noise=inputs[i][2]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<unsigned(dut.seed)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector,
                    driver, native=True)
    expected_values = []
    for a, b, _noise in inputs:
        argument = (b if a & 1 else ~b) & 15 if scope == "lazy_helper" else a ^ b
        expected_values.append(((argument + (1 if argument & 1 else -1)) & 15, 4))
    assert [row[:2] for row in plain] == [row[:2] for row in native] == expected_values
    assert all(bits == count == 0 for _, _, bits, count in plain)
    for index, (a, b, _noise) in enumerate(inputs):
        _, _, bits, count = native[index]
        if index and inputs[index-1][:2] == (a, b):
            assert bits == count == 0, (index, native)
            continue
        argument = (b if a & 1 else ~b) & 15 if scope == "lazy_helper" else a ^ b
        roles = [plan.events[item.source_event].role for i, item in enumerate(plan.invocations)
                 if bits & (1 << i) and plan.events[item.source_event].role != "location"]
        expected = {"process", "if-true" if argument & 1 else "if-false"}
        if scope == "lazy_helper":
            expected.add("expression-true" if a & 1 else "expression-false")
        assert set(roles) == expected and len(roles) == len(expected), (index, native)
        assert count == bits.bit_count(), (index, native)


@pytest.mark.eda("verilator", "make")
@pytest.mark.isolation
@pytest.mark.parametrize("scope", ["helper", "process"])
def test_isolated_initializer_observation_uses_compiler_dependencies(tmp_path, scope):
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer
    from behave.sim.rtl.source_goals import manifest_of

    from .test_rtl_combinational_events import _collector_traces
    from .test_rtl_continuous_events import _prepare
    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    helper = HELPER.replace("outer=f(v);", "logic [3:0] t=f(v ^ {3'b0,noise}); outer=t;")
    process = "q=outer(a)+SEED;"
    if scope == "process":
        helper = HELPER
        process = "begin automatic logic [3:0] t=outer(a ^ {3'b0,noise}); q=t+SEED; end"
    source = ("module M(input [3:0] a,input noise,output logic [3:0] q);\n" + helper
              + "localparam [3:0] SEED=f(3);\nalways_comb " + process + " endmodule")
    plan, _collector, _sensitivity = _prepare(tmp_path, source)
    # This verifies the native producer with an explicit source declaration;
    # it does not change the default RTLIL catalogue or establish its mapping.
    prepared = prepare_source_observer(source, top="M", execution_sha256="2" * 64,
        catalogue=manifest_of(plan.declarations, filename="design.sv"),
        callback_limit=1000, scope_root="")
    assert type(prepared) is PreparedNativeEvents, prepared.as_dict()
    assert prepared.source == plan.native(scoped=True).text
    traces = _collector_traces(tmp_path, source, prepared.source, prepared.plan)
    plain, native = traces["plain"]["samples"], traces["native"]["samples"]
    assert len(plain) == len(native) == 32
    for index, ((plain_value, _, _), (value, events, count)) in enumerate(zip(plain, native, strict=True)):
        argument = (index // 2) ^ (index % 2)
        expected = (argument + (1 if argument & 1 else -1) + 4) & 15
        assert plain_value == value == expected
        # noise is a genuine read here, unlike the unrelated-input fixture.
        # Every sample changes a read; equal output values still need events.
        roles = [plan.events[plan.invocations[event].source_event].role for event in events]
        assert set(roles) - {"location"} == {"process", "if-true" if argument & 1 else "if-false"}
        assert roles.count("process") == 1
        assert count == len(events)
    assert len(traces["native"]["idle"]) == 31
    assert all(not events and count == 0 for _, events, count in traces["native"]["idle"])
    assert all(not events and count == 0 for _, events, count in plain + traces["plain"]["idle"])
