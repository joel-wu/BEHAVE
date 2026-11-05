"""Source-owned catalogue, native projection and durable occurrence evidence.

No synthesized-action catalogue injection or fixed historical event numbers.
Source activation IR comparison lives in test_rtl_source_ir / source_batch;
whole-history bounded analysis remains a separate qualification.
"""

import copy
import json
from collections import Counter
from dataclasses import replace

import pytest

from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.refusal import Refused
from behave.sim.rtl.native_events import NativeEventPlan
from behave.sim.rtl.source_declarations import declare_source
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_plan import plan_source
from behave.sim.rtl.source_projection import project_source

from .test_rtl_combinational_events import _trace
from .test_rtl_event_decoding import linear_decode
from .test_rtl_loop_entry_events import HELPER_SOURCE
from .test_rtl_source_declarations import DESIGN, _documents
from .test_rtl_source_locations import _collector, _occurrence_meanings

pytestmark = pytest.mark.eda("slang")


def _prepared(tmp_path, source):
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    collector, manifest = _collector(plan)
    return plan, collector, manifest


def _seen(collector, bits):
    return {i for i in range(collector.bindings.event_count) if bits >> i & 1}


def test_source_catalogue_is_complete_and_does_not_use_compiler_survivors(tmp_path):
    _, ast = _documents(tmp_path, DESIGN)
    declaration = declare_source(DESIGN, ast, filename="design.sv")
    manifest = manifest_of(declaration, filename="design.sv")
    assert Counter(goal.family for goal in manifest.values()) == {
        "location": 8, "control-choice": 8, "boolean-expression": 6, "loop-activation": 2}
    # Seven locations in each instance plus the helper at two calls in each.
    assert len(manifest.occurrences) == 36 + 7*2 + 2*2
    assert {context.scope for context in declaration.contexts} == {
        ("M", "lanes[0]", "inst"), ("M", "lanes[1]", "inst")}
    assert CatalogueManifest.from_dict(json.loads(json.dumps(manifest.as_dict()))) == manifest


@pytest.mark.parametrize("initial,stop,step,expected", [
    (3, 0, "--", {3, 2, 1}), (-3, 0, "++", {-3, -2, -1}), (1, 6, "+=2", {1, 3, 5}),
])
def test_generate_contexts_use_elaborated_values_not_list_indices(tmp_path, initial, stop, step, expected):
    compare = ">" if step == "--" else "<"
    source = f"""module Leaf(input clk,a,output reg q);
      always @(posedge clk) if(a) q<=a;
    endmodule
    module M(input clk,a,output wire q);
      for(genvar g={initial};g{compare}{stop};g{step}) begin: lanes
        wire y; Leaf leaf(clk,a,y);
      end
      assign q=a;
    endmodule"""
    _, ast = _documents(tmp_path, source)
    declaration = declare_source(source, ast, filename="design.sv")
    assert {context.scope for context in declaration.contexts
            if context.process_start in declaration.process_starts} == {
        ("M", f"lanes[{value}]", "leaf") for value in expected}


