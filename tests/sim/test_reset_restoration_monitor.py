"""Reset restoration is one finite trace goal provider, not a replay heuristic."""

from __future__ import annotations

import itertools

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.core.monitor import Monitor, monitor_for
from behave.sim.core.ready_valid_product import (
    ConcreteAlgebra,
    OrderedReadyValidConfig,
    OrderedReadyValidEvent,
    ordered_ready_valid_initial,
    ordered_ready_valid_step,
)
from behave.sim.core.reset_monitor import (
    ResetRestorationConfig,
    ResetRestorationEvent,
    ResetRestorationProvider,
    ResetRestorationState,
    leading_reset_edges,
    reset_restoration_initial,
    reset_restoration_state_image,
    reset_restoration_step,
    reset_state_domains,
)
from behave.sim.rtl.task_request import ReadyValidRequestCodec
from behave.sim.rtl.task_symbolic import Z3Algebra
from tests.support.device_boundary import evaluate_device


def test_reset_transition_truth_table_includes_inactive_control_steps():
    """Independent scalar specification for every small-state one-step case."""
    def read(term):
        value = z3.simplify(term)
        return z3.is_true(value) if z3.is_bool(value) else value.as_long()

    for hold in (1, 2, 3):
        config = ResetRestorationConfig(hold, ("q",))
        for streak, bits in itertools.product(
                range(hold + 1), itertools.product((False, True), repeat=7)):
            valid, seen, readable, reset, active, now_readable, changed = bits
            count = min(streak + int(active), hold) if reset else 0
            complete = active and reset and count == hold
            capture = complete and not valid
            scenario = complete and valid and seen
            expected = {
                "reference": (int(changed) if capture else 0,),
                "reference_valid": valid or capture,
                "reference_readable": now_readable if capture else readable,
                "seen_non_reset": (False if capture or scenario else
                                   seen or (valid and active and not reset)),
                "reset_streak": count,
            }
            expected_bits = (scenario,
                             scenario and readable and now_readable and changed,
                             scenario and not (readable and now_readable))
            for algebra, boolean, integer, decode in (
                    (ConcreteAlgebra(), bool, int, lambda value: value),
                    (Z3Algebra(), z3.BoolVal, z3.IntVal, read)):
                state = ResetRestorationState(
                    (integer(0),), boolean(valid), boolean(readable),
                    boolean(seen), integer(streak))
                event = ResetRestorationEvent(
                    boolean(reset), (integer(int(changed)),), boolean(now_readable),
                    active=boolean(active))
                result = reset_restoration_step(config, state, event, algebra)
                assert reset_restoration_state_image(result.state, decode) == expected
                assert tuple(decode(value) for value in (
                    result.scenario, result.violation, result.unresolved)) == expected_bits


def test_initial_hold_cannot_bridge_an_inactive_release():
    def event(active, reset):
        return api.EventSlot(ticks=("a",) if active else (),
                             resets=("a",) if reset else ())

    assert leading_reset_edges([
        event(True, True), event(False, False), event(True, True)],
        clock_domain="a") == 1
    assert leading_reset_edges([
        event(False, True), event(False, False), event(True, True),
        event(False, True), event(True, True), event(False, False)],
        clock_domain="a") == 2
    assert leading_reset_edges([event(True, False), event(True, True)],
                               clock_domain="a") == 0
    assert leading_reset_edges([api.EventSlot(reset=True)] * 2 +
                               [api.EventSlot()]) == 2


def _contract():
    u1 = api.FiniteType(1)
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN, [api.Field("a", u1, "a")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT, [api.Field("y", u1, "y")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=1),
        ],
        clocks=[api.ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=2,
            reset_restores=True)],
    )


def _stimulus():
    def slot(*, reset=False):
        return api.EventSlot(
            reset=reset,
            controls={"rst": int(reset), "out_ready": 0})

    return api.Stimulus(
        {"in": ()},
        [slot(reset=True), slot(reset=True), slot(),
         slot(reset=True), slot(reset=True), slot()],
        source="reset-restoration scenario")


def _trace(states):
    stimulus = _stimulus()
    records = [api.SlotRecord(
        accepted={"in": None, "out": None},
        outputs={"in_ready": 0, "out_valid": 0, "y": 0},
        state={"q": state}) for state in states]
    return api.AuditedTrace(stimulus, initial_state={"q": 0}, records=records)


