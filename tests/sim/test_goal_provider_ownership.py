"""Frontend registration preserves declarations; it is not an event producer."""

import importlib
import json
import pickle
import subprocess
import sys

import pytest

from behave.sim.core.catalogue import (
    COVERAGE,
    SAFETY,
    CatalogueManifest,
    Goal,
    OccurrenceKey,
    merge_catalogues,
)
from behave.sim.core.goal_provider import CAPABILITIES, standard_goal_schemas
from behave.sim.core.refusal import Refused

# These are hand-authored frontend records. Their goal spellings deliberately do
# not reveal their schema. Multiple contexts must not become multiple public goals.
ROWS = {
    "python-structural": (
        ("b0", COVERAGE, "location", "location"),
        ("b1", COVERAGE, "decision", "control-choice"),
        ("b2", COVERAGE, "condition", "boolean-expression"),
        ("b3", COVERAGE, "loop", "loop-activation"),
        ("b4", SAFETY, "div-zero", "artifact-failure"),
    ),
    "rtl-structural": (
        ("r0", COVERAGE, "location", "location"),
        ("r1", COVERAGE, "case-arm", "control-choice"),
    ),
}


def frontend_fixture(owner):
    return CatalogueManifest(
        tuple(Goal(goal, cls, family=family, provider=owner,
                   event="fixture-event", policy="fixture/1")
              for goal, cls, family, _schema in ROWS[owner]),
        occurrences=tuple(
            (OccurrenceKey(owner, (goal, context), "fixture-event"), goal)
            for goal, _cls, _family, _schema in ROWS[owner]
            for context in ("first", "second")),
    )


def policy_images(composer):
    """Identical input to pre/post-refactor modules for a bytewise comparison."""
    from .test_api_path import contract

    task = contract(deadline=5)
    return {
        label: composer.evaluation_goal_policy(
            task, catalogue, artifact_kind=kind).as_dict()
        for label, kind, catalogue in (
            ("behavior", "behavior", frontend_fixture("python-structural")),
            ("rtl", "rtl", frontend_fixture("rtl-structural")),
            ("empty-behavior", "behavior", CatalogueManifest(())),
            ("empty-rtl", "rtl", CatalogueManifest(())),
            ("device-monitors", "device", CatalogueManifest(())),
        )
    }


@pytest.mark.parametrize("owner,frontend,version", (
    ("python-structural", "behavior", 1),
    ("rtl-structural", "rtl", 3),
))
def test_frontend_owns_schema_assignment_and_preserves_every_occurrence(
        owner, frontend, version):
    from behave.sim.goal_policy import builtin_frontend_goal_providers

    registration = importlib.import_module(f"behave.sim.{frontend}.goal_provider")
    manifest = frontend_fixture(owner)
    providers = registration.goal_providers(manifest)
    assert {goal: provider.schema.name
            for provider in providers for goal in provider.catalogue} == {
                goal: schema for goal, _cls, _family, schema in ROWS[owner]}
    assert merge_catalogues(*(p.catalogue for p in providers)) == manifest
    assert all(p.identity.name == f"{frontend}-{p.schema.name}"
               and p.identity.version == version for p in providers)
    assert [p.as_dict() for p in providers] == [
        p.as_dict() for p in builtin_frontend_goal_providers(manifest)]
    for provider in providers:
        for kind in CAPABILITIES:
            adapter = provider.binding(kind).adapter
            assert adapter.catalogue is provider.catalogue
            assert set(adapter(provider.catalogue)) == set(provider.catalogue)


def test_composition_keeps_frontend_owners_separate():
    from behave.sim.goal_policy import builtin_frontend_goal_providers

    behavior = frontend_fixture("python-structural")
    rtl = frontend_fixture("rtl-structural")
    combined = merge_catalogues(behavior, rtl)
    providers = builtin_frontend_goal_providers(combined)
    assert len(providers) == 7
    assert merge_catalogues(*(p.catalogue for p in providers)) == combined
    assert all(len({g.provider for g in p.catalogue.values()}) == 1
               for p in providers)


def test_legacy_actions_remain_diagnostic_without_certifying_source_enumeration():
    from behave.sim.core.goal_provider import CONCRETE_PHASE, INCOMPLETE, RELEASE_PHASE
    from behave.sim.goal_policy import evaluation_goal_policy
    from behave.sim.rtl.source_goals import SCHEMAS

    from .test_api_path import contract

    frontend = frontend_fixture("rtl-structural")
    policy = evaluation_goal_policy(contract(deadline=5), frontend)
    assert policy.validate(CONCRETE_PHASE) is policy
    assert {s.schema.name for s in policy.selections if s.status == INCOMPLETE} == SCHEMAS
    assert all(policy.manifest[key] == goal for key, goal in frontend.items())
    assert set(frontend.occurrences) <= set(policy.manifest.occurrences)
    with pytest.raises(Refused, match="goal-policy-catalogue-incomplete"):
        policy.validate(RELEASE_PHASE)


