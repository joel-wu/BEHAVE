"""Selection and schedule identity for the general RTL exact task product."""

from __future__ import annotations

import pytest

from behave.sim import api
from behave.sim.core.goal_provider import EXACT_BOUNDED_HIT
from behave.sim.core.monitor import Monitor
from behave.sim.core.refusal import Refused
from behave.sim.rtl.exact_product import (
    global_schedule_for_stimuli,
    needs_global_schedule_product,
    schedule_matches_stimulus,
    stimulus_for_global_schedule,
)

from .test_api_path import contract as one_pair_contract
from .test_multi_endpoint_evaluation import _contract as endpoint_product_contract
from .test_multi_endpoint_evaluation import _stimulus as endpoint_product_stimulus
from .test_protocol_adapters import _contract as adapter_contract


def test_same_clock_endpoint_product_has_one_lossless_global_schedule():
    contract = endpoint_product_contract()
    stimulus = endpoint_product_stimulus()

    assert needs_global_schedule_product(contract)
    schedule = global_schedule_for_stimuli(
        contract, (stimulus,), horizon=len(stimulus.slots))

    assert len(schedule.timings) == 1
    assert schedule.timings[0].name == "clk"
    assert all(slot.events == ("clk",) for slot in schedule.slots)
    assert schedule.slots[0].resets == ("clk",)
    assert schedule_matches_stimulus(contract, schedule, stimulus)

    decoded = stimulus_for_global_schedule(
        schedule, dict(stimulus.queues), source="round trip")
    assert decoded.queues == stimulus.queues
    assert schedule_matches_stimulus(contract, schedule, decoded)
    assert decoded.clock_schedule.as_dict() == schedule.as_dict()


def test_one_global_product_does_not_merge_different_action_schedules():
    contract = endpoint_product_contract()
    first = endpoint_product_stimulus()
    slots = list(first.slots)
    slots[-1] = api.EventSlot(
        offers=slots[-1].offers,
        controls={
            **dict(slots[-1].controls),
            "right_result_ready": 0,
        })
    second = api.Stimulus(
        queues=dict(first.queues), slots=slots, source="different back pressure")

    with pytest.raises(Refused) as refused:
        global_schedule_for_stimuli(contract, (first, second))
    assert refused.value.refusal.kind == \
        "exact-action-schedule-not-unique"


def test_runtime_replay_request_is_not_an_empty_exact_schedule():
    from behave.sim.core.runtime_schedule import ReplayPolicy

    contract = endpoint_product_contract()
    request = api.Stimulus(
        queues={"left": ((10,),), "right": ((20,),)}, slots=(),
        replay_policy=ReplayPolicy.for_contract(contract), source="runtime request")
    with pytest.raises(Refused) as refused:
        global_schedule_for_stimuli(contract, (request,), horizon=3)
    assert refused.value.refusal.kind == "rtl-exact-action-schedule-unbound"


def test_missing_goal_encoder_is_reported_before_schedule_preparation(monkeypatch):
    from behave.sim.core.refusal import Refusal
    from behave.sim.rtl.analysis import RTLAnalysisRequest

    request = object.__new__(RTLAnalysisRequest)
    cause = Refusal("goal-provider-enabled-capability-missing",
                    "the source event has no bounded encoder")
    object.__setattr__(request, "goal_refusal", cause)

    def unexpected(*args):
        pytest.fail("an unsupported exact request attempted schedule preparation")

    monkeypatch.setattr("behave.sim.rtl.analysis_binding.bind_analysis", unexpected)
    with pytest.raises(Refused) as refused:
        request.bind()
    assert refused.value.refusal is cause


def test_endpoint_local_multi_endpoint_monitor_goals_are_exact():
    contract = endpoint_product_contract()
    provider = Monitor(contract).goal_provider_capabilities()

    assert provider.has(EXACT_BOUNDED_HIT)
    assert set(provider.catalogue) == {
        "monitor.input-completion", "monitor.sink-stability"}


def test_the_one_pair_contract_keeps_the_symbolic_schedule_product():
    assert not needs_global_schedule_product(one_pair_contract())


def test_registered_start_done_selects_the_finite_protocol_schedule_product():
    """Topology does not select the legacy ready/valid-only encoding."""

    contract = adapter_contract("start-done")
    provider = Monitor(contract).goal_provider_capabilities()

    assert needs_global_schedule_product(contract)
    assert provider.has(EXACT_BOUNDED_HIT)
