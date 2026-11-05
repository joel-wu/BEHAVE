"""Observer selection is admission evidence, never an outcome-based retry."""

from dataclasses import replace

import pytest

from behave.sim.core.failure import FailureRaised, RunFailure
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.rtl import observation_binding as module
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.catalogue import Site
from behave.sim.rtl.execution_policy import execution_adapter_version
from behave.sim.rtl.native_preparation import PreparedNativeEvents

from .test_rtl_execution_policy import _contract
from .test_rtl_native_preparation import _catalogue, _plan, _reply


def _prepared():
    plan = _plan()
    return PreparedNativeEvents("source", plan.collector_source(), plan, "b" * 64, _reply())


def _site(trigger=("posedge", "clk")):
    return Site("M", "p", r"\q", "design.sv:1.1-1.2", "1'1", 1, (),
                ("1'1", "default"), triggers=[trigger])


def _choose(monkeypatch, result, *, empty=False):
    calls = []

    def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(module, "prepare_scheduled_observer", prepare)
    chosen = module.prepare_observation(
        "source", artifact="a" * 64, top="M", contract=_contract(),
        catalogue=CatalogueManifest(()) if empty else _catalogue(_plan()), timeout_s=7)
    return chosen, calls


def test_native_selection_is_closed_detached_and_changes_execution_identity(monkeypatch):
    chosen, calls = _choose(monkeypatch, _prepared())
    binding = chosen.binding
    assert len(calls) == 1 and calls[0][1]["timeout_s"] == 7
    assert binding.native and binding.native_plan == chosen.native.plan.identity
    assert module.ObservationBinding.from_dict(binding.as_dict()) == binding
    with pytest.raises(AttributeError):
        binding.native_plan = "c" * 64
    before = execution_adapter_version("p", native_events=True, observation=binding)
    changed = replace(binding, preparation="c" * 64)
    assert before != execution_adapter_version("p", native_events=True, observation=changed)
    assert before != execution_adapter_version("p", native_events=True)
    with pytest.raises(ValueError):
        execution_adapter_version("p", native_events=False, observation=binding)
    for key in ("artifact", "contract", "catalogue"):
        with pytest.raises(ValueError, match="different"):
            replace(binding, **{key: "c" * 64}).check("a" * 64, _contract(), _catalogue(_plan()))
    with pytest.raises(ValueError, match="closed"):
        module.ObservationBinding.from_dict(binding.as_dict() | {"override": True})


def test_empty_structural_catalogue_needs_only_interface_observation(monkeypatch):
    chosen, calls = _choose(monkeypatch, None, empty=True)
    assert not calls and chosen.native is None
    assert not chosen.binding.native
    chosen.binding.check("a" * 64, _contract(), CatalogueManifest(()))


def test_nonempty_source_catalogue_still_prepares_without_compiler_sites(monkeypatch):
    prepared = _prepared()
    calls = []

    def prepare(*_args, **kwargs):
        calls.append(kwargs)
        return prepared

    monkeypatch.setattr(module, "prepare_scheduled_observer", prepare)
    catalogue = _catalogue(prepared.plan)
    chosen = module.prepare_observation(
        "source", artifact="a" * 64, top="M", contract=_contract(),
        catalogue=catalogue, timeout_s=7)
    assert len(calls) == 1 and calls[0]["catalogue"] is catalogue
    assert chosen.native is prepared


def test_compiler_reported_trigger_refusal_cannot_fall_back_to_snapshot(monkeypatch):
    from behave.sim.rtl import native_preparation

    def compile_observer(*_args, **kwargs):
        from behave.sim.rtl.trigger_support import driver_triggers

        assert kwargs["allowed_triggers"] == driver_triggers(_contract())
        return RunFailure.verifier("rtl-native-preparation", Refusal(
            "native-events-unscheduled-trigger", "compiler-resolved trigger is not scheduled"))

    monkeypatch.setattr(native_preparation, "prepare_source_observer", compile_observer)
    with pytest.raises(Refused, match="native-events-unscheduled-trigger"):
        module.prepare_observation(
            "source", artifact="a" * 64, top="M", contract=_contract(),
            catalogue=_catalogue(_plan()), timeout_s=7)


def test_settled_input_edges_belong_to_drive_not_an_invented_clock():
    from behave.sim import api
    from behave.sim.rtl.trigger_support import driver_triggers, scheduled_triggers, unsupported_triggers

    contract = api.TaskContract(endpoints=(
        api.Endpoint("in", "in", (api.Field("phase", api.FiniteType(1), "phase"),),
                     api.settled("in"), clock=None),
        api.Endpoint("out", "out", (api.Field("q", api.FiniteType(1), "q"),),
                     api.settled("out"), clock=None),
    ), clocks=())
    for edge in ("posedge", "negedge"):
        assert not unsupported_triggers(_site((edge, "phase")), driver_triggers(contract))
    for signal in ("q", "undeclared", "child.phase"):
        assert unsupported_triggers(_site(("posedge", signal)), driver_triggers(contract))
    assert ("posedge", "phase") not in scheduled_triggers(contract.clocks)


