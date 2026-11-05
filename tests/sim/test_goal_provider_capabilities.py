"""T3-10's versioned goal-provider matrix and declarative extension semantics."""

from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.core.catalogue import (
    COVERAGE,
    CatalogueManifest,
    Goal,
    OccurrenceKey,
)
from behave.sim.core.goal_provider import (
    BEHAVIOR_LOCAL_GUIDANCE_POLICY,
    CONCRETE_HIT,
    CONCRETE_PHASE,
    DECLARATION,
    DISABLED,
    ENABLED,
    EXACT_ANALYSIS_PHASE,
    EXACT_BOUNDED_HIT,
    INAPPLICABLE,
    INCOMPLETE,
    LOCAL_GUIDANCE,
    RELEASE_PHASE,
    RTL_LOCAL_GUIDANCE_POLICY,
    CapabilityBinding,
    DeclarativeGoalSemantics,
    DeclarativeGuidanceAdapter,
    FiniteMonitor,
    FiniteMonitorTransition,
    FinitePredicate,
    FiniteVariable,
    GoalCapabilityUse,
    GoalPolicySelection,
    GoalProvider,
    GoalProviderIdentity,
    GoalProviderRegistry,
    GoalSchemaDefinition,
    VersionedReason,
    standard_goal_schemas,
)
from behave.sim.core.monitor import Monitor
from behave.sim.core.refusal import Refused
from behave.sim.goal_policy import (
    builtin_frontend_goal_providers,
    evaluation_goal_policy,
)


class _Z3Algebra:
    @staticmethod
    def literal(value):
        if type(value) is bool:
            return z3.BoolVal(value)
        if type(value) is int:
            return z3.IntVal(value)
        if type(value) is str:
            return z3.StringVal(value)
        raise TypeError(type(value).__name__)

    equal = staticmethod(lambda left, right: left == right)
    land = staticmethod(z3.And)
    lor = staticmethod(z3.Or)
    ite = staticmethod(z3.If)


def _schema(name):
    return next(item for item in standard_goal_schemas() if item.name == name)


def _declarative_provider(*, guidance=True):
    schema = _schema("value-observation")
    predicates = (
        FinitePredicate(
            "extension.bin.low", (FiniteVariable("x", (0, 1, 2)),), ((0,),)),
        FinitePredicate(
            "extension.bin.high", (FiniteVariable("x", (0, 1, 2)),), ((2,),)),
    )
    semantics = DeclarativeGoalSemantics(predicates=predicates)
    catalogue = CatalogueManifest(tuple(
        Goal(
            predicate.goal, COVERAGE, family="value-observation",
            provider="task-extension", event="sample",
            policy="extension-bins/1", evidence="trace")
        for predicate in predicates))
    provider = GoalProvider.declarative(
        GoalProviderIdentity("task-extension", 1), schema, catalogue, semantics,
        guidance=(DeclarativeGuidanceAdapter(semantics, catalogue)
                  if guidance else None))
    return schema, provider


def _enabled_policy(provider):
    registry = GoalProviderRegistry((provider.schema,), (provider,))
    return registry.freeze(
        "test-goals/1",
        (GoalPolicySelection(provider.schema, ENABLED, (provider,)),))


def test_complete_capability_of_an_empty_policy_is_vacuously_bound():
    policy = GoalProviderRegistry((), ()).freeze("test-empty/1", ())

    use = policy.bind_complete_implementation(
        EXACT_BOUNDED_HIT, "test-empty-exact/1", policy.manifest)

    assert use.providers == ()
    assert tuple(use.targets) == ()
    assert use.source_catalogue is policy.manifest
    assert use.reconcile(
        EXACT_BOUNDED_HIT, "test-empty-exact/1", policy.manifest, ()) is use


def test_one_finite_predicate_drives_concrete_and_symbolic_hits():
    predicate = FinitePredicate(
        "extension.bin.high",
        (FiniteVariable("x", (0, 1, 2)),),
        ((2,),),
    )
    x = z3.Int("x")
    encoded = predicate.symbolic({"x": x}, _Z3Algebra())

    for value in (0, 1, 2):
        concrete = predicate.holds({"x": value})
        assert z3.is_true(z3.simplify(z3.substitute(
            encoded.hit, (x, z3.IntVal(value))))) is concrete
        assert z3.is_true(z3.simplify(z3.substitute(
            encoded.valid, (x, z3.IntVal(value)))))
    assert z3.is_false(z3.simplify(z3.substitute(
        encoded.valid, (x, z3.IntVal(3)))))
    with pytest.raises(ValueError, match="outside x"):
        predicate.holds({"x": 3})


def test_one_total_finite_monitor_drives_concrete_and_symbolic_hits():
    goal = "extension.sequence.ab"
    monitor = FiniteMonitor(
        states=("idle", "seen-a"), events=("a", "b"), initial="idle",
        goals=(goal,),
        transitions=(
            FiniteMonitorTransition("idle", "a", "seen-a"),
            FiniteMonitorTransition("idle", "b", "idle"),
            FiniteMonitorTransition("seen-a", "a", "seen-a"),
            FiniteMonitorTransition("seen-a", "b", "idle", (goal,)),
        ),
    )
    state, event = z3.Strings("state event")
    symbolic = monitor.symbolic(state, event, _Z3Algebra())
    symbolic_hit = dict(symbolic.hits)[goal]

    for before in monitor.states:
        for observed in monitor.events:
            concrete = monitor.step(before, observed)
            substitutions = (
                (state, z3.StringVal(before)),
                (event, z3.StringVal(observed)),
            )
            assert z3.simplify(z3.substitute(
                symbolic.state, *substitutions)).as_string() == concrete.state
            assert z3.is_true(z3.simplify(z3.substitute(
                symbolic.valid, *substitutions)))
            assert z3.is_true(z3.simplify(z3.substitute(
                symbolic_hit, *substitutions))) is (goal in concrete.goals)
    assert z3.is_false(z3.simplify(z3.substitute(
        symbolic.valid,
        (state, z3.StringVal("outside")),
        (event, z3.StringVal("a")))))

    with pytest.raises(ValueError, match="every state/event"):
        FiniteMonitor(
            states=("idle",), events=("a", "b"), initial="idle", goals=(),
            transitions=(FiniteMonitorTransition("idle", "a", "idle"),))


