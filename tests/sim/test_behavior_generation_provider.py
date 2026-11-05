"""Public source-Behavior guidance stays behind its closed named-job handle."""

from __future__ import annotations

import zlib

import pytest

from behave.sim import api
from behave.sim.core import reference as reference_module
from behave.sim.core.reference import LoadedReference, ReferenceArtifact
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.behavior import generation as generation_module
from behave.sim.engines.behavior import generation_job
from behave.sim.generation import ProbeSelection
from tests.support.reference import load

U8 = api.FiniteType(8)
U9 = api.FiniteType(9)


SOURCE = b"""\
class M:
    def reset(self):
        pass
    def process(self, a, b):
        if a < b:
            y = a + b
        else:
            y = a - b
        return {"y": y}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        left = slot["inputs"]["left"]["a"]
        right = slot["inputs"]["right"]["b"]
        return {"result": self.process(left, right)}
"""


FALLIBLE_SOURCE = SOURCE.replace(
    b"            y = a + b\n",
    b"            y = a // (b & 1)\n",
)


def _endpoint(name, direction, field, port, width):
    return api.Endpoint(
        name, direction, [api.Field(field, width, port)],
        api.ready_valid(direction, f"{name}_valid", f"{name}_ready"),
        clock="clk", kind=api.Kind.TRANSACTION)


def _contract():
    return api.TaskContract(
        endpoints=[
            _endpoint("left", api.Direction.IN, "left_data", "a", U8),
            _endpoint("right", api.Direction.IN, "right_data", "b", U8),
            _endpoint("result", api.Direction.OUT, "result_data", "y", U9),
        ],
        clocks=[api.ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=1)],
        source="a simultaneous two-endpoint transaction product")


def _realization():
    ports = [
        api.Port("a", 8, driven_by="environment", clock="clk"),
        api.Port("b", 8, driven_by="environment", clock="clk"),
        api.Port("y", 9, driven_by="artifact", clock="clk"),
        api.Port("left_valid", 1, driven_by="environment"),
        api.Port("left_ready", 1, driven_by="artifact"),
        api.Port("right_valid", 1, driven_by="environment"),
        api.Port("right_ready", 1, driven_by="artifact"),
        api.Port("result_valid", 1, driven_by="artifact"),
        api.Port("result_ready", 1, driven_by="environment"),
        api.Port("clk", 1, driven_by="environment"),
        api.Port("rst", 1, driven_by="environment"),
    ]
    return api.Realization(
        "device", ports,
        channel_ports={"left": ["a"], "right": ["b"], "result": ["y"]},
        identity="target-a")


def _stimulus():
    return api.Stimulus(
        queues={"left": [(2,)], "right": [(3,)]},
        slots=[
            api.EventSlot(reset=True),
            api.EventSlot(offers=("left", "right")),
            api.EventSlot(),
        ], source="two inputs accepted together")


def _trace(seed):
    if not dict(seed.queues):
        return api.AuditedTrace(
            seed, records=[api.SlotRecord() for _slot in seed.slots],
            artifact="target-a", domain="test-domain")
    return api.AuditedTrace(
        seed,
        records=[
            api.SlotRecord(),
            api.SlotRecord(accepted={"left": (2,), "right": (3,)}),
            api.SlotRecord(),
        ],
        artifact="target-a", domain="test-domain")


