"""The common baseline is finite, versioned and independent of solver output."""

from __future__ import annotations

import pytest

from behave.sim import api
from behave.sim.baseline import BaselinePolicy, build_baseline
from behave.sim.generation import stimulus_bytes
from behave.sim.stimulus_set import GenerationSpec, stimulus_admissible

U4 = api.FiniteType(4)


def _recursive_contract():
    record = api.ValueType.record({
        "flag": api.ValueType.boolean(),
        "items": api.ValueType.array(2, api.ValueType.integer(U4)),
        "mode": api.ValueType.enumeration(("idle", "run", "done")),
    })
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("payload", record, "in_payload")],
                api.ready_valid("in", "in_valid", "in_ready"), clock="clk"),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("payload", record, "out_payload")],
                api.ready_valid("out", "out_valid", "out_ready"), clock="clk"),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="recursive baseline fixture")


def test_single_clock_baseline_is_deterministic_recursive_and_canonical():
    contract = _recursive_contract()
    spec = GenerationSpec(
        horizon=9, queue_bound=3, reset_hold_cycles=2, drain=2,
        stall_every=3)
    policy = BaselinePolicy(seed="fixed", case_limit=6, max_queue_depth=3)

    left = build_baseline(contract, spec, policy=policy)
    right = build_baseline(contract, spec, policy=policy)

    assert left.as_dict() == right.as_dict()
    assert left.policy.identity.startswith("generation-baseline/13:")
    assert tuple(stimulus_bytes(item) for item in left.stimuli) == tuple(sorted(
        stimulus_bytes(item) for item in left.stimuli))
    assert any(item.depth("in") == 3 for item in left.stimuli)
    assert all(not stimulus_admissible(
        item, contract, horizon=9, reset_hold_cycles=2, queue_bound=3)
        for item in left.stimuli)
    values = [dict(item.queues)["in"][0][0] for item in left.stimuli]
    assert all(set(value) == {"flag", "items", "mode"} for value in values)
    assert all(type(value["flag"]) is bool for value in values)
    assert all(type(value["items"]) is tuple and len(value["items"]) == 2
               for value in values)
    assert {value["mode"] for value in values} <= {"idle", "run", "done"}
    by_case = {
        case: dict(item.queues)["in"][0][0]
        for item in left.stimuli
        for row in left.provenance_dict()[item.fingerprint]
        for case in row["cases"]
    }
    assert by_case[3] not in (by_case[0], by_case[2])
    assert all(
        row["baseline_policy"] == policy.identity
        for rows in left.provenance_dict().values() for row in rows)

    # Provenance is part of the command identity, not a mutable annotation beside it.
    # Both the retained record and detached public views must therefore be unable to
    # change a baseline after construction.
    before = left.as_dict()
    retained = left.provenance[0][1][0]
    with pytest.raises(TypeError):
        retained["baseline_policy"] = "forged"
    detached = left.provenance_dict()
    next(iter(detached.values()))[0]["cases"].append(99)
    assert left.as_dict() == before


def _multiclock_contract():
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "w_in", api.Direction.IN,
                [api.Field("data", U4, "w_in_data")],
                api.ready_valid("in", "w_in_valid", "w_in_ready"),
                clock="w"),
            api.Endpoint(
                "r_in", api.Direction.IN,
                [api.Field("data", U4, "r_in_data")],
                api.ready_valid("in", "r_in_valid", "r_in_ready"),
                clock="r"),
            api.Endpoint(
                "w_out", api.Direction.OUT,
                [api.Field("data", U4, "w_out_data")],
                api.ready_valid("out", "w_out_valid", "w_out_ready"),
                clock="w"),
            api.Endpoint(
                "r_out", api.Direction.OUT,
                [api.Field("data", U4, "r_out_data")],
                api.ready_valid("out", "r_out_valid", "r_out_ready"),
                clock="r"),
        ],
        clocks=[
            api.ClockDomain("w", "w_clk", "w_rst"),
            api.ClockDomain("r", "r_clk", "r_rst"),
        ],
        source="atomic baseline fixture")


def _multiclock_realization():
    ports = [
        api.Port("w_clk", 1, driven_by="environment"),
        api.Port("w_rst", 1, driven_by="environment"),
        api.Port("r_clk", 1, driven_by="environment"),
        api.Port("r_rst", 1, driven_by="environment"),
    ]
    return api.Realization("rtl", ports, identity="two-clock")


def test_multiclock_baseline_preserves_coincident_global_slots():
    contract = _multiclock_contract()
    spec = GenerationSpec(
        horizon=12, queue_bound=2, reset_hold_cycles=1, drain=2,
        stall_every=0)

    got = build_baseline(
        contract, spec, realization=_multiclock_realization(),
        policy=BaselinePolicy(case_limit=5, max_queue_depth=2))

    schedules = {item.clock_schedule.digest for item in got.stimuli}
    assert len(schedules) == 1
    assert any(len(slot.ticks) == 2 for slot in got.stimuli[0].slots)
    assert all(set(dict(item.queues)) == {"r_in", "w_in"}
               for item in got.stimuli)
    assert all(not stimulus_admissible(
        item, contract, horizon=12, reset_hold_cycles=1, queue_bound=2)
        for item in got.stimuli)


def _plain_multiclock_contract():
    return api.TaskContract(
        endpoints=[api.Endpoint(
            f"{domain}_in", api.Direction.IN,
            [api.Field("data", U4, f"{domain}_data")],
            api.sampled_edge("in"), clock=domain, kind=api.Kind.TRANSACTION)
            for domain in ("r", "w")],
        clocks=[api.ClockDomain("r", "r_clk", "r_rst"),
                api.ClockDomain("w", "w_clk", "w_rst", edge="falling")])


