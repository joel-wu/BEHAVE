"""Full phase/occurrence records, with real transport and real production Drivers."""

import copy
import json
import random
import re
from collections import Counter
from dataclasses import replace

import pytest

from behave.sim import api
from behave.sim.core.driver import check_channel_shape
from behave.sim.core.failure import FailureDisposition, FailureRaised
from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING, HitVector, HitVectorCodec
from behave.sim.core.record_value import thaw_record_value
from behave.sim.core.stimulus import AuditedTrace, SlotRecord
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.execution_policy import execution_policy
from behave.sim.rtl.native_evidence_codec import pack, unpack
from behave.sim.rtl.native_observation import (
    NativeObservation,
    _union_bits,
    read_native_evidence,
    read_native_goal_bits,
)

from .test_rtl_event_decoding import _bits
from .test_rtl_native_events import _load, _plan
from .test_rtl_native_preparation import _catalogue


def test_packed_step_audit_matches_all_occurrences_and_nested_projections():
    from behave.sim.core.catalogue import COVERAGE, CatalogueManifest, Goal
    from behave.sim.rtl.native_events import NativeEventPlan
    from behave.sim.rtl.native_observation import VERSION

    from .test_rtl_event_decoding import bindings, linear_decode

    mapping = bindings()
    plan = NativeEventPlan(mapping, "a" * 64, 100)
    catalogue = CatalogueManifest([Goal(name, COVERAGE) for name in mapping.goal_catalogue],
                                  occurrences=mapping.catalogue_pairs)
    codec = HitVectorCodec(mapping.goal_catalogue)
    stimulus = api.Stimulus({}, [api.EventSlot()])
    for value in range(256):
        seen = [index for index in range(8) if value >> index & 1]
        image = {"version": VERSION, "plan": plan.evidence_image,
                 "execution": execution_policy(native_events=True),
                 "stimulus": stimulus.fingerprint, "completed_steps": 1,
                 "intervals": [{"phase": phase, "callbacks": len(seen) if phase == "clock" else 0,
                                "events": pack(bytes([value if phase == "clock" else 0]))}
                               for phase in ("startup", "control", "clock", "advance")]}
        try:
            expected = linear_decode(mapping, seen)
        except ValueError:
            for read in (read_native_evidence, read_native_goal_bits):
                with pytest.raises(ValueError, match="enclosing control entry"):
                    read(image, stimulus, catalogue, expected_plan=plan)
        else:
            named, = read_native_evidence(image, stimulus, catalogue, expected_plan=plan)
            bits, = read_native_goal_bits(image, stimulus, catalogue, expected_plan=plan)
            assert named == expected
            assert codec.from_bits(bits) == codec.vector(expected.goals)


@pytest.mark.parametrize("width", [0, 1, 7, 8, 9, 129, 2048])
def test_phase_bitmap_union_preserves_every_bit(width):
    rng = random.Random(width)
    for _ in range(20):
        left, right = rng.randbytes(width), rng.randbytes(width)
        assert _union_bits(left, right) == bytes(a | b for a, b in zip(left, right))
        assert _union_bits(left, left) is left
        assert _union_bits(left, bytes(width)) == left
    with pytest.raises(ValueError, match="different goal bitmap widths"):
        _union_bits(bytes(width), bytes(width + 1))


@pytest.fixture(params=[5000, None], ids=["explicit-quota", "no-quota"])
def recorded(tmp_path, request):
    plan = replace(_plan(count=3), callback_limit=request.param)
    stimulus = api.Stimulus({}, [api.EventSlot(), api.EventSlot()])
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    emit = library.__behave_native_event_hit
    emit(2)  # Startup never becomes a scheduled-step goal.
    observer.start_slots()
    emit(0)
    observer.sampled()
    emit(1)
    observer.clocked()
    first = observer.successor()
    observer.next_slot()
    observer.sampled()
    emit(1)
    observer.clocked()
    second = observer.successor()
    evidence = observer.finish()
    return plan, stimulus, evidence, (first, second)