@pytest.mark.parametrize('prefix', ('path-proposal:', 'native-path:'))
def test_public_provider_selects_one_simultaneous_multiendpoint_occurrence(prefix):
    reference = load({"behavior.py": SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    spec = api.GenerationSpec(
        horizon=3, queue_bound=1, reset_hold_cycles=1, drain=1,
        stall_every=0)
    provider = api.prepare_behavior_generation(
        task, spec, reference, _trace, timeout_s=60, timeout_ms=20_000)

    with pytest.raises(TypeError):
        provider._source["sources"][0]["base64"] = "spliced"
    detached = provider._common_payload()
    detached["source"]["sources"][0]["base64"] = "spliced"
    assert provider._common_payload()["source"]["sources"][0]["base64"] \
        != "spliced"

    manifest = provider.catalogue_manifest()
    capability = provider.goal_capability_use()
    assert capability.source_catalogue is manifest
    assert capability.implementation == provider.policy
    assert tuple(capability.targets) == provider.targetable_goals
    assert set(provider.targetable_goals) == {
        goal_id for goal_id, goal in manifest.items()
        if goal.provider == "python-structural"
    }

    selected = provider.select(_stimulus())

    assert type(selected) is ProbeSelection
    assert selected.catalogue_digest == provider.catalogue_manifest().digest()
    assert selected.requests
    for request in selected.requests:
        assert {field.endpoint for field in request.mutable_fields} == {
            "left", "right"}
        assert request.catalogue_digest == selected.catalogue_digest
    request = next(r for r in selected.requests if r.occurrence.startswith(prefix))
    answered = provider.probe(_stimulus(), request)
    expected = probing.Outcome.PROPOSED if prefix == 'path-proposal:' else probing.Outcome.SAT
    assert answered.outcome is expected, answered.as_dict()
    assert (answered.goal is None) == (expected is probing.Outcome.PROPOSED)
    assert answered.candidate.slots == _stimulus().slots

    empty_seed = api.Stimulus(
        queues={}, slots=[api.EventSlot(reset=True), api.EventSlot(), api.EventSlot()],
        source="no accepted procedure occurrence")
    empty = provider.select(empty_seed)
    assert type(empty) is ProbeSelection
    assert not empty.requests
    assert empty.catalogue_digest == manifest.digest()


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('prefix', ('path-proposal:', 'native-path:'))
def test_named_generation_job_constructs_array_state_in_its_private_scratch(prefix):
    # Fresh source identity forces the guest's own construction witness, not a
    # parent-process cache or pytest's direct-launch fallback.
    source = SOURCE.replace(b"        pass\n", b"        self.values = [0, 0]\n").replace(
        b'        return {"y": y}',
        b'        self.values[0] = y\n        return {"y": self.values[0]}')
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                  {"behavior.py": source}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    spec = api.GenerationSpec(3, 1, 1, 1, 0)
    provider = api.prepare_behavior_generation(
        task, spec, reference, _trace, timeout_s=60, timeout_ms=20_000)
    with provider.search_scope(probe_budget=1):
        selected = provider.select(_stimulus())
        assert type(selected) is ProbeSelection and selected.requests
        request = next(r for r in selected.requests if r.occurrence.startswith(prefix))
        answer = provider.probe(_stimulus(), request)
        expected = probing.Outcome.PROPOSED if prefix == 'path-proposal:' else probing.Outcome.SAT
        assert answer.outcome is expected, answer.as_dict()
        assert (answer.goal is None) == (expected is probing.Outcome.PROPOSED)
        assert answer.candidate.slots == _stimulus().slots


def test_isolated_selection_removes_seed_hits_and_probes_coverage_before_safety():
    reference = load(
        {"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    spec = api.GenerationSpec(
        horizon=4, queue_bound=2, reset_hold_cycles=1, drain=1,
        stall_every=0)
    seed = api.Stimulus(
        queues={"left": [(2,), (4,)], "right": [(3,), (1,)]},
        slots=[
            api.EventSlot(reset=True),
            api.EventSlot(offers=("left", "right")),
            api.EventSlot(offers=("left", "right")),
            api.EventSlot(),
        ], source="two accepted source occurrences")

    def trace(offered):
        assert offered == seed
        return api.AuditedTrace(
            offered,
            records=[
                api.SlotRecord(),
                api.SlotRecord(accepted={"left": (2,), "right": (3,)}),
                api.SlotRecord(accepted={"left": (4,), "right": (1,)}),
                api.SlotRecord(),
            ], artifact="target-a", domain="test-domain")

    provider = api.prepare_behavior_generation(
        task, spec, reference, trace, timeout_s=60, timeout_ms=20_000)

    selected = provider.select(seed)
    manifest = provider.catalogue_manifest()

    assert type(selected) is ProbeSelection
    regular = tuple(r for r in selected.requests if r.rank not in selected.requests.explorations)
    contexts = tuple(r for r in selected.requests if r.rank in selected.requests.explorations)
    assert len(regular) == 12
    assert contexts and all(manifest[g].cls == 'coverage' for r in contexts for g in r.targets)
    for proposal, path, complete in zip(regular[::3], regular[1::3], regular[2::3], strict=True):
        assert proposal.occurrence == 'path-proposal:' + complete.occurrence
        assert path.occurrence == 'native-path:' + complete.occurrence
        assert proposal.targets == path.targets == complete.targets
        assert [f.as_dict() for f in proposal.mutable_fields] == [f.as_dict() for f in path.mutable_fields]
        assert [f.as_dict() for f in path.mutable_fields] == [f.as_dict() for f in complete.mutable_fields]
    classes = [
        next(iter({manifest[goal].cls for goal in request.targets}))
        for request in regular
    ]
    assert classes == ["coverage"] * 6 + ["safety"] * 6
    coverage = regular[0]
    safety = regular[-1]
    assert coverage.rank < safety.rank
    assert set(coverage.targets) < {
        goal for goal in provider.targetable_goals
        if manifest[goal].cls == "coverage"
    }, "the concrete seed's own coverage was not removed"

    answer = provider.probe(seed, safety)

    assert answer.outcome is probing.Outcome.SAT, answer.as_dict()
    assert answer.goal in safety.targets
    assert answer.candidate is not None


@pytest.mark.parametrize("operation", ["//", "%"])
@pytest.mark.parametrize('prefix', ('path-proposal:', 'native-path:'))
def test_stateful_partial_operation_remains_searchable_and_native_confirmed(operation, prefix):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_plan import plan_source

    source = SOURCE.replace(b"        pass\n", b"        self.count = 0\n").replace(
        b"        if a < b:\n            y = a + b\n        else:\n            y = a - b\n",
        f"        self.count += 1\n        y = 8 {operation} a\n".encode())
    reference = load({"behavior.py": source}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    seed = _stimulus()
    provider = api.prepare_behavior_generation(
        task, api.GenerationSpec(3, 1, 1, 1, 0), reference, _trace,
        timeout_s=60, timeout_ms=20_000)
    with provider.search_scope(probe_budget=1):
        selected = provider.select(seed)
        manifest = provider.catalogue_manifest()
        safety = next(request for request in selected.requests
                      if request.occurrence.startswith(prefix)
                      and all(manifest[goal].cls == "safety" for goal in request.targets))
        answer = provider.probe(seed, safety)
        expected = probing.Outcome.PROPOSED if prefix == 'path-proposal:' else probing.Outcome.SAT
        assert answer.outcome is expected, answer.as_dict()
        if expected is probing.Outcome.PROPOSED:
            assert answer.goal is None
        else:
            assert answer.goal in safety.targets
        queues = dict(answer.candidate.queues)
        assert queues["left"][0] == (0,)

    namespace, _sites, hits = instrumented.load_source(source.decode(), file="behavior.py")
    native = namespace["M"]()
    native.reset()
    hits.clear()
    with pytest.raises(ZeroDivisionError) as failed:
        native.process(a=queues["left"][0][0], b=queues["right"][0][0])
    assert native.count == 1
    plan = plan_source(source_model(source.decode(), file="behavior.py"), source.decode())
    failure_hit = instrumented.attribute_safety_failure(failed.value, plan.safety_sites)
    assert failure_hit in safety.targets
    if answer.goal is not None:
        assert answer.goal == failure_hit


def test_prepared_behavior_replays_are_consumed_without_unused_select_jobs(monkeypatch):
    reference = load({"behavior.py": SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    seed = _stimulus()
    spec = api.GenerationSpec(4, 2, 1, 1)
    replays, selections = [], []

    def trace(offered):
        replays.append(offered)
        return _trace(offered)

    provider = api.prepare_behavior_generation(task, spec, reference, trace, timeout_s=60)
    original = generation_module.BehaviorIsolatedGenerationProvider._select_from_trace

    def select(self, offered, observed, targets):
        selections.append(offered)
        return original(self, offered, observed, targets)

    monkeypatch.setattr(generation_module.BehaviorIsolatedGenerationProvider,
                        "_select_from_trace", select)
    provider.prepare_seeds((seed,))
    assert replays == [seed] and selections == []
    answer = provider.select(seed)
    assert type(answer) is ProbeSelection
    assert replays == [seed] and selections == [seed]
    assert provider._prepared_traces == {}


def test_remaining_safety_group_keeps_its_policy_rank():
    reference = load(
        {"behavior.py": FALLIBLE_SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    spec = api.GenerationSpec(
        horizon=4, queue_bound=2, reset_hold_cycles=1, drain=1,
        stall_every=0)
    provider = api.prepare_behavior_generation(
        task, spec, reference, _trace, timeout_s=60)
    manifest = provider.catalogue_manifest()
    targets = provider.targetable_goals
    coverage = {
        goal for goal in targets if manifest[goal].cls == "coverage"}

    groups = generation_module._remaining_goal_groups(
        manifest, targets, hits=coverage)

    assert len(groups) == 1
    assert groups[0][0] == 1
    assert {manifest[goal].cls for goal in groups[0][1]} == {"safety"}


def _probe_semantics(result):
    return {
        "outcome": result.outcome.value,
        "scope": result.scope.value,
        "goal": result.goal,
        "detail": result.detail,
        "candidate": (
            None if result.candidate is None else result.candidate.as_dict()),
    }


def test_behavior_context_cache_is_diagnostic_and_replay_remains_live(
        tmp_path, monkeypatch):
    reference = load({"behavior.py": SOURCE}, config={"class": "M"})
    task = api.admit(_contract(), _realization(), reference=reference)
    spec = api.GenerationSpec(
        horizon=3, queue_bound=1, reset_hold_cycles=1, drain=1,
        stall_every=0)
    root = tmp_path / "durable-contexts"
    replays = []

    def traced(seed):
        replays.append(seed.fingerprint)
        return _trace(seed)

    def run(timeout_s, timeout_ms):
        provider = api.prepare_behavior_generation(
            task, spec, reference, traced, timeout_s=timeout_s,
            timeout_ms=timeout_ms, cache_root=root)
        selected = provider.select(_stimulus())
        assert type(selected) is ProbeSelection and selected.requests
        answer = provider.probe(_stimulus(), selected.requests[0])
        return provider, selected.as_dict(), _probe_semantics(answer)

    cold, cold_selection, cold_answer = run(60, 5_000)
    cold_image = cold.cache_telemetry().as_dict()
    stored = {path: path.read_bytes() for path in root.glob("entries/*/*.json")}
    assert cold_image["counts"]["miss"] == 3
    assert len(stored) == 3
    assert [event["kind"] for event in cold_image["events"]] == [
        "layout", "select", "probe"]
    assert [event["detail"] for event in cold_image["events"]] == [
        "", "provider-context-reused", "provider-context-reused"]

    # Resource ceilings do not name a semantic context.  A fresh provider still
    # replays the seed, while all three pure build contexts are detached cache hits.
    warm, warm_selection, warm_answer = run(120, 10_000)
    warm_image = warm.cache_telemetry().as_dict()
    assert warm_selection == cold_selection
    assert warm_answer == cold_answer
    assert warm_image["counts"]["hit"] == 3
    assert replays == [_stimulus().fingerprint, _stimulus().fingerprint]
    assert {path: path.read_bytes() for path in stored} == stored

    # Record corruption is an ordinary miss and cannot change the semantic answer.
    for path in stored:
        path.write_text("{}")
    repaired, repaired_selection, repaired_answer = run(90, 7_000)
    assert repaired_selection == cold_selection
    assert repaired_answer == cold_answer
    assert repaired.cache_telemetry().as_dict()["counts"]["miss"] == 3

    # Even a complete storage outage only changes diagnostic events.  Every ordinary
    # isolated build, Select solve/decode, and Probe solve/decode still runs cold.
    from behave.sim import generation_cache as cache_module

    monkeypatch.setattr(
        cache_module.GenerationContextCache, "get_or_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cache offline")))
    failed, failed_selection, failed_answer = run(60, 5_000)
    assert failed_selection == cold_selection
    assert failed_answer == cold_answer
    assert failed.cache_telemetry().as_dict()["counts"]["error"] == 3


def test_behavior_context_carrier_has_optional_quota_and_one_complete_stream(
        monkeypatch):
    """The opaque optimization cannot turn malformed cache bytes into state."""

    magic = generation_job._CONTEXT_MAGIC
    payload = b"one detached worker context"
    encoded = magic + zlib.compress(payload)

    assert generation_job._decoded_context_image(encoded) == payload
    assert generation_job._decoded_context_image(encoded[:-1]) is None
    assert generation_job._decoded_context_image(encoded + b"trailing") is None
    assert generation_job._CONTEXT_IMAGE_MAX_BYTES is None
    assert generation_job._CONTEXT_WIRE_MAX_BYTES is None
    larger = b"worker context" * 100_000
    assert generation_job._decoded_context_image(magic + zlib.compress(larger)) == larger

    monkeypatch.setattr(generation_job, "_CONTEXT_IMAGE_MAX_BYTES", 32)
    oversized = magic + zlib.compress(b"x" * 33)
    assert generation_job._decoded_context_image(oversized) is None


def test_loaded_reference_retains_source_and_config_not_mutable_callers():
    body = bytearray(SOURCE)
    config = {"class": "M"}
    reference = load({"behavior.py": body}, config=config)
    before = reference.digest
    body[:] = b"class Other: pass\n"
    config["class"] = "Other"

    sources, carried = reference._generation_source_payload()
    sources["behavior.py"] = b"spliced"
    carried["class"] = "Spliced"
    sources2, carried2 = reference._generation_source_payload()

    assert reference.digest == before
    assert sources2 == {"behavior.py": SOURCE}
    assert carried2 == {"class": "M"}
    assert "base64" not in repr(reference.as_dict())


def test_loaded_reference_refuses_spliced_bytes_or_configuration():
    artifact = ReferenceArtifact(
        {"behavior.py": SOURCE}, config={"class": "M"},
        adapter="tests/splice", adapter_impl="tests/splice/1")

    with pytest.raises(Refused, match="source-payload-mismatch"):
        LoadedReference(
            artifact, lambda: object(),
            sources={"behavior.py": b"class Spliced: pass\n"},
            config={"class": "M"}, _token=reference_module._AUTHORISED)
    with pytest.raises(Refused, match="config-payload-mismatch"):
        LoadedReference(
            artifact, lambda: object(), sources={"behavior.py": SOURCE},
            config={"class": "Spliced"}, _token=reference_module._AUTHORISED)
