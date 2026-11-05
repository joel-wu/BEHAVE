"""Common runner: closed plans and real differential replay against the retired loops."""

import asyncio
import pickle
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.execution_policy import execution_policy
from behave.sim.rtl.replay_plan import (
    ATOMIC_DRIVER_PLAN_VERSION,
    DRIVER_PLAN_VERSION,
    ReplayPlan,
)


def test_environment_pins_are_resolved_once_and_only_changes_are_written():
    from behave.sim.rtl.replay_runtime import _InputPins

    lookups, writes = [], []

    class Pin:
        def __init__(self, name):
            self.name = name

        @property
        def value(self):
            raise AssertionError("the writer does not read DUT outputs")

        @value.setter
        def value(self, value):
            writes.append((self.name, value))

    class DUT:
        def __getitem__(self, name):
            lookups.append(name)
            return Pin(name)

    pins = _InputPins(DUT())
    pins.drive({"data": 0, "valid": 0})
    pins.drive({"data": 5, "valid": 1})
    for _ in range(200):
        pins.drive({"data": 5, "valid": 1})  # Legal stalled presentation.
    pins.drive({"data": 0, "valid": 0})     # Inactive payload still cleared.
    assert lookups == ["data", "valid"]
    assert writes == [("data", 0), ("valid", 0), ("data", 5), ("valid", 1),
                      ("data", 0), ("valid", 0)]


def test_replay_images_share_projection_without_losing_a_slot_or_changed_bits():
    from behave.sim.core.hit_vector import HitVectorCodec
    from behave.sim.rtl.replay_runtime import _ReplayImages

    codec = HitVectorCodec(tuple(f"g{index:04}" for index in range(1025)))
    same = codec.vector(("g0000", "g1024"))
    changed = codec.vector(("g1024",), ("g0000",))
    vectors = [same] * 1000 + [changed, same]
    projection = _ReplayImages()
    rows = [projection.hits(vector) for vector in vectors]
    assert len(rows) == len(vectors)
    assert all(row is rows[0] for row in rows[:1000])
    assert rows == [vector.as_compact_dict() for vector in vectors]
    assert rows[1000] != rows[0] and rows[1001] == rows[0]


def test_memory_projection_preserves_repeated_writes_and_response_transitions():
    from behave.sim.core.memory_trace import memory_updates_record
    from behave.sim.memory_driver._protocol import Updates
    from behave.sim.rtl.replay_runtime import _ReplayImages

    idle = (("ram", Updates((), ((None, ()),), ((None, None),))),)
    write = (("ram", Updates(((3, 99),), ((None, ()),), ((None, None),))),)
    pending = (("ram", Updates((), ((None, ((1, 3, 99),)),), ((None, None),))),)
    response = (("ram", Updates((), ((None, ()),), ((None, (3, 99)),))),)
    updates = [idle] * 1000 + [write, write, pending, response, idle]
    projection = _ReplayImages()
    rows = [projection.memory(update) for update in updates]
    assert len(rows) == len(updates)
    assert all(row is rows[0] for row in rows[:1000])
    assert rows[1000] is rows[1001]
    assert rows == [memory_updates_record(update) for update in updates]
    assert rows[1000]["memories"][0][1]["writes"] == [[3, 99]]
    assert rows[1002] != rows[1003] != rows[1004]


def _plan(atomic):
    clocks = [api.ClockDomain("a", "clk", "rst")]
    if atomic:
        clocks.append(api.ClockDomain("b", "bclk", "brst"))
    contract = api.TaskContract(clocks=clocks, endpoints=[api.Endpoint(
        "out", api.Direction.OUT, [api.Field("y", api.FiniteType(8), "y")],
        api.ready_valid("out", "out_valid", "out_ready"), clock="a", kind=api.Kind.TRANSACTION)])
    common = {"version": ATOMIC_DRIVER_PLAN_VERSION if atomic else DRIVER_PLAN_VERSION,
              "contract": contract, "stimuli": [], "watch": ["q", "y"], "record_names": ["y"],
              "initial_state": None, "execution_policy": execution_policy()}
    if atomic:
        common.update(state_fields=[["q", 8]], artifact="rtl")
    else:
        common.update(state_names=["q"])
    return common


@pytest.mark.parametrize("atomic", [False, True])
def test_common_plan_preserves_both_carriers(atomic):
    plan = ReplayPlan.from_dict(_plan(atomic))
    assert plan.atomic is atomic
    assert plan.record_names == ("y",) and plan.watch == ("q", "y")
    assert plan.state_fields == (("q", 8 if atomic else None),)
    assert plan.records_initial_state


