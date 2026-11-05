"""Independent native/query controls for declared physical schedule-entry sets."""

import pickle
from itertools import combinations, product
from time import monotonic
from types import SimpleNamespace

import pytest
import z3

from behave.sim import api
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.goal_provider import CONCRETE_PHASE, EXACT_ANALYSIS_PHASE, RTL_LOCAL_GUIDANCE_POLICY
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.observation_symbolic import condition_terms
from behave.sim.core.ready_valid_product import ConcreteAlgebra
from behave.sim.core.transaction_monitor import DeclaredTransactionMonitor
from behave.sim.goal_policy import evaluation_goal_policy, source_guidance_catalogue
from behave.sim.rtl.task_symbolic import Z3Algebra

from .test_baseline_public_sequence import _contract
from .test_rtl_transaction_goal_loop import _confirm, _provider, _second_question, _seed


def _rule(*, second_clock=False, safety=False, transactions=True):
    atoms = (api.ScheduleEvent("clk", "clock-edge", "clk"),
             api.ScheduleEvent("rst", "reset-active", "clk"))
    if second_clock:
        atoms += (api.ScheduleEvent("aux", "clock-edge", "aux"),)
    classifiers = ((api.TransactionEvent("rare", "out", api.GoalCondition("eq", api.GoalField("y"), 9)),
                    api.TransactionEvent("other", "out")) if transactions else ())
    labels = sorted({event.event for event in (*atoms, *classifiers)})
    alphabet = tuple(event for size in range(len(labels) + 1) for event in combinations(labels, size))
    goals = ("after-empty", "coincident", "reset-only")
    rows = []
    for state, event in product((0, 1), alphabet):
        hits = []
        if state == 1 and "clk" in event and "rare" in event:
            hits.append("after-empty")
        if "clk" in event and "aux" in event:
            hits.append("coincident")
        if "rst" in event and "clk" not in event:
            hits.append("reset-only")
        rows.append(api.FiniteMonitorTransition(state, event, int(not event), hits))
    return api.TransactionMonitorSpec("schedule", api.FiniteMonitor((0, 1), alphabet, 0, goals, rows),
        classifiers, order=("out",) if transactions else (), schedule_events=atoms,
        sampling="schedule-entry", violations=("after-empty",) if safety else ())


def _task(rule):
    base = _contract()
    clocks = base.clocks + ((api.ClockDomain("aux", "aux_clk", "aux_rst"),)
                           if any(event.domain == "aux" for event in rule.schedule_events) else ())
    return api.TaskContract(base.endpoints, clocks, transaction_monitors=(rule,))


def _clock_seed(*, empty=True):
    seed = _seed(7)
    times = (0, 2, 4, 5, 6, 8) if empty else (0, 2, 4, 6, 8, 10)
    schedule = GlobalSchedule((ClockTiming("clk", 2, clock="clk", reset="rst"),), [
        GlobalEventSlot(time, event=api.EventSlot(
            ticks=() if i == 3 and empty else ("clk",),
            resets=("clk",) if slot.reset else (), offers=slot.offers, controls=dict(slot.controls)))
        for i, (time, slot) in enumerate(zip(times, seed.slots, strict=True))])
    return api.Stimulus(seed.queues, [slot.event for slot in schedule.slots], clock_schedule=schedule)