def test_canonical_declarative_provider_drives_complete_concrete_symbolic_step():
    predicate_goal = "extension.bin.high"
    sequence_goal = "extension.sequence.ab"
    predicate = FinitePredicate(
        predicate_goal, (FiniteVariable("x", (0, 1, 2)),), ((2,),))
    monitor = FiniteMonitor(
        states=("idle", "seen-a"), events=("a", "b"), initial="idle",
        goals=(sequence_goal,),
        transitions=(
            FiniteMonitorTransition("idle", "a", "seen-a"),
            FiniteMonitorTransition("idle", "b", "idle"),
            FiniteMonitorTransition("seen-a", "a", "seen-a"),
            FiniteMonitorTransition(
                "seen-a", "b", "idle", (sequence_goal,)),
        ),
    )
    semantics = DeclarativeGoalSemantics((predicate,), (monitor,))
    schema = GoalSchemaDefinition("custom-finite-goal", 1)
    catalogue = CatalogueManifest(tuple(Goal(
        goal, COVERAGE, family="custom-finite", provider="task-extension",
        event="sample", policy="custom-finite/1", evidence="trace")
        for goal in semantics.goals))
    provider = GoalProvider.declarative(
        GoalProviderIdentity("custom-finite-provider", 1),
        schema, catalogue, semantics)

    x = z3.Int("x")
    state, event = z3.Strings("state event")
    symbolic = provider.symbolic_step(
        {predicate_goal: {"x": x}},
        {monitor.key: state},
        {monitor.key: event},
        _Z3Algebra(),
    )
    symbolic_hits = dict(symbolic.hit_terms)
    assert provider.binding(CONCRETE_HIT).implementation == \
        provider.binding(EXACT_BOUNDED_HIT).implementation
    assert semantics.initial_monitor_states() == ((monitor.key, "idle"),)

    for value in (0, 1, 2):
        for before in monitor.states:
            for observed in monitor.events:
                concrete = provider.concrete_step(
                    {predicate_goal: {"x": value}},
                    {monitor.key: before},
                    {monitor.key: observed},
                )
                substitutions = (
                    (x, z3.IntVal(value)),
                    (state, z3.StringVal(before)),
                    (event, z3.StringVal(observed)),
                )
                assert z3.is_true(z3.simplify(z3.substitute(
                    symbolic.valid, *substitutions)))
                for goal, reached in concrete.hit_values:
                    assert z3.is_true(z3.simplify(z3.substitute(
                        symbolic_hits[goal], *substitutions))) is reached
                next_state = dict(concrete.monitor_states)[monitor.key]
                assert z3.simplify(z3.substitute(
                    dict(symbolic.monitor_states)[monitor.key],
                    *substitutions)).as_string() == next_state

    outside = (
        (x, z3.IntVal(3)),
        (state, z3.StringVal("idle")),
        (event, z3.StringVal("a")),
    )
    assert z3.is_false(z3.simplify(z3.substitute(
        symbolic.valid, *outside)))
    with pytest.raises(ValueError, match="outside x"):
        provider.concrete_step(
            {predicate_goal: {"x": 3}},
            {monitor.key: "idle"},
            {monitor.key: "a"},
        )
    with pytest.raises(ValueError, match="missing or extra goals"):
        provider.concrete_step(
            {}, {monitor.key: "idle"}, {monitor.key: "a"})


def test_declarative_identity_does_not_depend_on_set_or_variable_order():
    goal = "extension.cross.xy"
    x = FiniteVariable("x", (1, 0))
    y = FiniteVariable("y", (False, True))
    left = FinitePredicate(goal, (x, y), ((1, True),))
    right = FinitePredicate(goal, (y, x), ((True, 1),))
    monitor_rows = (
        FiniteMonitorTransition("idle", "a", "seen"),
        FiniteMonitorTransition("idle", "b", "idle"),
        FiniteMonitorTransition("seen", "a", "seen"),
        FiniteMonitorTransition("seen", "b", "idle", ("extension.ab",)),
    )
    monitor = FiniteMonitor(
        ("idle", "seen"), ("a", "b"), "idle", ("extension.ab",),
        monitor_rows)
    reordered = FiniteMonitor(
        ("seen", "idle"), ("b", "a"), "idle", ("extension.ab",),
        tuple(reversed(monitor_rows)))

    assert left.as_dict() == right.as_dict()
    assert monitor.key == reordered.key
    assert DeclarativeGoalSemantics((left,), (monitor,)).digest() == \
        DeclarativeGoalSemantics((right,), (reordered,)).digest()

    with pytest.raises(TypeError, match="finite strict JSON"):
        DeclarativeGoalSemantics((FinitePredicate(
            "extension.bad", (FiniteVariable("x", ("\ud800",)),),
            (("\ud800",),)),))