def test_source_catalogue_not_marked_incomplete_by_legacy_diagnostics():
    from behave.sim.core.goal_provider import CONCRETE_PHASE, RELEASE_PHASE
    from behave.sim.goal_policy import evaluation_goal_policy
    from behave.sim.rtl.goal_provider import source_enumeration_gaps

    from .test_api_path import contract

    goal = Goal("rtl-source:fixture:entry", COVERAGE, family="location",
                provider="rtl-structural", event="entry", policy="rtl-source-events/2")
    frontend = CatalogueManifest((goal,), occurrences=((
        OccurrenceKey("rtl-structural", ("design.sv", "location", 1, 2), "entry"), goal.id),))
    policy = evaluation_goal_policy(contract(deadline=5), frontend)
    assert not source_enumeration_gaps(frontend)
    assert not policy.incomplete_schemas
    assert policy.validate(CONCRETE_PHASE) is policy
    # The source adapter is registered separately from local guidance. Runtime
    # domain and native-evidence admission still happen when analysis binds.
    assert policy.validate(RELEASE_PHASE) is policy
    mixed = merge_catalogues(frontend, frontend_fixture("rtl-structural"))
    assert source_enumeration_gaps(mixed)
    assert not source_enumeration_gaps(frontend_fixture("python-structural"))


@pytest.mark.parametrize("frontend,wrong_owner", (
    ("behavior", "rtl-structural"), ("rtl", "python-structural"),
))
def test_frontend_does_not_reinterpret_another_frontends_rows(frontend, wrong_owner):
    registration = importlib.import_module(f"behave.sim.{frontend}.goal_provider")
    with pytest.raises(Refused, match="goal-provider-builtin-owner-unknown"):
        registration.goal_providers(frontend_fixture(wrong_owner))


def test_catalogue_subset_keeps_whole_occurrence_relation_and_checks_unknown_ids():
    from behave.sim.core.catalogue import catalogue_subset

    catalogue = frontend_fixture("python-structural")
    subset = catalogue_subset(catalogue, ("b4", "b1"))
    assert set(subset) == {"b1", "b4"}
    assert subset.occurrences == tuple(
        pair for pair in catalogue.occurrences if pair[1] in subset)
    assert len(subset.occurrences) == 4
    assert subset["b1"] is catalogue["b1"]
    with pytest.raises(KeyError):
        catalogue_subset(catalogue, ("not-a-goal",))


def test_behavior_coverage_and_failure_declarations_resolve_one_source_closure(monkeypatch):
    from behave.sim.behavior import catalogue, source_plan
    from behave.sim.behavior.extract import source_model

    source = """class M:
    def reset(self):
        pass
    def quotient(self, a):
        return 1 // a
    def process(self, a):
        if a:
            return {"y": self.quotient(a)}
        return {"y": 0}
"""
    model = source_model(source, "fixture.py", cls="M")
    resolve = source_plan.source_call_closure
    calls = []

    def counted(model, resolver=None):
        result = resolve(model, resolver)
        calls.append(result)
        return result

    monkeypatch.setattr(source_plan, "source_call_closure", counted)
    manifest = catalogue.source_frontend_catalogue(model, source, "fixture.py")
    assert len(calls) == 1
    # The unreachable-on-this-path division failure remains in the catalogue.
    # Resolving fewer times must not mean declaring fewer helper occurrences.
    failures = {key for key, goal in manifest.items() if goal.cls == SAFETY}
    assert failures and {manifest[key].family for key in failures} == {"divzero"}
    plan = source_plan.plan_source(model, source, "fixture.py")
    assert failures == {site.id for site in plan.safety_sites}
    assert len(calls) == 2  # the separate standalone request resolves once itself


def test_standard_schema_names_are_the_twelve_appendix_rules():
    assert {schema.name for schema in standard_goal_schemas()} == {
        "location", "control-choice", "boolean-expression", "loop-activation",
        "value-observation", "operation-event", "state-activity", "sequence",
        "latency", "schedule-interleaving", "artifact-failure", "monitor-obligation",
    }


@pytest.mark.parametrize("module", (
    "behave.sim.core.goal_provider", "behave.sim.core.goal_guidance",
    "behave.sim.behavior.goal_provider", "behave.sim.rtl.goal_provider",
    "behave.sim.goal_policy",
))
def test_goal_records_and_composition_do_not_load_execution_backends(module):
    script = f"import importlib, json, sys; importlib.import_module({module!r}); print(json.dumps(sorted(sys.modules)))"
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], capture_output=True, text=True, check=True)
    loaded = set(json.loads(result.stdout))
    forbidden = {
        "z3", "cocotb", "pyslang", "behave.sim.api", "behave.sim.generation",
        "behave.sim.symbolic.encoder", "behave.sim.behavior.lowering",
        "behave.sim.behavior.exact_service", "behave.sim.behavior.isolated",
        "behave.sim.rtl.artifact", "behave.sim.rtl.elaboration",
        "behave.sim.rtl.replay_runtime", "behave.sim.core.driver",
    }
    assert not loaded & forbidden


