"""Owned-row reuse changes allocation, not positions or carrier validation."""

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim.core.record_value import RecordRowDecoder, freeze_record_value, thaw_record_value
from behave.sim.core.stimulus import AuditedTrace, EventSlot, SlotRecord, Stimulus


@pytest.mark.parametrize('capacity', [0, 1, 2, 256])
def test_alternating_rows_keep_all_positions_and_values(monkeypatch, capacity):
    monkeypatch.setattr(defaults, 'SIM_RECORD_PROJECTION_CACHE_ENTRIES', capacity)
    first, second = {'value': [False, 0]}, {'value': [0, False]}
    rows = [first, second, first, second, dict(first)]
    calls = []

    def freeze(row):
        calls.append(row)
        return freeze_record_value(row)

    decode = RecordRowDecoder(freeze)
    result = [decode(row) for row in rows]
    assert [thaw_record_value(row) for row in result] == rows
    assert len(calls) == (3 if capacity >= 2 else 5)
    assert len(decode.recent) <= capacity
    if capacity >= 2:
        assert result[0] is result[2] and result[1] is result[3]
        assert result[0] is not result[4]  # equal but independently owned
    first['value'][0] = 99
    assert thaw_record_value(result[0]) == {'value': [False, 0]}


def test_bad_rows_are_not_retained_as_valid_projections():
    decode = RecordRowDecoder(EventSlot.from_dict)
    bad = {'unknown': True}
    with pytest.raises(ValueError):
        decode(bad)
    assert not decode.recent


def test_repeated_stimulus_slots_are_shared_only_inside_one_decode():
    first, second = EventSlot(reset=True).as_dict(), EventSlot(reset=False).as_dict()
    document = Stimulus().as_dict()
    document['slots'] = [first, second, first, second]
    result = Stimulus.from_dict(document)
    assert len(result.slots) == 4
    assert result.slots[0] is result.slots[2]
    assert result.slots[1] is result.slots[3]
    assert result.slots[0] is not Stimulus.from_dict(document).slots[0]
    assert result.as_dict() == document
    second['unknown'] = True
    with pytest.raises(ValueError):
        Stimulus.from_dict(document)


def test_trace_identity_projection_does_not_expand_repeated_schedule_rows():
    import hashlib

    from behave.sim.core.record_value import canonical_json

    slot = EventSlot(reset=False)
    trace = AuditedTrace(Stimulus(slots=[slot] * 100), records=[SlotRecord()] * 100)
    internal = trace._fields(shared_slots=True)['stimulus']['slots']
    assert all(row is internal[0] for row in internal)
    public = trace.as_dict()
    assert public['stimulus']['slots'][0] is not public['stimulus']['slots'][1]
    assert trace.fingerprint == hashlib.sha256(canonical_json(public).encode()).hexdigest()
    public['stimulus']['slots'][0]['reset'] = True
    assert trace.stimulus.slots[0].reset is False
    assert public['stimulus']['slots'][1]['reset'] is False
