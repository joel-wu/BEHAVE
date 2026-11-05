"""The interval index preserves the former exhaustive projection semantics."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from itertools import combinations
from multiprocessing.reduction import ForkingPickler

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim.core.catalogue import OccurrenceKey
from behave.sim.core.hit_vector import HitVectorCodec
from behave.sim.rtl.native_events import NativeEventPlan
from behave.sim.rtl.native_evidence_codec import pack
from behave.sim.rtl.native_observation import _events, _legacy_events
from behave.sim.rtl.source_bindings import BoundOccurrence, EventBindings, NativeHits
from behave.sim.rtl.source_projection import SourceOutcome


def _bits(events, count):
    data = bytearray((count + 7) // 8)
    for event in events:
        data[event // 8] |= 1 << (event % 8)
    return pack(bytes(data))


def linear_decode(bindings, events):
    """Independent reference: the pre-index implementation, kept only in tests."""
    seen = set()
    for event in events:
        if type(event) is not int or not 0 <= event < bindings.event_count:
            raise ValueError("unknown native source event")
        seen.add(event)
    active = []
    for binding in bindings.occurrences:
        if binding.event in seen:
            if any(parent not in seen for parent in binding.ancestors):
                raise ValueError("a native event is missing its enclosing control entry")
            active.append(binding)
    for item in bindings.projections:
        for event, parents in zip(item.events, item.ancestors, strict=True):
            if event in seen and any(parent not in seen for parent in parents):
                raise ValueError("a native source outcome is missing its enclosing control entry")
        if seen.intersection(item.events):
            active.append(item)
    return NativeHits(frozenset(item.occurrence for item in active),
                      frozenset(item.goal for item in active))


def key(name):
    return OccurrenceKey("rtl-source", (name,), "location")


def bindings():
    return EventBindings(8, (
        BoundOccurrence(key("process"), "entry", 0, ()),
        BoundOccurrence(key("nested"), "location", 3, (0, 1)),
    ), projections=(
        SourceOutcome(key("call-a"), "choice", (2, 4), ancestors=((0,), (0, 1))),
        # One event can implement multiple occurrences with different ancestry.
        SourceOutcome(key("call-b"), "choice", (4, 6), ancestors=((0, 2), (0, 1, 2))),
        SourceOutcome(key("call-c"), "another", (3,), ancestors=((0, 1),)),
        SourceOutcome(key("zero-trip-body"), "unreachable", (), ancestors=()),
    ))


def test_every_interval_matches_exhaustive_projection():
    mapping = bindings()
    for size in range(mapping.event_count + 1):
        for seen in combinations(range(mapping.event_count), size):
            try:
                expected = linear_decode(mapping, seen)
            except ValueError:
                with pytest.raises(ValueError, match="enclosing control entry"):
                    mapping.decode(iter(seen))
            else:
                assert mapping.decode(iter(seen)) == expected
                assert mapping.decode((*seen, *seen)) == expected
    assert key("zero-trip-body") not in mapping.decode(range(8)).occurrences
    assert {key("call-a"), key("call-b")} <= mapping.decode(range(8)).occurrences


@pytest.mark.parametrize("cache_entries", [0, 2, 256])
@pytest.mark.parametrize("byte_entries", [0, 1, 1024])
def test_every_bitmap_matches_independent_occurrence_projection(monkeypatch, cache_entries, byte_entries):
    monkeypatch.setattr(defaults, "SIM_RTL_EVENT_PROJECTION_CACHE_ENTRIES", cache_entries)
    monkeypatch.setattr(defaults, "SIM_RTL_EVENT_BYTE_PROJECTION_CACHE_ENTRIES", byte_entries)
    mapping = bindings()
    codec = HitVectorCodec(mapping.goal_catalogue)
    for bits in range(1 << mapping.event_count):
        events = [event for event in range(mapping.event_count) if bits >> event & 1]
        try:
            expected = linear_decode(mapping, events)
        except ValueError:
            with pytest.raises(ValueError, match="enclosing control entry"):
                mapping.decode_bitmap(bytes([bits]))
        else:
            decoded = codec.from_bits(mapping.decode_bitmap(bytes([bits])))
            assert {goal for goal, hit in decoded if hit} == expected.goals
            # Projection still retains all separate call/loop occurrences.
    assert mapping.decode(events).occurrences == expected.occurrences


def test_byte_projection_rechecks_cross_byte_ancestors_on_every_interval():
    mapping = EventBindings(17, (
        BoundOccurrence(key("child"), "child", 16, (0, 8)),
        BoundOccurrence(key("other"), "other", 8, (0,)),
    ))
    assert mapping.decode_bitmap(b"\x01\x01\x01")
    for bits in (b"\x00\x01\x01", b"\x01\x00\x01"):
        with pytest.raises(ValueError, match="enclosing control entry"):
            mapping.decode_bitmap(bits)
    assert mapping._byte_decoder.cache_info().hits > 0
    assert mapping._byte_decoder.cache_info().currsize <= 3


def test_bitmap_cache_is_local_to_the_exact_mapping():
    mapping = bindings()
    first = mapping.decode_bitmap(b"\xff")
    assert mapping.decode_bitmap(bytes(bytearray(b"\xff"))) is first
    assert mapping._bitmap_decoder.cache_info().hits == 1
    changed = replace(mapping, projections=())
    assert changed._bitmap_decoder is not mapping._bitmap_decoder
    assert HitVectorCodec(changed.goal_catalogue).from_bits(changed.decode_bitmap(b"\xff")) == (
        HitVectorCodec(changed.goal_catalogue).vector(linear_decode(changed, range(8)).goals))
    assert changed._bitmap_decoder.cache_info().misses == 1


@pytest.mark.parametrize("entries,byte_budget,capacity", [(2, 100, 2), (100, 4, 2),
                                                         (100, 1, 0), (0, 100, 0)])
def test_bitmap_cache_is_bounded_and_eviction_preserves_answers(monkeypatch, entries, byte_budget, capacity):
    monkeypatch.setattr(defaults, "SIM_RTL_EVENT_PROJECTION_CACHE_ENTRIES", entries)
    monkeypatch.setattr(defaults, "SIM_RTL_EVENT_PROJECTION_CACHE_BYTES", byte_budget)
    mapping = bindings()  # One event byte plus one goal byte per entry.
    values = (0, 1, 7, 255, 0, 255)
    for value in values:
        seen = [i for i in range(8) if value >> i & 1]
        assert HitVectorCodec(mapping.goal_catalogue).from_bits(mapping.decode_bitmap(bytes([value]))) == (
            HitVectorCodec(mapping.goal_catalogue).vector(linear_decode(mapping, seen).goals))
        info = mapping._bitmap_decoder.cache_info()
        assert info.maxsize == capacity and info.currsize <= capacity
    assert mapping._bitmap_decoder.cache_info().misses >= 5


def test_bitmap_cache_does_not_accept_mutation_or_cache_invalid_ancestry():
    mapping = bindings()
    mapping.decode_bitmap(b"\xff")
    for _ in range(2):
        with pytest.raises(ValueError, match="enclosing control entry"):
            mapping.decode_bitmap(b"\x14")
        with pytest.raises(ValueError, match="wrong width"):
            mapping.decode_bitmap(bytearray(b"\xff"))
    info = mapping._bitmap_decoder.cache_info()
    assert info.hits == 0 and info.misses == 3 and info.currsize == 1


def test_bitmap_cache_keeps_plan_serialization_and_threaded_answers():
    mapping = bindings()
    plan = NativeEventPlan(mapping, "a" * 64, 100)
    image, fields = plan.as_dict(), asdict(mapping)
    values = [bytes([x]) for x in (0, 1, 7, 255)] * 20
    expected = [HitVectorCodec(mapping.goal_catalogue).vector(linear_decode(
        mapping, [i for i in range(8) if value[0] >> i & 1]).goals) for value in values]
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = list(pool.map(mapping.decode_bitmap, values))
    assert [HitVectorCodec(mapping.goal_catalogue).from_bits(bits) for bits in got] == expected
    assert plan.as_dict() == image and asdict(mapping) == fields
    assert mapping._bitmap_decoder.cache_info().currsize == 4


@pytest.mark.parametrize("warmed", [None, "byte", "bitmap"])
def test_observer_worker_handoff_rebuilds_only_process_local_caches(warmed):
    mapping = bindings()
    pristine = bytes(ForkingPickler.dumps(mapping))
    plan = NativeEventPlan(mapping, "a" * 64, 100)
    image = plan.as_dict()
    if warmed == "byte":
        mapping._byte_decoder(0, 255)
    elif warmed == "bitmap":
        mapping.decode_bitmap(b"\xff")

    # The trusted trace-worker transport must also accept a plan after the
    # coordinator has decoded baseline/search feedback with that same plan.
    restored = ForkingPickler.loads(ForkingPickler.dumps(plan))
    assert restored.as_dict() == image
    assert bytes(ForkingPickler.dumps(mapping)) == pristine
    assert "_bitmap_decoder" not in restored.bindings.__dict__
    assert "_byte_decoder" not in restored.bindings.__dict__
    assert restored.bindings.decode_bitmap(b"\xff") == mapping.decode_bitmap(b"\xff")
    assert restored.bindings.decode(range(8)) == linear_decode(mapping, range(8))
    with pytest.raises(ValueError, match="enclosing control entry"):
        restored.bindings.decode_bitmap(b"\x14")
    assert restored.bindings._bitmap_decoder is not mapping._bitmap_decoder


@pytest.mark.parametrize("count", [0, 1, 7, 8, 9, 63, 64, 65, 1024])
def test_bitmap_coordinates_cross_byte_and_word_boundaries(count):
    mapping = EventBindings(count, tuple(
        BoundOccurrence(key(str(i)), f"goal:{i // 2}", i, () if i == 0 else (0,))
        for i in range(count)))
    codec = HitVectorCodec(mapping.goal_catalogue)
    for events in ([], list(range(count)), [0, count - 1] if count else []):
        value = sum(1 << event for event in set(events)).to_bytes((count + 7) // 8, "little")
        assert codec.from_bits(mapping.decode_bitmap(value)) == codec.vector(
            linear_decode(mapping, events).goals)
    if count > 1:
        with pytest.raises(ValueError, match="enclosing control entry"):
            mapping.decode_bitmap((1 << (count - 1)).to_bytes((count + 7) // 8, "little"))


def test_bitmap_does_not_need_the_named_interval_decoder(monkeypatch):
    mapping = bindings()
    expected = HitVectorCodec(mapping.goal_catalogue).vector(linear_decode(mapping, range(8)).goals)
    before = NativeEventPlan(mapping, "a" * 64, 100).as_dict()

    def forbidden(*args):
        raise AssertionError("live bitmap projection must not rebuild name sets")

    monkeypatch.setattr(EventBindings, "decode", forbidden)
    assert HitVectorCodec(mapping.goal_catalogue).from_bits(mapping.decode_bitmap(b"\xff")) == expected
    assert NativeEventPlan(mapping, "a" * 64, 100).as_dict() == before


@pytest.mark.parametrize("bits", [b"", b"\0\0", b"\x80", bytearray(b"\0"), None])
def test_bitmap_transport_width_and_padding_are_not_ignored(bits):
    with pytest.raises(ValueError):
        EventBindings(7, ()).decode_bitmap(bits)


@pytest.mark.parametrize("invalid", [-1, 8, True, False, 0.0, "0", None])
def test_unknown_transport_events_are_still_rejected(invalid):
    for mapping in (EventBindings(8, ()), bindings()):
        for decode in (mapping.decode, lambda events, mapping=mapping: linear_decode(mapping, events)):
            with pytest.raises(ValueError, match="unknown native source event"):
                decode([0, invalid])


def test_all_ancestries_checked_even_after_one_route_has_hit():
    # call-a is already hit by event 2. A later malformed hit for that same
    # occurrence must still be rejected, not short-circuited as already covered.
    mapping = bindings()
    with pytest.raises(ValueError, match="enclosing control entry"):
        mapping.decode([0, 2, 4])
    # call-a's event 4 is valid, but call-b's same event needs parent 2 as well.
    with pytest.raises(ValueError, match="enclosing control entry"):
        mapping.decode([0, 1, 4])


def test_derived_index_changes_neither_plan_image_nor_identity():
    mapping = bindings()
    plan = NativeEventPlan(mapping, "a" * 64, 100)
    image, fields = plan.as_dict(), asdict(mapping)
    mapping.decode(range(8))
    routes = mapping._event_routes
    assert mapping.decode([]) == NativeHits(frozenset(), frozenset())
    assert mapping.decode([7]) == NativeHits(frozenset(), frozenset())
    assert mapping._event_routes is routes
    assert plan.as_dict() == image and asdict(mapping) == fields
    assert NativeEventPlan.from_dict(image) == plan

    changed = replace(mapping, projections=())
    assert changed.decode(range(8)) == linear_decode(changed, range(8))
    assert changed._event_routes is not routes
    assert mapping.decode(range(8)) == linear_decode(mapping, range(8))


def test_independent_intervals_can_share_one_immutable_binding():
    mapping = bindings()
    intervals = [(), (0,), (0, 1, 2), tuple(range(8))] * 20
    expected = [linear_decode(mapping, events) for events in intervals]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(mapping.decode, intervals)) == expected


def test_interval_projection_does_not_rehash_unchanged_occurrence_keys(monkeypatch):
    mapping = bindings()
    intervals = [(), (0,), (0, 1, 2), tuple(range(8))]
    expected = [linear_decode(mapping, events) for events in intervals]
    mapping.decode(range(8))  # Prepare the mapping, not an interval-result cache.
    original_hash = OccurrenceKey.__hash__
    hashes = []

    def counted(key):
        hashes.append(key)
        return original_hash(key)

    monkeypatch.setattr(OccurrenceKey, "__hash__", counted)
    assert [mapping.decode(events) for events in intervals] == expected
    assert not hashes


def test_shared_event_checks_all_occurrence_and_projection_parents():
    mapping = EventBindings(5, (
        BoundOccurrence(key("left"), "shared", 4, (0,)),
        BoundOccurrence(key("right"), "shared", 4, (1,)),
    ), projections=(
        SourceOutcome(key("a"), "shared", (4,), ancestors=((2,),)),
        SourceOutcome(key("b"), "shared", (4,), ancestors=((3,),)),
    ))
    for size in range(6):
        for events in combinations(range(5), size):
            try:
                expected = linear_decode(mapping, events)
            except ValueError as error:
                with pytest.raises(ValueError) as caught:
                    mapping.decode(events)
                assert str(caught.value) == str(error)
            else:
                assert mapping.decode(events) == expected
    got = mapping.decode(range(5))
    assert got.goals == {"shared"}
    assert len(got.occurrences) == 4


def test_empty_transport_and_unimplemented_mapping_are_not_confused():
    assert EventBindings(0, ()).decode([]) == NativeHits(frozenset(), frozenset())
    malformed = replace(bindings(), projections=(
        SourceOutcome(key("missing"), "not-proved-false", (1,), ancestors=()),))
    with pytest.raises(ValueError, match="incomplete or malformed"):
        NativeEventPlan(malformed, "a" * 64, 100)


def test_sparse_evidence_decodes_every_byte_without_changing_coordinates():
    for count in (8, 9, 16, 1024):
        for value in range(256):
            expected = frozenset(bit for bit in range(8) if value & (1 << bit))
            if count > 8:
                expected |= {count - 1}
            assert _events(_bits(expected, count), count) == expected
        assert _events(_bits((), count), count) == frozenset()
    assert _events(_bits((), 0), 0) == frozenset()


@pytest.mark.parametrize("image,count", [("01", 0), ("00", 9), ("0002", 9), ("FF", 8)])
def test_sparse_evidence_keeps_width_padding_and_canonical_checks(image, count):
    with pytest.raises(ValueError):
        _legacy_events(image, count)
    if image == "FF":
        assert _events(pack(bytes.fromhex(image)), count) == frozenset(range(8))
    else:
        with pytest.raises(ValueError):
            _events(pack(bytes.fromhex(image)), count)
