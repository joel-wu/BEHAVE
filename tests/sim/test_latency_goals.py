"""Latency pairing/timebase oracles; absence is not a timeout violation."""

import pickle
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.goal_provider import CONCRETE_PHASE, EXACT_BOUNDED_HIT, LOCAL_GUIDANCE
from behave.sim.core.latency_monitor import LatencyMonitor
from behave.sim.goal_policy import evaluation_goal_policy

from .test_baseline_public_sequence import REFERENCE, RTL, _contract
from .test_command_handler import _BaselineOnly


def _rule(*, keyed=True, order="start-first", basis=None, reset_domains=()):
    return api.LatencyGoal("response", api.LatencyEvent("in", ("a",) if keyed else ()),
        api.LatencyEvent("out", ("y",) if keyed else ()),
        (api.LatencyBin("zero", 0, 0), api.LatencyBin("one", 1, 1),
         api.LatencyBin("two", 2, 2), api.LatencyBin("later", 3), api.LatencyBin("any", 0)),
        basis or api.LatencyTimebase("clock", "clk"), order, reset_domains)


def _declared(rule):
    base = _contract()
    return api.TaskContract(base.endpoints, base.clocks, latency_goals=(rule,))


def _record(start=None, end=None, *, behavior=False, padding=False):
    accepted = {} if start is None else {"in": (start,)}
    outputs = {}
    if end is not None:
        if behavior:
            outputs["out"] = ((end,),)
        else:
            accepted["out"] = (end,)
    return api.SlotRecord(accepted=accepted, outputs=outputs, monitor={"padding": True} if padding else {})


def _report(rule, records, *, behavior=False, events=None, schedule=None, contract=None):
    stimulus = api.Stimulus(slots=events or [api.EventSlot() for _ in records], clock_schedule=schedule)
    trace = SimpleNamespace(stimulus=stimulus, records=records)
    monitor = LatencyMonitor(contract or _declared(rule), artifact_kind="behavior" if behavior else "rtl")
    return monitor.check(stimulus, trace)


def _hits(report):
    return {(hit.slot, hit.goal.rsplit(":", 1)[1]) for hit in report.hits}


def test_public_wire_identity_eligibility_and_bound_capability():
    c = _declared(_rule())
    assert api.TaskContract.from_dict(c.as_dict()).as_dict() == c.as_dict()
    assert pickle.loads(pickle.dumps(c)).digest == c.digest
    old = _contract().as_dict()
    old.pop("latency_goals")
    old.pop("operation_rules")
    old.pop("source_read_rules")
    old["version"] = "task-contract/12"
    assert api.TaskContract.from_dict(old).digest == _contract().digest
    policy = evaluation_goal_policy(c, CatalogueManifest(()), artifact_kind="rtl")
    policy.validate(CONCRETE_PHASE)
    provider = next(p for p in policy.enabled_providers() if p.schema.name == "latency")
    assert len(provider.catalogue) == 5
    assert all(goal.cls == api.COVERAGE for goal in provider.catalogue.values())
    assert provider.has(LOCAL_GUIDANCE) and provider.has(EXACT_BOUNDED_HIT)
    behavior = evaluation_goal_policy(c, CatalogueManifest(()), artifact_kind="behavior")
    assert not any(goal.family == "latency" for goal in behavior.manifest.values())
    selection = next(row for row in behavior.selections if row.schema.name == "latency")
    assert selection.reason.code == "untimed-behavior-no-physical-latency"


@pytest.mark.parametrize("elapsed", range(5))
def test_inclusive_bins_and_zero_latency(elapsed):
    records = ([_record(7, 7)] if elapsed == 0 else
               [_record(7)] + [_record() for _ in range(elapsed - 1)] + [_record(end=7)])
    report = _report(_rule(), records)
    bin_name = ("zero", "one", "two")[elapsed] if elapsed < 3 else "later"
    assert _hits(report) == {(elapsed, bin_name), (elapsed, "any")}
    assert all(count == 1 for count in report.opportunities.values())
    assert not report.violations and not report.unresolved


@pytest.mark.parametrize("keyed", (False, True))
def test_fifo_and_identifier_pair_different_actual_starts(keyed):
    report = _report(_rule(keyed=keyed), [_record(7), _record(9), _record(end=9), _record(end=7)])
    expected = {(2, "one"), (3, "later")} if keyed else {(2, "two"), (3, "two")}
    assert _hits(report) == expected | {(2, "any"), (3, "any")}


def test_tag_reuse_and_concurrent_occurrences_keep_fifo_within_each_key():
    report = _report(_rule(order="end-first"), [
        _record(7), _record(7, 7), _record(7), _record(end=7), _record(end=7)])
    assert _hits(report) == {(1, "one"), (1, "any"), (3, "two"), (3, "any"), (4, "two"), (4, "any")}


@pytest.mark.parametrize("order,expected", (("start-first", {(0, "zero"), (0, "any")}),
                                           ("end-first", {(1, "one"), (1, "any")})))
def test_same_entry_order_is_explicit_not_guessed(order, expected):
    report = _report(_rule(order=order), [_record(7, 7), _record(end=7)])
    assert _hits(report) == expected


