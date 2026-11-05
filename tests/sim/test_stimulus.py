"""Stimulus intent and audited execution are distinct immutable records.

These tests distinguish runs that a row list cannot tell apart, including idle
and reset slots retained by the explicit legacy-input conversion boundary.
"""
import pytest

from behave.sim.core.refusal import Refused
from behave.sim.core.stimulus import (
    AuditedTrace,
    Completion,
    EventSlot,
    SlotRecord,
    Stimulus,
    canonical_json,
    from_legacy,
    trace_domain,
)


def _drive(name="in", **kw):
    return EventSlot(offers=[name], **kw)


# ── two runs a row list calls one ──

def test_a_reset_between_two_drives_is_a_different_stimulus():
    """T3-01: `drive(5); reset; drive(2)` differs from the same queues without reset.

    The queues are identical and every transaction value is identical. What differs is
    the schedule, which is the part the row list has no place for -- and the design's
    state at the second drive is not the same state.
    """
    queues = {"in": [(5,), (2,)]}
    with_reset = Stimulus(queues=queues,
                          slots=[_drive(), EventSlot(reset=True), _drive()])
    without = Stimulus(queues=queues, slots=[_drive(), _drive()])
    assert with_reset != without
    assert with_reset.fingerprint != without.fingerprint


def test_back_pressure_alone_makes_two_stimuli_distinct():
    """T3-01: identical transaction values, different back-pressure, still two."""
    queues = {"in": [(7,)]}
    ready = Stimulus(queues=queues, slots=[_drive(controls={"out_ready": 1})])
    stalled = Stimulus(queues=queues, slots=[_drive(controls={"out_ready": 0})])
    assert ready != stalled


def test_the_memory_policy_and_the_initial_state_policy_are_part_of_the_stimulus():
    """A witness replayed under a different memory latency is a different run, and a
    fingerprint that ignored it would deduplicate away the run that finds something."""
    queues, slots = {"in": [(1,)]}, [_drive()]
    fast = Stimulus(queues=queues, slots=slots, memory_policy={"read_latency": 1})
    slow = Stimulus(queues=queues, slots=slots, memory_policy={"read_latency": 4})
    powered = Stimulus(queues=queues, slots=slots, initial_state={"policy": "power-up"})
    after = Stimulus(queues=queues, slots=slots, initial_state={"policy": "post-reset"})
    assert fast != slow
    assert powered != after


def test_a_tick_is_a_slot_and_is_not_droppable():
    """The entry `rows_for()` discards. Dropping it makes `drive; tick; drive` and
    `drive; drive` the same stimulus, and the design ran a cycle in one and not the
    other."""
    queues = {"in": [(1,), (2,)]}
    ticked = Stimulus(queues=queues,
                      slots=[_drive(), EventSlot(ticks=["clk"]), _drive()])
    plain = Stimulus(queues=queues, slots=[_drive(), _drive()])
    assert ticked != plain
    assert ticked.offered() == plain.offered() == 2, "a tick is not a transaction"


def test_two_endpoints_firing_together_stay_one_slot():
    """T3-01: two endpoints or clock domains firing together remain one event slot.

    Serialising a coincidence as two slots describes a different run: the design sees
    both offers in the same cycle in one and in consecutive cycles in the other.
    """
    together = Stimulus(queues={"a": [(1,)], "b": [(2,)]},
                        slots=[EventSlot(offers=["a", "b"])])
    apart = Stimulus(queues={"a": [(1,)], "b": [(2,)]},
                     slots=[_drive("a"), _drive("b")])
    assert len(together.slots) == 1 and len(apart.slots) == 2
    assert together != apart
    assert together.offered() == apart.offered() == 2


def test_source_is_provenance_and_is_not_part_of_identity():
    """The same stimulus reached by a random draw and by a solver is one stimulus. A
    fingerprint that separated them would replay it twice and count it twice."""
    a = Stimulus(queues={"in": [(3,)]}, slots=[_drive()], source="random")
    b = Stimulus(queues={"in": [(3,)]}, slots=[_drive()], source="solver")
    assert a == b and len({a, b}) == 1
    assert a.as_dict()["source"] != b.as_dict()["source"], "but it is still recorded"