@pytest.mark.eda("g++")
def test_phase_record_retains_occurrences_and_survives_trace_roundtrip(recorded):
    plan, stimulus, evidence, vectors = recorded
    seen = read_native_evidence(evidence, stimulus, _catalogue(plan), expected_plan=plan)
    bits = read_native_goal_bits(evidence, stimulus, _catalogue(plan), expected_plan=plan)
    codec = HitVectorCodec(plan.bindings.goal_catalogue)
    assert [codec.from_bits(value) for value in bits] == [codec.vector(hit.goals) for hit in seen]
    assert [h.goals for h in seen] == [{"goal:0", "goal:1"}, {"goal:1"}]
    assert [h.occurrences for h in seen] == [
        {b.occurrence for b in plan.bindings.occurrences[:2]},
        {plan.bindings.occurrences[1].occurrence}]
    trace = AuditedTrace(stimulus, records=[SlotRecord(hits=v) for v in vectors],
                         source_evidence=evidence)
    restored = AuditedTrace.from_dict(trace.as_dict())
    assert restored.fingerprint == trace.fingerprint
    evidence["intervals"].clear()
    assert len(thaw_record_value(restored.source_evidence)["intervals"]) == 7
    assert trace.fingerprint != AuditedTrace(stimulus, records=trace.records).fingerprint


@pytest.mark.eda("g++")
def test_repeated_intervals_share_storage_without_erasing_callback_counts(tmp_path):
    plan = _plan(count=2)
    stimulus = api.Stimulus(slots=[api.EventSlot() for _ in range(4)])
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    observer.start_slots()
    vectors = []
    for index in range(4):
        if index:
            observer.next_slot()
        for _ in range(2 if index == 2 else 1):
            library.__behave_native_event_hit(0)
        observer.sampled()
        library.__behave_native_event_hit(1)
        observer.clocked()
        vectors.append(observer.successor())
    evidence = observer.finish()
    rows = evidence["intervals"]
    assert len(rows) == 13
    assert rows[1] is rows[4]
    assert rows[7] is not rows[4]  # Same hit bitmap, different callback count.
    assert rows[7]["callbacks"] == 2 and rows[4]["callbacks"] == 1
    assert rows[2] is rows[5] is rows[8] is rows[11]
    hits = read_native_evidence(evidence, stimulus, _catalogue(plan), expected_plan=plan)
    assert len(hits) == 4 and all(hit.goals == {"goal:0", "goal:1"} for hit in hits)
    assert all(vector is vectors[0] for vector in vectors)


@pytest.mark.eda("g++")
def test_changing_hits_do_not_reuse_the_previous_slot_vector(tmp_path):
    plan = _plan(count=2)
    expected = [(), (0,), (0,), (1,), (), ()]
    stimulus = api.Stimulus(slots=[api.EventSlot() for _ in expected])
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    observer.start_slots()
    vectors = []
    for index, events in enumerate(expected):
        if index:
            observer.next_slot()
        observer.sampled()
        observer.clocked()
        for event in events:
            library.__behave_native_event_hit(event)
        vectors.append(observer.successor())
    evidence = observer.finish()
    hits = read_native_evidence(evidence, stimulus, _catalogue(plan), expected_plan=plan)
    for vector, observed, events in zip(vectors, hits, expected):
        goals = {f"goal:{event}" for event in events}
        assert vector == HitVector.from_names(observer.catalogue, goals)
        assert observed.goals == goals
    assert vectors[1] is vectors[2] and vectors[4] is vectors[5]
    assert vectors[2] is not vectors[3] and vectors[3] is not vectors[4]


