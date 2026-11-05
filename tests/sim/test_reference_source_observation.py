"""Reference feedback comes from matching's existing execution, never a second run."""

import pytest

from behave.sim import api
from behave.sim.behavior.isolated import IsolatedModel
from behave.sim.core.reference_observation import (
    ReferenceSourceObservation,
    ReferenceSourceRecorder,
)
from behave.sim.evaluation import ReplayExecution
from tests.sim.test_api_path import Doubling, _ref, contract, realization, schedule

SOURCE = """class M:
    def reset(self):
        pass
    def process(self, in_a):
        if in_a & 1:
            y = in_a
        else:
            y = in_a
        return {"out_y": y}
"""
TRUE = "branch:behavior.py:5:11:0:T"
FALSE = "branch:behavior.py:5:11:0:F"


def _execution(reference, *, gain=1):
    task = api.admit(contract(), realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    return ReplayExecution(task, Doubling(gain), reference, catalogue)


def _reference():
    return api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                              {"behavior.py": SOURCE.encode()}, config={"class": "M"})


@pytest.mark.parametrize("has_observer", [False, True])
def test_reference_binding_does_not_serialize_trace_while_child_is_live(has_observer):
    from types import SimpleNamespace

    from behave.sim.core.reference_progress import ReferenceProgress

    live = True
    reads = []
    catalogue = api.CatalogueManifest(())
    recorder = ReferenceSourceRecorder(catalogue, lambda: ()) if has_observer else None
    bundle = SimpleNamespace(digest="reference", _source_recorder=lambda model: recorder)

    class Trace:
        @property
        def fingerprint(self):
            assert not live, "trace serialization consumed the reference execution lifetime"
            reads.append(True)
            return "trace"

    progress = ReferenceProgress(contract())
    assert progress.begin_source(bundle, object(), Trace()) is recorder
    progress.observe_source()
    assert reads == []
    live = False
    observed = progress.source_observation
    assert reads == ([True] if has_observer else [])
    if has_observer:
        assert observed == ReferenceSourceObservation("reference", "trace", catalogue, frozenset())
    else:
        assert observed is None


@pytest.mark.isolation
def test_comparison_serializes_trace_only_after_reference_close(monkeypatch):
    execution = _execution(_reference())
    original_init, original_close = IsolatedModel.__init__, IsolatedModel.close
    fingerprint = api.AuditedTrace.fingerprint.fget
    active = set()
    reads = []

    def opened(model, *args, **kwargs):
        original_init(model, *args, **kwargs)
        active.add(id(model))

    def closed(model):
        result = original_close(model)
        active.discard(id(model))
        return result

    def identify(trace):
        assert not active, "reference child was idle while its caller serialized the trace"
        reads.append(True)
        return fingerprint(trace)

    monkeypatch.setattr(IsolatedModel, "__init__", opened)
    monkeypatch.setattr(IsolatedModel, "close", closed)
    monkeypatch.setattr(api.AuditedTrace, "fingerprint", property(identify))
    result = execution.evaluate((schedule([1, 2]),))
    assert result.complete and result.passed, result.as_dict()
    assert reads and not active
    observed = result.results[0].comparison.reference_source
    assert {TRUE, FALSE} <= observed.hits
    assert observed.trace == result.results[0].trace.fingerprint