# ── a stimulus that cannot be run is refused, not discovered mid-replay ──

def test_a_slot_offering_from_an_endpoint_with_no_queue_is_refused():
    with pytest.raises(Refused, match="stimulus-unknown-endpoint"):
        Stimulus(queues={"in": [(1,)]}, slots=[_drive("nope")])


def test_a_stalled_transaction_is_offered_in_every_slot_it_is_held_for():
    """The model this class got wrong the first time, and the reason it could not have
    been wired to production.

    An earlier version counted one queue item per **offer** and refused when the offers
    outran the queue. Under ready/valid the driver holds the same transaction on the bus
    while `valid && !ready`, so one transaction is offered in many slots and the offers
    legitimately outnumber the queue. That rule made a stalled handshake unwritable.

    The pointer advances on transfer. Whether a transfer happened is a fact about the
    run, so the schedule alone cannot be held to a depth, and `head` takes the accepted
    count rather than a slot index.
    """
    s = Stimulus(queues={"in": [(5,), (2,)]},
                 slots=[_drive(controls={"in_ready": 0}),
                        _drive(controls={"in_ready": 0}),
                        _drive(controls={"in_ready": 1}),
                        _drive(controls={"in_ready": 1})])
    assert s.offered("in") == 4, "four offers"
    assert s.depth("in") == 2, "of two transactions"

    # Held across the stall, then advanced by the transfer.
    assert s.head("in", 0) == (5,)
    assert s.head("in", 1) == (2,)
    assert s.head("in", 2) is None, "the queue is drained"

    # The record says which of the four offers closed.
    trace = AuditedTrace(s, records=[SlotRecord(accepted={"in": None}),
                                     SlotRecord(accepted={"in": None}),
                                     SlotRecord(accepted={"in": (5,)}),
                                     SlotRecord(accepted={"in": (2,)})])
    assert trace.accepted_count() == 2


def test_a_record_is_held_against_the_pointer_the_schedule_implies():
    """The depth check the schedule cannot make, made where the accepted count lives."""
    s = Stimulus(queues={"in": [(5,), (2,)]}, slots=[_drive(), _drive(), _drive()])

    # A transfer of something that is not the head is a record of some other run.
    with pytest.raises(Refused, match="trace-transfer-not-the-head"):
        AuditedTrace(s, records=[SlotRecord(accepted={"in": (2,)}), SlotRecord(),
                                 SlotRecord()])

    # More transfers than the queue holds is a driver inventing transactions, and the
    # pointer catches it: past the end there is no head for the payload to be.
    with pytest.raises(Refused, match="trace-transfer-not-the-head"):
        AuditedTrace(s, records=[SlotRecord(accepted={"in": (5,)}),
                                 SlotRecord(accepted={"in": (2,)}),
                                 SlotRecord(accepted={"in": (9,)})])

    # But offering past the end is not: that is the driver running dry, and a schedule
    # written before the run cannot know where that falls.
    AuditedTrace(s, records=[SlotRecord(accepted={"in": (5,)}),
                             SlotRecord(accepted={"in": (2,)}),
                             SlotRecord(accepted={"in": None})])

    # A released transaction remains on the bus after its release slot, so a later
    # transfer is valid even though that later slot releases no new entry.
    two = Stimulus(queues={"in": [(5,)]}, slots=[_drive(), EventSlot()])
    AuditedTrace(two, records=[SlotRecord(), SlotRecord(accepted={"in": (5,)})])

    # Without an earlier release, the same record invents a presentation.
    unreleased = Stimulus(queues={"in": [(5,)]}, slots=[EventSlot()])
    with pytest.raises(Refused, match="trace-transfer-without-released-input"):
        AuditedTrace(unreleased, records=[SlotRecord(accepted={"in": (5,)})])

    # And the well-formed run passes.
    AuditedTrace(two, records=[SlotRecord(accepted={"in": (5,)}), SlotRecord()])