@pytest.mark.parametrize("memory", [False, True])
@pytest.mark.parametrize("state", [False, True])
def test_finish_projection_keeps_owned_values_and_complete_wire_bytes(memory, state):
    from behave.sim.core.record_value import canonical_json
    from behave.sim.rtl.replay_runtime import _record_images

    records = [api.SlotRecord(
        accepted={"in": (index, {"nested": [1, 2]})},
        outputs={"out": {"zero": -0.0, "bits": [True, index]}},
        state={"old": [1, 2]}, monitor={"queued": index},
        environment={"old-env": {"cell": [index]}}, hits={"old-goal": True})
        for index in range(3)]
    hits = [{"all": [index], "encoding": "test"} for index in range(3)]
    memories = [{"writes": [[index, 0]]} for index in range(3)] if memory else []
    states = [{"z": 0, "a": index} for index in range(3)] if state else []
    expected = []
    for index, record in enumerate(records):
        image = record.as_dict()
        image["hits"] = hits[index]
        if memory:
            image["environment"] = [["external-memory", memories[index]]]
        if state:
            image["state"] = [[name, value] for name, value in sorted(states[index].items())]
        expected.append(image)
    actual = _record_images(records, hits, memories, states, state_fields=state)
    assert canonical_json(actual) == canonical_json(expected)
    for record, image in zip(records, actual, strict=True):
        assert image["accepted"] is record.accepted
        assert image["outputs"] is record.outputs
        assert image["monitor"] is record.monitor
    with pytest.raises(ValueError, match="shorter"):
        _record_images(records, hits[:-1], memories, states, state_fields=state)


@pytest.mark.parametrize("changed", ["hits", "memory", "state"])
def test_finish_shares_only_rows_with_identical_overlays(changed):
    from behave.sim.rtl.replay_runtime import _record_images

    record = api.SlotRecord(outputs={"y": 5})
    hits = [{"true": "AA=="} for _ in range(4)]
    memories = [{"writes": []} for _ in range(4)]
    states = [{"q": 0} for _ in range(4)]
    if changed == "hits":
        hits[2] = {"true": "AQ=="}
    elif changed == "memory":
        memories[2] = {"writes": [[0, 7]]}
    else:
        states[2] = {"q": 7}
    rows = _record_images([record] * 4, hits, memories, states, state_fields=True)
    assert len(rows) == 4
    assert rows[0] is rows[1]
    assert rows[2] is not rows[1] and rows[3] is not rows[2]
    assert rows[0] == rows[3]


@pytest.mark.parametrize('atomic', [False, True])
@pytest.mark.parametrize('completed', [0, 2])
def test_interrupted_capture_never_promotes_a_preclock_sample(atomic, completed):
    from dataclasses import replace

    from behave.sim.core.hit_vector import HitVectorCodec
    from behave.sim.rtl.replay_runtime import _ReplayCapture

    stimulus = api.Stimulus(slots=[api.EventSlot()] * 4)
    plan = replace(ReplayPlan.from_dict(_plan(atomic)), stimuli=(stimulus,))
    capture = _ReplayCapture(plan, None)
    capture.state_widths = [['q', 8]]
    if completed:
        capture.initial_state = {'q': 0}
    # Single-clock Driver has already sampled a prospective transfer while
    # AtomicDriver has not committed its slot. Neither has a successor yet.
    count = completed if atomic else completed + 1
    capture.driver = SimpleNamespace(records=[api.SlotRecord(outputs={'y': i}) for i in range(count)])
    capture.hits = [HitVectorCodec(()).vector(()).as_compact_dict()] * completed
    capture.states = [{'q': i + 1} for i in range(completed)]
    capture.pending_sample = {'y': 99, 'q': 7}
    image = capture.image(interrupted=True)
    assert image['interrupted'] is True and image['stimulus'] == stimulus.fingerprint
    assert len(image['records']) == completed
    assert image['pending_sample'] == {'y': 99, 'q': 7}
    assert [dict(row['state']) for row in image['records']] == capture.states
    assert ('initial_state' in image) == bool(completed)
    assert len(capture.driver.records) == count  # Publication does not mutate replay.
    if not atomic:
        with pytest.raises(ValueError, match='shorter'):
            capture.image()  # Normal publication still rejects the missing slot.