def test_schedule_wire_roundtrip_and_behavior_has_no_physical_clock_goals():
    contract = _task(_rule())
    assert api.TaskContract.from_dict(contract.as_dict()).digest == contract.digest
    assert pickle.loads(pickle.dumps(contract)).as_dict() == contract.as_dict()
    old = contract.as_dict()
    old.pop("source_read_rules")
    old["version"] = "task-contract/14"
    with pytest.raises(TypeError, match="task-contract/15"):
        api.TaskContract.from_dict(old)
    old = _contract().as_dict()
    old.pop("source_read_rules")
    old["version"] = "task-contract/14"
    assert api.TaskContract.from_dict(old).digest == _contract().digest
    for kind in ("behavior", "rtl"):
        policy = evaluation_goal_policy(contract, CatalogueManifest(), artifact_kind=kind)
        policy.validate(CONCRETE_PHASE)
        policy.validate(EXACT_ANALYSIS_PHASE)
        names = set(policy.manifest)
        source = set(source_guidance_catalogue(contract, CatalogueManifest(), artifact_kind=kind))
        product_ = TransactionProduct(contract, artifact_kind=kind)
        for goal in contract.transaction_monitors[0].declarations():
            assert (goal.id in names) is (kind == "rtl")
            assert (goal.id in source) is (kind == "rtl")
            assert (goal in product_.goals) is (kind == "rtl")
    assert DeclaredTransactionMonitor(contract, behavior=True).check(
        None, SimpleNamespace(records=[api.SlotRecord()])).unresolved == ()


def test_schedule_declares_complete_alphabet_and_declared_clock_domains():
    rule = _rule()
    image = rule.as_dict()
    image["schedule_events"][0]["domain"] = "missing"
    with pytest.raises(ValueError, match="undeclared clock"):
        _task(api.TransactionMonitorSpec.from_dict(image))
    image = rule.as_dict()
    image["sampling"] = "entry-set"
    with pytest.raises(ValueError, match="require schedule-entry"):
        api.TransactionMonitorSpec.from_dict(image)
    with pytest.raises(ValueError, match="alphabet"):
        api.TransactionMonitorSpec("short", api.FiniteMonitor((0,), (("clk",),), 0, ("hit",),
            (api.FiniteMonitorTransition(0, ("clk",), 0, ("hit",)),)), (), order=(),
            schedule_events=(api.ScheduleEvent("clk", "clock-edge", "clk"),), sampling="schedule-entry")


@pytest.mark.parametrize("safety", (False, True))
def test_native_and_symbolic_entry_oracle_exhaustive(safety):
    contract = _task(_rule(second_clock=True, safety=safety))
    native = DeclaredTransactionMonitor(contract, behavior=False)
    binding = TransactionProduct(contract)
    # Independent expected events. Presence is from this schedule and actual
    # accepted outputs, not a union of trace-wide goal hits or pin snapshots.
    entries = (((), (), None), (("clk",), (), None), (("aux",), (), None),
               (("clk", "aux"), (), None), ((), ("clk",), None),
               (("clk",), ("clk",), None), (("clk",), (), 9), (("clk",), (), 0))
    for pair in product(entries, repeat=2):
        states = [binding.initial(algebra) for algebra in (ConcreteAlgebra, Z3Algebra())]
        previous_empty = False
        expected, records, slots = set(), [], []
        for index, (ticks, resets, output) in enumerate(pair):
            labels = ({"clk"} if "clk" in ticks else set()) | ({"aux"} if "aux" in ticks else set())
            labels |= {"rst"} if "clk" in resets else set()
            labels |= {"rare" if output == 9 else "other"} if output is not None else set()
            goals = set()
            if previous_empty and {"clk", "rare"} <= labels:
                goals.add("monitor:schedule:after-empty")
            if {"clk", "aux"} <= labels:
                goals.add("monitor:schedule:coincident")
            if "rst" in labels and "clk" not in labels:
                goals.add("monitor:schedule:reset-only")
            expected.update((index, goal) for goal in goals)
            previous_empty = not labels
            samples = {"out": ((False, (9,)),) + (() if output is None else ((True, (output,)),))}
            for side, algebra in enumerate((ConcreteAlgebra, Z3Algebra())):
                states[side], hit = binding.advance(states[side], samples, algebra,
                    condition=condition_terms if side else None, ticks=ticks,
                    resets={name: algebra.bool(True) for name in resets})
                actual = {goal for goal, value in hit.items()
                          if (z3.is_true(z3.simplify(value)) if side else value)}
                assert actual == goals, (pair, index, side)
            records.append(api.SlotRecord(accepted={} if output is None else {"out": (output,)}))
            slots.append(api.EventSlot(ticks=ticks, resets=resets))
        stimulus = api.Stimulus({}, slots)  # Multi-domain records already have explicit event semantics.
        report = native.check(stimulus, SimpleNamespace(records=records))
        assert {(hit.slot, hit.goal) for hit in (*report.hits, *report.violations)} == expected
        assert not report.unresolved
        assert len(native.goals()) == 3