@pytest.mark.isolation
@pytest.mark.parametrize("gain", [1, 2])
def test_matching_retains_source_hits_across_resets_without_reexecution(monkeypatch, gain):
    execution = _execution(_reference(), gain=gain)
    first, second = schedule([1]), schedule([2])
    seed = api.Stimulus(queues={"in": [(1,), (2,)]}, slots=first.slots + second.slots)
    calls = []
    say = IsolatedModel._say

    def counted(model, request):
        calls.append(request)
        return say(model, request)

    monkeypatch.setattr(IsolatedModel, "_say", counted)
    result = execution.evaluate((seed,))
    assert result.complete and result.passed is (gain == 1), result.as_dict()
    comparison = result.results[0].comparison
    observed = comparison.reference_source
    assert type(observed) is ReferenceSourceObservation
    assert {TRUE, FALSE} <= observed.hits  # first epoch's true hit survives reset
    assert observed.reference == execution.reference.digest
    assert observed.catalogue == execution.reference.source_catalogue
    assert observed.trace == result.results[0].trace.fingerprint
    assert not (observed.hits & set(execution.catalogue))  # not candidate coverage
    assert comparison.as_dict()["reference_source"] == observed.as_dict()
    assert sum(row.get("call") == "process" for row in calls) == 2

    before = list(calls)
    feedback, = execution.reference_feedback((seed,), observed.catalogue)
    again = execution.evaluate((seed,))
    assert set(feedback.hits) == observed.hits
    assert feedback.record == observed.fingerprint
    assert feedback.catalogue_digest == observed.catalogue.digest()
    assert again.results[0] is result.results[0]
    assert calls == before  # neither feedback nor final scoring reruns the reference

    with pytest.raises(api.Refused, match="catalogue-reference-feedback-unknown-goal"):
        execution.reference_feedback((seed,), api.CatalogueManifest(()))


@pytest.mark.isolation
def test_native_source_feedback_does_not_build_ir(monkeypatch):
    from behave.sim.behavior import lowering

    def forbidden(*args, **kwargs):
        pytest.fail("output matching/source observation must not lower Behavior")

    monkeypatch.setattr(lowering, "lower_procedure", forbidden)
    execution = _execution(_reference())
    seed = schedule([2])
    result = execution.evaluate((seed,))
    assert result.complete and result.passed, result.as_dict()
    observed = result.results[0].comparison.reference_source
    assert FALSE in observed.hits and TRUE not in observed.hits


@pytest.mark.isolation
def test_reset_and_idle_tick_do_not_credit_shared_helper_step_goals():
    source = """class M:
    def reset(self):
        self.helper(0)
    def helper(self, value):
        if value:
            return value
        return 0
    def process(self, in_a):
        return {"out_y": self.helper(in_a)}
    def tick(self):
        self.helper(0)
"""
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                  {"behavior.py": source.encode()}, config={"class": "M"})
    execution = _execution(reference)
    result = execution.evaluate((schedule([1], drain=3),))
    assert result.complete and result.passed, result.as_dict()
    observed = result.results[0].comparison.reference_source
    # Reset/tick really execute the false helper arm, but never as a process step.
    assert TRUE in observed.hits and FALSE not in observed.hits
    assert FALSE in observed.catalogue  # still a legitimate uncovered target


@pytest.mark.isolation
def test_observation_failure_is_explicit_and_does_not_change_output_score(monkeypatch):
    from behave.sim.core import reference as reference_module

    execution = _execution(_reference())
    builder, _version = reference_module._SOURCE_OBSERVERS[api.ISOLATED_REFERENCE_ADAPTER]

    def bad_events(model):
        recorder = builder(model)
        recorder._take_hits = lambda: ("not-a-declared-goal",)
        return recorder

    # Keep the loaded adapter identity; inject the bad event at the producer boundary.
    original = reference_module.LoadedReference._source_recorder

    def broken(bundle, model):
        if bundle is execution.reference:
            return bad_events(model)
        return original(bundle, model)

    monkeypatch.setattr(reference_module.LoadedReference, "_source_recorder", broken)
    seed = schedule([2])
    result = execution.evaluate((seed,))
    assert result.complete and result.passed, result.as_dict()
    refused = result.results[0].comparison.reference_source
    assert type(refused) is api.Refusal
    assert refused.kind == "reference-source-observation-invalid"
    assert execution.reference_feedback((seed,), api.CatalogueManifest(())) == (refused,)