def test_enabled_declarative_provider_freezes_all_four_capabilities_and_catalogue():
    _schema_row, provider = _declarative_provider()
    policy = _enabled_policy(provider)

    assert policy.manifest == provider.catalogue
    assert policy.as_dict()["version"] == "goal-capability-policy/5"
    assert {binding.kind for binding in provider.capabilities} == {
        DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT}
    assert provider.binding(CONCRETE_HIT).implementation == \
        provider.binding(EXACT_BOUNDED_HIT).implementation
    assert policy.as_dict()["selections"][0]["providers"][0][
        "capabilities"] == [binding.as_dict() for binding in provider.capabilities]
    for phase in (CONCRETE_PHASE, EXACT_ANALYSIS_PHASE, RELEASE_PHASE):
        assert policy.validate(phase) is policy
    for capability in (
            DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT):
        assert policy.reconcile(
            provider.identity, capability,
            provider.binding(capability).adapter) == tuple(
                sorted(provider.catalogue))


def test_local_guidance_is_a_checked_nonempty_subset_of_the_provider_catalogue():
    schema, complete = _declarative_provider(guidance=False)
    low = complete.catalogue["extension.bin.low"]
    guidance_catalogue = CatalogueManifest((low,))
    provider = GoalProvider.declarative(
        complete.identity, schema, complete.catalogue, complete.semantics,
        guidance=DeclarativeGuidanceAdapter(
            complete.semantics, complete.catalogue,
            targets=("extension.bin.low",)),
    )
    policy = _enabled_policy(provider)

    assert policy.manifest == complete.catalogue
    assert provider.binding(LOCAL_GUIDANCE).adapter.catalogue == guidance_catalogue
    assert policy.validate(RELEASE_PHASE) is policy
    assert policy.reconcile(
        provider.identity, LOCAL_GUIDANCE,
        provider.binding(LOCAL_GUIDANCE).adapter,
    ) == ("extension.bin.low",)

    with pytest.raises(Refused) as refused:
        DeclarativeGuidanceAdapter(
            complete.semantics, complete.catalogue, targets=())
    assert refused.value.refusal.kind == \
        "goal-provider-guidance-catalogue-empty"


def test_declarative_guidance_is_executable_and_uses_the_shortest_monitor_path():
    predicate = FinitePredicate(
        "extension.value.one",
        (FiniteVariable("x", (0, 1)),),
        ((1,),),
    )
    sequence = "extension.sequence.ab"
    monitor = FiniteMonitor(
        states=("idle", "seen-a"), events=("a", "b"), initial="idle",
        goals=(sequence,),
        transitions=(
            FiniteMonitorTransition("idle", "a", "seen-a"),
            FiniteMonitorTransition("idle", "b", "idle"),
            FiniteMonitorTransition("seen-a", "a", "seen-a"),
            FiniteMonitorTransition("seen-a", "b", "idle", (sequence,)),
        ),
    )
    semantics = DeclarativeGoalSemantics((predicate,), (monitor,))
    catalogue = CatalogueManifest(tuple(Goal(
        goal, COVERAGE, family="custom-finite", provider="task-extension",
        event="sample", policy="custom-finite/1", evidence="trace")
        for goal in semantics.goals))
    guidance = DeclarativeGuidanceAdapter(semantics, catalogue)

    value_witness = guidance.select(predicate.goal)
    assert len(value_witness.steps) == 1
    observations, _events = value_witness.steps[0].concrete_inputs()
    assert observations[predicate.goal] == {"x": 1}

    sequence_witness = guidance.select(sequence)
    assert [dict(step.monitor_events)[monitor.key]
            for step in sequence_witness.steps] == ["a", "b"]
    state = monitor.initial
    for event in ("a", "b"):
        state = monitor.step(state, event).state
    assert state == "idle"


@pytest.mark.parametrize("semantics,goal", (
    (
        DeclarativeGoalSemantics(predicates=(FinitePredicate(
            "extension.never.predicate",
            (FiniteVariable("x", (0, 1)),),
            (),
        ),)),
        "extension.never.predicate",
    ),
    (
        DeclarativeGoalSemantics(monitors=(FiniteMonitor(
            states=("idle",), events=("tick",), initial="idle",
            goals=("extension.never.monitor",),
            transitions=(FiniteMonitorTransition("idle", "tick", "idle"),),
        ),)),
        "extension.never.monitor",
    ),
), ids=("predicate", "monitor"))
def test_declarative_guidance_refuses_an_unreachable_target(semantics, goal):
    catalogue = CatalogueManifest((Goal(
        goal, COVERAGE, family="custom-finite", provider="task-extension",
        event="sample", policy="custom-finite/1", evidence="trace"),))

    with pytest.raises(Refused) as refused:
        DeclarativeGuidanceAdapter(semantics, catalogue)
    assert refused.value.refusal.kind == \
        "goal-provider-guidance-target-unreachable"


def test_declarative_semantics_refuses_ambiguous_goal_ownership():
    goal = "extension.ambiguous"
    predicate = FinitePredicate(
        goal, (FiniteVariable("x", (0, 1)),), ((1,),))
    monitor = FiniteMonitor(
        states=("idle",), events=("tick",), initial="idle", goals=(goal,),
        transitions=(FiniteMonitorTransition(
            "idle", "tick", "idle", (goal,)),),
    )

    with pytest.raises(Refused) as refused:
        DeclarativeGoalSemantics((predicate,), (monitor,))
    assert refused.value.refusal.kind == \
        "goal-provider-declarative-owner-ambiguous"