def test_provider_declares_one_scenario_and_one_violation_for_the_claim():
    provider = ResetRestorationProvider(_contract())
    goals = provider.goals()

    assert set(goals) == {
        "monitor.reset-restoration.clk.scenario",
        "monitor.reset-restoration.clk.violation",
    }
    assert goals["monitor.reset-restoration.clk.scenario"].cls == "coverage"
    assert goals["monitor.reset-restoration.clk.violation"].cls == "safety"
    assert {goal.provider for goal in goals.values()} == {"reset-restoration"}
    assert {goal.policy for goal in goals.values()} == {"reset-restoration/3"}
    assert all(goal.evidence == "trace" for goal in goals.values())


@pytest.mark.parametrize('global_events', [False, True])
def test_reset_state_demand_uses_calendar_not_data_or_readability(global_events):
    contract = _contract()
    assert reset_state_domains(contract) == frozenset({'clk'})
    algebra = ConcreteAlgebra()
    for suffix in itertools.product((False, True), repeat=4):
        resets = (True, True, *suffix)
        slots = tuple(api.EventSlot(reset=reset if not global_events else False,
            ticks=('clk',) if global_events else (),
            resets=('clk',) if global_events and reset else ()) for reset in resets)
        hold = leading_reset_edges(slots, clock_domain='clk' if global_events else None)
        required = reset_state_domains(contract, slots, global_events=global_events)
        for readable, changed in itertools.product((False, True), repeat=2):
            config = ResetRestorationConfig(hold, ('q',))
            state = reset_restoration_initial(config, (0,), algebra)
            seen, violations = False, False
            for i, reset in enumerate(resets):
                step = reset_restoration_step(config, state,
                    ResetRestorationEvent(reset=reset, active=True,
                        state_after=(int(changed and i > 2),), state_readable=readable), algebra)
                state = step.state
                seen |= step.scenario
                violations |= step.violation
            assert required == (frozenset({'clk'}) if seen else frozenset())
            if not required:
                assert not violations


def test_reset_state_demand_respects_independent_domain_edges_and_releases():
    contract = api.TaskContract([], [
        api.ClockDomain('left', 'lclk', 'lrst', reset_restores=True),
        api.ClockDomain('right', 'rclk', 'rrst', reset_restores=True)])
    slots = [
        api.EventSlot(ticks=('left','right'), resets=('left','right')),
        api.EventSlot(ticks=('left',), resets=('right',)),
        api.EventSlot(ticks=('right',)),
        # A release followed by reset without an active edge is not restoration.
        api.EventSlot(resets=('left',)),
        api.EventSlot(ticks=('right',), resets=('right',)),
    ]
    assert reset_state_domains(contract, slots, global_events=True) == frozenset({'right'})
    assert reset_state_domains(contract) == frozenset({'left','right'})


def test_concrete_trace_reaches_the_scenario_and_faulty_state_hits_violation():
    contract = _contract()
    stimulus = _stimulus()
    correct = Monitor(contract).check(
        stimulus, _trace([0, 0, 1, 0, 0, 0]),
        reset_state_fields=("q",))
    faulty = Monitor(contract).check(
        stimulus, _trace([0, 0, 1, 1, 1, 1]),
        reset_state_fields=("q",))

    scenario = "monitor.reset-restoration.clk.scenario"
    violation = "monitor.reset-restoration.clk.violation"
    assert [(hit.goal, hit.slot) for hit in correct.hits
            if hit.goal.startswith("monitor.reset-restoration")] == [(scenario, 4)]
    assert not [bad for bad in correct.violations if bad.goal == violation]
    assert {(hit.goal, hit.slot) for hit in faulty.hits} >= {
        (scenario, 4), (violation, 4)}
    assert [(bad.goal, bad.slot) for bad in faulty.violations
            if bad.goal == violation] == [(violation, 4)]


def test_missing_state_never_becomes_a_clean_restoration_result():
    contract = _contract()
    stimulus = _stimulus()
    trace = api.AuditedTrace(
        stimulus,
        records=[api.SlotRecord(
            accepted={"in": None, "out": None},
            outputs={"in_ready": 0, "out_valid": 0, "y": 0})
                 for _ in stimulus.slots])
    report = Monitor(contract).check(stimulus, trace)

    scenario = "monitor.reset-restoration.clk.scenario"
    violation = "monitor.reset-restoration.clk.violation"
    assert [(hit.goal, hit.slot) for hit in report.hits
            if hit.goal == scenario] == [(scenario, 4)]
    assert not [hit for hit in report.hits if hit.goal == violation]
    assert [(item.goal, item.slot) for item in report.unresolved
            if item.goal == violation] == [(violation, 4)]