def test_adapter_without_source_observer_does_not_claim_empty_verified_feedback():
    execution = _execution(_ref(), gain=2)
    seed = schedule([3])
    result = execution.evaluate((seed,))
    assert result.complete and result.passed
    assert result.results[0].comparison.reference_source is None
    feedback, = execution.reference_feedback((seed,), api.CatalogueManifest(()))
    assert feedback.kind == "reference-source-observation-unavailable"


def test_invalid_source_event_is_not_a_truth_value_or_an_empty_hit_set():
    recorder = ReferenceSourceRecorder(api.CatalogueManifest(()), lambda: ("unknown",))
    recorder.observe()
    result = recorder.finish("reference", "trace")
    assert type(result) is api.Refusal
    assert result.kind == "reference-source-observation-invalid"
    recorder.observe(())
    assert recorder.finish("reference", "trace") is result


@pytest.mark.isolation
def test_reference_declaration_does_not_need_a_run_hit_collector(monkeypatch):
    # Register the lazy production adapter before replacing its observer.
    # Injecting into an uninitialized registry breaks the module's first import
    # and leaves its other registrations behind for subsequent tests.
    from behave.sim import reference_isolated  # noqa: F401
    from behave.sim.core import reference as reference_module

    def no_collector(*args):
        pytest.fail("loading a source declaration must not construct a run hit collector")

    monkeypatch.setitem(reference_module._SOURCE_OBSERVERS,
                        api.ISOLATED_REFERENCE_ADAPTER, (no_collector, "test/1"))
    reference = _reference()
    assert {TRUE, FALSE} <= set(reference.source_catalogue)


@pytest.mark.isolation
def test_loaded_reference_keeps_its_observer_without_per_run_identity_scans(monkeypatch):
    from behave.sim.core import reference as reference_module

    reference = _reference()
    with reference.new_model() as model:
        def changed(*args):
            pytest.fail("a loaded reference must not rediscover its observer")

        monkeypatch.setitem(reference_module._SOURCE_OBSERVERS,
                            api.ISOLATED_REFERENCE_ADAPTER, (changed, "changed"))
        monkeypatch.setattr(reference_module, "_adapter_identity", changed)
        recorder = reference._source_recorder(model)
        model.process(in_a=1)
        recorder.observe()
        observed = recorder.finish(reference.digest, "trace")
        assert TRUE in observed.hits and FALSE not in observed.hits


@pytest.mark.isolation
def test_atomic_reference_keeps_domain_events_from_the_same_output_run():
    from tests.sim.test_multiclock_authority import _AtomicDevice, _evaluate

    result = _evaluate(_AtomicDevice())
    assert result.complete and result.passed, result.as_dict()
    observed = result.results[0].comparison.reference_source
    assert type(observed) is ReferenceSourceObservation
    # The independently specified reference has one return in each handler.
    assert observed.hits == frozenset({
        "location:behavior.py:7:8:0:entry", "location:behavior.py:10:8:1:entry"})


