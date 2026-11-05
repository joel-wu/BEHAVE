"""Compact evidence must preserve every event, occurrence and interrupted phase."""

import base64
import copy
import hashlib
import json
import random
import zlib
from dataclasses import replace

import pytest

from behave.sim.core.record_value import canonical_json
from behave.sim.rtl.native_events import NativeEventPlan
from behave.sim.rtl.native_evidence_codec import event_bytes, events_from_bytes, pack, unpack
from behave.sim.rtl.native_observation import _events, read_native_evidence

from .test_rtl_event_decoding import _bits, bindings, linear_decode
from .test_rtl_native_observation import recorded
from .test_rtl_native_preparation import _catalogue


def test_owned_plan_encoding_matches_the_full_canonical_oracle():
    from behave.sim.rtl.native_events import VERSION

    plan = NativeEventPlan(bindings(), "a" * 64, 100)
    image = plan.as_dict()
    identity_image = {
        "version": VERSION, "build": plan.build_sha256,
        "collector": hashlib.sha256(plan._template.encode()).hexdigest(),
        "callback_limit": plan.callback_limit, "event_count": image["event_count"],
        "coordinates": image["coordinates"], "scope_root": plan.scope_root,
        "occurrences": image["occurrences"], "projections": image["projections"],
    }
    assert plan.identity == hashlib.sha256(canonical_json(identity_image).encode()).hexdigest()
    assert unpack(plan.evidence_image) == canonical_json(image).encode()


@pytest.mark.parametrize("count", [0, 1, 7, 8, 9, 63, 64, 65, 1024, 77095])
def test_full_empty_sparse_and_dense_vectors(count):
    rng = random.Random(count)
    for expected in (set(), set(range(count)),
                     set(rng.sample(range(count), min(count, 50)))):
        image = _bits(expected, count)
        assert _events(image, count) == expected
        # An independent per-byte oracle, including the final partial word.
        data = event_bytes(image, count)
        assert {i * 8 + b for i, byte in enumerate(data)
                for b in range(8) if byte & (1 << b)} == expected


def test_compression_retains_all_projection_and_ancestry_checks():
    mapping = bindings()
    for bits in range(256):
        events = {b for b in range(8) if bits & (1 << b)}
        decoded = _events(_bits(events, 8), 8)
        try:
            expected = linear_decode(mapping, events)
        except ValueError:
            with pytest.raises(ValueError, match="enclosing control entry"):
                mapping.decode(decoded)
        else:
            assert mapping.decode(decoded) == expected


@pytest.mark.parametrize("data,count", [(b"", 1), (b"\0\0", 8), (b"\x80", 7),
                                       (b"\0\x02", 9)])
def test_semantic_width_and_padding_still_reject(data, count):
    with pytest.raises(ValueError):
        _events(pack(data), count)


@pytest.mark.parametrize("mutation", ["truncate", "trailing", "second-stream", "oversized",
                                    "bad-base64", "base64-padding", "not-a-string"])
def test_malformed_compressed_vector_rejected(mutation):
    encoded = zlib.compress(b"\x01")
    if mutation == "truncate":
        encoded = encoded[:-1]
    elif mutation == "trailing":
        encoded += b"\0"
    elif mutation == "second-stream":
        encoded += zlib.compress(b"\0")
    elif mutation == "oversized":
        encoded = zlib.compress(b"\0" * 10000)
    image = base64.b64encode(encoded).decode()
    if mutation == "bad-base64":
        image += "\n"
    elif mutation == "base64-padding":
        image += "="
    elif mutation == "not-a-string":
        image = 1
    with pytest.raises(ValueError):
        event_bytes(image, 8)


def test_plan_reuse_compares_complete_mapping_and_keeps_its_index(monkeypatch):
    plan = NativeEventPlan(bindings(), "a" * 64, 100)
    plan.bindings.decode(range(8))
    routes = plan.bindings._event_routes
    image = plan.evidence_image
    assert NativeEventPlan.from_evidence(image) == plan
    assert replace(plan, callback_limit=200).evidence_image != image
    with monkeypatch.context() as patch:
        def no_rebuild(*args, **kwargs):
            pytest.fail("a matching prepared mapping must not be reconstructed")
        patch.setattr(NativeEventPlan, "from_dict", no_rebuild)
        restored = NativeEventPlan.from_evidence(image, expected=plan)
        assert restored is plan and restored.bindings._event_routes is routes
        # DEFLATE encoders/levels can differ without changing mapping meaning.
        other = base64.b64encode(zlib.compress(unpack(image), level=0)).decode()
        assert NativeEventPlan.from_evidence(other, expected=plan) is plan

    for field, replacement in (("callback_limit", True), ("event_count", 8.0),
                               ("scope_root", "FOREIGN")):
        changed = json.loads(unpack(image))
        changed[field] = replacement
        with pytest.raises(ValueError, match="different prepared collector"):
            NativeEventPlan.from_evidence(pack(canonical_json(changed).encode()), expected=plan)
    assert plan.evidence_image is image