def test_a_raw_capability_record_cannot_impersonate_declarative_guidance():
    schema, provider = _declarative_provider(guidance=False)
    raw = CapabilityBinding(
        LOCAL_GUIDANCE, "raw-guidance-label/1", provider.catalogue)

    with pytest.raises(TypeError, match="DeclarativeGuidanceAdapter"):
        GoalProvider.declarative(
            provider.identity, schema, provider.catalogue, provider.semantics,
            guidance=raw)


def test_phase_implementation_is_bound_to_the_frozen_policy_not_its_label():
    _schema_row, provider = _declarative_provider()
    policy = _enabled_policy(provider)

    with pytest.raises(Refused) as unregistered:
        policy.bind_implementation(
            LOCAL_GUIDANCE, "same-shape-but-not-registered/1",
            policy.manifest, tuple(policy.manifest))
    assert unregistered.value.refusal.kind == \
        "goal-provider-capability-implementation-unregistered"

    with pytest.raises(Refused) as deleted:
        policy.bind_implementation(
            LOCAL_GUIDANCE,
            provider.binding(LOCAL_GUIDANCE).implementation,
            policy.manifest, ("extension.bin.low",))
    assert deleted.value.refusal.kind == \
        "catalogue-local-guidance-missing-goal"

    selected = policy.bind_implementation(
        LOCAL_GUIDANCE,
        provider.binding(LOCAL_GUIDANCE).implementation,
        policy.manifest, ("extension.bin.low",), exhaustive=False)
    assert tuple(selected.targets) == ("extension.bin.low",)
    assert selected.source_catalogue is policy.manifest

    with pytest.raises(Refused) as changed:
        selected.reconcile(
            LOCAL_GUIDANCE,
            provider.binding(LOCAL_GUIDANCE).implementation,
            policy.manifest,
            ("extension.bin.high",),
        )
    assert changed.value.refusal.kind == \
        "goal-provider-capability-use-changed"


def test_phase_binding_refuses_a_registered_guidance_view_missing_occurrences():
    schema, complete = _declarative_provider(guidance=False)
    occurrence = OccurrenceKey(
        "task-extension", ("predicates", 0), "hit")
    declaration = CatalogueManifest(
        tuple(complete.catalogue.values()),
        occurrences=((occurrence, "extension.bin.low"),),
    )
    # GoalProvider permits a guidance subset.  Deliberately remove the occurrence
    # while retaining both public IDs: only the production binding knows that this
    # implementation claims to be exhaustive and must therefore reject the deletion.
    goals_only = CatalogueManifest(tuple(declaration.values()))
    provider = GoalProvider.declarative(
        complete.identity,
        schema,
        declaration,
        complete.semantics,
        guidance=DeclarativeGuidanceAdapter(
            complete.semantics, goals_only),
    )
    policy = _enabled_policy(provider)

    with pytest.raises(Refused) as deleted:
        policy.bind_implementation(
            LOCAL_GUIDANCE,
            provider.binding(LOCAL_GUIDANCE).implementation,
            policy.manifest,
        )
    assert deleted.value.refusal.kind == \
        "catalogue-local-guidance-unknown-occurrence"


def test_behavior_policy_names_the_sealed_production_guidance_implementation():
    from behave.sim.engines.behavior.generation import (
        BehaviorIsolatedGenerationProvider,
    )

    from .test_api_path import contract

    frontend = CatalogueManifest((Goal(
        "behavior.branch.true", COVERAGE, family="decision",
        provider="python-structural", event="T", policy="source-site/1"),))
    policy = evaluation_goal_policy(
        contract(), frontend, artifact_kind="behavior")
    use = policy.bind_implementation(
        LOCAL_GUIDANCE, BehaviorIsolatedGenerationProvider.policy,
        frontend, tuple(frontend))

    assert BehaviorIsolatedGenerationProvider.policy == \
        BEHAVIOR_LOCAL_GUIDANCE_POLICY
    assert use.implementation == BEHAVIOR_LOCAL_GUIDANCE_POLICY


def test_capability_views_cannot_forge_another_goal_or_occurrence_relation():
    schema, complete = _declarative_provider(guidance=False)
    one_goal = CatalogueManifest((
        complete.catalogue["extension.bin.low"],))

    with pytest.raises(Refused) as missing:
        GoalProvider.builtin(
            GoalProviderIdentity("missing-concrete-row", 1), schema,
            complete.catalogue,
            (CapabilityBinding(CONCRETE_HIT, "partial-concrete/1", one_goal),),
        )
    assert missing.value.refusal.kind == "catalogue-concrete-hit-missing-goal"

    unknown = CatalogueManifest((Goal(
        "extension.bin.unknown", COVERAGE, family="value-observation",
        provider="task-extension", event="sample", policy="extension-bins/1",
        evidence="trace"),))
    with pytest.raises(Refused) as outside:
        DeclarativeGuidanceAdapter(complete.semantics, unknown)
    assert outside.value.refusal.kind == \
        "goal-provider-guidance-semantics-catalogue-mismatch"

    first = Goal(
        "extension.first", COVERAGE, family="value-observation",
        provider="task-extension", event="sample", policy="extension-bins/1",
        evidence="trace")
    second = Goal(
        "extension.second", COVERAGE, family="value-observation",
        provider="task-extension", event="sample", policy="extension-bins/1",
        evidence="trace")
    occurrence = OccurrenceKey("task-extension", ("field", 0), "sample")
    declared = CatalogueManifest(
        (first, second), occurrences=((occurrence, second.id),))
    remapped = CatalogueManifest(
        (first,), occurrences=((occurrence, first.id),))
    with pytest.raises(Refused) as stale_occurrence:
        GoalProvider.builtin(
            GoalProviderIdentity("remapped-guidance", 1), schema, declared,
            (CapabilityBinding(
                LOCAL_GUIDANCE, "test-local-guidance/5", remapped),),
        )
    assert stale_occurrence.value.refusal.kind == \
        "catalogue-local-guidance-stale-occurrence"


