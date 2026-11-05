"""A worker witness must retain its initialization phase across native replay."""

import hashlib
import json
import pickle
from dataclasses import replace

import pytest

from behave.sim import api
from behave.sim.core.stimulus import canonical_json
from behave.sim.rtl.initial_request import InitialStateCoordinates
from behave.sim.rtl.initial_state import RTLInitialState
from behave.sim.rtl.startup_state import RTLStartupState

from .test_rtl_analysis_session import _proxy
from .test_trace_postprocessing import fixture

ARTIFACT = "a" * 64


def _choice(*, a=2):
    return RTLStartupState(RTLInitialState(ARTIFACT, [["saved", 2, 3]]),
        [["a", 2, a], ["clk", 1, 0], ["iv", 1, 0], ["ordy", 1, 0], ["rst", 1, 0]])


def test_postprocessor_retains_the_confirmed_startup_receipt():
    plan, stimulus, payload = fixture()
    choice = RTLStartupState(RTLInitialState(ARTIFACT, []), [["clk", 1, 0]])
    plan = replace(plan, identity=ARTIFACT, startup_state=choice)
    payload["traces"][0]["startup_state"] = choice.as_dict()
    trace = plan.decode(stimulus, json.dumps(payload).encode(), {})
    assert type(trace) is api.AuditedTrace
    assert trace.as_dict()["initialization"] == choice.as_dict()


def test_initialization_is_frozen_fingerprinted_and_survives_wire_roundtrip():
    choice = _choice()
    stimulus = api.Stimulus()
    image = choice.as_dict()
    trace = api.AuditedTrace(stimulus, initialization=image)
    image["pins"][0][2] = 0
    assert trace.as_dict()["initialization"] == choice.as_dict()
    other = api.AuditedTrace(stimulus, initialization=_choice(a=1).as_dict())
    settled = api.AuditedTrace(stimulus, initialization=choice.registers.as_dict())
    plain = api.AuditedTrace(stimulus)
    assert len({entry.fingerprint for entry in (trace, other, settled, plain)}) == 4
    for entry in (trace, other, settled, plain):
        restored = api.AuditedTrace.from_dict(json.loads(canonical_json(entry.as_dict())))
        assert restored.as_dict() == entry.as_dict()
        assert restored.fingerprint == entry.fingerprint
        assert pickle.loads(pickle.dumps(entry)).as_dict() == entry.as_dict()
        assert entry.fingerprint == hashlib.sha256(canonical_json(entry.as_dict()).encode()).hexdigest()
    assert "initialization" not in plain.as_dict()
    with pytest.raises(TypeError):
        api.AuditedTrace(stimulus, initialization=[])


@pytest.mark.parametrize("mode", ["missing", "registers", "pins", "phase"])
def test_construction_audit_rejects_a_missing_or_different_choice(mode):
    choice = _choice()
    image = choice.as_dict()
    if mode == "missing":
        image = None
    elif mode == "registers":
        image["registers"]["fields"][0][2] = 1
    elif mode == "pins":
        image["pins"][0][2] = 1
    else:
        image = choice.registers.as_dict()
    trace = api.AuditedTrace(api.Stimulus(), initial_state={"saved": 3}, initialization=image)
    with pytest.raises(api.Refused) as caught:
        choice.check_trace(trace)
    assert caught.value.refusal.kind == "rtl-initialization-receipt-mismatch"


def test_startup_registers_are_not_the_settled_initial_state():
    choice = _choice()
    # A legal initial block can overwrite saved before the first scheduled slot.
    trace = api.AuditedTrace(api.Stimulus(), initial_state={"saved": 0},
                            initialization=choice.as_dict())
    choice.check_trace(trace)
    with pytest.raises(api.Refused):
        choice.registers.check_trace(trace)
    settled = api.AuditedTrace(api.Stimulus(), initial_state={"saved": 0},
                              initialization=choice.registers.as_dict())
    with pytest.raises(api.Refused) as caught:
        choice.registers.check_trace(settled)
    assert caught.value.refusal.kind == "rtl-initial-state-readback-mismatch"


def test_startup_request_coordinates_cover_registers_and_all_pins():
    choice = _choice()
    initial = InitialStateCoordinates(ARTIFACT, {"saved": 2},
                                     startup_pins={name: width for name, width, _ in choice.pins.fields})
    common = initial.encode(choice)
    assert common == {"initial.rtl.saved": 3, "initial.pin.a": 2, "initial.pin.clk": 0,
                      "initial.pin.iv": 0, "initial.pin.ordy": 0, "initial.pin.rst": 0}
    assert initial.decode([common, common]).as_dict() == choice.as_dict()
    assert initial.bounds["initial.pin.a"] == (0, 3)
    with pytest.raises(TypeError):
        initial.bounds["initial.pin.a"] = (0, 10)
    with pytest.raises((TypeError, ValueError)):
        initial.encode({"saved": 3})
    for coordinate in common:
        bad = dict(common)
        del bad[coordinate]
        with pytest.raises(api.Refused):
            initial.decode([bad])
        bad = {**common, coordinate: common[coordinate] ^ 1}
        with pytest.raises(api.Refused):
            initial.decode([common, bad])