def test_a_restored_mapping_retains_the_validated_complete_wire_image(monkeypatch):
    plan = NativeEventPlan(bindings(), "a" * 64, 100)
    image = plan.evidence_image
    # A persisted record can have a different lossless compression level.
    image = base64.b64encode(zlib.compress(unpack(image), level=0)).decode()
    restored = NativeEventPlan.from_evidence(image)

    def no_encoding(*args):
        pytest.fail("a validated complete mapping need not be encoded again")

    monkeypatch.setattr("behave.sim.rtl.native_events.pack", no_encoding)
    assert restored.evidence_image is image
    assert NativeEventPlan.from_evidence(image, expected=restored) is restored


@pytest.mark.eda("g++")
def test_v1_and_current_evidence_agree_when_clock_return_is_quiet(recorded):
    plan, stimulus, image, _ = recorded
    legacy = copy.deepcopy(image)
    legacy["version"] = "rtl-native-observation/1"
    legacy.pop("completed_steps")
    legacy["plan"] = plan.as_dict()
    legacy["execution"].update(version="rtl-scheduled-execution-native/3",
        clock_return="inactive-edge-settles-before-next-drive",
        structural_sample="native-control-and-clock-events-with-occurrence-evidence")
    legacy["execution"]["phases"] = legacy["execution"]["phases"][:5] + ["record-successor"]
    # Equal immutable interval rows may share storage. Build the legacy wire
    # rows independently instead of decoding the same aliased row twice.
    legacy["intervals"] = [
        {**row, "events": event_bytes(row["events"], plan.bindings.event_count).hex()}
        for row in image["intervals"]
    ]
    assert read_native_evidence(legacy, stimulus, _catalogue(plan), expected_plan=plan) == \
        read_native_evidence(image, stimulus, _catalogue(plan), expected_plan=plan)


def test_native_word_decoding_includes_out_of_range_transport_bits():
    # The collector validates before trimming the final machine word for wire
    # storage. Nonzero padding must not silently disappear at that boundary.
    mapping = bindings()
    events = events_from_bytes(b"\0" * 7 + b"\x80")
    assert events == {63}
    with pytest.raises(ValueError, match="unknown native source event"):
        mapping.decode(events)


@pytest.mark.eda("g++")
@pytest.mark.parametrize("whole_history", [False, True])
def test_bound_source_audit_parses_the_complete_mapping_once(recorded, monkeypatch, whole_history):
    from behave.sim.core.stimulus import AuditedTrace, SlotRecord
    from behave.sim.rtl.initial_request import InitialStateCoordinates
    from behave.sim.rtl.observation_binding import ObservationBinding
    from behave.sim.rtl.source_trace import read_source_trace, read_source_history
    from behave.sim.rtl import native_observation

    plan, stimulus, evidence, vectors = recorded
    catalogue = _catalogue(plan)
    artifact, contract = "a" * 64, "b" * 64
    binding = ObservationBinding(artifact, contract, catalogue.digest(),
                                 plan.identity, "c" * 64)
    trace = AuditedTrace(stimulus, artifact=artifact, domain=contract,
                         records=[SlotRecord(hits=v) for v in vectors],
                         source_evidence=evidence)
    parses = []
    intervals = []
    original = NativeEventPlan.from_dict
    read_intervals = native_observation._read_intervals

    def parsed(cls, value):
        parses.append(value)
        return original(value)

    def counted(*args, **kwargs):
        for phase in read_intervals(*args, **kwargs):
            intervals.append(phase)
            yield phase

    monkeypatch.setattr(NativeEventPlan, "from_dict", classmethod(parsed))
    monkeypatch.setattr(native_observation, "_read_intervals", counted)
    reader = read_source_history if whole_history else read_source_trace
    options = {} if whole_history else {"prefix": "test"}
    result = reader(stimulus, trace, InitialStateCoordinates(artifact, {}),
                    catalogue, observation=binding, **options)
    assert result.hits == (frozenset({"goal:0", "goal:1"}), frozenset({"goal:1"}))
    assert len(intervals) == 1 + 3 * len(stimulus.slots)
    assert result.phases == (tuple(intervals) if whole_history else ())
    assert result.occurrences == (
        frozenset(b.occurrence for b in plan.bindings.occurrences[:2]),
        frozenset({plan.bindings.occurrences[1].occurrence}))
    assert len(parses) == 1