def test_enabled_standard_schema_refuses_a_missing_capability():
    schema, complete = _declarative_provider()
    partial = GoalProvider.builtin(
        GoalProviderIdentity("partial-provider", 1), schema, complete.catalogue,
        (
            CapabilityBinding(
                DECLARATION, "partial-declaration/1", complete.catalogue),
            CapabilityBinding(
                CONCRETE_HIT, "partial-concrete/1", complete.catalogue),
        ),
    )
    registry = GoalProviderRegistry((schema,), (partial,))

    policy = registry.freeze(
        "test-goals/1",
        (GoalPolicySelection(schema, ENABLED, (partial,)),))

    assert policy.manifest == partial.catalogue
    assert policy.validate(CONCRETE_PHASE) is policy
    with pytest.raises(Refused) as exact:
        policy.validate(EXACT_ANALYSIS_PHASE)
    assert exact.value.refusal.kind == "goal-provider-enabled-capability-missing"
    assert exact.value.refusal.as_dict()["detail"] == {
        "schema": schema.key,
        "provider": partial.identity.key,
        "phase": EXACT_ANALYSIS_PHASE,
        "missing": [EXACT_BOUNDED_HIT],
    }
    with pytest.raises(Refused) as release:
        policy.validate(RELEASE_PHASE)
    assert set(release.value.refusal.detail["missing"]) == {
        LOCAL_GUIDANCE, EXACT_BOUNDED_HIT}


def test_extension_schema_can_deliberately_omit_local_guidance():
    _standard, provider = _declarative_provider(guidance=False)
    extension = GoalSchemaDefinition("custom-value-bin", 1)
    provider = GoalProvider.declarative(
        GoalProviderIdentity("custom-bins", 1), extension,
        provider.catalogue, provider.semantics)

    policy = GoalProviderRegistry((extension,), (provider,)).freeze(
        "test-goals/1",
        (GoalPolicySelection(extension, ENABLED, (provider,)),))

    assert policy.manifest == provider.catalogue
    assert not provider.has(LOCAL_GUIDANCE)
    assert set(extension.required) == {
        DECLARATION, CONCRETE_HIT, EXACT_BOUNDED_HIT}


def test_table_three_schema_requirements_are_explicit_and_versioned():
    schemas = {schema.name: schema for schema in standard_goal_schemas()}

    assert set(schemas) == {
        "location", "control-choice", "boolean-expression",
        "loop-activation", "value-observation", "operation-event",
        "state-activity", "sequence", "latency", "schedule-interleaving",
        "artifact-failure", "monitor-obligation",
    }
    assert all(schema.version == 1 and schema.standard
               for schema in schemas.values())
    assert LOCAL_GUIDANCE not in schemas["schedule-interleaving"].required
    assert all(LOCAL_GUIDANCE in schema.required
               for name, schema in schemas.items()
               if name != "schedule-interleaving")


def test_disabled_and_inapplicable_schemas_retain_versioned_reasons_and_no_goals():
    schema, partial = _declarative_provider(guidance=False)
    other = _schema("operation-event")
    registry = GoalProviderRegistry((schema, other), (partial,))
    policy = registry.freeze(
        "test-goals/1",
        (
            GoalPolicySelection(
                schema, DISABLED, (partial,), VersionedReason(
                    "backend-capability-open", "test-goals/1",
                    "local guidance is not implemented for this build")),
            GoalPolicySelection(
                other, INAPPLICABLE, (), VersionedReason(
                    "no-eligible-operation", "test-goals/1",
                    "the admitted artifact has no operation sites")),
        ),
    )

    assert len(policy.manifest) == 0
    rows = {row["schema"]["name"]: row
            for row in policy.as_dict()["selections"]}
    assert rows["value-observation"]["status"] == DISABLED
    assert rows["value-observation"]["providers"][0]["capabilities"]
    assert rows["operation-event"]["status"] == INAPPLICABLE
    assert rows["operation-event"]["reason"]["policy"] == "test-goals/1"

    with pytest.raises(ValueError, match="different goal policy"):
        GoalProviderRegistry((other,), ()).freeze(
            "test-goals/2",
            (GoalPolicySelection(
                other, INAPPLICABLE, (), VersionedReason(
                    "no-eligible-operation", "test-goals/1", "none")),))


@pytest.mark.parametrize("capability", (
    DECLARATION,
    CONCRETE_HIT,
    LOCAL_GUIDANCE,
    EXACT_BOUNDED_HIT,
))
def test_deleting_any_enabled_capability_row_fails_at_the_provider_boundary(
        capability):
    _schema_row, provider = _declarative_provider()
    policy = _enabled_policy(provider)

    with pytest.raises(Refused) as deleted_provider:
        policy.reconcile_all(capability, {})
    assert deleted_provider.value.refusal.kind == \
        "goal-provider-capability-provider-missing"


