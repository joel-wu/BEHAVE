"""Continuous RHS activation in the real native launch, not VPI reevaluation."""

import subprocess
from dataclasses import replace

import pytest

from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _collector_traces, _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_plan import _run
from .test_rtl_source_locations import _occurrence_meanings

pytestmark = pytest.mark.eda("slang", "verilator", "make")

FUNCTION = """function automatic [3:0] choose(input [3:0] v);
    if(v[0]) choose=v+1; else choose=v-1;
endfunction
"""


def _prepare(where, source):
    (where / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(where, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    assert not plan.native_issues
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=1000)
    collector.check_catalogue(manifest_of(plan.declarations, filename="design.sv"))
    return plan, collector, sensitivity


@pytest.mark.parametrize("assignments", ["assign q=choose(a);",
    "assign q=choose(a), n=noise;", "assign {q,n}={choose(a),noise};"])
def test_typed_continuous_context_and_original_driver_are_preserved(tmp_path, assignments):
    source = ("module M(input [3:0] a,input noise,output [3:0] q,output n); "
              + FUNCTION + assignments + " endmodule")
    plan, collector, sensitivity = _prepare(tmp_path, source)
    contexts = {context.process_start for context in plan.declarations.contexts}
    assert {row["start"] for row in sensitivity if row["procedure"] == "ContinuousAssign"} == contexts
    manifest = manifest_of(plan.declarations, filename="design.sv")
    assert len(collector.bindings.projections) == len(manifest.occurrences)
    assert len(manifest) == (7 if ", n=noise" in assignments else 6)
    assert {goal.family for goal in manifest.values()} == {"location", "control-choice"}
    assert "assign" in plan.native().text
    # Compiler analysis still receives precisely the original expressions.
    assert assignments in plan.compiler.text
    with pytest.raises(ValueError, match="compiler helper"):
        plan.require_compiler_binding()


@pytest.mark.parametrize("target", ["q", "q[3:0]", "{q,n}"])
def test_generated_continuous_storage_reelaborates_with_the_shared_frontend(tmp_path, target):
    source = ("module M(input [3:0] a,output [3:0] q,output n); "
              + FUNCTION + f"assign {target}=choose(a); endmodule")
    plan, _, _ = _prepare(tmp_path, source)
    emitted = tmp_path / "emitted"
    emitted.mkdir()
    (emitted / "design.sv").write_text(plan.native(scoped=True).text)
    # The private computation variable is explicitly a variable, not an
    # implicit net. Both frontends must accept its compiler-bound type.
    (_, document), _ = parse_observation_source(emitted, "M", run=_installed)
    assert document["design"]


@pytest.mark.parametrize("producer", ["always_comb", "deferred", "specialized"])
def test_generated_event_storage_reelaborates_with_the_shared_frontend(tmp_path, producer):
    source = {
        "always_comb": """module M(input [7:0] a, output logic [7:0] q);
          always_comb q=a; endmodule""",
        "deferred": """module M(input [7:0] a, output logic [7:0] q, output n);
          always @(posedge a[0]) q[a[3:1]] <= a[4];
          assign n=q[0]; endmodule""",
        "specialized": """module Cell #(parameter W=4)(input [W-1:0] a, output logic [W-1:0] q);
          always_comb q=a; endmodule
          module M(input [7:0] a, output [11:0] q);
          Cell #(.W(4)) narrow(a[3:0],q[3:0]);
          Cell #(.W(8)) wide(a,q[11:4]); endmodule""",
    }[producer]
    plan, _, _ = _prepare(tmp_path, source)
    emitted = tmp_path / "emitted"
    emitted.mkdir()
    (emitted / "design.sv").write_text(plan.native(scoped=True).text)
    (_, document), _ = parse_observation_source(emitted, "M", run=_installed)
    assert document["design"]


@pytest.mark.isolation
@pytest.mark.parametrize("producer", ["direct", "chain", "register"])
def test_public_launch_continuous_helper_has_no_idle_or_unchanged_read_hits(tmp_path, producer):
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer

    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    body = {"direct": "assign q=choose(a);",
            "chain": "wire [3:0] middle; assign middle=a>>1; assign q=choose(middle);",
            "register": "logic [3:0] middle=0; always @(posedge noise) middle<=a; assign q=choose(middle);"}[producer]
    source = "module M(input [3:0] a,input noise,output [3:0] q); " + FUNCTION + body + " endmodule"
    plan, _collector, _ = _prepare(tmp_path, source)
    # Exercise the same source manifest through installed public preparation.
    prepared = prepare_source_observer(source, top="M", execution_sha256="2"*64,
        catalogue=manifest_of(plan.declarations, filename="design.sv"),
        callback_limit=1000, scope_root="")
    assert type(prepared) is PreparedNativeEvents, prepared.as_dict()
    assert prepared.source == plan.native(scoped=True).text
    traces = _collector_traces(tmp_path, source, prepared.source, prepared.plan)
    meanings = _occurrence_meanings(source, plan)
    plain, native = traces["plain"]["samples"], traces["native"]["samples"]
    assert [q for q, _, _ in native] == [q for q, _, _ in plain]
    assert all(events == [] and count == 0 for _, events, count in traces["native"]["idle"])
    assert all(events == [] and count == 0 for _, events, count in plain + traces["plain"]["idle"])
    for index, (value, events, count) in enumerate(native):
        if producer == "direct":
            argument, changed = index // 2, index % 2 == 0
        elif producer == "chain":
            argument, changed = index // 4, index % 4 == 0
        else:
            argument = max(0, (index-1) // 2)
            changed = index == 0 or index >= 3 and index % 2 == 1
        assert value == (argument + (1 if argument & 1 else -1)) & 15
        expected = (_call_meanings("M", "a" if producer == "direct" else "middle", argument)
                    if changed else set())
        written = producer == "register" and index % 2 or producer == "chain" and index % 2 == 0
        if written:
            expected.add(("M", "location", "middle<=a;" if producer == "register" else "middle=a>>1", "entry"))
        actual = prepared.plan.bindings.decode(events).occurrences
        assert {meanings[key] for key in actual} == expected, index
        assert len(actual) == len(expected), index
        assert count == 5 * changed + 2 * bool(written), (index, native)


def _call_meanings(scope, argument, value):
    decision = "if(v[0]) choose=v+1; else choose=v-1;"
    return {(scope, "location", f"q=choose({argument})", "entry"),
            (scope, "location", decision, "entry"),
            (scope, "control-choice", decision, "true" if value & 1 else "false"),
            (scope, "location", "choose=v+1;" if value & 1 else "choose=v-1;", "entry")}


@pytest.mark.parametrize(("expression", "width", "expected"), [
    ("choose(a)+a", 8, lambda a: ((a + (1 if a & 1 else -1)) & 15) + a),
    ("$signed(choose(a)) >>> 1", 8,
     lambda a: (((((a + (1 if a & 1 else -1)) & 15) ^ 8)-8) >> 1) & 255),
    ("a[0] ? choose(a) : choose(a+1)", 4,
     lambda a: ((a if a & 1 else (a+1) & 15) + 1) & 15),
])
def test_continuous_context_preserves_width_signedness_and_lazy_values(tmp_path, expression, width, expected):
    source = f"module M(input [3:0] a,output [{width-1}:0] q); " + FUNCTION + "assign q=" + expression + "; endmodule"
    plan, collector, _ = _prepare(tmp_path, source)
    driver = '''
      for(unsigned i=0;i<32;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i/2;dut.eval(); uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in native] == [row[0] for row in plain] == [expected(i//2) for i in range(32)]
    assert all(bits == calls == 0 for _, bits, calls in native[1::2])


@pytest.mark.eda("iverilog", "vvp")
def test_independent_event_driven_continuous_oracle(tmp_path):
    # This reference retains the original continuous assignment. Its helper
    # hook reads no extra signal, and it never uses the source planner or DPI.
    source = """module M(input [3:0] a,input noise,output [3:0] q);
function automatic [3:0] choose(input [3:0] v);
  $display("CALL %0d",v);
  if(v[0]) choose=v+1; else choose=v-1;
endfunction
wire [3:0] middle; assign middle=a>>1; assign q=choose(middle);
endmodule
module tb;
reg [3:0] a=0; reg noise=0; wire [3:0] q; M dut(a,noise,q);
initial begin
  for(integer i=0;i<32;i++) begin
    a=i/2; noise=i%2; #1;
    $display("SAMPLE %0d %0d",i,q);
    #1; $display("IDLE");
  end
  $finish;
end
endmodule
"""
    (tmp_path / "oracle.sv").write_text(source)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "oracle", "oracle.sv")
    run = subprocess.run(["vvp", "oracle"], cwd=tmp_path, text=True, capture_output=True,
                         check=True, timeout=10)
    pending, samples = [], 0
    for row in run.stdout.splitlines():
        if row.startswith("CALL "):
            pending.append(row.split()[1])
        elif row.startswith("SAMPLE "):
            _, index, value = row.split()
            index, value = int(index), int(value)
            argument = index // 4
            assert value == (argument + (1 if argument & 1 else -1)) & 15
            # Icarus starts the original net at Z before its first drive.
            # Assert that four-state startup explicitly; do not silently drop
            # it or claim it equals Verilator's two-state initialization.
            expected = ["Z", "0"] if index == 0 else [str(argument)] if index % 4 == 0 else []
            assert pending == expected
            pending = []
            samples += 1
        elif row == "IDLE":
            assert not pending
    assert samples == 32 and not pending


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
def test_default_public_baseline_with_continuous_expression(tmp_path, wrong):
    from behave.sim import api
    from tests.support.reference import load

    from .test_settled_protocol import contract

    expression = "in_a-1" if wrong else "in_a+1"
    source = ("module M(input [7:0] in_a, output [7:0] out_y); "
              f"assign out_y=in_a[0] ? {expression} : in_a-1; endmodule")
    path = tmp_path / "M.sv"
    path.write_text(source)
    c = contract()
    reference = load({"behavior.py": b"""class M:
    def reset(self): pass
    def process(self, in_a):
        return {"out_y": (in_a + (1 if in_a & 1 else -1)) & 255}
"""}, config={"class": "M"})
    task = api.admit(c, api.realization_of_rtl_source(source, top="M", contract=c), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"),
                             api.GenerationSpec(3, 3, 0, 0))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation", reference=reference,
        baseline=api.BaselinePolicy(case_limit=5, max_queue_depth=100),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance",
                                             enable_local_guidance=False)))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative
    assert result.evaluation.passed is not wrong, result.as_dict()
    assert result.decision.passed is not wrong
    if wrong:
        assert result.evaluation.mismatch_count
        assert result.evaluation.failure_witness is not None
        assert result.evaluation.unexecuted and not result.evaluation.complete
        assert result.generated.search_summary["stop_reason"] == "baseline-failed"
    else:
        assert result.evaluation.complete
    assert len(handle.frontend_catalogue) == 3
    assert {goal.family for goal in handle.frontend_catalogue.values()} == {"location", "control-choice"}
    assert all(run.trace.source_evidence for run in result.evaluation.results)


@pytest.mark.isolation
def test_hierarchical_continuous_calls_keep_instance_identity(tmp_path):
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer

    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    source = ("module Inner(input [3:0] a,output [3:0] q); " + FUNCTION
              + "assign q=choose(a); endmodule "
              "module M(input [3:0] a,input noise,output [7:0] q); "
              "Inner lo(a,q[3:0]); Inner hi(a^4'b1,q[7:4]); endmodule")
    plan, _, _ = _prepare(tmp_path, source)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    assert len(manifest) == 6 and len(manifest.occurrences) == 12
    prepared = prepare_source_observer(source, top="M", execution_sha256="2"*64,
        catalogue=manifest, callback_limit=1000, scope_root="")
    assert type(prepared) is PreparedNativeEvents, prepared.as_dict()
    traces = _collector_traces(tmp_path, source, prepared.source, prepared.plan)
    names = _occurrence_meanings(source, plan)
    assert len(names) == 12
    assert {meaning[0] for meaning in names.values()} == {"M.lo", "M.hi"}
    for index, ((plain, _, _), (value, events, count)) in enumerate(zip(
            traces["plain"]["samples"], traces["native"]["samples"], strict=True)):
        a = index // 2
        values = {"M.lo": a, "M.hi": a ^ 1}
        adjusted = {name: (v + (1 if v & 1 else -1)) & 15 for name, v in values.items()}
        assert value == plain == adjusted["M.lo"] | adjusted["M.hi"] << 4
        hits = prepared.plan.bindings.decode(events)
        expected = (set().union(*(_call_meanings(name, "a", v) for name, v in values.items()))
                    if index % 2 == 0 else set())
        # The independent original-source hook below confirms that hi first
        # receives 1. The old initial-forever rewrite invented a call with 0
        # before the port expression had propagated; that is not a source hit.
        assert {names[key] for key in hits.occurrences} == expected
        assert len(hits.occurrences) == len(expected)
        assert count == (10 if index % 2 == 0 else 0)
    assert all(not events and count == 0 for _, events, count in traces["native"]["idle"])


def test_original_hierarchy_startup_helper_values(tmp_path):
    """Record original helper arguments without a generated activation guard."""
    source = ("module Inner(input [3:0] a,output [3:0] q); "
              'import "DPI-C" context function void source_oracle(input int value); '
              + FUNCTION.replace("    if(v[0])", "    source_oracle(v); if(v[0])")
              + "assign q=choose(a); endmodule "
              "module M(input [3:0] a,input noise,output [7:0] q); "
              "Inner lo(a,q[3:0]); Inner hi(a^4'b1,q[7:4]); endmodule")
    # An unrelated fixture supplies only the empty transport. It neither parses
    # the reference helper nor generates the independent oracle's callback.
    _plan, collector, _ = _prepare(tmp_path,
        "module M(input [3:0] a,output [3:0] q); assign q=a; endmodule")
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    preamble = '''
#include "svdpi.h"
#include <cstdlib>
#include <string>
unsigned low_values=0, high_values=0;
extern "C" void source_oracle(int value) {
    const std::string scope=svGetNameFromScope(svGetScope());
    if(value<0 || value>15) std::abort();
    (scope.find(".hi")!=std::string::npos ? high_values : low_values) |= 1U<<value;
}
'''
    driver = '''
      if(behave_native_events_begin_v1(0)) return 1;
      dut.a=0; dut.noise=0; dut.eval();
      uint64_t bits=0,calls=0;
      if(behave_native_events_end_v1(0,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<low_values<<" "<<high_values<<"\\n";
    '''
    rows = _trace(tmp_path / "reference", source, flat, driver,
                  native=True, preamble=preamble)
    # The locked two-state model propagates these port expressions before the
    # original continuous helper runs: lo sees 0 and hi sees 1, not 0 then 1.
    assert rows == [(0x2f, 1 << 0, 1 << 1)]
