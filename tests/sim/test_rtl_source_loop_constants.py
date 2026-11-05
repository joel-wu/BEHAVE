"""Compiler-owned bounds: native coordinates and exact per-instance iterations."""

from dataclasses import replace

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_ir import SourceCompiler
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_ir import _lower, _encode, _run_activation
from .test_rtl_source_locations import _collector, _occurrence_meanings
from .test_rtl_source_net_drivers import _prepare

pytestmark = [pytest.mark.eda("slang", "verilator", "make"), pytest.mark.solver]


@pytest.mark.parametrize("parameters,header,count", [
    ("parameter int N=4;", "int i=0;i<N;i++", 4),
    ("parameter int N=2; localparam int LIMIT=2*N;", "int i=0;i<LIMIT;i++", 4),
    ("parameter int N=5, STEP=2;", "int i=N-1;i>=0;i=i-STEP", 3),
    ("parameter int FIRST=-3, LAST=2;", "int i=FIRST;i<LAST;i++", 5),
    ("parameter int unsigned N=4;", "int i=0;i<N;i++", 4),
    ("parameter longint N=4;", "int i=0;i<N;i++", 4),
    ("parameter int N=0;", "int i=0;i<N;i++", 0),
    ("parameter int N=4;", "int i=0;i<(N-1);i++", 3),
    ("parameter int N=8;", "int i=0;i<$clog2(N);i++", 3),
])
def test_resolved_constant_headers_match_ir_and_smt(tmp_path, parameters, header, count):
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      {parameters}
      always @(posedge clk) begin
        q=a;
        for({header}) q=q+1;
      end
    endmodule"""
    lowered, names, cst, ast = _lower(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues
    assert len(plan.loops[0].variants) == 1
    enc = _encode(lowered)
    for value in range(16):
        post, _, hits = _run_activation(lowered, names, {"a": value, "q": 13}, enc)
        assert post[names["q"]] == (value + count) % 16
        assert {hit.role for hit in hits if hit.path[1] == "loop-activation"} == {
            "nonzero" if count else "zero"}


@pytest.mark.parametrize("parameters,header", [
    ("int N=4;", "int i=0;i<N;i++"),
    ("parameter int N=4;", "int i=0;i<a;i++"),
    ("parameter int unsigned N=4;", "int i=-1;i<N;i++"),
    ("parameter int N=2147483647;", "int i=N;i<=N;i++"),
])
def test_runtime_or_wrapping_bounds_do_not_become_constants(tmp_path, parameters, header):
    source = f"""module M(input [3:0] a,output reg [7:0] q);
      {parameters}
      always_comb begin q=a; for({header}) q=q+1; end
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), _ = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast)
    assert any("loop" in issue for issue in plan.native_issues)
    # Unsupported observation must not remove either declared loop outcome.
    assert plan.declarations.sites_for("loop-activation")[0].outcomes == ("zero", "nonzero")


