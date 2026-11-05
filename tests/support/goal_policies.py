"""Finite goal-policy fixtures built through the production policy entry point."""

from __future__ import annotations

from behave.sim.core.catalogue import (
    COVERAGE,
    SAFETY,
    CatalogueManifest,
    Goal,
    OccurrenceKey,
)
from behave.sim.core.contract import (
    ClockDomain,
    Direction,
    Endpoint,
    Field,
    FiniteType,
    Kind,
    ResetPolicy,
    TaskContract,
    ready_valid,
    start_done,
)
from behave.sim.core.goal_provider import (
    ENABLED,
    INAPPLICABLE,
    DeclarativeGoalSemantics,
    DeclarativeGuidanceAdapter,
    FiniteMonitor,
    FiniteMonitorTransition,
    FinitePredicate,
    FiniteVariable,
    GoalPolicySelection,
    GoalProvider,
    GoalProviderIdentity,
    GoalProviderRegistry,
    VersionedReason,
    standard_goal_schemas,
)
from behave.sim.goal_policy import evaluation_goal_policy

__all__ = ["goal_policy_fixtures"]


def _ready_valid_contract():
    return TaskContract(
        (
            Endpoint(
                "in", Direction.IN, (Field("data", FiniteType(8), "in_data"),),
                ready_valid(Direction.IN, "in_valid", "in_ready"), "clk",
                max_outstanding=2, response_deadline=4,
                kind=Kind.TRANSACTION,
                reset_policy=ResetPolicy.CLEARS),
            Endpoint(
                "out", Direction.OUT,
                (Field("data", FiniteType(8), "out_data"),),
                ready_valid(Direction.OUT, "out_valid", "out_ready"), "clk",
                answers_per_transaction=1,
                terminal_quiescence=True, kind=Kind.TRANSACTION,
                reset_policy=ResetPolicy.CLEARS),
        ),
        (ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=3,
            reset_restores=True),),
        source="tracked release goal-policy fixture",
    )


def _start_done_contract():
    return TaskContract(
        (
            Endpoint(
                "in", Direction.IN, (Field("data", FiniteType(8), "in_data"),),
                start_done(Direction.IN, "start", "done"), "clk",
                max_outstanding=1, response_deadline=4,
                kind=Kind.TRANSACTION, reset_policy=ResetPolicy.CLEARS),
            Endpoint(
                "out", Direction.OUT,
                (Field("data", FiniteType(8), "out_data"),),
                start_done(Direction.OUT, "start", "done"), "clk",
                answers_per_transaction=1, terminal_quiescence=True,
                kind=Kind.TRANSACTION, reset_policy=ResetPolicy.CLEARS),
        ),
        (ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=3,
            reset_restores=True),),
        source="tracked start/done release goal-policy fixture",
    )


def _behavior_frontend():
    goals = (
        Goal(
            "release.behavior.location.branch-body", COVERAGE,
            family="location", provider="python-structural", event="entry",
            policy="source-site/1"),
        Goal(
            "release.behavior.branch.true", COVERAGE, family="decision",
            provider="python-structural", event="T", policy="source-site/1"),
        Goal(
            "release.behavior.condition.true", COVERAGE, family="condition",
            provider="python-structural", event="T", policy="source-site/1"),
        Goal(
            "release.behavior.loop.entered", COVERAGE, family="loop",
            provider="python-structural", event="T", policy="source-site/1"),
        Goal(
            "release.behavior.failure", SAFETY, family="failure",
            provider="python-structural", event="Fail",
            policy="source-site/1"),
    )
    return CatalogueManifest(goals, occurrences=(
        (OccurrenceKey(
            "python-structural", ("process", "if", 0, "body", 0), "entry"),
         goals[0].id),
        (OccurrenceKey(
            "python-structural", ("process", "if", 0), "true"), goals[1].id),
        (OccurrenceKey(
            "python-structural", ("process", "while", 0), "true"), goals[2].id),
        (OccurrenceKey(
            "python-structural", ("process", "for", 0), "entered"), goals[3].id),
        (OccurrenceKey(
            "python-structural", ("process", "fail", 0), "failure"),
         goals[4].id),
    ))