# ── offered is not accepted ──

def test_offered_and_accepted_are_different_counts_and_live_in_different_records():
    """The score counts accepted input-endpoint transfers. A row list has one entry per
    offer and no place to say the handshake did not close, which is why the count
    production reports today is a count of rows."""
    s = Stimulus(queues={"in": [(1,), (2,)]}, slots=[_drive(), _drive()])
    trace = AuditedTrace(s, records=[SlotRecord(accepted={"in": (1,)}),
                                     SlotRecord(accepted={"in": None})])
    assert s.offered() == 2
    assert trace.accepted_count() == 1


def test_an_all_bottom_slot_keeps_its_place_and_adds_no_transaction():
    """T3-01: an all-bottom accepted batch preserves its slot but adds no transaction."""
    s = Stimulus(queues={"in": [(1,)]}, slots=[EventSlot(), _drive(), EventSlot()])
    trace = AuditedTrace(s, records=[SlotRecord(), SlotRecord(accepted={"in": (1,)}),
                                     SlotRecord(accepted={"in": None})])
    assert len(trace.records) == 3
    assert trace.accepted_count() == 1


def test_a_trace_shorter_than_its_stimulus_is_refused_unless_it_says_it_is_incomplete():
    """A trace that stops early and does not say so is compared as if it were a whole
    run of a shorter stimulus."""
    s = Stimulus(queues={"in": [(1,), (2,)]}, slots=[_drive(), _drive()])
    with pytest.raises(Refused, match="trace-slot-count"):
        AuditedTrace(s, records=[SlotRecord()])
    AuditedTrace(s, records=[SlotRecord()], completion=Completion.INCOMPLETE)


def test_padding_after_a_failure_keeps_the_slots_and_does_not_score():
    """T3-01/T3-02: a supported artifact failure produces exactly `N` recorded slots and
    its concrete completion predicate stays false. Collapsing `padded` into `normal` is
    how analysis padding turns a failed concrete run into a passing one."""
    s = Stimulus(queues={"in": [(1,), (2,)]}, slots=[_drive(), _drive()])
    trace = AuditedTrace(s, records=[SlotRecord(accepted={"in": (1,)}), SlotRecord()],
                         completion=Completion.PADDED)
    assert len(trace.records) == len(s.slots)
    assert not trace.completion.scores
    assert Completion.NORMAL.scores


# ── canonical encoding ──

def test_a_stimulus_round_trips_byte_for_byte():
    """T3-01: round-trip serialization reproduces the complete stimulus under canonical
    encoding. A record that serialises two ways cannot be compared by its bytes, and
    every fingerprint here is over those bytes."""
    s = Stimulus(queues={"b": [(9, 8)], "a": [(1,), (2,)]},
                 slots=[EventSlot(offers=["a"], controls={"z": 0, "y": 1}),
                        EventSlot(reset=True, ticks=["clk2", "clk1"]),
                        EventSlot(offers=["a", "b"])],
                 memory_policy={"read_latency": 2}, initial_state={"policy": "reset"},
                 source="test")
    once = canonical_json(s.as_dict())
    twice = canonical_json(Stimulus.from_dict(s.as_dict()).as_dict())
    assert once == twice
    assert Stimulus.from_dict(s.as_dict()).fingerprint == s.fingerprint


def test_an_audited_trace_round_trips_byte_for_byte():
    s = Stimulus(queues={"in": [(1,)]}, slots=[_drive(), EventSlot()])
    trace = AuditedTrace(
        s, initial_state={"y": 0},
        records=[SlotRecord(accepted={"in": (1,)}, state={"y": 1},
                            outputs={"o": 3}, hits={"g": True}, monitor={"n": 1}),
                 SlotRecord(accepted={"in": None}, state={"y": 1})],
        completion=Completion.NORMAL, domain="dom-abc")
    once = canonical_json(trace.as_dict())
    twice = canonical_json(AuditedTrace.from_dict(trace.as_dict()).as_dict())
    assert once == twice
    assert AuditedTrace.from_dict(trace.as_dict()).fingerprint == trace.fingerprint


