"""Reuse canonical bytes without changing wire records or identity checks."""
import hashlib
import pickle

import pytest

from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.memory import MemoryEnvironment, MemoryRun
from behave.sim.core.stimulus import EventSlot, Stimulus, canonical_json


def test_referenced_wire_slots_keep_positions_identity_and_immutable_sharing():
    row = EventSlot(controls={"ready": 1}).as_dict()
    changed = EventSlot(controls={"ready": 0}).as_dict()
    image = {"slots": [row] * 100 + [changed, row]}
    decoded = Stimulus.from_dict(image)
    independently_decoded = Stimulus(slots=[EventSlot.from_dict(item) for item in image["slots"]])
    assert decoded.as_dict() == independently_decoded.as_dict()
    assert decoded.fingerprint == independently_decoded.fingerprint
    assert decoded.slots[0] is decoded.slots[99]
    assert decoded.slots[100].controls == (("ready", 0),)
    assert decoded.slots[101].controls == (("ready", 1),)
    row["controls"][0][1] = 7
    assert decoded.slots[0].controls == (("ready", 1),)


def test_internal_slot_images_share_but_public_views_remain_independent(monkeypatch):
    row = EventSlot(controls={"ready": 1})
    other = EventSlot(controls={"ready": 0})
    project = EventSlot.as_dict
    calls = []

    def counted(self):
        calls.append(self)
        return project(self)

    monkeypatch.setattr(EventSlot, "as_dict", counted)
    stimulus = Stimulus(slots=[row] * 100 + [other, row])
    assert len(calls) == 3
    private = stimulus._image(shared_slots=True)
    public = stimulus.as_dict()
    assert canonical_json(private) == canonical_json(public)
    assert private["slots"][0] is private["slots"][99]
    assert public["slots"][0] is not public["slots"][99]
    public["slots"][0]["controls"][0][1] = 2
    assert public["slots"][1]["controls"] == [["ready", 1]]
    assert stimulus.slots[0].controls == (("ready", 1),)
from behave.sim.generation import stimulus_bytes


def _uncached(stimulus):
    body = stimulus.as_dict()
    body.pop("source")
    return canonical_json(body).encode("utf-8")


def _cases():
    yield Stimulus()
    yield Stimulus(queues={"in": [(True,), (1,), (-0.0,), (0.0,)]},
                   slots=[EventSlot(offers=["in"])] * 4)
    yield Stimulus(
        queues={"in": [({"é": [1, False, None, 1.25], "x": (3, 4)},)]},
        slots=[EventSlot(reset=True), EventSlot(offers=["in"],
                                                controls={"ready": 0})],
        initial_state={"policy": "after-reset"}, memory_policy={"latency": 2},
        source="test 来源")
    yield Stimulus(memory_environment=MemoryEnvironment([
        MemoryRun("ram", initial={0: 7, 16: 123}, read_latency=3)]))
    schedule = GlobalSchedule(
        [ClockTiming("a", 2), ClockTiming("b", 3)],
        [GlobalEventSlot(0, ("a", "b")), GlobalEventSlot(2, ("a",)),
         GlobalEventSlot(3, ("b",))])
    yield Stimulus(slots=[slot.event for slot in schedule.slots],
                   clock_schedule=schedule)


@pytest.mark.parametrize("stimulus", list(_cases()))
def test_bytes_and_digest_match_original_projection_and_roundtrips(stimulus):
    expected = _uncached(stimulus)
    for item in (stimulus, Stimulus.from_dict(stimulus.as_dict()),
                 pickle.loads(pickle.dumps(stimulus))):
        assert stimulus_bytes(item) == expected
        assert item.identity_bytes == expected
        assert hashlib.sha256(expected).hexdigest() == item.fingerprint
        assert item.as_dict() == stimulus.as_dict()
        assert "_identity_bytes" not in item.as_dict()


def test_repeated_reads_do_not_traverse_the_schedule(monkeypatch):
    stimulus = Stimulus(slots=[EventSlot(controls={"ready": 1})] * 4096)
    expected = _uncached(stimulus)

    def no_projection(*_args, **_kwargs):
        raise AssertionError("reading canonical bytes rebuilt the record")

    monkeypatch.setattr(Stimulus, "as_dict", no_projection)
    monkeypatch.setattr(EventSlot, "as_dict", no_projection)
    for _ in range(32):
        assert stimulus_bytes(stimulus) is stimulus.identity_bytes
        assert stimulus_bytes(stimulus) == expected


def test_nested_inputs_and_returned_views_cannot_change_cached_identity():
    payload = {"items": [1, {"enabled": True}]}
    stimulus = Stimulus(queues={"in": [(payload,)]},
                        slots=[EventSlot(offers=["in"])])
    expected = _uncached(stimulus)
    payload["items"][1]["enabled"] = False
    view = stimulus.as_dict()
    view["queues"][0][1][0][0]["items"].append(999)
    view["slots"][0]["offers"].clear()
    assert _uncached(stimulus) == expected == stimulus_bytes(stimulus)
    for name in ("identity_bytes", "_identity_bytes", "queues"):
        with pytest.raises(AttributeError):
            setattr(stimulus, name, b"changed")
        with pytest.raises(AttributeError):
            delattr(stimulus, name)
    with pytest.raises(AttributeError):
        stimulus.__init__()


def test_provenance_is_excluded_but_semantic_types_are_not():
    a = Stimulus(queues={"in": [(True,)]}, source="baseline")
    b = Stimulus(queues={"in": [(True,)]}, source="solver")
    c = Stimulus(queues={"in": [(1,)]}, source="solver")
    assert a.as_dict() != b.as_dict()
    assert stimulus_bytes(a) == stimulus_bytes(b)
    assert stimulus_bytes(a) != stimulus_bytes(c)


def test_byte_key_does_not_trust_the_display_digest():
    a = Stimulus(queues={"in": [(1,)]})
    b = Stimulus(queues={"in": [(2,)]})
    # The scheduler separately rejects this mismatch before merging provenance.
    object.__setattr__(b, "_fingerprint", a.fingerprint)
    assert a.fingerprint == b.fingerprint
    assert stimulus_bytes(a) != stimulus_bytes(b)
    assert hashlib.sha256(stimulus_bytes(b)).hexdigest() != b.fingerprint


def test_subclass_cannot_supply_a_different_identity():
    class PretendStimulus(Stimulus):
        @property
        def identity_bytes(self):
            return b"{}"

    with pytest.raises(TypeError, match="expected Stimulus"):
        stimulus_bytes(PretendStimulus())