def test_deleted_or_stale_goals_fail_capability_reconciliation():
    _schema_row, provider = _declarative_provider()
    policy = _enabled_policy(provider)

    one_goal = CatalogueManifest((
        provider.catalogue["extension.bin.low"],))
    with pytest.raises(Refused) as deleted_emitter:
        provider.binding(CONCRETE_HIT).adapter(one_goal)
    assert deleted_emitter.value.refusal.kind == \
        "catalogue-concrete-hit-missing-goal"

    changed = Goal(
        "extension.bin.low", COVERAGE, family="different",
        provider="task-extension", event="sample",
        policy="extension-bins/1", evidence="trace")
    with pytest.raises(Refused) as stale:
        provider.binding(EXACT_BOUNDED_HIT).adapter(CatalogueManifest(
            (changed, provider.catalogue["extension.bin.high"])))
    assert stale.value.refusal.kind == "catalogue-exact-bounded-hit-stale-goal"

    with pytest.raises(TypeError, match="registered adapter"):
        policy.reconcile(provider.identity, CONCRETE_HIT, provider.catalogue)


def test_registry_requires_every_schema_disposition_and_exact_provider_version():
    schema, provider = _declarative_provider()
    other = _schema("operation-event")
    registry = GoalProviderRegistry((schema, other), (provider,))

    with pytest.raises(Refused) as incomplete:
        registry.freeze(
            "test-goals/1",
            (GoalPolicySelection(schema, ENABLED, (provider,)),))
    assert incomplete.value.refusal.kind == \
        "goal-policy-schema-disposition-incomplete"

    with pytest.raises(Refused) as unknown:
        registry.provider(GoalProviderIdentity("task-extension", 2))
    assert unknown.value.refusal.kind == "goal-provider-version-unsupported"


def test_existing_monitor_and_adapter_report_only_their_real_capabilities():
    from .test_api_path import contract
    from .test_protocol_adapters import _contract as adapter_contract

    ready_valid = Monitor(contract(answers=1, deadline=2))
    providers = ready_valid.goal_providers()
    provider = next(item for item in providers
                    if item.schema.name == "monitor-obligation")
    assert "monitor.response-deadline" in provider.catalogue
    assert provider.catalogue["monitor.response-deadline"].cls == "safety"
    assert not any(item.schema.name == "latency" for item in providers)
    for item in providers:
        assert item.has(DECLARATION) and item.has(CONCRETE_HIT)
        assert item.has(LOCAL_GUIDANCE)
        assert item.binding(LOCAL_GUIDANCE).implementation == \
            RTL_LOCAL_GUIDANCE_POLICY
        assert item.has(EXACT_BOUNDED_HIT)

    start_done = Monitor(adapter_contract("start-done"))
    adapter_provider = start_done.goal_provider_capabilities()
    assert set(adapter_provider.catalogue) == {
        "monitor.input-completion",
        "monitor.start-done.done-without-outstanding",
    }
    assert adapter_provider.has(EXACT_BOUNDED_HIT)
    assert adapter_provider.has(LOCAL_GUIDANCE)
    assert adapter_provider.binding(LOCAL_GUIDANCE).implementation == \
        RTL_LOCAL_GUIDANCE_POLICY
    assert all(item.schema.name != "latency"
               for item in start_done.goal_providers())
    no_deadline_policy = evaluation_goal_policy(
        start_done.contract, CatalogueManifest(()))
    no_deadline_status = {
        row["schema"]["name"]: row["status"]
        for row in no_deadline_policy.as_dict()["selections"]
    }
    assert no_deadline_status["latency"] == INAPPLICABLE

    deadline_monitor = Monitor(adapter_contract("start-done", deadline=1))
    deadline_policy = evaluation_goal_policy(
        deadline_monitor.contract, CatalogueManifest(()))
    deadline_status = {
        row["schema"]["name"]: row["status"]
        for row in deadline_policy.as_dict()["selections"]
    }
    assert deadline_status["latency"] == INAPPLICABLE
    assert deadline_status["monitor-obligation"] == ENABLED

    registry = GoalProviderRegistry(
        (provider.schema,), (provider,))
    policy = registry.freeze(
        "test-goals/1",
        (GoalPolicySelection(provider.schema, ENABLED, (provider,)),))
    assert set(policy.manifest) == set(provider.catalogue)
    assert policy.validate(CONCRETE_PHASE) is policy
    assert policy.validate(EXACT_ANALYSIS_PHASE) is policy
    assert policy.validate(RELEASE_PHASE) is policy

    # Deleting the real local adapter is visible at the release capability boundary;
    # declaration/exactness do not imply guidance.
    missing_local = GoalProvider.builtin(
        adapter_provider.identity, adapter_provider.schema,
        adapter_provider.catalogue,
        tuple(binding for binding in adapter_provider.capabilities
              if binding.kind != LOCAL_GUIDANCE))
    deleted = GoalProviderRegistry(
        (missing_local.schema,), (missing_local,)).freeze(
            "test-goals/1",
            (GoalPolicySelection(
                missing_local.schema, ENABLED, (missing_local,)),))
    with pytest.raises(Refused) as missing:
        deleted.validate(RELEASE_PHASE)
    assert missing.value.refusal.detail["missing"] == (LOCAL_GUIDANCE,)


def test_reset_restoration_is_an_independent_complete_standard_provider():
    from .test_reset_restoration_monitor import _contract

    monitor = Monitor(_contract())
    providers = monitor.goal_providers()
    reset = next(item for item in providers
                 if item.identity.key == "reset-restoration/v2")
    scenario = next(item for item in providers
                    if item.identity.key == "reset-scenario/v1")
    protocol = next(item for item in providers
                    if item.identity.key == "task-monitor/v1")

    assert set(reset.catalogue) == {"monitor.reset-restoration.clk.violation"}
    assert set(scenario.catalogue) == {"monitor.reset-restoration.clk.scenario"}
    assert scenario.schema.name == "sequence"
    assert reset.schema.name == "monitor-obligation"
    assert not set(reset.catalogue) & set(protocol.catalogue)
    assert {binding.kind for binding in reset.capabilities} == {
        DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT,
    }
    assert reset.binding(LOCAL_GUIDANCE).adapter.catalogue == reset.catalogue
    assert {binding.kind for binding in scenario.capabilities} == {
        DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT,
    }
    assert scenario.binding(LOCAL_GUIDANCE).adapter.catalogue == scenario.catalogue

    policy = evaluation_goal_policy(
        _contract(), CatalogueManifest(()), artifact_kind="device")
    assert policy.validate(RELEASE_PHASE) is policy
    assert policy.reconcile_all(LOCAL_GUIDANCE, {
        provider.identity: provider.binding(LOCAL_GUIDANCE).adapter
        for provider in policy.enabled_providers(LOCAL_GUIDANCE)
    })


