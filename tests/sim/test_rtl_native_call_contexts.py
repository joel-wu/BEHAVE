"""Native call IDs are distinct from the proof of compiler-copy identity."""

import json
import re
from dataclasses import replace

import pytest

from behave.sim.rtl.catalogue import sites_of
from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.source_bindings import bind_source_events, bind_source_execution
from behave.sim.rtl.source_goals import manifest_of, occurrence_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_native_helpers import FUNCTION, _source
from .test_rtl_source_declarations import _documents
from .test_rtl_source_plan import _compile

pytestmark = pytest.mark.eda("slang", "yosys", "verilator", "make")


def _adjust(value):
    return (value + (1 if value & 1 else -1)) & 15


CASES = {
    "repeated": ("q<=adjust(a)+adjust(a+1);", 6, 11,
                 lambda a: [(0, a), (1, (a+1) & 15)],
                 lambda a: (_adjust(a) + _adjust((a+1) & 15)) & 15),
    "nested": ("q<=outer(a)+outer(a+1);", 7, 13,
               lambda a: [(0, a), (1, (a+1) & 15)],
               lambda a: (_adjust(a) + _adjust((a+1) & 15)) & 15),
    "ternary": ("q<=a[0] ? adjust(a) : adjust(a+1);", 8, 13,
                lambda a: [(0, a)] if a & 1 else [(1, (a+1) & 15)],
                lambda a: _adjust(a if a & 1 else (a+1) & 15)),
    "short_and": ("q<=a[0] && adjust(a);", 10, 10,
                  lambda a: [(0, a)] if a & 1 else [],
                  lambda a: int(bool(a & 1) and bool(_adjust(a)))),
    "short_or": ("q<=a[0] || adjust(a);", 10, 10,
                 lambda a: [] if a & 1 else [(0, a)],
                 lambda a: int(bool(a & 1) or bool(_adjust(a)))),
}


