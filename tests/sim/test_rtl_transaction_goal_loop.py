"""Real RTL Select/Probe/replay controls for history-dependent goal families.

The short traces isolate sampling semantics. They are not a replacement for
the default 50-by-100 baseline qualification or whole-domain analysis.
"""

import json
from time import monotonic

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.latency_monitor import LatencyMonitor
from behave.sim.core.observation_monitor import TransactionMonitor
from behave.sim.core.transaction_monitor import DeclaredTransactionMonitor
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.rtl.artifact import RTLArtifact

from .test_baseline_public_sequence import _contract
from .test_finite_transaction_queries import _sequence
from .test_transaction_goal_products import RTL

pytestmark = [pytest.mark.cosim, pytest.mark.usefixtures("search_lifetime")]


def _seed(first, *, reset=False):
    return api.Stimulus(queues={"in": ((first,), (0,))}, slots=[
        api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 0}),
        api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 0}),
        api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
        api.EventSlot(reset=reset, controls={"rst": int(reset), "out_ready": 1}),
        api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
        api.EventSlot(controls={"rst": 0, "out_ready": 1}),
    ])


def _provider(tmp_path, contract, seed):
    path = tmp_path / "M.sv"
    source = RTL if len(contract.clocks) == 1 else RTL.replace("input wire clk,rst,", "input wire clk,rst,aux_clk,aux_rst,")
    path.write_text(source)
    artifact = RTLArtifact(path, "M", tmp_path / "native")
    spec = api.GenerationSpec(len(seed.slots), 2, 2, 0)
    return RTLGenerationProvider.from_artifact(artifact, contract, spec,
        artifact.frontend_catalogue(), workdir=tmp_path / "search", clock_schedule=seed.clock_schedule)


def _second_question(provider, seed, goal):
    selected = provider.select(seed, targets=(goal,))
    assert isinstance(selected, api.ProbeSelection), selected.as_dict()
    assert goal not in selected.replay.hits
    request, = [request for request in selected.requests
                if {field.transaction for field in request.mutable_fields} == {1}]
    assert request.targets == (goal,)
    return selected, request


def _confirm(provider, seed, selected, answer, goal, expected_value, monitor, record_property, started):
    assert answer.outcome.value == "sat", answer.as_dict()
    assert answer.goal == goal and answer.scope.value == "rtl-local-hit"
    assert dict(answer.candidate.queues)["in"] == (dict(seed.queues)["in"][0], (expected_value,))
    assert answer.candidate.slots == seed.slots
    if seed.clock_schedule is None:
        assert answer.candidate.clock_schedule is None
    else:
        assert answer.candidate.clock_schedule.as_dict() == seed.clock_schedule.as_dict()
    # The query is not a native hit; only the subsequent real replay earns it.
    assert goal not in selected.replay.hits
    confirmed = provider.select(answer.candidate, targets=())
    assert isinstance(confirmed, api.ProbeSelection), confirmed.as_dict()
    assert goal in confirmed.replay.hits
    assert confirmed.catalogue_digest == selected.catalogue_digest
    trace = provider._isolated._context.selection_trace(answer.candidate)
    # Independent, concrete transfer oracle: no invented sample during silence/reset.
    outputs = [(slot, dict(row.accepted)["out"][0]) for slot, row in enumerate(trace.records)
               if dict(row.accepted).get("out") is not None]
    assert outputs == [(2, dict(seed.queues)["in"][0][0] ^ 85), (4, expected_value ^ 85)]
    report = monitor.check(answer.candidate, trace)
    slots = [hit.slot for hit in (*report.hits, *report.violations) if hit.goal == goal]
    assert slots == [4]
    assert not any(item.goal == goal for item in report.unresolved)
    record_property("native_goal_loop", json.dumps({
        "goal": goal, "declaration": provider.catalogue_manifest()[goal].as_dict(),
        "baseline_hit": False, "query": answer.as_dict(),
        "confirmed": confirmed.as_dict(), "native_outputs": outputs, "native_hit_slots": slots,
        "elapsed_s": monotonic() - started,
    }))
    return trace