def test_key_order_does_not_change_the_fingerprint():
    """Two producers building the same stimulus in different orders must deduplicate."""
    a = Stimulus(queues={"a": [(1,)], "b": [(2,)]},
                 slots=[EventSlot(offers=["b", "a"], controls={"p": 1, "q": 2})])
    b = Stimulus(queues={"b": [(2,)], "a": [(1,)]},
                 slots=[EventSlot(offers=["a", "b"], controls={"q": 2, "p": 1})])
    assert a.fingerprint == b.fingerprint


def test_record_constructors_preserve_types_instead_of_coercing_them():
    """A run record is an observation, not a parser with permission to change it.

    The old constructors applied ``str``, ``bool`` and ``int``.  Consequently endpoint
    7 became endpoint ``"7"``, reset 1 became ``True``, and a payload ``True`` or
    ``"1"`` became integer 1.  The exact finite carrier preserves payload values and
    rejects fields whose own type is part of the record schema.
    """
    with pytest.raises(TypeError, match="exact strings"):
        EventSlot(offers=[7])
    with pytest.raises(TypeError, match="exact Boolean"):
        EventSlot(reset=1)
    with pytest.raises(TypeError, match="non-string key"):
        Stimulus(queues={7: [(1,)]})
    with pytest.raises(TypeError, match="exact string"):
        Stimulus(source=7)

    values = (True, 1, 1.0, "1")
    stimulus = Stimulus(queues={"in": [values]}, slots=[_drive()])
    assert stimulus.head("in", 0) == values
    assert stimulus.as_dict()["queues"][0][1][0] == list(values)
    assert Stimulus(
        queues={"in": [(True,)]}, slots=[_drive()]).fingerprint != Stimulus(
            queues={"in": [(1,)]}, slots=[_drive()]).fingerprint


def test_exact_payload_equality_does_not_identify_boolean_and_integer():
    stimulus = Stimulus(queues={"in": [(True,)]}, slots=[_drive()])
    with pytest.raises(Refused, match="trace-transfer-not-the-head"):
        AuditedTrace(
            stimulus, records=[SlotRecord(accepted={"in": (1,)})])

    signed_zero = Stimulus(queues={"in": [(-0.0,)]}, slots=[_drive()])
    with pytest.raises(Refused, match="trace-transfer-not-the-head"):
        AuditedTrace(
            signed_zero, records=[SlotRecord(accepted={"in": (0.0,)})])


def test_set_shaped_slot_fields_reject_duplicates_instead_of_deduplicating():
    for field in ("offers", "ticks", "resets"):
        with pytest.raises(ValueError, match="more than once"):
            EventSlot(**{field: ["clk", "clk"]})

    with pytest.raises(ValueError, match="more than once"):
        EventSlot.from_dict({
            "controls": [["ready", 0], ["ready", 1]],
        })
    with pytest.raises(ValueError, match="more than once"):
        Stimulus.from_dict({
            "queues": [["in", [[1]]], ["in", [[2]]]],
            "slots": [],
        })
    with pytest.raises(ValueError, match="more than once"):
        SlotRecord.from_dict({
            "state": [["q", 0], ["q", 1]],
        })