def test_startup_capture_keeps_absence_of_unobserved_initial_state():
    from dataclasses import replace

    from behave.sim.rtl.replay_runtime import _ReplayCapture

    stimulus = api.Stimulus(slots=[api.EventSlot()])
    plan = replace(ReplayPlan.from_dict(_plan(False)), stimuli=(stimulus,),
                   startup_state={'test-only-choice': 7})
    capture = _ReplayCapture(plan, None)
    image = capture.image(interrupted=True)
    assert image['records'] == [] and image['pending_sample'] is None
    assert 'initial_state' not in image
    assert image['startup_state'] == plan.startup_state
    assert image['initialization'] is None  # A plan is not successful installation.
    capture.initialization = plan.startup_state
    assert capture.image(interrupted=True)['initialization'] == plan.startup_state


def test_interrupted_capture_is_not_a_normal_trace_even_with_all_slots():
    from dataclasses import replace

    from behave.sim.core.driver import check_channel_shape
    from behave.sim.core.hit_vector import HitVectorCodec
    from behave.sim.core.refusal import Refusal
    from behave.sim.rtl.replay_runtime import _ReplayCapture
    from behave.sim.rtl.trace_decoder import TraceDecoder

    stimulus = api.Stimulus(slots=[api.EventSlot(controls={'rst': 0, 'out_ready': 1})])
    plan = replace(ReplayPlan.from_dict(_plan(False)), stimuli=(stimulus,))
    capture = _ReplayCapture(plan, None)
    capture.state_widths, capture.initial_state = [['q', 8]], {'q': 0}
    capture.driver = SimpleNamespace(records=[api.SlotRecord(outputs={'out_valid': 0, 'y': 0, 'q': 0})])
    capture.hits = [HitVectorCodec(()).vector(()).as_compact_dict()]
    capture.states = [{'q': 0}]
    got = TraceDecoder(None, {}).decode(plan.contract, stimulus, capture.image(interrupted=True),
                                       check_channel_shape, 'dut')
    assert type(got) is Refusal and got.kind == 'rtl-artifact-structural-record-malformed'


@pytest.mark.parametrize('driver_cause', [None, 'valid', 'malformed'])
@pytest.mark.parametrize('failure_hit', [False, True])
@pytest.mark.parametrize('metadata', ['missing', 'valid', 'malformed', 'extra-trace',
                                     'other-stimulus', 'extra-slot', 'unmarked'])
def test_sampled_prefix_cannot_create_or_erase_a_native_failure(failure_hit, metadata, driver_cause):
    from behave.sim.core.record_value import canonical_json
    from behave.sim.rtl.native_events import NativeEventPlan
    from behave.sim.rtl.native_evidence_codec import pack
    from behave.sim.rtl.native_observation import PREFIX_VERSION
    from behave.sim.rtl.replay_interruption import interrupted_replay
    from behave.sim.rtl.source_bindings import BoundOccurrence, EventBindings

    goal = api.Goal('rtl-source:' + '0' * 64 + ':failure', api.SAFETY,
                    family='artifact-failure', provider='rtl-structural')
    key = api.OccurrenceKey('rtl-source', ('initial',), 'failure')
    plan = NativeEventPlan(EventBindings(1, (BoundOccurrence(key, goal.id, 0, ()),)), 'a' * 64, 100)
    catalogue = api.CatalogueManifest([goal], occurrences=[(key, goal.id)])
    stimulus = api.Stimulus(slots=[api.EventSlot()])
    native = {'version': PREFIX_VERSION, 'plan': plan.evidence_image,
        'execution': execution_policy(native_events=True), 'stimulus': stimulus.fingerprint,
        'completed_steps': 0, 'intervals': [{'phase': 'startup', 'callbacks': int(failure_hit),
                                          'events': pack(bytes([int(failure_hit)]))}]}
    sample = {'interrupted': True, 'stimulus': stimulus.fingerprint, 'records': [], 'pending_sample': None}
    if metadata == 'other-stimulus':
        sample['stimulus'] = 'another request'
    elif metadata == 'extra-slot':
        sample['records'] = [{}]
    elif metadata == 'unmarked':
        sample['interrupted'] = False
    payload = (None if metadata == 'missing' else b'{' if metadata == 'malformed' else
               canonical_json({'traces': [sample] * (2 if metadata == 'extra-trace' else 1)}))
    result = interrupted_replay(canonical_json(native), stimulus, catalogue,
        expected_plan=plan, execution={'returncode': 1}, replay_payload=payload,
        refusal_payload=(None if driver_cause is None else b'{' if driver_cause == 'malformed' else
                         canonical_json({'kind': 'input-framing-violated', 'why': 'invalid input'})))
    assert result.reason.kind == ('rtl-source-failure' if failure_hit else 'rtl-native-run-interrupted')
    assert result.origin is (api.FailureOrigin.CANDIDATE if failure_hit else api.FailureOrigin.VERIFIER)
    assert set(result.observed.catalogue) == ({goal.id} if failure_hit else set())
    assert ('replay_prefix' in result.reason.detail) == (metadata == 'valid')
    assert ('replay_prefix_refusal' in result.reason.detail) == (metadata not in {'missing', 'valid'})
    assert ('driver_refusal' in result.reason.detail) == (driver_cause == 'valid')
    assert ('driver_refusal_invalid' in result.reason.detail) == (driver_cause == 'malformed')