def test_zero_outer_loop_retains_inner_goals_with_proved_empty_images(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin q<=a;
        for(int i=0;i<0;i++) for(int j=0;j<2;j++) q<=0;
      end
    endmodule"""
    plan, collector, manifest = _prepared(tmp_path, source)
    loops = [item for item in collector.bindings.projections
             if manifest[item.goal].family == "loop-activation"]
    assert len(loops) == 4
    assert sorted(len(item.events) for item in loops) == [0, 0, 1, 1]
    assert NativeEventPlan.from_dict(json.loads(json.dumps(collector.as_dict()))) == collector
    dropped = replace(plan, declared_events=plan.declared_events[:-1])
    with pytest.raises(ValueError, match="no native producer"):
        project_source(dropped, collector.bindings, filename="design.sv").require_complete()


def test_missing_boolean_emitter_stays_declared_not_false(tmp_path):
    source = "module M(input clk,a,b,output reg q); always @(posedge clk) q<=a&&b; endmodule"
    plan, collector, _ = _prepared(tmp_path, source)
    projection = project_source(replace(plan, declared_events=()), collector.bindings, filename="design.sv")
    assert len([g for g in projection.catalogue.values() if g.family == "boolean-expression"]) == 4
    assert all(item.issue for item in projection.outcomes)
    with pytest.raises(ValueError, match="no native producer"):
        projection.require_complete()


@pytest.fixture
def helper_trace(tmp_path):
    plan, collector, manifest = _prepared(tmp_path, HELPER_SOURCE)
    driver = '''
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0;dut.a=a;dut.eval();dut.clk=1;dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    plain = _trace(tmp_path / "plain", HELPER_SOURCE,
                   replace(collector, bindings=replace(collector.bindings, coordinates=())), driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in native] == [row[0] for row in plain]
    assert [row[0] for row in native] == [2*(a if a & 1 else a+1) % 16 for a in range(16)]
    return plan, collector, manifest, native


@pytest.mark.eda("verilator", "make")
def test_real_collector_keeps_all_source_occurrences_and_parent_checks(helper_trace):
    _, collector, manifest, native = helper_trace
    controls = {goal for goal in manifest if manifest[goal].family != "location"}
    assert len(controls) == 4
    assert len([item for item in collector.bindings.projections if item.goal in controls]) == 6
    calls = []
    for a, (_, bits, _) in enumerate(native):
        seen = _seen(collector, bits)
        hits = collector.bindings.decode(seen)
        assert hits == linear_decode(collector.bindings, seen)
        assert {manifest[goal].event for goal in hits.goals & controls} == {
            "nonzero", "true" if a & 1 else "false"}
        calls.append({key for key in hits.occurrences if key.path[1] == "loop-activation"})
        assert len(calls[-1]) == 1
    assert calls[0] != calls[1]  # Same helper, different source call occurrences.
    bad = collector.as_dict()
    bad["projections"].append(copy.deepcopy(bad["projections"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        NativeEventPlan.from_dict(bad)
    nested = next(event for item in collector.bindings.projections
                  for event, parents in zip(item.events, item.ancestors) if parents)
    with pytest.raises(ValueError, match="enclosing"):
        collector.bindings.decode({nested})
    omitted = replace(collector, bindings=replace(collector.bindings,
                                                projections=collector.bindings.projections[:-1]))
    with pytest.raises(Refused, match="missing-occurrence"):
        omitted.check_catalogue(manifest)


@pytest.mark.isolation
def test_public_native_preparation_accepts_the_original_source_denominator(tmp_path):
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer

    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    _, collector, manifest = _prepared(tmp_path, HELPER_SOURCE)
    prepared = prepare_source_observer(HELPER_SOURCE, top="M", catalogue=manifest,
                                      execution_sha256="2"*64, callback_limit=1000)
    assert isinstance(prepared, PreparedNativeEvents), prepared
    prepared.plan.check_catalogue(manifest)
    assert prepared.plan.bindings.catalogue_pairs == collector.bindings.catalogue_pairs


@pytest.mark.eda("verilator", "make", "g++")
def test_source_outcomes_survive_the_shared_driver_record_and_audit(tmp_path, helper_trace):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.core.stimulus import AuditedTrace, EventSlot, SlotRecord, Stimulus
    from behave.sim.rtl.native_observation import NativeObservation, read_native_evidence

    from .test_rtl_native_events import _load

    _, collector, manifest, native = helper_trace
    stimulus = Stimulus({}, [EventSlot(), EventSlot()])
    library = _load(tmp_path, collector)
    observer = NativeObservation(collector, stimulus, library=library)
    observer.start_slots()
    vectors, expected = [], []
    for index, (_, bits, _) in enumerate(native[:2]):
        if index:
            observer.next_slot()
        observer.sampled()
        events = _seen(collector, bits)
        for event in sorted(events):
            library.__behave_native_event_hit(event)
        observer.clocked()
        vectors.append(observer.successor())
        expected.append(linear_decode(collector.bindings, events))
    trace = AuditedTrace(stimulus, records=[SlotRecord(hits=vector) for vector in vectors],
                         source_evidence=observer.finish())
    restored = AuditedTrace.from_dict(trace.as_dict())
    observed = read_native_evidence(thaw_record_value(restored.source_evidence), stimulus,
                                    manifest, expected_plan=collector)
    assert observed == tuple(expected)
    assert observed[0].occurrences != observed[1].occurrences
    assert set(observer.catalogue) == set(manifest)
    assert restored.fingerprint == trace.fingerprint


def test_source_registration_keeps_local_guidance_separate_from_bounded_analysis(tmp_path):
    from behave.sim.core.goal_provider import (
        CONCRETE_HIT, DECLARATION, EXACT_BOUNDED_HIT, LOCAL_GUIDANCE,
        RTL_EXACT_BOUNDED_POLICY, RTL_LOCAL_GUIDANCE_POLICY)
    from behave.sim.goal_policy import builtin_frontend_goal_providers

    _, ast = _documents(tmp_path, DESIGN)
    manifest = manifest_of(declare_source(DESIGN, ast, filename="design.sv"), filename="design.sv")
    providers = builtin_frontend_goal_providers(manifest)
    assert {goal for provider in providers for goal in provider.catalogue} == set(manifest)
    for provider in providers:
        assert provider.has(DECLARATION)
        assert provider.has(CONCRETE_HIT)
        # The recorded-boundary local adapter and whole-source bounded analysis
        # are separate capabilities with their own policies; neither implies the other.
        assert provider.binding(LOCAL_GUIDANCE).implementation == RTL_LOCAL_GUIDANCE_POLICY
        assert provider.binding(EXACT_BOUNDED_HIT).implementation == RTL_EXACT_BOUNDED_POLICY


@pytest.mark.cosim
@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("kind", ["loop", "expression", "boolean"])
def test_source_manifest_through_production_driver_and_source_trace(tmp_path, atomic, kind):
    from .test_rtl_native_observation import _check_production_observation

    # Source declaration is now the public default, not an injected mixed policy.
    trace, artifact = _check_production_observation(
        tmp_path, atomic, False, loop=kind == "loop", expression=kind == "expression",
        boolean=kind == "boolean")
    manifest = artifact.frontend_catalogue()
    family = {"loop": "loop-activation", "expression": "control-choice", "boolean": "boolean-expression"}[kind]
    goals = {goal for goal in manifest if manifest[goal].family == family}
    assert len(goals) == (2 if kind == "loop" else 4 if kind == "boolean" else 4 + 2*atomic)
    expected = ([set(), {"nonzero"}, {"nonzero"}] if kind == "loop" else
                [set(), {"true"}, {"false"}] if kind == "boolean" else
                [{"true"}, {"false", "true"}, {"false"}])
    assert [{manifest[goal].event for goal, hit in record.hits if hit is True and goal in goals}
            for record in trace.records] == expected


@pytest.mark.eda("verilator", "make")
def test_hierarchical_source_projection_keeps_instance_identity(tmp_path):
    source = """module Leaf(input clk,a,output reg q);
      always @(posedge clk) if(a) for(int i=0;i<2;i++) q<=a;
    endmodule
    module M(input clk,a,b,output [1:0] q);
      Leaf left(clk,a,q[0]); Leaf right(clk,b,q[1]);
    endmodule"""
    plan, collector, _ = _prepared(tmp_path, source)
    assert {context.scope for context in plan.declarations.contexts} == {("M", "left"), ("M", "right")}
    rows = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, '''
      for(unsigned x=0;x<4;x++) {
        if(behave_native_events_begin_v1(x)) return 1;
        dut.clk=0;dut.a=x&1;dut.b=x>>1;dut.eval();dut.clk=1;dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(x,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    ''', native=True)
    assert [row[0] for row in rows] == [0, 1, 3, 3]
    meanings = _occurrence_meanings(source, plan)
    for x, (_, bits, _) in enumerate(rows):
        hits = collector.bindings.decode(_seen(collector, bits))
        controls = {(scope, family, outcome) for key in hits.occurrences
                    for scope, family, _, outcome in (meanings[key],) if family != "location"}
        expected = set()
        for scope, value in (("M.left", x & 1), ("M.right", x >> 1)):
            expected.add((scope, "control-choice", "true" if value else "false"))
            if value:
                expected.add((scope, "loop-activation", "nonzero"))
        assert controls == expected