@pytest.mark.parametrize("startup", [False, True])
def test_parent_dispatches_the_worker_choice_without_changing_its_phase(monkeypatch, startup):
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    choice = _choice() if startup else _choice().registers
    stimulus, seen = api.Stimulus(), []
    proxy = _proxy()
    trace = api.AuditedTrace(stimulus, initial_state={"saved": 0 if startup else 3},
                            initialization=choice.as_dict())
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, "_raw_call",
        lambda *args, **kwargs: {"stimulus": stimulus.as_dict(), "initialization": choice.as_dict()})
    def replay(actual, **kwargs):
        seen.append((actual, kwargs))
        return trace
    proxy._context.source_trace = replay
    got_stimulus, got_trace = proxy._source_payload([{}])
    assert got_stimulus == stimulus and got_trace is trace
    key = "startup_state" if startup else "initial_state"
    assert set(seen[0][1]) == {key, "reuse"}
    assert seen[0][1]["reuse"] is False
    assert seen[0][1][key].as_dict() == choice.as_dict()


@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("bad", ["missing", "changed", "boolean", "float", "negative", "overflow", "collision"])
def test_both_request_codecs_reject_invalid_construction_coordinates(atomic, bad):
    from behave.sim.rtl.atomic_request import AtomicRequestCodec
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_request_layout import _codec, _request

    original = _codec(atomic, "out_ready")
    pins = {"a": 1, "clk": 1, "in_valid": 1, "out_ready": 1, "rst": 1}
    initial = InitialStateCoordinates(ARTIFACT, {"q": 3}, startup_pins=pins)
    if bad == "collision":
        with pytest.raises(api.Refused) as caught:
            original = _codec(atomic, "initial.pin.a")
            builder = AtomicRequestCodec if atomic else ReadyValidRequestCodec
            builder(original.contract, 2, original.schedule if atomic else 1, initial=initial)
        assert caught.value.refusal.kind == "rtl-request-coordinate-collision"
        return
    codec = (AtomicRequestCodec(original.contract, 2, original.schedule, initial=initial) if atomic else
             ReadyValidRequestCodec(original.contract, 2, 1, initial=initial))
    stimulus = original.decode(_request(original, atomic))
    startup = RTLStartupState(RTLInitialState.from_values(ARTIFACT, {"q": 3}, {"q": 0}),
                              sorted((name, width, 0) for name, width in pins.items()))
    rows = codec.encode(stimulus, initial_state=startup)
    assert codec.decode(rows).as_dict() == original.decode(_request(original, atomic)).as_dict()
    assert initial.decode(rows).as_dict() == startup.as_dict()
    coordinate = "initial.pin.a"
    if bad == "missing":
        del rows[0][coordinate]
    elif bad == "changed":
        # The initialization restriction is independent of the later schedule.
        with pytest.raises(api.Refused):
            initial.decode([rows[0], {**rows[0], coordinate: 1}])
        return
    else:
        rows[0][coordinate] = {"boolean": False, "float": 0.0, "negative": -1, "overflow": 2}[bad]
    with pytest.raises(api.Refused):
        codec.decode(rows)


@pytest.mark.parametrize("mutation", ["old-version", "bad-version", "old-startup", "extra", "boolean", "phase"])
def test_parent_rejects_malformed_worker_initialization_before_replay(monkeypatch, mutation):
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    proxy = _proxy()
    choice = _choice().as_dict()
    value = {"stimulus": api.Stimulus().as_dict(), "initialization": choice}
    if mutation == "old-version":
        value = {"stimulus": value["stimulus"], "initial_state": _choice().registers.as_dict()}
    elif mutation == "bad-version":
        choice["version"] = "rtl-startup-state/999"
    elif mutation == "old-startup":
        choice["version"] = "rtl-startup-state/1"
    elif mutation == "extra":
        choice["inferred_from_baseline"] = True
    elif mutation == "boolean":
        choice["pins"][0][2] = True
    else:
        choice["version"] = "rtl-initial-state/1"
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, "_raw_call", lambda *a, **k: value)
    proxy._context.source_trace = lambda *a, **k: pytest.fail("invalid initialization reached replay")
    with pytest.raises((TypeError, ValueError)):
        proxy._source_payload([{}])


def test_old_worker_envelopes_cannot_enter_the_new_session():
    from behave.sim.rtl.analysis_job import decode_analysis_reply

    with pytest.raises(ValueError, match="expected identity"):
        decode_analysis_reply({"version": "rtl-exact-analysis/24", "operation": "decode",
                               "status": "ok", "value": {}}, "decode")


@pytest.mark.parametrize("mutation", ["artifact", "width", "missing-pin", "extra-pin", "overlap"])
def test_construction_coordinate_layout_cannot_be_replaced_at_encode(mutation):
    choice = _choice()
    pins = {name: width for name, width, _ in choice.pins.fields}
    initial = InitialStateCoordinates(ARTIFACT, {"saved": 2}, startup_pins=pins)
    image = choice.as_dict()
    if mutation == "artifact":
        image["registers"]["artifact"] = "b" * 64
    elif mutation == "width":
        image["pins"][0][1] = 3
    elif mutation == "missing-pin":
        image["pins"].pop(0)
    elif mutation == "extra-pin":
        image["pins"].append(["z", 1, 0])
    else:
        with pytest.raises(ValueError, match="disjoint"):
            InitialStateCoordinates(ARTIFACT, {"saved": 2}, startup_pins={"saved": 2})
        return
    with pytest.raises(api.Refused):
        initial.encode(RTLStartupState.from_dict(image))