@pytest.mark.parametrize("family", ("value", "activity", "sequence", "obligation"))
@pytest.mark.parametrize("reset", (False, True))
def test_rtl_search_confirms_transaction_families_and_keeps_native_history(
        tmp_path, record_property, family, reset):
    started = monotonic()
    base = _contract()
    condition = api.GoalCondition("eq", api.GoalField("y"), 9)
    if family == "activity":
        condition = api.GoalCondition("and", condition,
            api.GoalCondition("eq", api.GoalField("y", previous=True), 7))
    rules = (api.TransactionGoal("requested", "out", condition),) if family in ("value", "activity") else ()
    monitors = (_sequence(safety=family == "obligation"),) if family in ("sequence", "obligation") else ()
    # Keep an impossible value declared: a local result cannot delete its goal.
    outside = api.TransactionGoal("outside", "out", api.GoalCondition("eq", api.GoalField("y"), 256))
    contract = api.TaskContract(base.endpoints, base.clocks,
        observation_goals=(*rules, outside), transaction_monitors=monitors)
    goal = "observation:requested" if rules else "monitor:chain:hit"
    seed = _seed(7 ^ 85, reset=reset)
    provider = _provider(tmp_path, contract, seed)
    monitor = (TransactionMonitor(contract, behavior=False) if rules else
               DeclaredTransactionMonitor(contract, behavior=False))
    catalogue = provider.catalogue_manifest().as_dict()
    selected, request = _second_question(provider, seed, goal)
    with provider.search_scope(probe_budget=3):
        answer = provider.probe(seed, request)
        _confirm(provider, seed, selected, answer, goal, 9 ^ 85, monitor, record_property, started)
        if family != "value":
            # Change only the fixed prefix. The second input cannot manufacture
            # the preceding 7/monitor state, even though the global goal is reachable.
            different = _seed(0, reset=reset)
            other, question = _second_question(provider, different, goal)
            negative = provider.probe(different, question)
            assert negative.outcome.value == "local-unsat", negative.as_dict()
            assert goal not in other.replay.hits
        _absent, question = _second_question(provider, seed, "observation:outside")
        negative = provider.probe(seed, question)
        assert negative.outcome.value == "local-unsat", negative.as_dict()
    assert provider.catalogue_manifest().as_dict() == catalogue
    assert provider.catalogue_manifest()[goal].cls == (api.SAFETY if family == "obligation" else api.COVERAGE)


@pytest.mark.parametrize("basis,elapsed", (("clock", 2), ("transaction", 1), ("global-time", 4)))
@pytest.mark.parametrize("cancel", (False, True))
def test_rtl_latency_search_uses_pending_keys_and_explicit_reset_cancellation(
        tmp_path, record_property, basis, elapsed, cancel):
    started = monotonic()
    base = _contract()
    rule = api.LatencyGoal("paired", api.LatencyEvent("in", ("a",)),
        api.LatencyEvent("out", ("y",)), (api.LatencyBin("wanted", elapsed, elapsed),),
        api.LatencyTimebase(basis, {"clock": "clk", "transaction": "in"}.get(basis, "")),
        reset_domains=("clk",) if cancel else ())
    contract = api.TaskContract(base.endpoints, base.clocks, latency_goals=(rule,))
    seed = _seed(7, reset=True)
    if basis == "global-time":
        # Same observations, different timebase: two clock slots are four time
        # units. The atomic path must retain timestamps, not infer slot indices.
        contract = api.TaskContract(base.endpoints,
            (*base.clocks, api.ClockDomain("aux", "aux_clk", "aux_rst")), latency_goals=(rule,))
        schedule = GlobalSchedule((ClockTiming("clk", 2, clock="clk", reset="rst"),
                                   ClockTiming("aux", 2, clock="aux_clk", reset="aux_rst")), [
            GlobalEventSlot(2 * i, event=api.EventSlot(ticks=("clk", "aux"),
                resets=("clk", "aux") if row.reset else (), offers=row.offers,
                controls={**dict(row.controls), "aux_rst": int(row.reset)})) for i, row in enumerate(seed.slots)])
        seed = api.Stimulus(queues=dict(seed.queues), slots=[entry.event for entry in schedule.slots],
                            clock_schedule=schedule)
    provider = _provider(tmp_path, contract, seed)
    goal = "latency:paired:wanted"
    catalogue = provider.catalogue_manifest().as_dict()
    selected, request = _second_question(provider, seed, goal)
    with provider.search_scope(probe_budget=1):
        answer = provider.probe(seed, request)
        if cancel:
            assert answer.outcome.value == "local-unsat", answer.as_dict()
            record_property("local_only", json.dumps({"goal": goal, "cancel": True,
                "outcome": answer.outcome.value, "elapsed_s": monotonic() - started}))
        else:
            _confirm(provider, seed, selected, answer, goal, 7 ^ 85,
                     LatencyMonitor(contract, artifact_kind="rtl"), record_property, started)
    assert provider.catalogue_manifest().as_dict() == catalogue