@pytest.mark.cosim
@pytest.mark.parametrize("edge", ["posedge", "negedge"])
def test_native_settled_input_edge_records_only_actual_changes(tmp_path, edge):
    from behave.sim import api

    contract = api.TaskContract((
        api.Endpoint("in", "in", (api.Field("phase", api.FiniteType(1), "phase"),),
                     api.settled("in"), clock=None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", (api.Field("q", api.FiniteType(1), "q"),),
                     api.settled("out"), clock=None, kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1),
    ), clocks=(), protocol_instances=(api.ProtocolInstance("compute", ("in", "out")),))
    source = tmp_path / "phase.sv"
    source.write_text(f"""module M(input phase, output reg q);
      initial q=0;
      always @({edge} phase) q<=~q;
    endmodule""")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    stimulus = api.reset_then(contract, [(v,) for v in (0, 0, 1, 1, 0, 0, 1)], drain=0)
    trace, = artifact.traces_per_process(contract, (stimulus,), workers=1)
    assert type(trace) is api.AuditedTrace, trace
    expected = [0, 0, 1, 1, 1, 1, 0] if edge == "posedge" else [0, 0, 0, 0, 1, 1, 1]
    assert api.transfers(trace, "out") == [(v,) for v in expected]
    hit_slots = {i for i, row in enumerate(trace.records) if any(value for _, value in row.hits)}
    assert hit_slots == ({2, 6} if edge == "posedge" else {4})
    evidence = trace.source_evidence
    assert all(row["callbacks"] == 0 for row in evidence["intervals"]
               if row["phase"] in {"clock", "advance"})


def test_mapping_gap_refuses_instead_of_selecting_a_snapshot(monkeypatch):
    refusal = RunFailure.verifier("rtl-native-preparation", Refusal(
        "native-events-source-mapping-unavailable", "unsupported source context"))
    with pytest.raises(Refused, match="native-events-source-mapping-unavailable"):
        _choose(monkeypatch, refusal)


def test_interface_only_binding_cannot_claim_nonempty_structural_goals():
    catalogue = _catalogue(_plan())
    binding = module.ObservationBinding("a" * 64, _contract().digest, catalogue.digest())
    with pytest.raises(ValueError, match="nonempty structural catalogue"):
        binding.check("a" * 64, _contract(), catalogue)


def test_source_audit_rejects_unbound_legacy_structural_bits():
    from behave.sim import api
    from behave.sim.rtl.initial_request import InitialStateCoordinates
    from behave.sim.rtl.source_trace import read_source_trace

    stimulus = api.Stimulus({}, [api.EventSlot()])
    trace = api.AuditedTrace(stimulus, records=[api.SlotRecord()], artifact="a" * 64)
    with pytest.raises(Refused, match="source-native-evidence-required"):
        read_source_trace(stimulus, trace, InitialStateCoordinates("a" * 64, {}),
                          _catalogue(_plan()), prefix="test")


def test_legacy_reset_diagnostic_cannot_reuse_a_sealed_native_collector(tmp_path, monkeypatch):
    from behave.sim import api

    source = tmp_path / "M.sv"
    source.write_text("source")
    artifact = RTLArtifact(source, "M", tmp_path / "not-built")
    contract, catalogue, native = _contract(), _catalogue(_plan()), _prepared()
    artifact._observation = module.PreparedObservation(module.ObservationBinding(
        artifact.identity, contract.digest, catalogue.digest(), native.plan.identity,
        native.request_sha256), native)
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_: catalogue)
    stimulus = api.Stimulus({}, [api.EventSlot(controls={"rst": 0, "out_ready": 1})])
    results = artifact.traces(contract, [stimulus, stimulus], verify_batch=False)
    assert [result.kind for result in results] == ["rtl-native-shared-process-unsupported"] * 2
    assert not (tmp_path / "not-built").exists()


@pytest.mark.parametrize("kind", ["native-events-runtime-unavailable",
                                 "native-events-preparation-incomplete",
                                 "native-events-preparation-invalid",
                                 "native-events-unknown-context"])
def test_failures_do_not_select_another_observer(monkeypatch, kind):
    failure = RunFailure.verifier("rtl-native-preparation", Refusal(kind, "failed"))
    chosen, calls = _choose(monkeypatch, failure)
    assert chosen is failure and len(calls) == 1