def test_nested_record_values_are_detached_and_immutable_all_the_way_down():
    import pickle

    nested = {"array": [1, {"valid": True}]}
    stimulus = Stimulus(
        queues={"in": [(nested,)]}, slots=[_drive()],
        memory_policy={"memory": nested})
    record = SlotRecord(
        accepted={"in": (nested,)}, state={"state": nested},
        outputs={"output": nested}, monitor={"monitor": nested})
    trace = AuditedTrace(
        stimulus, initial_state={"initial": nested}, records=[record])

    nested["array"][0] = 99
    nested["array"][1]["valid"] = False
    image = trace.as_dict()
    assert image["stimulus"]["queues"][0][1][0][0] == {
        "array": [1, {"valid": True}]}
    assert image["records"][0]["state"][0][1] == {
        "array": [1, {"valid": True}]}
    assert image["initial_state"][0][1] == {
        "array": [1, {"valid": True}]}

    frozen = dict(record.state)["state"]
    with pytest.raises(TypeError):
        frozen["array"] = ()
    with pytest.raises(TypeError):
        frozen["array"][0] = 99

    # The immutable carrier is also the spawn-worker carrier.  Its write protection
    # must not make a valid stimulus unpicklable or change its public image.
    restored = pickle.loads(pickle.dumps(trace))
    assert restored.as_dict() == image


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {1, 2}, object()])
def test_every_core_record_uses_the_same_closed_finite_value_carrier(bad):
    with pytest.raises((TypeError, ValueError), match="finite|non-finite"):
        EventSlot(controls={"control": bad})
    with pytest.raises((TypeError, ValueError), match="finite|non-finite"):
        Stimulus(memory_policy={"policy": bad})
    with pytest.raises((TypeError, ValueError), match="finite|non-finite"):
        SlotRecord(state={"state": bad})


def test_wire_records_reject_unknown_fields_and_wrong_record_types():
    class IterationTrap:
        def __iter__(self):
            raise AssertionError("the boundary iterated a candidate-owned object")

    class SlotSubclass(EventSlot):
        pass

    with pytest.raises(ValueError, match="unknown fields"):
        EventSlot.from_dict({"offers": [], "unrecorded": 1})
    with pytest.raises(TypeError, match="exact record"):
        SlotRecord.from_dict([("state", [])])
    with pytest.raises(TypeError, match="finite tuple or list"):
        Stimulus.from_dict({"slots": IterationTrap()})
    with pytest.raises(TypeError, match="finite tuple or list"):
        AuditedTrace.from_dict({"stimulus": Stimulus().as_dict(),
                                "records": IterationTrap()})
    with pytest.raises(TypeError, match="exact EventSlot"):
        Stimulus(slots=[SlotSubclass()])
    with pytest.raises(TypeError, match="exact Stimulus"):
        AuditedTrace(object())
    stimulus = Stimulus()
    with pytest.raises(TypeError, match="exact Completion"):
        AuditedTrace(stimulus, completion="normal")
    with pytest.raises(TypeError, match="exact string"):
        AuditedTrace(stimulus, domain=1)


def test_legacy_deserialization_preserves_values_and_rejects_bad_record_types():
    class TruthTrap:
        def __bool__(self):
            raise AssertionError("the boundary executed a candidate-owned truth test")

    got = from_legacy({"inputs": [[True, 1, 1.0, "1"]]}, endpoint="in")
    assert got.head("in", 0) == (True, 1, 1.0, "1")

    with pytest.raises(TypeError, match="exact string"):
        from_legacy({"inputs": [[1]]}, endpoint=1)
    with pytest.raises(TypeError, match="exact strings"):
        from_legacy({"interleave": [["tick", 1, []]]})
    with pytest.raises(TypeError, match="triple"):
        from_legacy({"interleave": [["tick", "clk"]]})
    with pytest.raises(TypeError, match="exact string"):
        from_legacy({"inputs": [[1]], "source": 1}, endpoint="in")
    with pytest.raises(TypeError, match="finite tuple or list"):
        from_legacy({"inputs": TruthTrap()}, endpoint="in")
    with pytest.raises(TypeError, match="tick payload"):
        from_legacy({"interleave": [["tick", "clk", object()]]})