def test_deep_reorder_has_no_fixed_monitor_queue_limit():
    count = 200
    records = [_record(i) for i in range(count)] + [_record(end=i) for i in reversed(range(count))]
    report = _report(_rule(), records)
    assert report.opportunities["latency:response:any"] == count
    assert not report.unresolved and not report.violations
    assert (count, "one") in _hits(report)
    assert (2 * count - 1, "later") in _hits(report)


def test_identity_comparison_uses_logical_values_without_narrowing_wider_responses():
    base = _contract()
    output = api.Endpoint("out", "out", [api.Field("y", api.FiniteType(16), "y")],
        api.ready_valid("out", "out_valid", "out_ready"), clock="clk", answers_per_transaction=1)
    rule = _rule()
    contract = api.TaskContract([base.inputs()[0], output], base.clocks, latency_goals=(rule,))
    # 263 is not 7: narrowing the wider identifier would invent an early match.
    report = _report(rule, [_record(7), _record(end=263), _record(end=7)], contract=contract)
    assert _hits(report) == {(2, "two"), (2, "any")}


def test_unmatched_events_and_padding_are_not_hits_or_safety_failures():
    report = _report(_rule(), [_record(end=7), _record(9), _record(end=7), _record(end=9, padding=True)])
    assert not report.hits and not report.violations and not report.opportunities


@pytest.mark.parametrize("cancel", (False, True))
def test_reset_cancellation_is_declared(cancel):
    rule = _rule(reset_domains=("clk",) if cancel else ())
    report = _report(rule, [_record(7), _record(), _record(end=7)],
                     events=[api.EventSlot(), api.EventSlot(reset=True), api.EventSlot()])
    assert _hits(report) == (set() if cancel else {(2, "two"), (2, "any")})


def test_unreadable_key_is_not_an_unknown_response_or_an_invented_latency():
    records = [api.SlotRecord(accepted={"in": (None,)}), _record(end=7), _record(9), _record(end=9)]
    report = _report(_rule(), records)
    assert not report.hits and report.unresolved and not report.violations


@pytest.mark.parametrize("behavior", (False, True))
def test_transaction_count_is_not_idle_slots_or_a_fake_behavior_clock(behavior):
    rule = _rule(basis=api.LatencyTimebase("transaction", "in"))
    records = [_record(7, behavior=behavior), _record(), _record(9, behavior=behavior),
               _record(), _record(), _record(end=7, behavior=behavior), _record(end=9, behavior=behavior)]
    report = _report(rule, records, behavior=behavior)
    assert _hits(report) == {(5, "one"), (5, "any"), (6, "zero"), (6, "any")}


@pytest.mark.parametrize("basis,expected", ((api.LatencyTimebase("clock", "fast"), "one"),
                                           (api.LatencyTimebase("global-time"), "later")))
def test_real_clock_edges_and_global_timestamps_ignore_control_only_entries(basis, expected):
    base = _contract()
    endpoints = []
    for endpoint in base.endpoints:
        image = endpoint.as_dict()
        image["clock"] = "fast" if endpoint.direction is api.Direction.IN else "slow"
        endpoints.append(api.Endpoint.from_dict(image))
    clocks = (api.ClockDomain("fast", "fast_clk", "fast_rst"), api.ClockDomain("slow", "slow_clk", "slow_rst"))
    events = [api.EventSlot(ticks=("fast", "slow")), api.EventSlot(),
              api.EventSlot(ticks=("fast",)), api.EventSlot(ticks=("slow",))]
    schedule = GlobalSchedule((ClockTiming("fast", 2, clock="fast_clk", reset="fast_rst"),
                               ClockTiming("slow", 3, clock="slow_clk", reset="slow_rst")),
                              [GlobalEventSlot(i, event=event) for i, event in enumerate(events)])
    rule = _rule(basis=basis)
    contract = api.TaskContract(endpoints, clocks, latency_goals=(rule,))
    report = _report(rule, [_record(7), _record(), _record(), _record(end=7)],
                     events=events, schedule=schedule, contract=contract)
    assert _hits(report) == {(3, expected), (3, "any")}


def test_missing_global_time_is_unresolved_not_slot_index_fallback():
    report = _report(_rule(basis=api.LatencyTimebase("global-time")), [_record(7), _record(end=7)])
    assert report.unresolved and not report.hits


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
def test_public_baseline_records_real_latency_without_search_or_analysis(tmp_path, kind):
    rule = _rule(keyed=False, basis=api.LatencyTimebase("transaction", "in") if kind == "behavior" else None)
    contract = _declared(rule)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(REFERENCE)
        task = api.admit(contract, api.realization_of_behavior_source(REFERENCE, contract, file="candidate.py"),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
                                      bounded_analysis=False)
    else:
        path = tmp_path / "M.sv"
        path.write_text(RTL.replace("VALUE", "a"))
        task = api.admit(contract, api.realization_of_rtl_source(path.read_text(), top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.evaluation.complete and result.decision.authoritative and result.decision.passed
    assert "latency:response:zero" in result.evaluation.hits
    assert "latency:response:later" not in result.evaluation.hits
    assert result.evaluation.catalogue["latency:response:later"].cls == api.COVERAGE