@pytest.mark.parametrize("name", CASES)
def test_call_contexts_record_actual_evaluation_without_compiler_guessing(tmp_path, name):
    body, goals, occurrences, called, expected_output = CASES[name]
    functions = FUNCTION
    if name == "nested":
        functions += "function automatic [3:0] outer(input [3:0] v); outer=adjust(v); endfunction"
    source = _source(functions, body=body)
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues
    assert plan.binding_issues
    manifest = manifest_of(plan.declarations, filename="design.sv")
    assert len(manifest) == goals and len(manifest.occurrences) == occurrences
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100)
    collector.check_catalogue(manifest)

    driver = '''
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0;dut.a=a;dut.eval();dut.clk=1;dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    # No callback is used by the uninstrumented design. The flat collector
    # there avoids requiring a simulator DPI scope that cannot be referenced.
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert len(plain) == len(native) == 16
    assert all(bits == calls == 0 for _, bits, calls in plain)

    # The oracle names literal call sites, not generated callback indices.
    # Equal source goals still have separate invocation occurrences.
    body_start = source.index(body)
    calls = [((body_start + match.start(), body_start + match.end()),)
             for match in re.finditer(r"(?:adjust|outer)\(a(?:\+1)?\)", body)]
    if name == "nested":
        start = source.index("adjust(v)")
        calls = [(*call, (start, start + len("adjust(v)"))) for call in calls]
    branch_start = source.index("if(v[0])")
    branch_site, = [i for i, site in enumerate(plan.declarations.sites)
                    if site.start == branch_start and site.schema == "control-choice"]
    helper_keys = {
        occurrence_of(plan.declarations.sites[item.site], item, outcome, filename="design.sv"):
        (item.calls, outcome)
        for item in plan.declarations.occurrences if item.site == branch_site
        for outcome in ("true", "false")
    }
    assert len(helper_keys) == 2*len(calls)
    for a, (before, after) in enumerate(zip(plain, native, strict=True)):
        assert before[0] == after[0] == expected_output(a)
        bits = after[1]
        seen = {i for i in range(collector.bindings.event_count) if bits >> i & 1}
        hits = collector.bindings.decode(seen)
        assert {helper_keys[key] for key in hits.occurrences & helper_keys.keys()} == {
            (calls[index], "true" if value & 1 else "false") for index, value in called(a)}

    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    for bind in (bind_source_events, bind_source_execution):
        with pytest.raises(ValueError, match="compiler helper"):
            bind(plan, original, annotated, filename="design.sv")

    if name in {"short_and", "short_or"}:
        # This is a semantic mismatch, not just an absent call-copy label.
        # Yosys hoists this pure helper's computation: its two RTLIL arms
        # execute under the process root, without the source call's lazy guard.
        # Read that *declared compiler rule*, not a production snapshot observer.
        arms = [site for site in sites_of(original) if site.kind == "case-arm"]
        assert len(arms) == 2
        assert {site.signal for site in arms} == {r"\a [0]"}
        assert {site.outcome for site in arms} == {"1'1", "default"}
        assert all(not site.activation and site.triggers == (("posedge", r"\clk"),)
                   for site in arms)
        comparison = []
        for a, (_output, bits, _count) in enumerate(native):
            seen = {i for i in range(collector.bindings.event_count) if bits >> i & 1}
            native_outcomes = sorted(helper_keys[key][1] for key in
                                     collector.bindings.decode(seen).occurrences & helper_keys.keys())
            compiler_outcomes = ["true" if a & 1 else "false"]
            comparison.append({"input": a, "native": native_outcomes,
                               "rtlil_rule": compiler_outcomes})
        assert [row["input"] for row in comparison if row["native"] != row["rtlil_rule"]] == [
            a for a in range(16) if (bool(a & 1) if name == "short_or" else not a & 1)]
        (tmp_path / "goal-levels.json").write_text(json.dumps(comparison, indent=2))


def test_constant_helper_retains_source_events_erased_from_compiler_catalogue(tmp_path):
    source = _source(body="q<=adjust(0);")
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    # One outer assignment and the helper's if/two assignments, plus both
    # source outcomes. Constant folding must not remove any of these six rows.
    assert len(manifest) == len(manifest.occurrences) == 6
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100)
    collector.check_catalogue(manifest)
    original = _compile(tmp_path / "original", source)
    assert not [site for site in sites_of(original) if site.kind == "case-arm"]

    driver = '''
      for(unsigned i=0;i<3;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.clk=0;dut.eval();dut.clk=1;dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    executed = {source.index(text) for text in ("q<=adjust(0);", "if(v[0])", "adjust = v-1;")}
    expected = {goal.id for goal in manifest.values() if goal.event == "false"
                or goal.family == "location" and goal.where("start") in executed}
    assert len(expected) == 4 and len(plain) == len(native) == 3
    for before, after in zip(plain, native, strict=True):
        assert before == (15, 0, 0) and after[0] == 15
        seen = {i for i in range(collector.bindings.event_count) if after[1] >> i & 1}
        assert collector.bindings.decode(seen).goals == expected
        assert after[2] == 5  # process, three statement entries, actual false arm


def test_repeated_parameterless_tasks_observe_each_intermediate_state(tmp_path):
    """No argument/result wire can stand in for the identity of these calls."""
    body = "q=a; advance(); advance();"
    source = _source("""task automatic advance();
        if(q[0]) q=q+1; else q=q+2;
    endtask""", body=body)
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues
    manifest = manifest_of(plan.declarations, filename="design.sv")
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100)
    collector.check_catalogue(manifest)

    body_start = source.index(body)
    calls = [((body_start + match.start(), body_start + match.end()),)
             for match in re.finditer(r"advance\(\)", body)]
    branch_start = source.index("if(q[0])")
    branch_site, = [i for i, site in enumerate(plan.declarations.sites)
                    if site.start == branch_start and site.schema == "control-choice"]
    helper_keys = {
        occurrence_of(plan.declarations.sites[item.site], item, outcome, filename="design.sv"):
        (item.calls, outcome)
        for item in plan.declarations.occurrences if item.site == branch_site
        for outcome in ("true", "false")
    }
    assert len(calls) == 2 and len(helper_keys) == 4
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
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert len(plain) == len(native) == 16
    for a, (before, after) in enumerate(zip(plain, native, strict=True)):
        intermediate = (a + (1 if a & 1 else 2)) & 15
        result = (intermediate + 2) & 15  # first call always leaves an even value
        assert before == (result, 0, 0) and after[0] == result
        assert after[2] == 10  # process, three caller statements, three events per call
        seen = {i for i in range(collector.bindings.event_count) if after[1] >> i & 1}
        hits = collector.bindings.decode(seen).occurrences
        assert {helper_keys[key] for key in hits & helper_keys.keys()} == {
            (calls[0], "true" if a & 1 else "false"), (calls[1], "false")}

    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    # Native source correctness does not authorize assigning these callbacks
    # to synthesized copies by order or by a generated wire-name convention.
    for bind in (bind_source_events, bind_source_execution):
        with pytest.raises(ValueError, match="compiler helper"):
            bind(plan, original, annotated, filename="design.sv")