@pytest.mark.isolation
@pytest.mark.parametrize("runtime", (True, False), ids=("runtime", "fixed"))
def test_reference_search_consumes_baseline_hits_before_lowering(tmp_path, monkeypatch, runtime):
    from behave.sim.engines.behavior.generation import BehaviorIsolatedGenerationProvider
    from behave.sim.generation import UnavailableGenerationProvider, generate
    from behave.sim.production_generation import production_generation_providers

    execution = _execution(_reference())
    seed = api.Stimulus(queues={"in": [(1,), (2,)]}, replay_policy=api.ReplayPolicy(
        reset_hold_cycles=2, drain_slots=2, stall_every=0))
    if not runtime:
        seed = schedule([1, 2], drain=2)
    baseline = execution.evaluate((seed,))
    assert baseline.complete and baseline.passed
    observed = baseline.results[0].comparison.reference_source
    assert observed.hits == frozenset(observed.catalogue)
    spec = (api.GenerationSpec(16, 2, 2, 2) if runtime else
            api.GenerationSpec(len(seed.slots), 8, 3, 2, stall_every=2))
    capabilities = production_generation_providers(
        execution.task, execution.artifact, execution.reference, spec,
        api.CatalogueManifest(()), workdir=tmp_path, defer=True)

    def redundant(*args, **kwargs):
        pytest.fail("covered reference goals must not trigger lowering, Select/Probe or replay")

    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "_run_context_job", redundant)
    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "select", redundant)
    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "probe", redundant)
    monkeypatch.setattr(IsolatedModel, "process", redundant)
    provider = capabilities.reference.prepare(execution=execution, baseline=(seed,))
    assert type(provider) is not UnavailableGenerationProvider, provider.search_refusal()
    assert provider.catalogue_manifest() == observed.catalogue
    generated = generate((seed,), provider, contract=execution.task.contract,
                         generation_spec=spec, probe_budget=4)
    assert generated.telemetry["termination"] == "goals-covered", generated.as_dict()
    assert generated.telemetry["probes"] == 0
    assert not generated.generated
    assert set(generated.telemetry["baseline_coverage"]["hits"]) == observed.hits
    assert provider.execution_cost()["domains"] == []


@pytest.mark.solver
@pytest.mark.isolation
@pytest.mark.usefixtures("search_lifetime")  # generate() alone opens no finite Search stage.
@pytest.mark.parametrize("runtime", (True, False), ids=("runtime", "fixed"))
def test_reference_remaining_goal_prepares_then_solves_and_replays(tmp_path, monkeypatch, runtime):
    from behave.sim.engines.behavior.generation import BehaviorIsolatedGenerationProvider
    from behave.sim.generation import generate
    from behave.sim.production_generation import production_generation_providers

    execution = _execution(_reference())
    seed = api.Stimulus(queues={"in": [(1,)]}, replay_policy=api.ReplayPolicy(
        reset_hold_cycles=2, drain_slots=2, stall_every=0))
    if not runtime:
        seed = schedule([1], drain=2)
    baseline = execution.evaluate((seed,))
    assert baseline.complete and baseline.passed
    observed = baseline.results[0].comparison.reference_source
    assert TRUE in observed.hits and FALSE not in observed.hits
    spec = (api.GenerationSpec(16, 2, 2, 2) if runtime else
            api.GenerationSpec(len(seed.slots), 8, 3, 2, stall_every=2))
    layouts = []
    run_job = BehaviorIsolatedGenerationProvider._run_context_job

    def counted(provider, job, *args, **kwargs):
        if job == "behavior_generation_layout":
            layouts.append(job)
        return run_job(provider, job, *args, **kwargs)

    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "_run_context_job", counted)
    providers = production_generation_providers(
        execution.task, execution.artifact, execution.reference, spec,
        api.CatalogueManifest(()), workdir=tmp_path, defer=True)
    provider = providers.reference.prepare(execution=execution, baseline=(seed,))
    assert not layouts
    generated = generate((seed,), provider, contract=execution.task.contract,
                         generation_spec=spec, probe_budget=4)
    assert layouts == ["behavior_generation_layout"]
    assert generated.telemetry["probes"] == 1, generated.as_dict()
    assert generated.telemetry["termination"] == "goals-covered", generated.as_dict()
    assert FALSE not in generated.telemetry["baseline_coverage"]["hits"]
    assert len(generated.generated) == 1
    scored = execution.evaluate(generated.generated)
    assert scored.complete and scored.passed
    assert FALSE in scored.results[0].comparison.reference_source.hits
    if not runtime:
        assert generated.generated[0].slots == seed.slots
        assert generated.generated[0].replay_policy is None
        domains = provider.execution_cost()["domains"]
        assert len(domains) == 1
        assert domains[0]["generation_spec"] == spec.as_dict()