def _rtl_frontend():
    from behave.sim.rtl.case_match import invalid_label, match_schema
    from behave.sim.rtl.catalogue import (
        DEFAULT_STRUCTURAL_POLICY,
        LOCATION,
        Site,
        occurrence_key_of,
    )

    # This is the smallest fixture that exercises the complete structural-v2 shape:
    # several written case arms and the synthetic outcome of a switch with no default.
    # A one-goal fixture would prove that ``case-arm`` has an owner while leaving the
    # multiway/no-match rows that motivated v2 outside the tracked release preflight.
    labels = ("2'00", "2'01", "2'10", "no-match")
    goals = []
    occurrences = []
    if invalid_label(labels) is not None:
        raise RuntimeError("the tracked RTL release switch has an unreadable label")
    for index, outcome in enumerate(labels):
        goal = Goal(
            f"release.rtl.switch.arm.{index}", COVERAGE, family="case-arm",
            provider="rtl-structural", event="T",
            policy=DEFAULT_STRUCTURAL_POLICY.identity,
            provenance={
                "module": "release_rtl_fixture",
                "process": "$proc$release",
                "signal": "\\sel",
                "outcome": outcome,
                "match_rule": match_schema(outcome, labels),
            })
        goals.append(goal)
        occurrences.append((OccurrenceKey(
            "rtl-structural",
            ("release_rtl_fixture", "$proc$release", "\\sel", 0),
            outcome), goal.id))

    location = Site(
        "release_rtl_fixture", "$proc$release", "1'1",
        "release_fixture.sv:7.5-7.19", "entry", 1,
        (("\\sel", "2'01"),), kind=LOCATION,
        action="$0\\out_data \\in_data", triggers=(("posedge", "\\clk"),))
    goals.append(Goal(
        location.goal, COVERAGE, family="location",
        provider="rtl-structural", event="entry",
        policy=DEFAULT_STRUCTURAL_POLICY.identity,
        provenance={
            "module": location.module,
            "signal": "\\out_data",
            "src": location.src,
            "triggers": [list(pair) for pair in location.triggers],
            "action": "$0\\out_data \\in_data",
            "outcome": "entry",
            "occurrences": 1,
            "match_rule": ["activation", None],
        }))
    occurrences.append((occurrence_key_of(location), location.goal))
    return CatalogueManifest(
        tuple(goals), occurrences=tuple(occurrences))