@pytest.mark.parametrize("bound", ["(N>0 ? N : 0)", "limit(N)"])
def test_constant_proof_retains_condition_events(tmp_path, bound):
    source = f"""module M(input [3:0] a,output logic [7:0] q);
      parameter int N=4;
      function automatic int limit(input int n); return n; endfunction
      always_comb begin q=a; for(int i=0;i<{bound};i++) q=q+1; end
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), _ = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues
    assert plan.loops[0].condition_events
    assert tuple(plan.loops[0].condition_values) == (0, 1, 2, 3, 4)
    assert plan.declarations.sites_for("loop-activation")[0].outcomes == ("zero", "nonzero")
    if bound.startswith("("):
        assert plan.declarations.sites_for("control-choice")
    else:
        assert any(occurrence.calls for occurrence in plan.declarations.occurrences)


@pytest.mark.parametrize("short,long", [(2, 4), (0, 3)])
def test_parameter_overrides_keep_instance_native_and_ir_hits_separate(tmp_path, short, long):
    source = f"""module Part #(parameter int N=4)(input [3:0] a,output logic [7:0] q);
      always_comb begin q=a; for(int i=0;i<N;i++) q=q+1; end
    endmodule
    module M(input [3:0] a,output wire [15:0] q);
      wire [7:0] lo,hi;
      Part #(.N({short})) u_small(a,lo);
      Part #(.N({long})) u_large(a,hi);
      assign q={{hi,lo}};
    endmodule"""
    plan, ast, _ = _prepare(tmp_path, source)
    assert {len(values) for _, values in plan.loops[0].variants} == {short, long}
    with pytest.raises(ValueError, match="per-instance loop ranges"):
        plan.require_compiler_binding()
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    meanings = _occurrence_meanings(source, plan)
    driver = """
      for(unsigned i=0;i<4;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    observed = _trace(tmp_path / "observed", plan.native(scoped=True).text,
                      collector, driver, native=True)
    assert [row[0] for row in original] == [row[0] for row in observed] == [
        ((value + long) << 8) | (value + short) for value in range(4)]
    for name, count in (("u_small", short), ("u_large", long)):
        context, = [context for context in plan.declarations.contexts if context.scope == ("M", name)]
        lowered = compiler.lower(context)
        names = {compiler.symbols[identity]["name"]: target for target, identity in lowered.storage}
        encoded = _encode(lowered)
        for value, (_, bits, _) in enumerate(observed):
            post, _, ir_hits = _run_activation(lowered, names, {"a": value, "q": 0}, encoded)
            assert post[names["q"]] == value + count
            native_hits = collector.bindings.decode(
                {event for event in range(collector.bindings.event_count) if bits >> event & 1}).occurrences
            selected = {hit for hit in native_hits if meanings[hit][0] == "M." + name}
            assert selected == ir_hits


def test_block_local_constant_shadows_module_parameter(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      parameter int N=7;
      always @(posedge clk) begin : local_scope
        localparam int N=2;
        q=a; for(int i=0;i<N;i++) q=q+1;
      end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    post, _, _ = _run_activation(lowered, names, {"a": 3, "q": 0}, _encode(lowered))
    assert post[names["q"]] == 5


def test_shared_grid_does_not_prove_zero_trip_function_return_defined(tmp_path):
    from behave.sim.core.refusal import Refused

    source = """module Part #(parameter int N=3)(input [3:0] a,output logic [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        for(int i=0;i<N;i++) f=v+i;
      endfunction
      always_comb q=f(a);
    endmodule
    module M(input [3:0] a,output wire [15:0] q);
      wire [7:0] lo,hi;
      Part #(.N(0)) u_empty(a,lo);
      Part #(.N(3)) u_nonempty(a,hi);
      assign q={hi,lo};
    endmodule"""
    plan, ast, _ = _prepare(tmp_path, source)
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    empty, = [item for item in plan.declarations.contexts if item.scope == ("M", "u_empty")]
    with pytest.raises(Refused, match="rtl-source-ir-return-undefined"):
        compiler.lower(empty)
    nonempty, = [item for item in plan.declarations.contexts if item.scope == ("M", "u_nonempty")]
    lowered = compiler.lower(nonempty)
    names = {compiler.symbols[identity]["name"]: target for target, identity in lowered.storage}
    post, _, _ = _run_activation(lowered, names, {"a": 3, "q": 0}, _encode(lowered))
    assert post[names["q"]] == 5


@pytest.mark.isolation
@pytest.mark.parametrize("count", [0, 4])
@pytest.mark.parametrize("condition_events", [False, True])
def test_public_baseline_observes_parameterized_helper_loop(tmp_path, count, condition_events):
    from behave.sim import api

    from .test_baseline_public_sequence import REFERENCE, _contract
    from .test_rtl_source_public import SOURCE
    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    source = SOURCE.replace("    function automatic", f"    parameter int N={count};\n    function automatic")
    source = source.replace("      if (v[0]) return v+8'd1;", "      for(int i=0;i<N;i++) v=v+1;")
    if condition_events:
        source = source.replace("i<N", "i<(N>0 ? N : 0)")
    source = source.replace("adjust(8'd0)+a", "adjust(8'd0)+a-N")
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"),
                             api.GenerationSpec(220, 128, 2, 4))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    assert result.evaluation.complete
    goals = {goal.event: goal.id for goal in result.evaluation.catalogue.values()
             if goal.provider == "rtl-structural" and goal.family == "loop-activation"}
    assert set(goals) == {"zero", "nonzero"}
    reached = {name for run in result.evaluation.results for row in run.trace.records
               for name, active in row.hits if active is True}
    assert reached & set(goals.values()) == {goals["nonzero" if count else "zero"]}
    if condition_events:
        choices = {goal.event: goal.id for goal in result.evaluation.catalogue.values()
                   if goal.provider == "rtl-structural" and goal.family == "control-choice"}
        assert set(choices) == {"true", "false"}
        assert reached & set(choices.values()) == {choices["true" if count else "false"]}
    for run in result.evaluation.results[:len(result.generated.stimuli)]:
        assert run.stimulus.depth("in") == 100
        assert run.trace.accepted_count(["in"]) == 100
    assert path.read_text() == source
