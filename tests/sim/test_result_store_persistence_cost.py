"""Publication preserves all evidence without walking unchanged verdicts again."""

import io
import json

import pytest

from behave.sim import result_store
from behave.sim.core.record_value import _json_mapping, freeze_record_value

from .test_wave1_closure import _concrete


def test_report_update_reuses_only_the_latched_immutable_fingerprint(tmp_path, monkeypatch):
    store = result_store.ResultStore(tmp_path)
    store.begin()
    store.decided(_concrete())
    authority = store.snapshot().authority
    original = result_store.canonical_json
    encoded = []

    def watched(value):
        encoded.append(value)
        return original(value)

    monkeypatch.setattr(result_store, "canonical_json", watched)
    store.commit(report_update={"analysis_status": "not-requested"})
    assert store.snapshot().authority is authority
    assert not encoded
    assert store.committed_decision().passed is True
    assert result_store.read_record(tmp_path)[0]["passed"] is True

    # A separately supplied replacement does not inherit this shortcut, even
    # if its values happen to match the stored authority.
    store._commit(authority=store.standing_authority())
    assert encoded


def test_initial_evidence_cannot_enter_with_a_forged_fingerprint(tmp_path):
    store = result_store.ResultStore(tmp_path)
    store.begin()
    record = _concrete().as_dict()
    record["evaluation_fingerprint"] = "not-the-evaluation"
    summary = {key: value for key, value in record.items() if key != "evaluation"}
    with pytest.raises(ValueError, match="fingerprint"):
        store._commit(authority=record, terminal=True,
                      report={"authority": summary, "result_kind": record["kind"],
                              "passed": record["passed"]},
                      progress={"authority": summary, "passed": record["passed"]})
    assert not store.snapshot().terminal


def test_terminal_evidence_and_its_report_cannot_be_changed(tmp_path):
    store = result_store.ResultStore(tmp_path)
    store.begin()
    store.decided(_concrete())
    original = result_store.read_record(tmp_path)
    replacement = store.standing_authority()
    replacement["evaluation"]["accepted"] += 1
    with pytest.raises(ValueError, match="already decided"):
        store._commit(authority=replacement)
    with pytest.raises(ValueError, match="record says"):
        store.commit(report_update={"passed": False})
    assert result_store.read_record(tmp_path) == original


def test_record_array_writer_is_byte_identical_to_standard_json():
    image = {"z": [True, 1, -0.0, "é\n\\", None], "a": {
        "catalogue": [{"id": n, "fields": [["x", n], ["flag", False]]}
                      for n in range(200)], "empty": []}}
    stream = io.StringIO()
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    units = []

    def encode(value):
        units.append(value)
        return encoder.encode(value)

    result_store._write_record_arrays(image, stream, encode)
    assert stream.getvalue() == encoder.encode(image)
    assert not any(value is image or value is image["a"]["catalogue"] for value in units)


def test_failed_record_encoding_never_replaces_the_committed_file(tmp_path):
    path = tmp_path / "result.json"
    result_store.write_json_atomically(path, {"kept": True}, record_arrays=True)
    with pytest.raises(ValueError):
        result_store.write_json_atomically(path, {"rows": [1, float("nan")]}, record_arrays=True)
    assert json.loads(path.read_text()) == {"kept": True}


def test_frozen_record_writer_retains_exact_public_bytes_and_detached_exports(tmp_path):
    image = {"z": [True, 1, -0.0, "é\n\\", None], "rows": [
        {"fields": [["x", n], ["flag", False]], "empty": []} for n in range(200)]}
    envelope = result_store.RunEnvelope("owned-record", 1.25, progress=image)
    expected = json.dumps(envelope.as_record(), ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    path = tmp_path / "result.json"
    result_store.write_json_atomically(path, envelope._record_fields(),
        default=_json_mapping, record_arrays=True)
    assert path.read_text() == expected
    assert envelope._record_fields()["progress"] is envelope.progress
    public = envelope.as_record()
    public["progress"]["rows"][0]["fields"][0][1] = -1
    assert envelope.as_record()["progress"] == image
    payload = freeze_record_value({"evidence": image})
    expected_stamp = json.dumps(envelope.stamped(payload), ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False)
    result_store.write_json_atomically(path, envelope._stamped_fields(payload),
        default=_json_mapping, record_arrays=True)
    assert path.read_text() == expected_stamp


def test_phase_publication_does_not_thaw_the_frozen_command(tmp_path, monkeypatch):
    command = {"queues": [[[n, n + 1] for n in range(100)]]}
    store = result_store.ResultStore(tmp_path, progress={"command": command})
    store.begin()
    frozen_command = store.snapshot().progress["command"]
    original = result_store.thaw_record_value
    calls = []

    def watched(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(result_store, "thaw_record_value", watched)
    store.record(phase_reached="new-phase")
    assert store.snapshot().progress["command"] is frozen_command
    assert not any(value is frozen_command or value is store.snapshot().progress for value in calls)
    assert json.loads(store.result_path.read_text())["progress"]["command"] == command