def _declarative_policy():
    """Release fixtures for the standard declarative predicate/monitor schemas."""

    schemas = standard_goal_schemas()
    by_name = {schema.name: schema for schema in schemas}
    predicate = FinitePredicate(
        "release.extension.value.high",
        (FiniteVariable("value", (0, 1, 2)),),
        ((2,),),
    )
    operation = FinitePredicate(
        "release.extension.operation.saturates",
        (FiniteVariable("saturated", (0, 1)),),
        ((1,),),
    )
    sequence_goal = "release.extension.sequence.ab"
    monitor = FiniteMonitor(
        states=("idle", "seen-a"),
        events=("a", "b"),
        initial="idle",
        goals=(sequence_goal,),
        transitions=(
            FiniteMonitorTransition("idle", "a", "seen-a"),
            FiniteMonitorTransition("idle", "b", "idle"),
            FiniteMonitorTransition("seen-a", "a", "seen-a"),
            FiniteMonitorTransition(
                "seen-a", "b", "idle", (sequence_goal,)),
        ),
    )

    activity_goal = "release.extension.state.rising"
    activity = FiniteMonitor(
        states=("zero", "one"),
        events=("a", "b"),
        initial="zero",
        goals=(activity_goal,),
        transitions=(
            FiniteMonitorTransition("zero", "a", "zero"),
            FiniteMonitorTransition(
                "zero", "b", "one", (activity_goal,)),
            FiniteMonitorTransition("one", "a", "zero"),
            FiniteMonitorTransition("one", "b", "one"),
        ),
    )
    schedule_goal = "release.extension.schedule.a-then-b"
    schedule = FiniteMonitor(
        states=("idle", "seen-a"),
        events=("a", "b"),
        initial="idle",
        goals=(schedule_goal,),
        transitions=(
            FiniteMonitorTransition("idle", "a", "seen-a"),
            FiniteMonitorTransition("idle", "b", "idle"),
            FiniteMonitorTransition("seen-a", "a", "seen-a"),
            FiniteMonitorTransition(
                "seen-a", "b", "idle", (schedule_goal,)),
        ),
    )

    def provider(schema_name, semantics, goals, *, guided=True):
        goals = tuple(goals)
        catalogue = CatalogueManifest(
            goals,
            occurrences=tuple(
                (OccurrenceKey(
                    "task-extension", ("release", schema_name, index), "hit"),
                 goal.id)
                for index, goal in enumerate(goals)
            ),
        )
        guidance = (
            DeclarativeGuidanceAdapter(semantics, catalogue)
            if guided else None)
        return GoalProvider.declarative(
            GoalProviderIdentity(f"release-extension-{schema_name}", 1),
            by_name[schema_name],
            catalogue,
            semantics,
            guidance=guidance,
        )

    providers = (
        provider(
            "value-observation",
            DeclarativeGoalSemantics(predicates=(predicate,)),
            (Goal(
                predicate.goal, COVERAGE, family="value-observation",
                provider="task-extension", event="sample",
                policy="release-extension-value/1", evidence="trace"),),
        ),
        provider(
            "operation-event",
            DeclarativeGoalSemantics(predicates=(operation,)),
            (Goal(
                operation.goal, COVERAGE, family="operation-event",
                provider="task-extension", event="operation",
                policy="release-extension-operation/1", evidence="trace"),),
        ),
        provider(
            "state-activity",
            DeclarativeGoalSemantics(monitors=(activity,)),
            (Goal(
                activity_goal, COVERAGE, family="state-activity",
                provider="task-extension", event="transition",
                policy="release-extension-state/1", evidence="trace"),),
        ),
        provider(
            "sequence",
            DeclarativeGoalSemantics(monitors=(monitor,)),
            (Goal(
                sequence_goal, COVERAGE, family="sequence",
                provider="task-extension", event="transition",
                policy="release-extension-sequence/1", evidence="trace"),),
        ),
        provider(
            "schedule-interleaving",
            DeclarativeGoalSemantics(monitors=(schedule,)),
            (Goal(
                schedule_goal, COVERAGE, family="schedule-interleaving",
                provider="task-extension", event="transition",
                policy="release-extension-schedule/1", evidence="trace"),),
            guided=False,
        ),
    )
    selections = tuple(
        GoalPolicySelection(
            schema,
            ENABLED,
            tuple(item for item in providers if item.schema == schema),
        )
        if any(item.schema == schema for item in providers)
        else GoalPolicySelection(
            schema,
            INAPPLICABLE,
            reason=VersionedReason(
                "no-eligible-goals",
                "release-declarative-policy/1",
                "this declarative fixture declares no instance of the schema",
            ),
        )
        for schema in schemas
    )
    return GoalProviderRegistry(schemas, providers).freeze(
        "release-declarative-policy/1", selections)


def goal_policy_fixtures():
    """Return behavior, RTL and declarative policies for semantic tests."""

    ready_valid_contract = _ready_valid_contract()
    behavior_policy = evaluation_goal_policy(
        ready_valid_contract, _behavior_frontend(), artifact_kind="behavior")
    rtl_ready_valid_policy = evaluation_goal_policy(
        ready_valid_contract, _rtl_frontend(), artifact_kind="rtl")
    rtl_start_done_policy = evaluation_goal_policy(
        _start_done_contract(), _rtl_frontend(), artifact_kind="rtl")
    return {
        "behavior-structural/v3": behavior_policy,
        "rtl-structural-ready-valid/v3": rtl_ready_valid_policy,
        "rtl-structural-start-done/v2": rtl_start_done_policy,
        "declarative-standard-extensions/v2": _declarative_policy(),
    }