def test_multiclock_reset_projects_each_complete_owned_state_component():
    contract = api.TaskContract(
        endpoints=(),
        clocks=(
            api.ClockDomain(
                "a", "a_clk", "a_rst", reset_hold_cycles=1,
                reset_restores=True),
            api.ClockDomain(
                "b", "b_clk", "b_rst", reset_hold_cycles=1,
                reset_restores=True),
        ),
    )
    slots = (
        api.EventSlot(
            controls={"a_rst": 1, "b_rst": 1},
            ticks=("a", "b"), resets=("a", "b")),
        api.EventSlot(
            controls={"a_rst": 0, "b_rst": 0}, ticks=("a",)),
        api.EventSlot(
            controls={"a_rst": 0, "b_rst": 0}, ticks=("b",)),
        api.EventSlot(
            controls={"a_rst": 1, "b_rst": 1},
            ticks=("a", "b"), resets=("a", "b")),
    )
    stimulus = api.Stimulus({}, slots, source="two reset domains")

    def trace(final_b):
        states = (
            {"qa": 0, "qb": 0, "unrelated": 99},
            {"qa": 1, "qb": 0, "unrelated": 98},
            {"qa": 1, "qb": 2, "unrelated": 97},
            {"qa": 0, "qb": final_b, "unrelated": 96},
        )
        return api.AuditedTrace(
            stimulus, initial_state={"qa": 7, "qb": 8, "unrelated": 100},
            records=tuple(api.SlotRecord(state=state) for state in states))

    fields = {"a": ("qa",), "b": ("qb",)}
    correct = monitor_for(contract).check(
        stimulus, trace(0), reset_state_fields=fields)
    faulty = monitor_for(contract).check(
        stimulus, trace(3), reset_state_fields=fields)

    assert {
        (hit.goal, hit.slot) for hit in correct.hits
        if ".reset-restoration." in hit.goal
    } == {
        ("monitor.reset-restoration.a.scenario", 3),
        ("monitor.reset-restoration.b.scenario", 3),
    }
    assert [(bad.goal, bad.slot) for bad in faulty.violations] == [
        ("monitor.reset-restoration.b.violation", 3)]


def test_the_same_transition_computes_concrete_and_symbolic_goal_bits():
    config = ResetRestorationConfig(2, ("q",))
    resets = (True, True, False, True, True)
    values = (0, 0, 1, 1, 1)

    concrete = reset_restoration_initial(config, (0,), ConcreteAlgebra())
    concrete_bits = []
    for reset, value in zip(resets, values):
        stepped = reset_restoration_step(
            config, concrete,
            ResetRestorationEvent(reset, (value,), True),
            ConcreteAlgebra())
        concrete = stepped.state
        concrete_bits.append((stepped.scenario, stepped.violation))

    symbolic = reset_restoration_initial(
        config, (z3.BitVecVal(0, 1),), Z3Algebra())
    symbolic_bits = []
    solver = z3.Solver()
    for reset, value in zip(resets, values):
        stepped = reset_restoration_step(
            config, symbolic,
            ResetRestorationEvent(
                z3.BoolVal(reset), (z3.BitVecVal(value, 1),),
                z3.BoolVal(True)),
            Z3Algebra())
        symbolic = stepped.state
        symbolic_bits.append((stepped.scenario, stepped.violation))
    assert solver.check() == z3.sat
    model = solver.model()
    evaluated = [
        (z3.is_true(model.eval(scenario, model_completion=True)),
         z3.is_true(model.eval(violation, model_completion=True)))
        for scenario, violation in symbolic_bits]
    assert evaluated == concrete_bits
    assert concrete_bits[-1] == (True, True)