def test_monitor_obligations_do_not_swallow_reset_sequence_coverage():
    from .test_reset_restoration_monitor import _contract

    monitor = Monitor(_contract())
    providers = monitor.goal_providers()
    for provider in providers:
        if provider.schema.name == "monitor-obligation":
            assert all(goal.cls == "safety" for goal in provider.catalogue.values())
    scenario, = [provider for provider in providers if provider.schema.name == "sequence"]
    assert set(scenario.catalogue) == {"monitor.reset-restoration.clk.scenario"}
    assert scenario.catalogue["monitor.reset-restoration.clk.scenario"].cls == "coverage"
    assert {goal: row for provider in providers for goal, row in provider.catalogue.items()} == monitor.goals()


def test_production_policy_owns_the_denominator_and_analysis_gap_is_ancillary():
    from .test_witness_pool import _evaluation

    evaluation = _evaluation((2,))
    before = evaluation.as_dict()
    policy = evaluation_goal_policy(
        evaluation.task.contract, api.CatalogueManifest(()))

    assert evaluation.passed
    assert policy.manifest == evaluation.catalogue
    assert set(policy.manifest) == set(evaluation.catalogue)
    assert policy.validate(CONCRETE_PHASE) is policy
    dispositions = {
        row["schema"]["name"]: row["status"]
        for row in policy.as_dict()["selections"]
    }
    assert dispositions["monitor-obligation"] == ENABLED
    assert set(dispositions.values()) == {ENABLED, INAPPLICABLE}
    assert all(
        row["reason"] is not None
        for row in policy.as_dict()["selections"]
        if row["status"] == INAPPLICABLE)

    analysed = api.analyse(evaluation,
        wall_timeout_seconds=60)
    assert analysed.verdict is api.Verdict.REFUSED
    assert analysed.evidence["refusal"]["kind"] == \
        "analysis-exact-instance-missing"
    assert evaluation.as_dict() == before


@pytest.mark.parametrize("with_choice", (False, True))
def test_source_local_and_exact_capabilities_have_separate_bindings(with_choice):
    from behave.sim.core.goal_provider import RTL_EXACT_BOUNDED_POLICY
    from .test_api_path import contract

    frontend = CatalogueManifest((Goal(
        "rtl-source:test:true", COVERAGE, family="control-choice", provider="rtl-structural",
        event="true", policy="test-source/1"),) if with_choice else ())
    policy = evaluation_goal_policy(contract(), frontend, artifact_kind="rtl")
    rows = {row.schema.name: row for row in policy.selections}
    assert not any(row.status == INCOMPLETE for row in policy.selections)
    assert policy.as_dict()["enumeration_complete"] is True
    assert rows["control-choice"].status == (ENABLED if with_choice else INAPPLICABLE)
    assert set(frontend) <= set(policy.manifest)
    assert policy.validate(CONCRETE_PHASE) is policy
    if with_choice:
        choice = rows["control-choice"].providers[0]
        assert choice in policy.enabled_providers(CONCRETE_HIT)
        assert choice in policy.enabled_providers(LOCAL_GUIDANCE)
        assert choice.binding(LOCAL_GUIDANCE).implementation == RTL_LOCAL_GUIDANCE_POLICY
        assert choice in policy.enabled_providers(EXACT_BOUNDED_HIT)
        assert choice.binding(EXACT_BOUNDED_HIT).implementation == RTL_EXACT_BOUNDED_POLICY
        assert choice.binding(EXACT_BOUNDED_HIT).adapter is not choice.binding(LOCAL_GUIDANCE).adapter
        assert policy.validate(RELEASE_PHASE) is policy
        assert policy.reconcile(choice.identity, CONCRETE_HIT,
                                choice.binding(CONCRETE_HIT).adapter) is not None


def test_transaction_behavior_does_not_invent_signal_level_monitor_goals():
    from .test_api_path import contract

    frontend = CatalogueManifest((Goal(
        "behavior.branch.true",
        COVERAGE,
        family="decision",
        provider="python-structural",
        event="T",
        policy="source-site/1",
    ),))
    policy = evaluation_goal_policy(
        contract(), frontend, artifact_kind="behavior")

    assert policy.manifest == frontend
    dispositions = {
        row["schema"]["name"]: row["status"]
        for row in policy.as_dict()["selections"]
    }
    assert dispositions["monitor-obligation"] == INAPPLICABLE

    signal_policy = evaluation_goal_policy(
        contract(answers=1, deadline=2),
        CatalogueManifest(()),
        artifact_kind="device",
    )
    assert "monitor.response-deadline" in signal_policy.manifest