@pytest.mark.eda("g++")
def test_return_hits_are_concrete_evidence_not_an_unimplemented_exact_relation(tmp_path):
    from behave.sim.rtl.initial_request import InitialStateCoordinates
    from behave.sim.rtl.observation_binding import ObservationBinding
    from behave.sim.rtl.source_trace import read_source_trace

    plan = _plan(count=1)
    stimulus = api.Stimulus(slots=[api.EventSlot()])
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    observer.start_slots()
    observer.sampled()
    observer.clocked()
    library.__behave_native_event_hit(0)
    vector = observer.successor()
    image = observer.finish()
    catalogue = _catalogue(plan)
    hits, = read_native_evidence(image, stimulus, catalogue, expected_plan=plan)
    assert hits.goals == {"goal:0"}
    artifact, contract = "a" * 64, "b" * 64
    trace = AuditedTrace(stimulus, artifact=artifact, domain=contract,
                        records=[SlotRecord(hits=vector)], source_evidence=image)
    binding = ObservationBinding(artifact, contract, catalogue.digest(), plan.identity, "c" * 64)
    with pytest.raises(api.Refused, match="clock-return-relation-unsupported"):
        read_source_trace(stimulus, trace, InitialStateCoordinates(artifact, {}),
                          catalogue, prefix="test", observation=binding)


@pytest.mark.eda("g++")
def test_runtime_observation_seals_only_against_the_schedule_that_ran(tmp_path):
    plan = _plan(count=1)
    request = api.Stimulus(replay_policy=api.ReplayPolicy(0, 0, 0))
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, request, library=library)
    observer.start_slots()
    vectors = []
    for index in range(2):
        if index:
            observer.next_slot()
        library.__behave_native_event_hit(0)
        observer.sampled()
        observer.clocked()
        vectors.append(observer.successor())
    resolved = api.Stimulus(slots=[api.EventSlot(), api.EventSlot()])
    evidence = observer.finish(resolved=resolved)
    assert evidence["stimulus"] == resolved.fingerprint != request.fingerprint
    assert len(read_native_evidence(evidence, resolved, _catalogue(plan), expected_plan=plan)) == 2
    trace = AuditedTrace(resolved, records=[SlotRecord(hits=v) for v in vectors],
                         source_evidence=evidence, request=request)
    assert AuditedTrace.from_dict(trace.as_dict()).fingerprint == trace.fingerprint


@pytest.mark.eda("g++")
def test_checkpoint_does_not_reset_or_finish_native_collector(tmp_path):
    plan = _plan(count=1)
    request = api.Stimulus(replay_policy=api.ReplayPolicy(0, 0, 0))
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, request, library=library)
    observer.start_slots()
    observer.sampled()
    observer.clocked()
    library.__behave_native_event_hit(0)
    observer.successor()
    prefix = api.Stimulus(slots=[api.EventSlot()])
    image = observer.checkpoint(prefix)
    with pytest.raises(ValueError, match="unsupported schema"):
        read_native_goal_bits(image, prefix, _catalogue(plan), expected_plan=plan)
    bits = read_native_goal_bits(image, prefix, _catalogue(plan), expected_plan=plan,
                                 checkpoint=True)
    assert len(bits) == 1 and any(bits[0])
    observer.next_slot()
    observer.sampled()
    observer.clocked()
    observer.successor()
    whole = api.Stimulus(slots=[api.EventSlot(), api.EventSlot()])
    completed = observer.finish(resolved=whole)
    assert len(read_native_goal_bits(completed, whole, _catalogue(plan), expected_plan=plan)) == 2
    assert image["completed_steps"] == 1


@pytest.mark.eda("g++")
@pytest.mark.parametrize("mutation", ["missing", "extra", "order", "bool-count", "under-count",
                                    "overflow", "padding", "noncanonical", "foreign-stimulus",
                                    "foreign-policy", "completed-count", "mapping", "lost-all-events"])