def test_a_symbolic_mid_run_reset_also_resets_the_protocol_monitor():
    """Later reset is one environment action shared by RTL and monitor products."""

    algebra = Z3Algebra()
    config = OrderedReadyValidConfig(
        capacity=1, payload_fields=1, answers_per_transaction=1)
    state = ordered_ready_valid_initial(
        config, algebra, (z3.BitVecVal(0, 1),))
    reset = z3.Bool("symbolic_mid_run_reset")
    stepped = ordered_ready_valid_step(
        config, state,
        OrderedReadyValidEvent(
            reset=reset, slot=3, input_accepted=z3.BoolVal(True),
            output_valid=z3.BoolVal(False), output_ready=z3.BoolVal(False),
            output_payload=(z3.BitVecVal(0, 1),)),
        algebra)

    resetting = z3.Solver()
    resetting.add(reset)
    assert resetting.check() == z3.sat
    reset_model = resetting.model()
    assert reset_model.eval(stepped.state.outstanding, model_completion=True).as_long() == 0
    assert all(z3.is_false(reset_model.eval(hit, model_completion=True))
               for hit in stepped.hits.values())

    running = z3.Solver()
    running.add(z3.Not(reset))
    assert running.check() == z3.sat
    assert running.model().eval(stepped.state.outstanding, model_completion=True).as_long() == 1


def test_the_exact_request_has_one_leading_prefix_and_allows_later_reset():
    codec = ReadyValidRequestCodec(_contract(), queue_bound=1, reset_hold_cycles=2)
    common = {name: bounds[0] for name, bounds in codec.bounds.items()}
    common[codec.length] = 0
    rows = []
    for reset in (1, 1, 0, 1, 1):
        row = dict(common)
        row.update({codec.offer: 0, codec.ready: 0, codec.reset: reset})
        rows.append(row)

    stimulus = codec.decode(rows)

    assert [slot.reset for slot in stimulus.slots] == [True, True, False, True, True]
    invalid = [dict(row) for row in rows]
    invalid[2][codec.reset] = 1
    with pytest.raises(api.Refused, match="rtl-task-request-reset-prefix"):
        codec.decode(invalid)


def test_reset_does_not_constrain_the_environment_output_ready_level():
    codec = ReadyValidRequestCodec(_contract(), queue_bound=1, reset_hold_cycles=1)
    common = {name: bounds[0] for name, bounds in codec.bounds.items()}
    rows = []
    for reset, ready in ((1, 1), (0, 1)):
        row = dict(common)
        row.update({codec.offer: 0, codec.ready: ready, codec.reset: reset})
        rows.append(row)

    stimulus = codec.decode(rows)

    assert dict(stimulus.slots[0].controls)[codec.ready] == 1
    offered_during_reset = [dict(row) for row in rows]
    offered_during_reset[0][codec.offer] = 1
    with pytest.raises(api.Refused, match="rtl-task-request-reset-not-idle"):
        codec.decode(offered_during_reset)


def test_a_concrete_evaluation_attributes_the_scenario_without_changing_score():
    """The trace goal is reported beside output agreement and survives minimization."""

    from tests.sim.test_api_path import (
        Doubling,
        _ref,
        realization,
    )
    from tests.sim.test_api_path import (
        contract as score_contract,
    )

    base = score_contract()
    claimed = api.TaskContract(
        endpoints=base.endpoints,
        clocks=[api.ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=3,
            reset_restores=True)],
        source=base.source,
        authority=base.authority,
    )
    slots = [
        *[api.EventSlot(
            controls={"rst": 1, "out_ready": 0}, reset=True)
          for _ in range(3)],
        api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
        api.EventSlot(controls={"rst": 0, "out_ready": 1}),
        *[api.EventSlot(
            controls={"rst": 1, "out_ready": 0}, reset=True)
          for _ in range(3)],
    ]
    stimulus = api.Stimulus(
        {"in": ((3,),)}, slots, source="reset-restoration integration")
    task = api.admit(claimed, realization(), reference=_ref())
    evaluation = evaluate_device(
        task, (stimulus,), Doubling(), reference=_ref(),
        catalogue=api.evaluation_manifest(task, api.CatalogueManifest(())))

    scenario = "monitor.reset-restoration.clk.scenario"
    violation = "monitor.reset-restoration.clk.violation"
    assert evaluation.passed
    assert scenario in evaluation.hits
    assert violation not in evaluation.hits
    assert violation not in evaluation.safety_hits
    telemetry = dict(evaluation.telemetry)
    assert [(index, dict(hit)["goal"])
            for index, hit in telemetry["monitor_hits"]] == [(0, scenario)]
    assert [(index, dict(item)["goal"])
            for index, item in telemetry["monitor_unresolved"]] == [
                (0, violation)]

    pool = api.minimize_witnesses(evaluation)
    contribution = pool.contributions[stimulus.fingerprint]
    assert f"goal:{scenario}" in contribution