def test_goal_policy_construction_is_bound_to_the_artifact_kind():
    from .test_api_path import contract

    frontend = CatalogueManifest((Goal(
        "behavior.branch.true",
        COVERAGE,
        family="decision",
        provider="python-structural",
        event="T",
        policy="source-site/1",
    ),))
    policy = evaluation_goal_policy(
        contract(), frontend, artifact_kind="behavior")
    assert policy.manifest == frontend

    with pytest.raises(Refused) as wrong_kind:
        evaluation_goal_policy(
            contract(), frontend, artifact_kind="not-a-realization-kind")
    assert wrong_kind.value.refusal.kind == "goal-policy-artifact-kind-unknown"


def test_complete_builtin_rows_reconcile_every_capability_they_claim():
    from .test_api_path import contract

    frontend = CatalogueManifest((Goal(
        "behavior.branch.true",
        COVERAGE,
        family="decision",
        provider="python-structural",
        event="T",
        policy="source-site/1",
    ),))
    policy = evaluation_goal_policy(contract(), frontend)
    behavior = next(
        provider for provider in policy.enabled_providers()
        if provider.identity.key == "behavior-control-choice/v1")

    assert behavior.catalogue == frontend
    assert {binding.kind for binding in behavior.capabilities} == {
        DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT}
    for capability in (
            DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT):
        supplied = {
            provider.identity: provider.binding(capability).adapter
            for provider in policy.enabled_providers(capability)
        }
        assert policy.reconcile_all(capability, supplied)


def test_behavior_location_uses_the_complete_builtin_capability_chain():
    frontend = CatalogueManifest((Goal(
        "location:behavior.py:5:8:0:entry",
        COVERAGE,
        family="location",
        provider="python-structural",
        event="entry",
        policy="python-structural/3/source-site",
    ),))

    (provider,) = builtin_frontend_goal_providers(frontend)

    assert provider.identity.key == "behavior-location/v1"
    assert provider.schema.name == "location"
    assert provider.catalogue == frontend
    assert {binding.kind for binding in provider.capabilities} == {
        DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT}


def test_rtl_location_uses_the_complete_builtin_capability_chain():
    frontend = CatalogueManifest((Goal(
        "rtl-location:top:f.sv:5.1-5.8:abcd:ef01:entry",
        COVERAGE,
        family="location",
        provider="rtl-structural",
        event="entry",
        policy="rtl-structural/10/source-projected-action",
    ),))

    (provider,) = builtin_frontend_goal_providers(frontend)

    assert provider.identity.key == "rtl-location/v3"
    assert provider.schema.name == "location"
    assert provider.catalogue == frontend
    assert {binding.kind for binding in provider.capabilities} == {
        DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT}


def test_rtl_control_provider_binds_the_real_exact_local_guidance_adapter():
    frontend = CatalogueManifest((Goal(
        "rtl.branch.true",
        COVERAGE,
        family="case-arm",
        provider="rtl-structural",
        event="T",
        policy="rtl-sites/2",
    ),))
    (provider,) = builtin_frontend_goal_providers(frontend)
    policy = GoalProviderRegistry((provider.schema,), (provider,)).freeze(
        "test-goals/1",
        (GoalPolicySelection(provider.schema, ENABLED, (provider,)),),
    )

    assert {binding.kind for binding in provider.capabilities} == {
        DECLARATION, CONCRETE_HIT, LOCAL_GUIDANCE, EXACT_BOUNDED_HIT}
    assert provider.binding(LOCAL_GUIDANCE).implementation == \
        RTL_LOCAL_GUIDANCE_POLICY
    from behave.sim.engines.rtl.generation import RTLGenerationProvider
    assert RTLGenerationProvider.policy == RTL_LOCAL_GUIDANCE_POLICY
    assert policy.validate(EXACT_ANALYSIS_PHASE) is policy
    assert policy.validate(RELEASE_PHASE) is policy

    deleted = GoalProvider.builtin(
        provider.identity, provider.schema, provider.catalogue,
        tuple(binding for binding in provider.capabilities
              if binding.kind != LOCAL_GUIDANCE))
    missing_policy = GoalProviderRegistry(
        (deleted.schema,), (deleted,)).freeze(
            "test-goals/1",
            (GoalPolicySelection(deleted.schema, ENABLED, (deleted,)),))
    with pytest.raises(Refused) as release:
        missing_policy.validate(RELEASE_PHASE)
    assert release.value.refusal.detail["missing"] == (LOCAL_GUIDANCE,)


def test_builtin_policy_refuses_an_unregistered_frontend_family():
    frontend = CatalogueManifest((Goal(
        "behavior.unknown",
        COVERAGE,
        family="not-a-standard-owner",
        provider="python-structural",
        policy="source-sites/1",
    ),))

    with pytest.raises(Refused) as refused:
        builtin_frontend_goal_providers(frontend)
    assert refused.value.refusal.kind == "goal-provider-builtin-owner-unknown"


def test_capability_surface_is_public_but_does_not_enter_score_records():
    from behave.sim.core.finite_goal import DeclarativeConcreteStep, DeclarativeSymbolicStep

    assert api.GoalProvider is GoalProvider
    assert api.GoalCapabilityUse is GoalCapabilityUse
    assert api.DeclarativeGoalSemantics is DeclarativeGoalSemantics
    assert api.DeclarativeConcreteStep is DeclarativeConcreteStep
    assert api.DeclarativeSymbolicStep is DeclarativeSymbolicStep
    from .test_witness_pool import _evaluation

    evaluation = _evaluation((2,))
    before = evaluation.as_dict()
    _schema_row, provider = _declarative_provider()
    policy = _enabled_policy(provider)
    assert policy.manifest == provider.catalogue
    Monitor(evaluation.task.contract).goal_provider_capabilities()
    assert evaluation.as_dict() == before
    assert "goal_policy" not in evaluation.as_dict()