@pytest.mark.parametrize("field,bad", [
    ("horizon", True),
    ("horizon", "4"),
    ("reset_hold_cycles", False),
    ("queue_bound", 0),
    ("adapter_version", 1),
])
def test_trace_domain_identity_refuses_coercible_policy_values(field, bad):
    values = {
        "contract_digest": "contract",
        "horizon": 4,
        "reset_hold_cycles": 3,
        "adapter_version": "adapter/1",
        "queue_bound": 4,
    }
    values[field] = bad
    with pytest.raises((TypeError, ValueError)):
        trace_domain(**values)


def test_trace_domain_identity_uses_the_full_policy_digest():
    assert trace_domain(
        "contract", horizon=4, reset_hold_cycles=3,
        adapter_version="adapter/1", queue_bound=4,
    ) == (
        "contract:"
        "a8e30a5ca65c561b71eb8d8566e00d064b07337e48b4499b336667fa9f1800aa"
    )


# ── the legacy boundary, and only there ──

def test_the_two_legacy_forms_together_are_refused_rather_than_resolved():
    """Preferring one silently picks a stimulus, and the two can differ in exactly the
    way that matters: a tick or a reset in the action list has no row to appear as."""
    with pytest.raises(Refused, match="legacy-witness-two-forms"):
        from_legacy({"inputs": [[1]], "interleave": [["drive", "", [1]]]})


def test_a_legacy_row_list_and_the_same_actions_with_a_tick_are_not_one_stimulus():
    rows = from_legacy({"inputs": [[5], [2]]}, endpoint="in")
    ticked = from_legacy({"interleave": [["drive", "", [5]], ["tick", "", []],
                                         ["drive", "", [2]]]}, endpoint="in")
    assert rows.offered() == ticked.offered() == 2
    assert rows != ticked, "the tick is a cycle the design ran"


def test_a_legacy_reset_action_becomes_a_slot():
    got = from_legacy({"interleave": [["drive", "", [5]], ["reset", "", []],
                                      ["drive", "", [2]]]}, endpoint="in")
    assert [s.reset for s in got.slots] == [False, True, False]


def test_an_action_the_boundary_does_not_know_is_refused():
    """Guessing would put a run in the record that never happened."""
    with pytest.raises(Refused, match="legacy-witness-unknown-action"):
        from_legacy({"interleave": [["poke", "", [1]]]})


def test_an_empty_or_unrecognisable_record_is_refused():
    with pytest.raises(Refused, match="legacy-witness-empty"):
        from_legacy({"source": "nothing here"})
    with pytest.raises(Refused, match="legacy-witness-not-a-record"):
        from_legacy([[1, 2]])


def test_the_boundary_records_the_controls_rather_than_leaving_them_to_a_replay():
    """A legacy record does not say what the back-pressure was -- that is the information
    the format cannot hold, and it is `docs/single_clock_semantics.md` item 3. The caller
    declares it and it is recorded, instead of a replay inventing one per run."""
    got = from_legacy({"inputs": [[1]]}, endpoint="in", controls={"out_ready": 0})
    assert got.slots[0].controls == (("out_ready", 0),)


def test_the_legacy_tick_boundary_survives_the_public_stimulus_roundtrip():
    """The surviving boundary keeps idle slots, not a lossy projection to rows."""
    import json

    plain = from_legacy({"inputs": [[5], [2]]}, endpoint="in")
    actions = {"interleave": [["drive", "", [5]], ["tick", "", []], ["drive", "", [2]]]}
    ticked = from_legacy(actions, endpoint="in")

    assert plain.queues == ticked.queues
    assert plain.offered() == ticked.offered() == 2
    assert len(plain.slots) == 2 and len(ticked.slots) == 3
    assert [slot.offers for slot in ticked.slots] == [("in",), (), ("in",)]
    restored = Stimulus.from_dict(json.loads(json.dumps(ticked.as_dict())))
    assert restored == ticked and restored != plain
    with pytest.raises(TypeError, match="drop_others"):
        from_legacy(actions, endpoint="in", drop_others=True)