@pytest.mark.parametrize('typed', [False, True])
def test_driver_refusal_survives_without_a_complete_capture(tmp_path, monkeypatch, typed):
    import json
    from behave.sim.core.refusal import Refusal, Refused
    from behave.sim.rtl import replay_runtime

    (tmp_path / 'plan.pickle').write_bytes(pickle.dumps(_plan(False)))
    error = Refused(Refusal('input-framing-violated', 'bad input', slot=3)) if typed else RuntimeError('bug')

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(replay_runtime, '_replay', fail)
    with pytest.raises(type(error)) as raised:
        asyncio.run(replay_runtime.replay(None, tmp_path, period_ns=10))
    assert raised.value is error
    record = tmp_path / 'replay_refusal.json'
    assert record.exists() == typed
    if typed:
        assert json.loads(record.read_text()) == error.refusal.as_dict()
    assert not (tmp_path / 'replay_prefix.json').exists()


@pytest.mark.parametrize("atomic", [False, True])
def test_retired_site_table_is_not_a_replay_input(atomic):
    plan = _plan(atomic)
    plan["sites"] = {}
    with pytest.raises(RuntimeError, match="complete versioned shape"):
        ReplayPlan.from_dict(plan)


@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("fault", ["version", "missing", "extra", "policy", "watch", "records", "state",
                                   "contract", "stimuli", "mixed-names"])
def test_common_plan_keeps_the_closed_boundary(atomic, fault):
    plan = _plan(atomic)
    if fault == "version":
        plan["version"] = "unbound"
    elif fault == "missing":
        del plan["record_names"]
    elif fault == "extra":
        plan["predicted_hits"] = []
    elif fault == "policy":
        plan["execution_policy"] = execution_policy(native_events=True)
    elif fault == "watch":
        plan["watch"] = ["y", "q"]
    elif fault == "records":
        plan["record_names"] = ["unwatched"]
    elif fault == "contract":
        plan["contract"] = _plan(not atomic)["contract"]
    elif fault == "stimuli":
        plan["stimuli"] = [None]
    elif fault == "mixed-names":
        plan["watch"] = [None, "q"]
    elif atomic:
        plan["state_fields"] = [["q", True]]
    else:
        plan["state_names"] = ["q", "q"]
    with pytest.raises(RuntimeError):
        ReplayPlan.from_dict(plan)