def test_legacy_guidance_record_path_resolves_to_the_one_record_class():
    from behave.sim.core import goal_guidance, goal_provider

    assert goal_provider.DeclarativeGuidanceStep is goal_guidance.DeclarativeGuidanceStep
    step = goal_guidance.DeclarativeGuidanceStep((("bin", (("x", 2),)),), ())
    payload = pickle.dumps(step, protocol=2).replace(
        b"behave.sim.core.goal_guidance", b"behave.sim.core.goal_provider")
    loaded = pickle.loads(payload)  # trusted fixture generated above
    assert type(loaded) is goal_guidance.DeclarativeGuidanceStep
    assert loaded.as_dict() == step.as_dict()


def test_common_records_do_not_classify_frontend_spellings():
    import inspect

    from behave.sim.core import goal_provider

    body = inspect.getsource(goal_provider)
    assert 'goal.provider == "python-structural"' not in body
    assert 'goal.provider == "rtl-structural"' not in body
    assert '"case-arm"' not in body
    assert '"decision"' not in body
    assert not hasattr(goal_provider, "evaluation_goal_policy")


@pytest.mark.isolation
@pytest.mark.parametrize("bounded_analysis", (False, True))
def test_behavior_handle_carries_policy_into_commands_and_actual_replay(
        tmp_path, monkeypatch, bounded_analysis):
    from behave.sim import api

    from .test_behavior_public_exact_construction import SOURCE, _contract, _generation, _reference

    path = tmp_path / "source.py"
    path.write_text(SOURCE)
    contract = _contract()
    reference = _reference()
    realization = api.realization_of_behavior_source(SOURCE, contract, file="source.py")
    task = api.admit(contract, realization, reference=reference)
    spec = _generation(contract)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="source.py"), spec,
        bounded_analysis=bounded_analysis)

    def no_rebuild(*_args, **_kwargs):
        raise AssertionError("a prepared goal policy was rebuilt")

    monkeypatch.setattr("behave.sim.goal_policy.evaluation_goal_policy", no_rebuild)
    monkeypatch.setattr("behave.sim.command.evaluation_goal_policy", no_rebuild)
    providers = handle.generation_providers(
        reference, workdir=tmp_path / "search", enable_local_guidance=False)
    baseline = api.BaselinePolicy(case_limit=2, max_queue_depth=1)
    commands = [handle.command_request(
        run_directory=tmp_path / f"run-{i}", baseline=baseline,
        providers=providers, probe_budget=0, run_analysis=False,
        reference=reference) for i in range(2)]
    first, second = commands
    assert first.goal_policy is second.goal_policy
    assert first.catalogue is second.catalogue
    assert first.goal_policy is handle._context.goal_policy
    assert handle._context.validate_catalogues(
        handle.frontend_catalogue, first.catalogue) is first.goal_policy
    assert handle._context.source_goal_handoff()[1] is first.goal_policy
    if bounded_analysis:
        assert first.goal_policy is handle._analysis_request.goal_policy
    stimuli = api.build_baseline(contract, spec, policy=baseline, realization=realization).stimuli
    result = handle.replay(stimuli, reference=reference)
    assert result.authoritative and result.passed
    assert result.evaluation.catalogue is first.catalogue


@pytest.mark.isolation
def test_atomic_schedule_binding_retains_the_source_policy(tmp_path, monkeypatch):
    from behave.sim import api
    from tests.support.atomic_behavior import SOURCE, _contract, _schedule

    path = tmp_path / "atomic.py"
    path.write_text(SOURCE)
    contract = _contract()
    realization = api.realization_of_behavior_source(SOURCE, contract, file="atomic.py")
    task = api.admit(contract, realization)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="atomic.py"),
        api.GenerationSpec(horizon=3, queue_bound=4, reset_hold_cycles=0, drain=0),
        bounded_analysis=True)
    original = handle._context

    def no_rebuild(*_args, **_kwargs):
        raise AssertionError("schedule binding rebuilt the source policy")

    monkeypatch.setattr("behave.sim.goal_policy.evaluation_goal_policy", no_rebuild)
    scheduled = original.for_schedule(_schedule())
    assert scheduled is not original
    assert scheduled.clock_schedule is not None
    assert original.clock_schedule is None
    assert scheduled.source_catalogue is original.source_catalogue
    assert scheduled.goal_policy is original.goal_policy
    assert scheduled.source_goal_handoff()[1] is original.goal_policy
    assert handle._analysis_request.goal_policy is original.goal_policy