def test_schedule_unknown_context_or_member_is_not_empty_and_padding_never_advances():
    contract = _task(_rule())
    native = DeclaredTransactionMonitor(contract, behavior=False)
    report = native.check(None, SimpleNamespace(records=[api.SlotRecord()]))
    assert not report.hits and len(report.unresolved) == 3
    run = native.new_run()
    run.observe(api.SlotRecord(accepted={"out": (None,)}), 0, ticks=(), resets=())
    run.observe(api.SlotRecord(), 1, ticks=(), resets=())
    run.observe(api.SlotRecord(accepted={"out": (9,)}), 2, ticks=("clk",), resets=())
    assert not run.report().hits and run.report().unresolved
    seed = _clock_seed()
    records = [api.SlotRecord() for _ in seed.slots]
    records[3] = api.SlotRecord(monitor={"padding": True})
    records[4] = api.SlotRecord(accepted={"out": (9,)})
    assert not native.check(seed, SimpleNamespace(records=records)).hits
    binding = TransactionProduct(contract)
    with pytest.raises(ValueError, match="actual clock/reset"):
        binding.monitors.advance(binding.monitors.initial(ConcreteAlgebra), {}, ConcreteAlgebra)


def test_clock_only_monitor_advances_without_transactions_and_preserves_coincidence():
    contract = _task(_rule(second_clock=True, transactions=False))
    monitor = DeclaredTransactionMonitor(contract, behavior=False)
    stimulus = api.Stimulus({}, [api.EventSlot(ticks=("clk", "aux")),
        api.EventSlot(ticks=("clk",)), api.EventSlot(ticks=("aux",)),
        api.EventSlot(resets=("clk",))])
    report = monitor.check(stimulus, SimpleNamespace(records=[api.SlotRecord() for _ in stimulus.slots]))
    assert {(hit.goal, hit.slot) for hit in report.hits} == {
        ("monitor:schedule:coincident", 0), ("monitor:schedule:reset-only", 3)}
    assert all(count == 4 for count in report.opportunities.values())


def test_symbolic_reset_level_is_not_coerced_to_a_python_boolean():
    binding = TransactionProduct(_task(_rule(transactions=False)))
    reset = z3.Bool("reset_level")
    algebra = Z3Algebra()
    _, hits = binding.advance(binding.initial(algebra), {}, algebra,
                             ticks=(), resets={"clk": reset})
    hit = hits["monitor:schedule:reset-only"]
    for asserted in (False, True):
        assert z3.is_true(z3.simplify(z3.substitute(hit, (reset, z3.BoolVal(asserted))))) is asserted


@pytest.mark.cosim
@pytest.mark.usefixtures("search_lifetime")
@pytest.mark.parametrize("empty", (False, True))
@pytest.mark.parametrize("safety", (False, True))
def test_native_search_uses_actual_empty_entry_not_transaction_silence(tmp_path, record_property, empty, safety):
    started = monotonic()
    contract = _task(_rule(safety=safety))
    seed = _clock_seed(empty=empty)
    provider = _provider(tmp_path, contract, seed)
    goal = "monitor:schedule:after-empty"
    catalogue = provider.catalogue_manifest().as_dict()
    selected, question = _second_question(provider, seed, goal)
    with provider.search_scope(probe_budget=1):
        answer = provider.probe(seed, question)
        if empty:
            _confirm(provider, seed, selected, answer, goal, 9 ^ 85,
                     DeclaredTransactionMonitor(contract, behavior=False), record_property, started)
        else:
            assert answer.outcome.value == "local-unsat", answer.as_dict()
            assert goal not in selected.replay.hits
    assert provider.catalogue_manifest().as_dict() == catalogue
    assert provider.catalogue_manifest()[goal].cls == (api.SAFETY if safety else api.COVERAGE)