def test_phase_record_cannot_forge_a_complete_bound_run(recorded, mutation):
    plan, stimulus, evidence, _ = recorded
    changed = copy.deepcopy(evidence)
    rows = changed["intervals"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        rows.append(rows[-1])
    elif mutation == "order":
        rows[1], rows[2] = rows[2], rows[1]
    elif mutation == "bool-count":
        rows[1]["callbacks"] = True
    elif mutation == "under-count":
        rows[1]["callbacks"] = 0
    elif mutation == "lost-all-events":
        rows[1]["events"] = _bits((), 3)
    elif mutation == "overflow":
        rows[1]["callbacks"] = plan.callback_count_max + 1
    elif mutation == "padding":
        rows[1]["events"] = pack(b"\x80")
    elif mutation == "noncanonical":
        rows[1]["events"] = "0A"
    elif mutation == "foreign-stimulus":
        changed["stimulus"] = "different"
    elif mutation == "foreign-policy":
        changed["execution"]["phases"].reverse()
    elif mutation == "completed-count":
        changed["completed_steps"] -= 1
    else:
        mapping = json.loads(unpack(changed["plan"]))
        mapping["occurrences"][0]["event"] = 1
        changed["plan"] = pack(json.dumps(mapping).encode())
    for read in (read_native_evidence, read_native_goal_bits):
        with pytest.raises((TypeError, ValueError, api.Refused)):
            read(changed, stimulus, _catalogue(plan), expected_plan=plan)


@pytest.mark.eda("g++")
def test_packed_audit_never_materializes_named_occurrence_sets(recorded, monkeypatch):
    from behave.sim.rtl.source_bindings import EventBindings

    plan, stimulus, evidence, vectors = recorded
    def forbidden(*args):
        raise AssertionError("ordinary hit validation must not allocate occurrence sets per cycle")
    monkeypatch.setattr(EventBindings, "decode", forbidden)
    before = copy.deepcopy(evidence)
    bits = read_native_goal_bits(evidence, stimulus, _catalogue(plan), expected_plan=plan)
    codec = HitVectorCodec(plan.bindings.goal_catalogue)
    assert tuple(codec.from_bits(value) for value in bits) == vectors
    assert evidence == before


@pytest.mark.eda("g++")
@pytest.mark.parametrize("fault", ["early-seal", "wrong-phase", "unsettled-return", "extra-slot"])
def test_phase_machine_cannot_publish_after_a_fault(tmp_path, fault):
    plan = _plan(count=1)
    stimulus = api.Stimulus({}, [api.EventSlot()])
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    with pytest.raises(FailureRaised) as caught:
        if fault == "early-seal":
            observer.finish()
        elif fault == "wrong-phase":
            observer.sampled()
        else:
            observer.start_slots()
            observer.sampled()
            observer.clocked()
            if fault == "unsettled-return":
                library.__behave_native_event_hit(0)
                observer.finish()
            else:
                observer.successor()
                observer.next_slot()
    assert caught.value.failure.disposition is FailureDisposition.NO_RESULT
    with pytest.raises(FailureRaised):
        observer.finish()


@pytest.mark.eda("g++")
def test_artifact_decoder_requires_prepared_evidence_and_matching_public_bits(recorded):
    from .test_rtl_execution_policy import _contract

    plan, _stimulus, evidence, vectors = recorded
    contract = _contract()
    stimulus = api.Stimulus({}, [api.EventSlot(controls={"rst": 0, "out_ready": 1})] * 2)
    evidence["stimulus"] = stimulus.fingerprint
    artifact = RTLArtifact("unused.sv", "M", "/tmp/unused-native-record")
    artifact._frontend_catalogue = _catalogue(plan)
    document = {"execution_policy": execution_policy(native_events=True), "structural_refusals": {},
                "hit_encoding": HIT_VECTOR_ENCODING, "hit_catalogue": list(vectors[0].catalogue),
                "source_evidence": evidence,
                "records": [SlotRecord(hits=v, outputs={"y": 0, "out_valid": 0}).as_dict(
                    compact_hits=True) for v in vectors]}

    def decode(image, expected=plan):
        return artifact._trace_of(contract, stimulus, image, check_channel_shape, "rtl",
                                  expected_native=expected)

    assert type(decode(document)) is AuditedTrace
    assert type(decode(document, None)) is api.Refusal  # Cannot smuggle an unsolicited observer.
    missing = dict(document)
    del missing["source_evidence"]
    assert decode(missing).kind == "rtl-native-evidence-invalid"
    wrong = copy.deepcopy(document)
    wrong["records"][0]["hits"] = HitVector.from_names(vectors[0].catalogue).as_compact_dict()
    assert decode(wrong).kind == "rtl-native-hit-mismatch"


@pytest.mark.cosim
@pytest.mark.eda("slang")
@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("hierarchy", [False, True])
def test_real_production_drivers_record_event_time_not_snapshot_predicates(tmp_path, atomic, hierarchy):
    _check_production_observation(tmp_path, atomic, hierarchy)


@pytest.mark.cosim
@pytest.mark.eda("slang")
@pytest.mark.parametrize("atomic", [False, True])
def test_loop_observation_uses_the_same_production_driver(tmp_path, atomic):
    _check_production_observation(tmp_path, atomic, False, loop=True)


@pytest.mark.cosim
@pytest.mark.eda("slang")
@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("hierarchy", [False, True])
def test_helper_observation_uses_the_same_production_driver(tmp_path, atomic, hierarchy):
    _check_production_observation(tmp_path, atomic, hierarchy, helper=True)


def _check_production_observation(tmp_path, atomic, hierarchy, *, loop=False, helper=False,
                                  expression=False, boolean=False):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

    clocks = [api.ClockDomain("a", "clk", "rst")]
    extra = ""
    second_process = ""
    if atomic:
        clocks.append(api.ClockDomain("b", "bclk", "brst"))
        extra = ", input bclk, brst"
        second_process = """reg [7:0] bq = 0;
            always @(posedge bclk) if (brst) bq <= 0; else bq <= bq + 1;"""
    source = tmp_path / "native.sv"
    body = ("for(i=0;i<4;i=i+1) if(q == i+1) q=q+1;" if loop else
            "q=(q == 1 && q < 3) ? 5 : q;" if boolean else
            "q=(q == 1) ? 5 : q;" if expression else
            "if (q == 1) q = 5;")
    function = ""
    if helper:
        function = """function automatic [7:0] adjust(input [7:0] value);
            if(value == 1) adjust=5; else adjust=value;
        endfunction"""
        body = "q=adjust(q);"
    text = f"""module {'Leaf' if hierarchy else 'M'}(input clk, rst, out_ready,
        output out_valid, output [7:0] y{extra});
        reg [7:0] q = 0;
        {'integer i;' if loop else ''}
        {function}
        assign y = q; assign out_valid = 0;
        always @(posedge clk or posedge rst) begin
            if (rst) q = 0;
            else begin
                q = q + 1;
                {body}
            end
        end
        {second_process}
    endmodule"""
    if hierarchy:
        text += f"""\nmodule M(input clk, rst, out_ready,
            output out_valid, output [7:0] y{extra});
            Leaf child(.clk(clk), .rst(rst), .out_ready(out_ready),
                       .out_valid(out_valid), .y(y){', .bclk(bclk), .brst(brst)' if atomic else ''});
        endmodule"""
    source.write_text(text)
    contract = api.TaskContract(clocks=clocks, endpoints=[api.Endpoint(
        "out", api.Direction.OUT, [api.Field("y", api.FiniteType(8), "y")],
        api.ready_valid("out", "out_valid", "out_ready"), clock="a", kind=api.Kind.TRANSACTION)])
    slots = [api.EventSlot(reset=r, controls={"rst": int(r), "out_ready": 1})
             for r in (True, False, False)]
    schedule = None
    if atomic:
        slots = [api.EventSlot(ticks=("a", "b"), resets=("a", "b") if i == 0 else (),
                              controls={"rst": int(i == 0), "brst": int(i == 0),
                                        "out_ready": 1}) for i in range(3)]
        schedule = GlobalSchedule(
            (ClockTiming("a", 10, clock="clk", reset="rst"),
             ClockTiming("b", 10, clock="bclk", reset="brst")),
            tuple(GlobalEventSlot(i * 10, event=slot) for i, slot in enumerate(slots)))
    stimulus = api.Stimulus({}, slots, clock_schedule=schedule)
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    prefix = "child." if hierarchy else ""
    widths = {prefix + "q": 8, **({prefix + "bq": 8} if atomic else {})}
    kwargs = {"workers": 1, "observe_names": tuple(widths), "state_names": tuple(widths),
              "state_widths": widths}
    binding = artifact.bind_observation(contract)
    assert binding.native
    # This is the same default call made by generation Select and score replay.
    # There is no per-call native opt-in after admission.
    native = artifact.traces_per_process(contract, [stimulus], **kwargs)[0]
    if type(native) is not AuditedTrace:
        pytest.fail(json.dumps(native.as_dict(), indent=2))
    assert [dict(r.state)[prefix + "q"] for r in native.records] == [0, 5, 6]
    if atomic:
        assert [dict(r.state)[prefix + "bq"] for r in native.records] == [0, 1, 2]
    evidence = thaw_record_value(native.source_evidence)
    observed = read_native_evidence(evidence, stimulus, artifact.frontend_catalogue())
    # Source declarations replaced the old fixed RTLIL event numbers. Keep a
    # literal source oracle for every interval, including added locations and
    # startup-only continuous-driver entries; do not infer hits from q's value.
    from behave.sim.rtl.native_events import NativeEventPlan
    from behave.sim.rtl.native_observation import _events

    plan = NativeEventPlan.from_evidence(evidence["plan"])
    assert plan.scope_root == ""  # The actual cocotb launcher.
    manifest = artifact.frontend_catalogue()

    def meaning(family, fragment, outcome="entry"):
        return family, re.sub(r"\s+", "", fragment), outcome

    def location(fragment):
        return meaning("location", fragment)

    outer = f"if (rst) q = 0; else begin q = q + 1; {body} end"

    def primary(reset, first=False):
        hits = {location(outer), meaning("control-choice", outer, "true" if reset else "false")}
        if reset:
            return hits | {location("q = 0;")}
        hits |= {location("q = q + 1;"), location(body)}
        if loop:
            inner = "if(q == i+1) q=q+1;"
            hits |= {meaning("loop-activation", body, "nonzero"), location(inner),
                     meaning("control-choice", inner, "true" if first else "false")}
        elif helper:
            inner = "if(value == 1) adjust=5; else adjust=value;"
            hits |= {location(inner), meaning("control-choice", inner, "true" if first else "false"),
                     location("adjust=5;" if first else "adjust=value;")}
        elif expression or boolean:
            inner = "(q == 1 && q < 3) ? 5 : q" if boolean else "(q == 1) ? 5 : q"
            hits.add(meaning("control-choice", inner, "true" if first else "false"))
            if boolean:
                hits.add(meaning("boolean-expression", "q == 1", "true" if first else "false"))
                if first:
                    hits.add(meaning("boolean-expression", "q < 3", "true"))
        else:
            hits.add(meaning("control-choice", body, "true" if first else "false"))
            if first:
                hits.add(location("q = 5;"))
        return hits

    clock_hits = [primary(True), primary(False, True), primary(False)]
    if atomic:
        secondary = "if (brst) bq <= 0; else bq <= bq + 1;"
        for index, hits in enumerate(clock_hits):
            hits |= {location(secondary),
                     meaning("control-choice", secondary, "true" if index == 0 else "false"),
                     location("bq <= 0;" if index == 0 else "bq <= bq + 1;")}
    values = {goal.id: meaning(goal.family, text[goal.where("start"):goal.where("end")], goal.event)
              for goal in manifest.values()}
    intervals = [Counter(values[goal] for goal in plan.bindings.decode(
        _events(row["events"], plan.bindings.event_count)).goals) for row in evidence["intervals"]]
    expected_clock = [Counter(hits) for hits in clock_hits]
    if loop:
        # The loop body and preceding increment are distinct source locations,
        # even though their text is identical. Both execute on the first step.
        expected_clock[1][location("q=q+1;")] += 1
    for hits in expected_clock[1:]:
        hits[location("y = q")] += 1
    continuous = Counter({location("y = q"), location("out_valid = 0")})
    assert intervals == [continuous, Counter(primary(True)), expected_clock[0], Counter(), Counter(),
                         expected_clock[1], Counter(), Counter(), expected_clock[2], Counter()]
    assert all(h.occurrences for h in observed)
    assert observed[1] != observed[2]  # q==1 was evaluated after the blocking increment.
    # Variable initialization is not a goal; continuous driver entry is.
    assert intervals[0] == continuous
    assert [r["callbacks"] for r in evidence["intervals"] if r["phase"] == "advance"] == [0, 0, 0]
    from behave.sim.rtl.initial_request import InitialStateCoordinates
    from behave.sim.rtl.source_trace import read_source_trace
    source_trace = read_source_trace(
        stimulus, native, InitialStateCoordinates(artifact.identity, widths),
        artifact.frontend_catalogue(), prefix="test", observation=binding)
    assert [s[prefix + "q"] for s in source_trace.states] == [0, 0, 5, 6]
    assert source_trace.hits == tuple(h.goals for h in observed)
    assert source_trace.occurrences == tuple(h.occurrences for h in observed)
    from dataclasses import replace

    missing = native.as_dict()
    missing.pop("source_evidence")
    for supplied_trace, supplied_binding, reason in (
            (AuditedTrace.from_dict(missing), binding, "observation-missing"),
            (native, replace(binding, native_plan="f" * 64), "native-evidence-invalid")):
        with pytest.raises(api.Refused, match="source-" + reason):
            read_source_trace(stimulus, supplied_trace,
                              InitialStateCoordinates(artifact.identity, widths),
                              artifact.frontend_catalogue(), prefix="test", observation=supplied_binding)
    broken = native.as_dict()
    broken["source_evidence"]["intervals"].pop()
    with pytest.raises(api.Refused, match="source-native-evidence-invalid"):
        read_source_trace(stimulus, AuditedTrace.from_dict(broken),
                          InitialStateCoordinates(artifact.identity, widths),
                          artifact.frontend_catalogue(), prefix="test", observation=binding)
    assert AuditedTrace.from_dict(native.as_dict()).fingerprint == native.fingerprint
    return native, artifact


def test_source_preparation_receives_the_driver_phase_contract(monkeypatch):
    from behave.sim.rtl import native_preparation
    from behave.sim.rtl.trigger_support import driver_triggers

    from .test_rtl_execution_policy import _contract

    calls = []
    marker = object()

    def prepare(source, **kwargs):
        calls.append((source, kwargs))
        return marker

    monkeypatch.setattr(native_preparation, "prepare_source_observer", prepare)
    result = native_preparation.prepare_scheduled_observer(
        "source", top="M", contract=_contract(),
        catalogue=_catalogue(_plan(count=0)), timeout_s=1)
    assert result is marker
    assert len(calls) == 1 and calls[0][0] == "source"
    assert calls[0][1]["allowed_triggers"] == driver_triggers(_contract())
    assert "sites" not in calls[0][1]


def test_native_observation_identity_does_not_relabel_snapshot_records():
    from behave.sim.rtl.execution_policy import execution_adapter_version

    ordinary = execution_policy()
    native = execution_policy(native_events=True)
    assert ordinary["phases"] == native["phases"]
    assert ordinary["structural_sample"] != native["structural_sample"]
    assert execution_adapter_version("protocol") != execution_adapter_version("protocol", native_events=True)


def test_equal_public_goals_do_not_erase_different_native_occurrences():
    from dataclasses import replace

    from behave.sim.rtl.native_observation import VERSION
    from behave.sim.rtl.source_bindings import EventBindings

    plan = _plan(count=2)
    first, second = plan.bindings.occurrences
    plan = replace(plan, bindings=EventBindings(2, (first, replace(second, goal=first.goal))))
    stimulus = api.Stimulus({}, [api.EventSlot()])
    image = {"version": VERSION, "plan": plan.evidence_image,
             "execution": execution_policy(native_events=True), "stimulus": stimulus.fingerprint,
             "completed_steps": 1,
             "intervals": [{"phase": phase, "callbacks": int(phase == "clock"),
                            "events": _bits((0,) if phase == "clock" else (), 2)}
                           for phase in ("startup", "control", "clock", "advance")]}
    before = read_native_evidence(image, stimulus, _catalogue(plan), expected_plan=plan)
    image["intervals"][2]["events"] = _bits((1,), 2)
    after = read_native_evidence(image, stimulus, _catalogue(plan), expected_plan=plan)
    assert before[0].goals == after[0].goals
    assert before[0].occurrences == {first.occurrence}
    assert after[0].occurrences == {second.occurrence}
    assert before != after
