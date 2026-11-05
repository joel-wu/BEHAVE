"""Runtime guards preserve candidate attribution without hiding service faults."""

import json

import pytest

from behave.sim.behavior.isolated import Broken, IsolatedModel, Limits
from behave.sim.core.failure import CandidateFault


LIMITS = Limits(seconds=3, memory_bytes=512 << 20, wall_seconds=8)


def cache_model(phase, *, instance=False):
    owner = "self" if instance else "M"
    update = f"{owner}._GRID = (1, 2)"
    return f"""class M:
    _GRID = None
    def __init__(self):
        {update if phase == '__init__' else 'pass'}
    def reset(self):
        {update if phase == 'reset' else 'pass'}
    def process(self, a):
        {update if phase == 'process' else 'pass'}
        return {{'y': a}}
"""


@pytest.mark.isolation
@pytest.mark.parametrize("snapshot_policy", ("eager", "on-demand"))
@pytest.mark.parametrize("phase", ("__init__", "reset", "process"))
def test_class_cache_mutation_is_a_candidate_fault(phase, snapshot_policy):
    with pytest.raises(CandidateFault) as caught:
        with IsolatedModel(cache_model(phase), cls="M", limits=LIMITS,
                           snapshot_policy=snapshot_policy) as model:
            model.reset()
            model.process(a=7)
    assert caught.value.refusal.kind == "behavior-runtime-invariant"
    assert caught.value.refusal.why == "the model class table changed"


@pytest.mark.isolation
@pytest.mark.parametrize("snapshot_policy", ("eager", "on-demand"))
def test_instance_cache_and_reference_class_cache_remain_valid(snapshot_policy):
    for instance, strict in ((True, True), (False, False)):
        with IsolatedModel(cache_model("process", instance=instance), cls="M",
                           limits=LIMITS, strict_module=strict,
                           snapshot_policy=snapshot_policy) as model:
            model.reset()
            assert model.process(a=7) == {"y": 7}


@pytest.mark.parametrize("explicit_detail", (False, True))
def test_runtime_failure_does_not_publish_partial_state(monkeypatch, explicit_detail):
    model = object.__new__(IsolatedModel)
    model._state = {"last_good": 7}
    model._hits = ["last-good-hit"]
    closed = []
    reply = {"runtime_failure": {"kind": "behavior-runtime-invariant",
             "why": "the model class table changed"}}
    if explicit_detail:
        reply["runtime_failure"]["detail"] = {}
    monkeypatch.setattr(model, "_read_with_deadline", lambda: json.dumps(reply).encode())
    monkeypatch.setattr(model, "close", lambda: closed.append(True))
    with pytest.raises(CandidateFault):
        model._hear()
    assert closed == [True]
    assert model._state == {"last_good": 7}
    assert model._hits == ["last-good-hit"]


@pytest.mark.parametrize("record", [
    None,
    {"kind": "worker-crashed", "why": "service failed", "detail": {}},
    {"kind": "behavior-runtime-invariant", "why": "", "detail": {}},
    {"kind": "behavior-runtime-invariant", "why": "changed", "detail": []},
    {"kind": "behavior-runtime-invariant", "why": "changed", "detail": {}, "extra": 1},
])
def test_malformed_runtime_failure_is_still_a_service_fault(monkeypatch, record):
    model = object.__new__(IsolatedModel)
    closed = []
    monkeypatch.setattr(model, "_read_with_deadline",
                        lambda: json.dumps({"runtime_failure": record}).encode())
    monkeypatch.setattr(model, "close", lambda: closed.append(True))
    with pytest.raises(Broken):
        model._hear()
    assert closed == [True]
