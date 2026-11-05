"""Catalogue-bound hit vectors stay exact without per-goal Python records."""

import base64
import itertools

import pytest

from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING, HitVector, _reached_union, _unreadable_union
from behave.sim.core.refusal import Refused
from behave.sim.core.stimulus import AuditedTrace, EventSlot, SlotRecord, Stimulus


@pytest.mark.parametrize("offset", (0, 8, 24))
def test_iteration_matches_all_three_valued_byte_patterns(offset):
    catalogue = tuple(f"goal.{index:02d}" for index in range(32))
    for values in itertools.product((False, True, None), repeat=8):
        reached = bytearray(4)
        unreadable = bytearray(4)
        reached[offset // 8] = sum(1 << bit for bit, value in enumerate(values) if value is True)
        unreadable[offset // 8] = sum(1 << bit for bit, value in enumerate(values) if value is None)
        vector = HitVector(catalogue, bytes(reached), bytes(unreadable))
        expected = tuple((catalogue[offset + bit], value)
                         for bit, value in enumerate(values) if value is not False)

        assert tuple(vector) == expected
        assert tuple(vector) == expected  # Iteration never consumes the stored bits.
        assert len(vector) == len(expected)
        assert _unreadable_union([vector]) == {name for name, value in expected if value is None}
        assert _reached_union([vector]) == {name for name, value in expected if value is True}


def test_unreadable_union_never_expands_reached_names(monkeypatch):
    catalogue = tuple(f"g:{i:04d}" for i in range(1000))
    vectors = [HitVector.from_names(catalogue, catalogue[:-1]),
               HitVector.from_names(catalogue, unreadable=catalogue[-1:])]
    def forbidden(_self):
        raise AssertionError("unknown audit must not enumerate reached names")
    monkeypatch.setattr(HitVector, "__iter__", forbidden)
    assert _unreadable_union(iter(vectors)) == {catalogue[-1]}
    assert _unreadable_union([]) == frozenset()
    assert _reached_union([]) == frozenset()


@pytest.mark.parametrize("size", (0, 1, 7, 8, 9, 63, 64, 65, 418, 1000))
def test_iteration_keeps_cross_byte_order_padding_and_wire_identity(size):
    catalogue = tuple(f"goal.{index:04d}" for index in range(size))
    for stride in (1, 7, 8, 9, max(1, size + 1)):
        reached = {name for index, name in enumerate(catalogue) if index % stride == 0}
        unreadable = {name for index, name in enumerate(catalogue)
                      if index % stride != 0 and index % 5 == 0}
        vector = HitVector.from_names(catalogue, reached, unreadable)
        expected = tuple((name, True if name in reached else None)
                         for name in catalogue if name in reached or name in unreadable)
        wire = vector.as_dict()

        assert tuple(vector) == expected
        assert dict(vector) == dict(expected)
        assert vector.as_dict() == wire
        assert tuple(HitVector.from_dict(wire)) == expected


def test_dense_and_sparse_vectors_have_the_same_bounded_wire_size():
    catalogue = tuple(f"goal.{index:04d}" for index in range(1000))
    sparse = HitVector.from_names(catalogue, {catalogue[0]})
    dense = HitVector.from_names(catalogue, set(catalogue[:-1]))

    sparse_wire = sparse.as_compact_dict()
    dense_wire = dense.as_compact_dict()
    assert {len(value) for value in sparse_wire.values()} == {
        len(value) for value in dense_wire.values()}
    assert len(base64.b64decode(dense_wire["true"])) == 125
    assert dict(sparse) == {catalogue[0]: True}
    assert len(dense) == 999


def test_hit_vector_round_trips_and_rejects_ambiguous_bits():
    catalogue = ("a", "b", "c")
    vector = HitVector.from_names(catalogue, {"a"}, {"c"})

    assert HitVector.from_dict(vector.as_dict()) == vector
    assert HitVector.from_compact_dict(
        catalogue, vector.as_compact_dict()) == vector
    assert tuple(vector) == (("a", True), ("c", None))

    overlap = vector.as_compact_dict()
    overlap["unknown"] = overlap["true"]
    with pytest.raises(ValueError, match="both reached and unreadable"):
        HitVector.from_compact_dict(catalogue, overlap)

    padding = vector.as_compact_dict()
    padding["true"] = base64.b64encode(bytes([0x80])).decode("ascii")
    with pytest.raises(ValueError, match="padding bits"):
        HitVector.from_compact_dict(catalogue, padding)


def test_audited_trace_carries_one_catalogue_for_all_slot_bitsets():
    catalogue = ("goal.false", "goal.true", "goal.unknown")
    records = (
        SlotRecord(hits=HitVector.from_names(catalogue, {"goal.true"})),
        SlotRecord(hits=HitVector.from_names(
            catalogue, unreadable={"goal.unknown"})),
    )
    trace = AuditedTrace(
        Stimulus({}, (EventSlot(), EventSlot())), records=records)

    wire = trace.as_dict()
    assert wire["hit_encoding"] == HIT_VECTOR_ENCODING
    assert wire["hit_catalogue"] == list(catalogue)
    assert set(wire["records"][0]["hits"]) == {"true", "unknown"}
    assert "catalogue" not in wire["records"][0]["hits"]

    restored = AuditedTrace.from_dict(wire)
    assert restored.as_dict() == wire
    assert restored.records == trace.records
    assert restored.fingerprint == trace.fingerprint
    assert dict(restored.records[0].hits) == {"goal.true": True}
    assert dict(restored.records[1].hits) == {"goal.unknown": None}


def test_trace_rejects_a_changed_catalogue_even_when_every_bit_is_false():
    records = (
        SlotRecord(hits=HitVector.from_names(("declared.false",))),
        SlotRecord(hits=HitVector.from_names(("foreign.false",))),
    )
    with pytest.raises(Refused, match="trace-hit-vector-catalogue-mismatch"):
        AuditedTrace(Stimulus({}, (EventSlot(), EventSlot())), records=records)
@pytest.mark.parametrize("count", [0, 1, 8, 9, 65])
def test_raw_bits_keep_catalogue_width_padding_and_overlap_checks(count):
    from behave.sim.core.hit_vector import HitVectorCodec
    codec = HitVectorCodec(tuple(sorted(f"g:{i}" for i in range(count))))
    size = (count + 7) // 8
    bits = ((1 << count) - 1).to_bytes(size, "little")
    assert codec.from_bits(bits) == codec.vector(codec.catalogue)
    with pytest.raises(ValueError):
        codec.from_bits(bits + b"\0")
    with pytest.raises(TypeError):
        codec.from_bits(bytearray(bits))
    if count:
        with pytest.raises(ValueError, match="both reached and unreadable"):
            codec.from_bits(bits, bits)
    if count % 8:
        with pytest.raises(ValueError, match="padding"):
            codec.from_bits(bytes([0xff]) * size)