@pytest.mark.parametrize("primary_type", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("publication", ["ok", "interrupt", "encode", "write", "rename", "capture", "checkpoint"])
def test_interrupted_replay_keeps_execution_error_when_prefix_publication_fails(
        tmp_path, monkeypatch, primary_type, publication):
    from dataclasses import replace

    from behave.sim.rtl import native_events, native_observation, replay_checkpoint, replay_runtime

    (tmp_path / "plan.pickle").write_bytes(pickle.dumps({}))
    plan = replace(ReplayPlan.from_dict(_plan(False)), native_events={},
                   stimuli=(api.Stimulus(slots=[api.EventSlot()]),),
                   checkpoint=object() if publication == 'checkpoint' else None)
    monkeypatch.setattr(replay_runtime.ReplayPlan, "from_dict", lambda _image: plan)
    monkeypatch.setattr(native_events.NativeEventPlan, "from_dict", lambda image: image)
    primary = primary_type("the original simulator failure")
    secondary = OSError("the evidence filesystem failed")

    def fail_publication(*_args, **_kwargs):
        raise secondary

    native = SimpleNamespace(interruptible=True, interrupt=lambda: {"sealed": "prefix"}, catalogue=())
    monkeypatch.setattr(native_observation, "NativeObservation", lambda *_args: native)
    if publication == "interrupt":
        native.interrupt = fail_publication
    elif publication == "encode":
        monkeypatch.setattr(replay_runtime, "canonical_json", fail_publication)
    elif publication == "write":
        monkeypatch.setattr(Path, "write_text", fail_publication)
    elif publication == "rename":
        monkeypatch.setattr(Path, "replace", fail_publication)
    elif publication == "capture":
        monkeypatch.setattr(replay_runtime, 'write_trace_document', fail_publication)
    elif publication == "checkpoint":
        monkeypatch.setattr(replay_checkpoint, 'CheckpointClient',
                            lambda _plan: SimpleNamespace(invalidate=fail_publication))

    async def failed_replay(*_args, **_kwargs):
        raise primary

    monkeypatch.setattr(replay_runtime, "_replay", failed_replay)
    with pytest.raises(primary_type) as caught:
        asyncio.run(replay_runtime.replay(object(), tmp_path, period_ns=10))
    assert caught.value is primary
    if publication in {"ok", "capture", "checkpoint"}:
        assert (tmp_path / "native_prefix.json").read_text() == '{"sealed":"prefix"}'
        assert (tmp_path / "replay_prefix.json").exists() == (publication != 'capture')
    else:
        assert not (tmp_path / "native_prefix.json").exists()
    if publication == "ok":
        assert not getattr(primary, "__notes__", ())
    else:
        assert any(str(secondary) in note for note in primary.__notes__)


@pytest.mark.cosim
@pytest.mark.eda("slang")
@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_common_loop_preserves_state_and_transfers_across_clock_edges(tmp_path, atomic, edge):
    clocks = [api.ClockDomain("a", "clk", "rst", edge=edge)]
    pins, second, addend = "", "", "0"
    widths = {"q": 8, "full": 1}
    trigger = "posedge" if edge == "rising" else "negedge"
    if atomic:
        clocks.append(api.ClockDomain("b", "bclk", "brst", edge=edge))
        pins, addend = ", input bclk, brst", "bq"
        second = f"""reg [7:0] bq = 0;
            always @({trigger} bclk or posedge brst)
                if (brst) bq <= 0; else bq <= q;"""
        widths["bq"] = 8
    rtl = tmp_path / "dut.sv"
    rtl.write_text(f"""module M(input clk, rst, in_valid, input [7:0] in_data,
        input out_ready, output in_ready, out_valid, output [7:0] y{pins});
        reg [7:0] q = 0;
        reg full = 0;
        {second}
        assign in_ready = !full || out_ready;
        assign out_valid = full; assign y = q;
        always @({trigger} clk or posedge rst) begin
            if (rst) begin q <= 0; full <= 0; end
            else begin
                if (out_ready) full <= 0;
                if (in_valid && in_ready) begin q <= in_data + {addend}; full <= 1; end
            end
        end
    endmodule""")
    contract = api.TaskContract(clocks=clocks, endpoints=[
        api.Endpoint("in", api.Direction.IN, [api.Field("x", api.FiniteType(8), "in_data")],
                     api.ready_valid("in", "in_valid", "in_ready"), clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", api.Direction.OUT, [api.Field("y", api.FiniteType(8), "y")],
                     api.ready_valid("out", "out_valid", "out_ready"), clock="a", kind=api.Kind.TRANSACTION),
    ])
    slots = []
    for i in range(7):
        common = {"offers": ("in",) if i >= 2 else (),
                  "controls": {"rst": int(i < 2), "out_ready": int(i >= (5 if atomic else 4))}}
        if atomic:
            common["controls"]["brst"] = int(i < 2)
            ticks = ((), ("a", "b"), ("a",), ("b",), ("a",), ("a", "b"), ("a",))[i]
            if "a" not in ticks:
                common["offers"] = ()
            slots.append(api.EventSlot(ticks=ticks, resets=("a", "b") if i < 2 else (), **common))
        else:
            slots.append(api.EventSlot(reset=i < 2, **common))
    schedule = None
    if atomic:
        schedule = GlobalSchedule(
            tuple(ClockTiming(c.name, 10 if c.name == "a" else 15, phase=10,
                              clock=c.clock, reset=c.reset, edge=edge) for c in clocks),
            tuple(GlobalEventSlot(time, event=slot)
                  for time, slot in zip((0, 10, 20, 25, 30, 40, 50), slots)))
    stimulus = api.Stimulus({"in": [(5,), (9,)]}, slots, clock_schedule=schedule)
    kwargs = {"workers": 1, "observe_names": tuple(widths), "state_names": tuple(widths),
              "state_widths": widths}
    got = RTLArtifact(rtl, "M", tmp_path / "common").traces_per_process(contract, [stimulus], **kwargs)[0]
    assert type(got) is api.AuditedTrace, got.as_dict()

    assert got.stimulus == stimulus
    assert got.accepted_count(["in"]) == 2
    assert len(got.records) == len(stimulus.slots)
    assert [dict(record.state)["q"] for record in got.records] == (
        [0, 0, 5, 5, 5, 14, 14] if atomic else [0, 0, 5, 5, 9, 9, 9])
    if atomic:
        assert [dict(record.state)["bq"] for record in got.records] == [0, 0, 0, 5, 5, 5, 5]