@pytest.mark.parametrize("deferred", (False, True))
def test_standalone_generation_reuses_policy_on_failed_preparation(
        tmp_path, monkeypatch, deferred):
    from behave.sim import api, goal_policy
    from behave.sim.engines.rtl.generation import RTLGenerationProvider
    from behave.sim.production_generation import production_generation_providers

    from .test_api_path import Doubling, _ref, contract, realization

    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    policies, received = [], []
    compose = goal_policy.evaluation_goal_policy

    def counted(*args, **kwargs):
        policies.append(compose(*args, **kwargs))
        return policies[-1]

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("fixture: no reference guidance")

    def candidate_unavailable(*_args, goal_policy, **_kwargs):
        received.append(goal_policy)
        raise RuntimeError("fixture: no candidate guidance")

    monkeypatch.setattr(goal_policy, "evaluation_goal_policy", counted)
    monkeypatch.setattr(api, "prepare_behavior_generation", unavailable)
    monkeypatch.setattr(RTLGenerationProvider, "from_artifact", candidate_unavailable)
    providers = production_generation_providers(
        task, Doubling(), reference,
        api.GenerationSpec(horizon=5, queue_bound=2, reset_hold_cycles=0, drain=0),
        CatalogueManifest(()), workdir=tmp_path, defer=deferred)
    if deferred:
        candidates = [providers.candidate.prepare() for _ in range(2)]
    else:
        candidates = [providers.candidate]
    assert len(policies) == 1
    assert received and all(policy is policies[0] for policy in received)
    assert all(candidate.catalogue_manifest() is policies[0].manifest
               for candidate in candidates)


@pytest.mark.isolation
@pytest.mark.solver
def test_exact_worker_builds_policy_once_and_retains_wire_reconciliation(
        tmp_path, monkeypatch):
    from types import SimpleNamespace

    from behave.sim import api
    from behave.sim.analysis_budget import active_analysis
    from behave.sim.behavior import analysis_job
    from behave.sim.core import analysis

    from .test_behavior_public_exact_construction import SOURCE, _contract, _generation

    path = tmp_path / "source.py"
    path.write_text(SOURCE)
    contract = _contract()
    realization = api.realization_of_behavior_source(SOURCE, contract, file="source.py")
    task = api.admit(contract, realization)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="source.py"), _generation(contract),
        bounded_analysis=False)
    context = handle._context
    policy = context.goal_policy
    binding = {**context.initial_binding(2), "catalogue": policy.manifest.digest()}
    payload = context.payload(
        "analyse", 2, binding=binding, complete_catalogue=policy.manifest,
        memory_environment=None, memory_horizon=None, evaluation={},
        feasibility_timeout_ms=1000, query_timeout_ms=1000, batch=True)
    policies, capabilities, owners, closed = [], [], [], []
    compose = analysis_job.evaluation_goal_policy

    def counted(*args, **kwargs):
        owner = active_analysis()
        assert owner is not None
        owners.append(owner)
        owner.on_close(closed.append, "released")
        policies.append(compose(*args, **kwargs))
        return policies[-1]

    # Keep actual worker parsing, lowering, binding and capability handoff. The
    # downstream solver scheduler is not under test; do not run an unrelated sweep.
    def analyse(subject, *, goal_capability_use, **_kwargs):
        assert subject is task
        assert active_analysis() is owners[-1]
        capabilities.append(goal_capability_use)
        return SimpleNamespace(as_dict=lambda: {"fixture": "analysed"})

    monkeypatch.setattr(analysis_job, "evaluation_goal_policy", counted)
    monkeypatch.setattr(analysis, "_analysis_subject_from_wire", lambda *_args: task)
    monkeypatch.setattr(analysis, "_analyse_subject", analyse)
    reply = analysis_job.run_analysis_job(payload)
    assert reply["status"] == "ok", reply
    assert reply["value"] == {"fixture": "analysed"}
    assert len(policies) == len(capabilities) == 1
    assert capabilities[0].policy_digest == policies[0].digest()
    assert policies[0].manifest == policy.manifest
    assert active_analysis() is None and closed == ["released"]

    # A separately supplied wire record is still checked, not trusted because
    # later stages now share the worker's one policy.
    corrupted = dict(payload, complete_catalogue=CatalogueManifest(()).as_dict())
    bad = analysis_job.run_analysis_job(corrupted)
    assert bad["status"] == "refused", bad
    assert bad["refusal"]["kind"] == "behavior-exact-catalogue-mismatch"
    assert len(policies) == 2 and len(capabilities) == 1
    assert owners[0] is not owners[1]
    assert active_analysis() is None and closed == ["released", "released"]