def test_artifact_freezes_one_observer_and_rejects_per_call_replacement(tmp_path, monkeypatch):
    source = tmp_path / "M.sv"
    source.write_text("source")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    contract, catalogue = _contract(), _catalogue(_plan())
    prepared = _prepared()
    binding = module.ObservationBinding(
        artifact.identity, contract.digest, catalogue.digest(),
        prepared.plan.identity, prepared.request_sha256)
    calls = []
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_kw: catalogue)

    def prepare(*_args, **_kwargs):
        calls.append(True)
        return module.PreparedObservation(binding, prepared)

    monkeypatch.setattr(module, "prepare_observation", prepare)
    assert artifact.bind_observation(contract) is binding
    assert artifact.bind_observation(contract) is binding
    assert calls == [True] and len(artifact.tool_lineage()) == 1
    assert artifact._replay_observer(contract, None) is True
    with pytest.raises(Refused, match="rtl-observation-override"):
        artifact.traces_per_process(contract, [], native_events=False)
    with pytest.raises(Refused, match="rtl-observation-override"):
        artifact.exact_trace(contract, None, observe_names=(), state_names=(), native_events=False)
    source.write_text("changed")
    with pytest.raises(ValueError, match="different"):
        artifact._replay_observer(contract, None)


def test_failed_observer_preparation_is_not_frozen_as_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "M.sv"
    source.write_text("source")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_kw: _catalogue(_plan()))
    failure = RunFailure.infrastructure("rtl-native-preparation", Refusal("runtime-down", "not started"))
    monkeypatch.setattr(module, "prepare_observation", lambda *_a, **_kw: failure)
    with pytest.raises(FailureRaised) as caught:
        artifact.bind_observation(_contract())
    assert caught.value.failure is failure and artifact.observation_binding is None


def test_unbound_artifact_cannot_request_the_retired_observer(tmp_path, monkeypatch):
    source = tmp_path / "M.sv"
    source.write_text("source")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    catalogue, prepared = _catalogue(_plan()), _prepared()
    binding = module.ObservationBinding(artifact.identity, _contract().digest, catalogue.digest(),
                                        prepared.plan.identity, prepared.request_sha256)
    monkeypatch.setattr(artifact, "bind_observation", lambda _contract: binding)
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_kwargs: catalogue)
    with pytest.raises(Refused, match="rtl-observation-override"):
        artifact._replay_observer(_contract(), False)
    assert artifact._replay_observer(_contract(), None)


def test_context_cannot_reuse_cached_replays_after_observer_binding_changes(tmp_path):
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.rtl.exact_job_context import RTLExactJobContext
    from behave.sim.stimulus_set import GenerationSpec

    source = tmp_path / "M.sv"
    source.write_text("source")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    contract, catalogue = _contract(), CatalogueManifest(())
    context = RTLExactJobContext(
        artifact, contract, GenerationSpec(horizon=3, queue_bound=1, reset_hold_cycles=1, drain=0),
        catalogue, catalogue, workdir=tmp_path / "context")
    binding = module.ObservationBinding(
        artifact.identity, contract.digest, catalogue.digest())
    artifact._observation = module.PreparedObservation(binding, None)
    with pytest.raises(Refused, match="observation-changed-after-context-binding"):
        context.selection_traces(())
    with pytest.raises(Refused, match="observation-changed-after-context-binding"):
        context.source_trace(None)


def test_observation_survives_the_strict_worker_handoff(tmp_path):
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.rtl.exact_job_context import RTLExactJobContext
    from behave.sim.rtl.exact_job_instance import _decode_context
    from behave.sim.stimulus_set import GenerationSpec

    source = tmp_path / "M.sv"
    source.write_text("source")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    contract, catalogue = _contract(), CatalogueManifest(())
    binding = module.ObservationBinding(
        artifact.identity, contract.digest, catalogue.digest())
    artifact._observation = module.PreparedObservation(binding, None)
    context = RTLExactJobContext(
        artifact, contract, GenerationSpec(horizon=3, queue_bound=1, reset_hold_cycles=1, drain=0),
        catalogue, catalogue, workdir=tmp_path / "context")
    payload = context._payload()
    assert payload["observation"] == binding.as_dict()
    assert _decode_context(payload, ())[-1] == binding
    payload["observation"]["contract"] = "f" * 64
    with pytest.raises(ValueError, match="different"):
        _decode_context(payload, ())
    del payload["observation"]
    with pytest.raises(TypeError, match="exact payload fields"):
        _decode_context(payload, ())


@pytest.mark.parametrize("changes", [
    {"artifact": "a"}, {"contract": True}, {"catalogue": "A" * 64},
    {"native_plan": False}, {"preparation": None}, {"limitation": "not-a-capability"},
    {"native_plan": None}, {"native_plan": None, "preparation": None, "limitation": []},
])
def test_observer_identity_cannot_be_coerced_or_partially_downgraded(changes):
    with pytest.raises((TypeError, ValueError)):
        module.ObservationBinding(**{
            "artifact": "a" * 64, "contract": "b" * 64, "catalogue": "c" * 64,
            "native_plan": "d" * 64, "preparation": "e" * 64, **changes})