@pytest.mark.parametrize("also_clocked", [False, True])
def test_continuous_call_requires_compiler_dependency_evidence(tmp_path, also_clocked):
    source = _source(body="q<=adjust(a);" if also_clocked else "q<=w;",
                     declarations="wire [3:0] w; assign w=adjust(a);")
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    assert len(manifest) == 7
    assert len(manifest.occurrences) == (12 if also_clocked else 7)
    with pytest.raises(ValueError, match="continuous assignment needs compiler sensitivity"):
        plan.native()


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
@pytest.mark.parametrize("cases", [3, 5])
def test_public_baseline_uses_helper_cast_values_and_real_hits(tmp_path, wrong, cases):
    from behave.sim import api
    from behave.sim.core.catalogue import COVERAGE
    from tests.support.reference import load

    from .test_settled_protocol import contract

    signed = "$unsigned" if wrong else "$signed"
    source = f"""module M(input [7:0] in_a, output reg [7:0] out_y);
      function automatic [7:0] cast(input [7:0] v);
        if(v[0]) cast={signed}(v) >>> 1; else cast=$unsigned(v) >> 1;
      endfunction
      always_comb out_y=cast(in_a);
    endmodule"""
    path = tmp_path / "M.sv"
    path.write_text(source)
    c = contract()
    reference = load({"behavior.py": b"""class M:
    def reset(self): pass
    def process(self, in_a):
        value = in_a-256 if in_a & 1 and in_a & 128 else in_a
        return {"out_y": (value >> 1) & 255}
"""}, config={"class": "M"})
    task = api.admit(c, api.realization_of_rtl_source(source, top="M", contract=c), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"),
                             api.GenerationSpec(3, 3, 0, 0))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation", reference=reference,
        baseline=api.BaselinePolicy(case_limit=cases, max_queue_depth=100),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance",
                                             enable_local_guidance=False)))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    # A wrong design stops at its witnessed failure; its continuous workload is not replayed.
    assert result.evaluation.complete is not wrong and result.decision is not None
    assert result.decision.authoritative and (result.evaluation.failure_witness is not None) is wrong
    assert result.evaluation.passed is not wrong, result.as_dict()
    coverage = {goal.id: goal for goal in handle.frontend_catalogue.values() if goal.cls == COVERAGE}
    assert len(coverage) == 6
    assert {goal.event for goal in coverage.values()} == {"entry", "true", "false"}
    # Low/zero/high deduplicate to constant 0 and 255. The false arm runs at
    # startup, not again for identical zero inputs. Including the counter
    # recipe exercises both arms inside actual input steps.
    false_assignment = source.index("cast=$unsigned(v) >> 1;")
    false_only = {key for key, goal in coverage.items() if goal.event == "false"
                  or goal.family == "location" and goal.where("start") == false_assignment}
    assert len(false_only) == 2
    expected = set(coverage) - (false_only if cases == 3 else set())
    assert set(result.evaluation.hits) & set(coverage) == expected
    assert all(run.trace.source_evidence and run.trace.accepted_count(["in"]) >= 100
               for run in result.evaluation.results)