@pytest.mark.cosim
def test_real_multiclock_replay_preserves_clock_coincidence_and_off_edge_reset(tmp_path):
    contract = _task(_rule(second_clock=True, transactions=False))
    timings = (ClockTiming("clk", 2, clock="clk", reset="rst"),
               ClockTiming("aux", 3, clock="aux_clk", reset="aux_rst"))
    schedule = GlobalSchedule(timings, [
        GlobalEventSlot(time, event=api.EventSlot(ticks=ticks, resets=resets,
            offers=("in",) if time == 4 else (),
            controls={"rst": int("clk" in resets), "aux_rst": int("aux" in resets), "out_ready": 1}))
        for time, ticks, resets in (
            (0, ("clk", "aux"), ("clk", "aux")), (2, ("clk",), ("clk", "aux")),
            (3, ("aux",), ("aux",)), (4, ("clk",), ()), (5, (), ("clk",)),
            (6, ("clk", "aux"), ()), (8, ("clk",), ()))])
    seed = api.Stimulus({"in": ((7,),)}, [slot.event for slot in schedule.slots], clock_schedule=schedule)
    provider = _provider(tmp_path, contract, seed)
    selected = provider.select(seed, targets=())
    assert isinstance(selected, api.ProbeSelection), selected.as_dict()
    trace = provider._isolated._context.selection_trace(seed)
    report = DeclaredTransactionMonitor(contract, behavior=False).check(seed, trace)
    assert {(hit.goal, hit.slot) for hit in report.hits} == {
        ("monitor:schedule:coincident", 0), ("monitor:schedule:coincident", 5),
        ("monitor:schedule:reset-only", 4)}
    assert {hit.goal for hit in report.hits} <= set(selected.replay.hits)
    assert not report.unresolved


@pytest.mark.isolation
@pytest.mark.eda
@pytest.mark.parametrize("kind", ("rtl", "behavior"))
def test_public_clock_entry_goal_is_baseline_missed_then_native_confirmed(tmp_path, kind, record_property):
    import json

    from .test_baseline_public_sequence import REFERENCE, RTL

    labels = ("clock", "other", "rare")
    alphabet = tuple(event for size in range(4) for event in combinations(labels, size))
    spec = api.TransactionMonitorSpec("timed", api.FiniteMonitor((0,), alphabet, 0, ("target",), [
        api.FiniteMonitorTransition(0, event, 0, ("target",) if event == ("clock", "rare") else ())
        for event in alphabet]), (
            api.TransactionEvent("rare", "out", api.GoalCondition("eq", api.GoalField("y"), 173)),
            api.TransactionEvent("other", "out")), order=("out",), sampling="schedule-entry",
        schedule_events=(api.ScheduleEvent("clock", "clock-edge", "clk"),))
    contract = _task(spec)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    if kind == "rtl":
        path = tmp_path / "M.sv"
        path.write_text(RTL.replace("VALUE", "a"))
        task = api.admit(contract, api.realization_of_rtl_source(path.read_text(), top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
        providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                          if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
        assert len(providers) == 1
    else:
        path = tmp_path / "behavior.py"
        path.write_text(REFERENCE)
        task = api.admit(contract, api.realization_of_behavior_source(REFERENCE, contract, file=path.name),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name), bounded_analysis=False)
        providers = ()
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120 if kind == "rtl" else 0, debug=True),
        run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    if kind == "rtl":
        record_property("search", json.dumps(result.generated.searches[0].as_dict()))
    assert result.evaluation.complete and result.decision.authoritative and result.decision.passed
    goal = "monitor:timed:target"
    if kind == "behavior":
        assert goal not in result.evaluation.catalogue and goal not in result.evaluation.hits
        return
    assert goal in result.generated.searches[0].telemetry["baseline_coverage"]["unhit"]
    assert goal in result.evaluation.hits
    assert any(goal in dict(row)["targets"] for rows in result.generated.searches[0].provenance.values()
               for row in rows)
