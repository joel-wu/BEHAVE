"""Streaming publication preserves exact bytes and validates before writing."""

import io
import json
import math

import pytest

from behave.sim.core.frozen import FrozenMap
from behave.sim.core.record_value import (
    FrozenValueMap,
    canonical_json,
    write_canonical_json,
    freeze_record_value,
    thaw_record_value,
)


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("record_paths", [(), ((),), (("nested",),)])
@pytest.mark.parametrize("value", [
    None, False, True, -4, 2**512, -0.0, 1e-150, "Ω\n\"文",
    [1, (2, 3), {"z": -0.0, "a": [None, True]}],
    FrozenMap({"nested": (1, 2)}),
    FrozenValueMap((("z", [1, 2]), ("a", {"é": 3}))),
])
def test_stream_preserves_standard_canonical_bytes(monkeypatch, fallback, record_paths, value):
    if fallback:
        monkeypatch.setattr(json.encoder, "c_make_encoder", None)
    expected = json.dumps(thaw_record_value(freeze_record_value(value)),
                          sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    stream = io.StringIO()
    write_canonical_json(value, stream, record_paths=record_paths)
    assert stream.getvalue() == expected == canonical_json(value)


@pytest.mark.parametrize("record_paths", [(), ((),), (("rows",),)])
@pytest.mark.parametrize("value", [
    math.nan, math.inf, {1: "not a string key"}, {False: 1}, {"bad": object()},
    {"set": {1}}, FrozenValueMap((("x", 1), ("x", 2))),
])
def test_stream_rejects_before_writing_any_output(record_paths, value):
    stream = io.StringIO()
    with pytest.raises((ValueError, TypeError)) as original:
        canonical_json(value)
    with pytest.raises(type(original.value)) as streamed:
        write_canonical_json(value, stream, record_paths=record_paths)
    assert str(streamed.value) == str(original.value)
    assert stream.getvalue() == ""


def test_write_finishes_synchronously_without_retaining_caller_values():
    value = {"records": [{"a": [1, 2]}]}
    expected = canonical_json(value)
    stream = io.StringIO()
    write_canonical_json(value, stream)
    value["records"][0]["a"].append(object())
    value[17] = "invalid new key"
    assert stream.getvalue() == expected


def test_stream_rejects_cycles_and_caller_subclasses():
    class UserList(list):
        def __iter__(self):
            raise AssertionError("do not call foreign iterators")

    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="reference cycle"):
        write_canonical_json(cycle, io.StringIO())
    with pytest.raises(TypeError, match="outside the finite record carrier"):
        write_canonical_json(UserList([1]), io.StringIO())


def test_large_output_does_not_need_one_joined_string():
    value = [{"a": index, "nested": [1, 2, 3]} for index in range(20_000)]
    class CountingStream:
        def __init__(self):
            self.characters = self.writes = self.largest = 0

        def writelines(self, chunks):
            for chunk in chunks:
                self.characters += len(chunk)
                self.writes += 1
                self.largest = max(self.largest, len(chunk))

    stream = CountingStream()
    write_canonical_json(value, stream)
    assert stream.characters == len(canonical_json(value))
    assert stream.writes > 1
    assert stream.largest < stream.characters


def test_declared_record_boundaries_preserve_nested_envelope_and_bound_writes():
    row = {"a": [None, True, False, -0.0, 2**512], "z": "Ω\\n文"}
    value = {"traces": [{"records": [row] * 1000,
                         "source_evidence": {"intervals": [{"event": 7}] * 1000}},
                        {"records": [], "metadata": {"extra": [1, 2]}}]}
    paths = (("traces", None, "records"),
             ("traces", None, "source_evidence", "intervals"))

    class RecordingStream(io.StringIO):
        def __init__(self):
            super().__init__()
            self.largest = 0
            self.writes = 0

        def write(self, value):
            self.largest = max(self.largest, len(value))
            self.writes += 1
            return super().write(value)

    stream = RecordingStream()
    write_canonical_json(value, stream, record_paths=paths)
    assert stream.getvalue() == canonical_json(value)
    assert stream.largest == len(canonical_json(row))
    assert stream.writes < 5000  # Whole records, not their individual JSON tokens.


def test_row_writer_still_rejects_a_late_cycle_before_any_output():
    row = {"values": []}
    row["values"].append(row)
    stream = io.StringIO()
    with pytest.raises(ValueError, match="reference cycle"):
        write_canonical_json({"rows": [{"ok": True}, row]}, stream,
                             record_paths=(("rows",),))
    assert stream.getvalue() == ""
