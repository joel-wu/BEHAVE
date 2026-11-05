"""Finite semantic acceptance for schemas enabled by the release fixtures.

The fixtures below are deliberately finite. Each row is checked in both concrete
and exact form. Reachable goals have positive/negative witnesses; statically
discharged goals retain their catalogue entries and require a negative query
answer and no concrete hit. Production local-guidance execution is a separate requirement:
a finite-table witness alone is not evidence that a registered GenerationProvider can
realize a candidate.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.core.goal_provider import (
    DECLARATION,
    ENABLED,
    INAPPLICABLE,
    INCOMPLETE,
    RELEASE_PHASE,
    GoalPolicySelection,
    GoalProvider,
    GoalProviderRegistry,
    VersionedReason,
)
from behave.sim.core.protocol import adapter_program_for
from behave.sim.core.protocol_exact import AdapterExactTable
from behave.sim.core.ready_valid_product import (
    ConcreteAlgebra,
    OrderedReadyValidConfig,
    OrderedReadyValidEvent,
    OrderedReadyValidState,
    ordered_ready_valid_initial,
    ordered_ready_valid_state_image,
    ordered_ready_valid_step,
)
from behave.sim.core.refusal import Refused
from behave.sim.core.reset_monitor import (
    ResetRestorationConfig,
    ResetRestorationEvent,
    ResetRestorationState,
    reset_restoration_initial,
    reset_restoration_state_image,
    reset_restoration_step,
)
from behave.sim.rtl.catalogue import LOCATION, Site
from behave.sim.rtl.site_target import predicate_for, site_hits
from behave.sim.rtl.task_symbolic import Z3Algebra
from tests.support.goal_policies import goal_policy_fixtures


@dataclass(frozen=True)
class _Cell:
    concrete: frozenset[str]
    exact: frozenset[str]
    admissible: bool = True


@dataclass(frozen=True)
class _Evidence:
    goals: frozenset[str]
    cells: tuple[_Cell, ...]
    witnessed: frozenset[str]
    statically_false: frozenset[str] = frozenset()


class _BooleanAlgebra:
    """Concrete carrier used to evaluate the production symbolic table lookup."""

    literal = staticmethod(lambda value: value)
    equal = staticmethod(lambda left, right: left == right)
    land = staticmethod(lambda *values: all(values))
    lor = staticmethod(lambda *values: any(values))
    ite = staticmethod(
        lambda condition, when_true, when_false:
        when_true if condition else when_false)


def _read_z3(value):
    value = z3.simplify(value)
    if z3.is_true(value):
        return True
    if z3.is_false(value):
        return False
    if z3.is_int_value(value) or z3.is_bv_value(value):
        return value.as_long()
    raise AssertionError(f"the finite symbolic value did not simplify: {value}")


def _assert_acceptance(evidence):
    assert type(evidence) is _Evidence
    assert evidence.goals and evidence.cells
    assert evidence.statically_false <= evidence.goals
    assert evidence.witnessed == evidence.goals - evidence.statically_false
    for cell in evidence.cells:
        assert cell.concrete == cell.exact
    admissible = tuple(cell for cell in evidence.cells if cell.admissible)
    assert admissible
    for goal in evidence.goals:
        if goal in evidence.statically_false:
            assert all(goal not in cell.concrete for cell in admissible), \
                f"{goal} is statically discharged but has a concrete hit"
        else:
            assert any(goal in cell.concrete for cell in admissible), \
                f"{goal} has no positive concrete trace"
        assert any(goal not in cell.concrete for cell in admissible), \
            f"{goal} has no negative concrete trace"


def _rtl_structural_evidence():
    siblings = ("2'00", "2'01", "2'10", "no-match")
    arms = tuple(Site(
        "release", "$proc$release", "\\sel", "release.sv:1", outcome,
        0, (), siblings=siblings)
        for outcome in siblings)
    location = Site(
        "release", "$proc$release", "1'1", "release.sv:2", "entry",
        1, (("\\sel", "2'01"),), kind=LOCATION,
        action="$0\\y 1'1")
    sites = arms + (location,)
    goals = frozenset(site.goal for site in sites)
    selector = z3.BitVec("release_goal_matrix_selector", 2)

    class Explorer:
        @staticmethod
        def value_var(name):
            assert name == "sel"
            return selector, 2

    terms = {site.goal: predicate_for(Explorer(), site) for site in sites}
    assert all(z3.is_bool(term) for term in terms.values())
    cells = []
    for value in range(4):
        concrete, refused = site_hits(sites, {"sel": value})
        assert not refused
        substitutions = ((selector, z3.BitVecVal(value, 2)),)
        exact = frozenset(
            goal for goal, term in terms.items()
            if z3.is_true(z3.simplify(z3.substitute(term, *substitutions))))
        cells.append(_Cell(frozenset(concrete), exact))

    witnessed = set()
    for goal, term in terms.items():
        solver = z3.Solver()
        solver.add(term)
        assert solver.check() == z3.sat
        value = solver.model().eval(selector, model_completion=True).as_long()
        concrete, refused = site_hits(sites, {"sel": value})
        assert not refused and goal in concrete
        witnessed.add(goal)
    return _Evidence(goals, tuple(cells), frozenset(witnessed))


def _behavior_evidence():
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.symbolic import interp
    from behave.sim.symbolic.encoder import encode_step, validated_width
    from behave.sim.engines import local_ir as exact
    from behave.sim.engines import probing

    from .test_exact_provider import DIVIDES, GATED, LOOP, build, states_of

    rows = (
        # The nested branch is enabled only after ``armed`` changes, so its
        # positive and negative cells require a two-step state carrier.
        (GATED, frozenset(("decision", "condition")), 2, None),
        (DIVIDES, None, 1, None),
        (LOOP, frozenset(("loop",)), 1, None),
        # Line 8 is the assignment in the nested branch.  It is a genuine
        # source-derived location with both entering and non-entering traces;
        # unconditional locations cannot honestly satisfy the negative-cell
        # requirement of this small acceptance product.
        (GATED, frozenset(("location",)), 2, frozenset((8,))),
    )
    all_goals = set()
    all_cells = []
    witnessed = set()
    statically_false = set()
    for source, families, bound, source_lines in rows:
        proc, env = build(source, bound=bound)
        instance = behavior_instance.BehaviorInstance(
            proc, env, source=source, bound=bound)
        manifest = instance.frontend_catalogue_manifest()
        goals = frozenset(
            goal.id for goal in manifest.values()
            if ((goal.cls == "safety") if families is None
                else goal.family in families)
            and (source_lines is None
                 or dict(goal.provenance).get("line") in source_lines))
        assert goals, (families, tuple(
            (goal.id, goal.family) for goal in manifest.values()))
        all_goals.update(goals)
        # Constructor-proved attribute presence declares AttributeError goals
        # without emitting an executable failure. Keep them in this matrix,
        # and check the real query decision instead of pretending that a raw
        # step encoding has a missing hit term equal to False.
        discharged = goals & instance.ruled_out().keys()
        if discharged:
            answer = instance.query(discharged, bound, timeout_ms=20_000)
            assert set(answer.unreachable) == discharged
            assert not answer.reachable and not answer.undecided
            statically_false.update(discharged)
        first_positive = {}
        for state in states_of(proc, env):
            tag = f"_{len(all_cells)}"
            inputs = {
                name: z3.BitVec(
                    f"{name}{tag}", validated_width(env[name], name))
                for name in proc.inputs
            }
            encoded = encode_step(
                proc,
                pre={**exact.pin_state(proc, env, state, tag=tag), **inputs},
                env=env,
                tag=tag,
            )
            for value in range(8):
                solver = z3.Solver()
                solver.add(encoded.constraint, inputs["a"] == value)
                assert solver.check() == z3.sat
                model = solver.model()
                exact_hits = frozenset(
                    goal for goal in goals - discharged
                    if z3.is_true(model.eval(
                        encoded.goal_condition(goal), model_completion=True)))
                _state, _outputs, hits, _error = interp.run_trace(
                    proc, [{"a": value}], dict(state))
                concrete_hits = frozenset(set(hits) & goals)
                all_cells.append(_Cell(concrete_hits, exact_hits))
                for goal in concrete_hits:
                    first_positive.setdefault(goal, state)

        for goal, state in first_positive.items():
            answer = exact.local_hit(
                proc, env, state, goal, timeout_ms=20_000)
            assert answer.outcome is probing.Outcome.SAT
            _state, _outputs, hits, _error = interp.run_trace(
                proc, [answer.candidate], dict(state))
            assert goal in hits
            witnessed.add(goal)
    return _Evidence(
        frozenset(all_goals), tuple(all_cells), frozenset(witnessed),
        frozenset(statically_false))


def _start_done_evidence():
    from .test_protocol_adapters import _contract

    contract = _contract("start-done", deadline=0)
    table = AdapterExactTable(adapter_program_for(contract), contract)
    goals = frozenset(table.goal_names)
    cells = []
    for row in table.transitions:
        encoded = table.symbolic(
            row.state, row.reset, dict(row.presented), dict(row.scheduled),
            dict(row.observed), _BooleanAlgebra())
        assert encoded.valid is row.environment_admissible
        assert encoded.state == row.next_state
        concrete = frozenset(row.goals)
        exact = frozenset(goal for goal, hit in encoded.hits.items() if hit)
        cells.append(_Cell(concrete, exact, row.environment_admissible))

    # A breadth-first walk from the registered initial state supplies an admissible
    # semantic witness per goal.  The release gate separately exercises the production
    # RTL generation implementation that realizes such witnesses as candidates.
    frontier = ((table.initial_state, ()),)
    seen = {table.initial_state}
    witnessed = set()
    while frontier and witnessed != set(goals):
        following = []
        for state, path in frontier:
            for row in table.transitions:
                if row.state != state or not row.environment_admissible:
                    continue
                candidate = path + (row,)
                witnessed.update(set(row.goals) & goals)
                if row.next_state not in seen:
                    seen.add(row.next_state)
                    following.append((row.next_state, candidate))
        frontier = tuple(following)
    return _Evidence(goals, tuple(cells), frozenset(witnessed))


def _ready_valid_symbolic_state(state):
    return OrderedReadyValidState(
        accepted_count=z3.IntVal(state.accepted_count),
        pending=tuple(z3.BoolVal(value) for value in state.pending),
        owed=tuple(z3.IntVal(value) for value in state.owed),
        accepted_at=tuple(z3.IntVal(value) for value in state.accepted_at),
        offered=tuple(z3.BoolVal(value) for value in state.offered),
        reported=tuple(z3.BoolVal(value) for value in state.reported),
        stalled=z3.BoolVal(state.stalled),
        held=tuple(z3.BitVecVal(value, 1) for value in state.held),
    )


def _ready_valid_evidence():
    config = OrderedReadyValidConfig(
        capacity=2, payload_fields=1, answers_per_transaction=1,
        max_outstanding=1, response_deadline=0)
    goals = frozenset({
        "monitor.sink-stability",
        "monitor.outstanding-exceeded",
        "monitor.output-without-transaction",
        "monitor.response-deadline",
    })
    initial = ordered_ready_valid_initial(config, ConcreteAlgebra(), (0,))
    frontier = ((initial, ()),)
    cells = []
    witnessed = set()
    for slot in range(2):
        following = {}
        for state, path in frontier:
            for reset, accepted, valid, ready, payload in itertools.product(
                    (False, True), repeat=5):
                event = OrderedReadyValidEvent(
                    reset, slot, accepted, valid, ready, (int(payload),))
                concrete = ordered_ready_valid_step(
                    config, state, event, ConcreteAlgebra())
                symbolic = ordered_ready_valid_step(
                    config, _ready_valid_symbolic_state(state),
                    OrderedReadyValidEvent(
                        z3.BoolVal(reset), slot, z3.BoolVal(accepted),
                        z3.BoolVal(valid), z3.BoolVal(ready),
                        (z3.BitVecVal(int(payload), 1),)),
                    Z3Algebra())
                concrete_hits = frozenset(
                    goal for goal, hit in concrete.hits.items() if hit)
                exact_hits = frozenset(
                    goal for goal, hit in symbolic.hits.items()
                    if _read_z3(hit))
                assert ordered_ready_valid_state_image(concrete.state) == \
                    ordered_ready_valid_state_image(symbolic.state, _read_z3)
                cells.append(_Cell(concrete_hits, exact_hits))
                witnessed.update(concrete_hits)
                image = repr(ordered_ready_valid_state_image(concrete.state))
                following.setdefault(image, (concrete.state, path + (event,)))
        frontier = tuple(following.values())
    return _Evidence(goals, tuple(cells), frozenset(witnessed))


def _reset_symbolic_state(state):
    return ResetRestorationState(
        reference=tuple(z3.BitVecVal(value, 1) for value in state.reference),
        reference_valid=z3.BoolVal(state.reference_valid),
        reference_readable=z3.BoolVal(state.reference_readable),
        seen_non_reset=z3.BoolVal(state.seen_non_reset),
        reset_streak=z3.IntVal(state.reset_streak),
    )


def _reset_evidence():
    config = ResetRestorationConfig(1, ("q",))
    scenario = "monitor.reset-restoration.clk.scenario"
    violation = "monitor.reset-restoration.clk.violation"
    goals = frozenset((scenario, violation))
    initial = reset_restoration_initial(config, (0,), ConcreteAlgebra())
    frontier = ((initial, ()),)
    cells = []
    witnessed = set()
    correct_frontier = (initial,)
    correct_violation = False
    for _slot in range(3):
        following = {}
        for state, path in frontier:
            for reset, value, readable in itertools.product(
                    (False, True), repeat=3):
                event = ResetRestorationEvent(reset, (int(value),), readable)
                concrete = reset_restoration_step(
                    config, state, event, ConcreteAlgebra())
                symbolic = reset_restoration_step(
                    config, _reset_symbolic_state(state),
                    ResetRestorationEvent(
                        z3.BoolVal(reset), (z3.BitVecVal(int(value), 1),),
                        z3.BoolVal(readable)),
                    Z3Algebra())
                concrete_hits = frozenset(
                    goal for goal, hit in (
                        (scenario, concrete.scenario),
                        (violation, concrete.violation)) if hit)
                exact_hits = frozenset(
                    goal for goal, hit in (
                        (scenario, symbolic.scenario),
                        (violation, symbolic.violation)) if _read_z3(hit))
                assert reset_restoration_state_image(concrete.state) == \
                    reset_restoration_state_image(symbolic.state, _read_z3)
                cells.append(_Cell(concrete_hits, exact_hits))
                witnessed.update(concrete_hits)
                image = repr(reset_restoration_state_image(concrete.state))
                following.setdefault(image, (concrete.state, path + (event,)))
        frontier = tuple(following.values())

        # A correct finite design may change q while running but always presents the
        # captured value 0 on reset.  Exhaustion of that constrained product proves
        # the violation is unreachable through this bound.
        correct_following = {}
        for state in correct_frontier:
            for reset, value in itertools.product((False, True), repeat=2):
                observed = 0 if reset else int(value)
                stepped = reset_restoration_step(
                    config, state,
                    ResetRestorationEvent(reset, (observed,), True),
                    ConcreteAlgebra())
                correct_violation |= bool(stepped.violation)
                correct_following[
                    repr(reset_restoration_state_image(stepped.state))
                ] = stepped.state
        correct_frontier = tuple(correct_following.values())
    assert not correct_violation
    return _Evidence(goals, tuple(cells), frozenset(witnessed))


def _declarative_evidence():
    policy = goal_policy_fixtures()["declarative-standard-extensions/v2"]
    all_goals = set()
    all_cells = []
    witnessed = set()
    for provider in policy.enabled_providers():
        semantics = provider.semantics
        all_goals.update(semantics.goals)
        adapter = provider.guidance
        if adapter is not None:
            for goal in semantics.goals:
                witness = adapter.select(goal)
                assert witness.steps

        if semantics.predicates:
            predicate = semantics.predicates[0]
            terms = {variable.name: z3.Int(variable.name)
                     for variable in predicate.variables}
            encoded = predicate.symbolic(terms, Z3Algebra())
            for values in itertools.product(
                    *(variable.values for variable in predicate.variables)):
                observation = dict(zip(terms, values))
                substitutions = tuple(
                    (terms[name], z3.IntVal(value))
                    for name, value in observation.items())
                concrete = predicate.holds(observation)
                exact = z3.is_true(z3.simplify(z3.substitute(
                    encoded.hit, *substitutions)))
                all_cells.append(_Cell(
                    frozenset((predicate.goal,)) if concrete else frozenset(),
                    frozenset((predicate.goal,)) if exact else frozenset()))
                if concrete:
                    witnessed.add(predicate.goal)
        else:
            monitor = semantics.monitors[0]
            # The tracked sequence fixture uses strings, which the public finite
            # algebra supports.  Use the provider test algebra for that carrier.
            from .test_goal_provider_capabilities import _Z3Algebra
            state, event = z3.Strings(
                "declarative_state declarative_event")
            encoded = monitor.symbolic(state, event, _Z3Algebra())
            hits = dict(encoded.hits)
            for before in monitor.states:
                for observed in monitor.events:
                    concrete = monitor.step(before, observed)
                    substitutions = (
                        (state, z3.StringVal(before)),
                        (event, z3.StringVal(observed)),
                    )
                    exact = frozenset(
                        goal for goal, term in hits.items()
                        if z3.is_true(z3.simplify(z3.substitute(
                            term, *substitutions))))
                    all_cells.append(_Cell(
                        frozenset(concrete.goals), exact))
                    witnessed.update(concrete.goals)
    return _Evidence(
        frozenset(all_goals), tuple(all_cells), frozenset(witnessed))


_ACCEPTANCE_FACTORIES = (
    _behavior_evidence,
    _rtl_structural_evidence,
    _start_done_evidence,
    _ready_valid_evidence,
    _reset_evidence,
    _declarative_evidence,
)


@pytest.mark.parametrize(
    "factory",
    _ACCEPTANCE_FACTORIES,
    ids=lambda factory: factory.__name__.removeprefix("_").removesuffix("_evidence"),
)
def test_enabled_goal_semantics_have_complete_finite_acceptance(factory):
    _assert_acceptance(factory())


def test_every_release_provider_is_owned_by_one_acceptance_product():
    expected = {
        ("behavior-structural/v3", "behavior-artifact-failure/v1"):
            _behavior_evidence,
        ("behavior-structural/v3", "behavior-boolean-expression/v1"):
            _behavior_evidence,
        ("behavior-structural/v3", "behavior-control-choice/v1"):
            _behavior_evidence,
        ("behavior-structural/v3", "behavior-location/v1"):
            _behavior_evidence,
        ("behavior-structural/v3", "behavior-loop-activation/v1"):
            _behavior_evidence,
        ("rtl-structural-ready-valid/v3", "rtl-control-choice/v3"):
            _rtl_structural_evidence,
        ("rtl-structural-ready-valid/v3", "rtl-location/v3"):
            _rtl_structural_evidence,
        ("rtl-structural-ready-valid/v3", "task-monitor/v1"):
            _ready_valid_evidence,
        ("rtl-structural-ready-valid/v3", "reset-scenario/v1"):
            _reset_evidence,
        ("rtl-structural-ready-valid/v3", "reset-restoration/v2"):
            _reset_evidence,
        ("rtl-structural-start-done/v2", "rtl-control-choice/v3"):
            _rtl_structural_evidence,
        ("rtl-structural-start-done/v2", "rtl-location/v3"):
            _rtl_structural_evidence,
        ("rtl-structural-start-done/v2", "task-monitor/v1"):
            _start_done_evidence,
        ("rtl-structural-start-done/v2", "reset-scenario/v1"):
            _reset_evidence,
        ("rtl-structural-start-done/v2", "reset-restoration/v2"):
            _reset_evidence,
        ("declarative-standard-extensions/v2",
         "release-extension-operation-event/v1"):
            _declarative_evidence,
        ("declarative-standard-extensions/v2",
         "release-extension-schedule-interleaving/v1"):
            _declarative_evidence,
        ("declarative-standard-extensions/v2",
         "release-extension-sequence/v1"):
            _declarative_evidence,
        ("declarative-standard-extensions/v2",
         "release-extension-state-activity/v1"):
            _declarative_evidence,
        ("declarative-standard-extensions/v2",
         "release-extension-value-observation/v1"):
            _declarative_evidence,
    }
    actual = {
        (fixture_name, provider.identity.key)
        for fixture_name, policy in goal_policy_fixtures().items()
        for provider in policy.enabled_providers()
    }
    assert actual == set(expected)
    assert set(expected.values()) == set(_ACCEPTANCE_FACTORIES)


def _replace_provider(policy, victim, replacement):
    selections = tuple(
        GoalPolicySelection(
            selection.schema,
            selection.status,
            tuple(replacement if provider is victim else provider
                  for provider in selection.providers),
            reason=selection.reason,
        ) if selection.contributes else GoalPolicySelection(
            selection.schema, selection.status, reason=selection.reason)
        for selection in policy.selections
    )
    providers = tuple(
        provider for selection in selections for provider in selection.providers)
    return GoalProviderRegistry(
        tuple(selection.schema for selection in selections), providers,
    ).freeze(policy.version, selections)


def _release_capability_rows():
    rows = []
    for fixture_name, policy in goal_policy_fixtures().items():
        for provider in policy.enabled_providers():
            for capability in provider.schema.required:
                rows.append((fixture_name, policy, provider, capability))
    return tuple(rows)


_RELEASE_CAPABILITY_ROWS = _release_capability_rows()


@pytest.mark.parametrize(
    "_fixture_name,policy,provider,capability",
    _RELEASE_CAPABILITY_ROWS,
    ids=(
        f"{fixture_name}:{provider.identity.key}:{capability}"
        for fixture_name, _policy, provider, capability
        in _RELEASE_CAPABILITY_ROWS
    ),
)
def test_deleting_each_required_capability_from_each_release_provider_fails(
        _fixture_name, policy, provider, capability):
    incomplete = GoalProvider.builtin(
        provider.identity, provider.schema, provider.catalogue,
        tuple(binding for binding in provider.capabilities
              if binding.kind != capability))
    if capability == DECLARATION:
        with pytest.raises(Refused) as refused:
            _replace_provider(policy, provider, incomplete)
        assert refused.value.refusal.kind == \
            "goal-provider-enabled-capability-missing"
        return

    broken = _replace_provider(policy, provider, incomplete)
    with pytest.raises(Refused) as refused:
        broken.validate(RELEASE_PHASE)
    assert refused.value.refusal.kind == \
        "goal-provider-enabled-capability-missing"
    assert capability in refused.value.refusal.detail["missing"]


def test_every_non_enabled_release_schema_has_a_versioned_disposition():
    for policy in goal_policy_fixtures().values():
        for selection in policy.selections:
            if selection.status == ENABLED:
                continue
            assert selection.status in (INAPPLICABLE, INCOMPLETE)
            assert type(selection.reason) is VersionedReason
            assert selection.reason.policy == policy.version
            if selection.status == INAPPLICABLE:
                assert not selection.providers


def test_rtl_release_profiles_cannot_certify_unimplemented_schema_enumeration():
    for name, policy in goal_policy_fixtures().items():
        if name.startswith("rtl-"):
            assert policy.incomplete_schemas
            with pytest.raises(Refused, match="goal-policy-catalogue-incomplete"):
                policy.validate(RELEASE_PHASE)