@pytest.mark.parametrize("hold", (2, 3, 4))
def test_plain_multiclock_baseline_supplies_every_released_domain_edge(hold):
    from behave.sim.core.atomic import AtomicDriver

    contract = _plain_multiclock_contract()
    spec = GenerationSpec(
        horizon=24, queue_bound=24, reset_hold_cycles=hold, drain=0,
        stall_every=0)
    policy = BaselinePolicy(case_limit=5, max_queue_depth=24)
    baseline = build_baseline(
        contract, spec, realization=_multiclock_realization(), policy=policy)
    assert baseline.as_dict() == build_baseline(
        contract, spec, realization=_multiclock_realization(),
        policy=policy).as_dict()
    assert any(slot.resets and slot.offers for slot in baseline.stimuli[0].slots)

    for stimulus in baseline.stimuli:
        assert not stimulus_admissible(
            stimulus, contract, horizon=24, reset_hold_cycles=hold, queue_bound=24)
        driver = AtomicDriver(contract, stimulus)
        for index, slot in enumerate(stimulus.slots):
            active = {endpoint.name for endpoint in contract.inputs()
                      if endpoint.clock in slot.ticks
                      and endpoint.clock not in slot.resets}
            assert set(slot.offers) == active
            drive = driver.drive(index)
            driver.observe(index, drive, dict(drive.signals))
            assert {name for name, payload in driver.records[-1].accepted
                    if payload is not None} == active
        trace = driver.trace()
        for endpoint in contract.inputs():
            assert stimulus.depth(endpoint.name) == trace.accepted_count([endpoint.name])
            assert stimulus.depth(endpoint.name) == sum(
                endpoint.clock in slot.ticks and endpoint.clock not in slot.resets
                for slot in stimulus.slots)


def test_plain_multiclock_drain_and_insufficient_queue_are_still_refused():
    contract = _plain_multiclock_contract()
    for drain, bound, kind in (
            (4, 24, "generation-baseline-sampled-idle"),
            (0, 1, "generation-baseline-required-input-depth")):
        spec = GenerationSpec(horizon=24, queue_bound=bound,
                              reset_hold_cycles=3, drain=drain, stall_every=0)
        with pytest.raises(api.Refused) as caught:
            build_baseline(contract, spec, realization=_multiclock_realization(),
                           policy=BaselinePolicy(max_queue_depth=24))
        assert caught.value.refusal.kind == kind


def test_ready_valid_domain_release_does_not_remove_the_declared_drain():
    contract = _multiclock_contract()
    spec = GenerationSpec(horizon=24, queue_bound=2,
                          reset_hold_cycles=3, drain=4, stall_every=0)
    got = build_baseline(contract, spec, realization=_multiclock_realization())
    for stimulus in got.stimuli:
        assert any(slot.resets and slot.offers for slot in stimulus.slots)
        assert all(not slot.offers for slot in stimulus.slots[-4:])
        for slot in stimulus.slots:
            assert all(endpoint.clock not in slot.resets
                       for endpoint in contract.inputs() if endpoint.name in slot.offers)
        assert not stimulus_admissible(
            stimulus, contract, horizon=24, reset_hold_cycles=3, queue_bound=2)


def test_start_done_baseline_never_guesses_when_a_second_request_is_legal():
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN, [api.Field("a", U4, "in_a")],
                api.start_done("in", "start", "done"), clock="clk",
                reset_policy=api.ResetPolicy.CLEARS,
            ),
            api.Endpoint(
                "out", api.Direction.OUT, [api.Field("y", U4, "out_y")],
                api.start_done("out", "start", "done"), clock="clk",
                reset_policy=api.ResetPolicy.CLEARS,
            ),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="fixed start/done baseline fixture",
    )
    spec = GenerationSpec(
        horizon=12, queue_bound=8, reset_hold_cycles=1, drain=2,
        stall_every=0,
    )

    got = build_baseline(
        contract, spec,
        policy=BaselinePolicy(case_limit=6, max_queue_depth=8),
    )

    assert got.stimuli
    assert all(stimulus.depth("in") == 1 for stimulus in got.stimuli)
    assert all(not stimulus_admissible(
        stimulus, contract, horizon=12, reset_hold_cycles=1, queue_bound=8)
        for stimulus in got.stimuli)


def test_baseline_carries_one_finite_run_for_each_external_memory():
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN, [api.Field("a", U4, "in_a")],
                api.ready_valid("in", "in_valid", "in_ready"), clock="clk"),
            api.Endpoint(
                "out", api.Direction.OUT, [api.Field("y", U4, "out_y")],
                api.ready_valid("out", "out_valid", "out_ready"), clock="clk"),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        memories=[api.MemoryInterface(
            "ram", 3, 4, "clk", [api.MemoryPort("rw")])],
        source="memory baseline fixture",
    )
    spec = GenerationSpec(
        horizon=7, queue_bound=2, reset_hold_cycles=1, drain=2,
        stall_every=0)

    got = build_baseline(contract, spec)

    environments = {stimulus.memory_environment.digest
                    for stimulus in got.stimuli}
    assert len(environments) == 1
    environment = got.stimuli[0].memory_environment
    assert environment.faults_against(contract.memories, 7) == ()
    run = environment.run("ram")
    assert run.read_latency == 1
    assert dict(run.ready)[None] == (1,) * 7
    assert all(
        row["memory_environment"] == environment.digest
        for rows in got.provenance_dict().values() for row in rows)
