"""Custom attachments compose through public baseline, search and native replay."""

import pytest

from behave.sim import api

from .test_baseline_public_sequence import REFERENCE, RTL, _contract


def _custom_contract():
    base = _contract()
    field = api.GoalField
    eq = lambda name, value: api.GoalCondition("eq", field(name), value)
    monitor = api.FiniteMonitor((0, 1), ("one", "other"), 0, ("pair",), (
        api.FiniteMonitorTransition(0, "one", 1, ()),
        api.FiniteMonitorTransition(1, "one", 1, ("pair",)),
        api.FiniteMonitorTransition(0, "other", 0, ()),
        api.FiniteMonitorTransition(1, "other", 0, ()),
    ))
    return api.TaskContract(base.endpoints, base.clocks,
        observation_goals=(
            api.TransactionGoal("one", "out", eq("y", 1)),
            api.TransactionGoal("stable", "out", api.GoalCondition(
                "eq", field("y"), field("y", previous=True))),
        ),
        source_read_rules=(api.SourceReadRule("a", "zero", eq("value", 0)),),
        operation_rules=(api.OperationRule("add", api.OperationGoal("one", eq("result", 1))),),
        transaction_monitors=(api.TransactionMonitorSpec("ones", monitor, (
            api.TransactionEvent("one", "out", eq("y", 1)),
            api.TransactionEvent("other", "out")), order=("out",)),),
        latency_goals=(api.LatencyGoal("response", api.LatencyEvent("in"), api.LatencyEvent("out"),
            (api.LatencyBin("paired", 0),), api.LatencyTimebase("transaction", "in")),))


def test_public_custom_declarations_roundtrip_together():
    contract = _custom_contract()
    restored = api.TaskContract.from_dict(contract.as_dict())
    assert restored.as_dict() == contract.as_dict()
    assert restored.digest == contract.digest
    for name in ("observation_goals", "source_read_rules", "operation_rules",
                 "transaction_monitors", "latency_goals"):
        assert getattr(restored, name)


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ["behavior", pytest.param("rtl", marks=pytest.mark.eda)])
def test_public_baseline_composes_custom_and_automatic_goals(tmp_path, kind):
    contract = _custom_contract()
    reference_source = REFERENCE.replace('return {"y": a}', 'return {"y": (a + 1) & 255}')
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "behavior.py"
        path.write_text(reference_source)
        task = api.admit(contract, api.realization_of_behavior_source(
            reference_source, contract, file=path.name), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name), bounded_analysis=False)
    else:
        source = RTL.replace("VALUE", "(a + 8'd1)")
        path = tmp_path / "design.sv"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=100), providers=(),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    hits = set(result.evaluation.hits)
    assert {"observation:one", "observation:stable", "monitor:ones:pair",
            "latency:response:paired"} <= hits
    for family in ("location", "value-observation", "operation-event"):
        assert any(goal.family == family and goal.id in hits
                   for goal in handle.frontend_catalogue.values()), family
    assert all(stimulus.depth("in") == 100 for stimulus in result.generated.stimuli)
    assert result.analysis is None


def _search_contract():
    from itertools import combinations, product

    base = _contract()
    eq = lambda name, value: api.GoalCondition("eq", api.GoalField(name), value)
    rare_input, rare_output = eq("a", 137), eq("y", 138)
    sequence = api.TransactionMonitorSpec("sequence", api.FiniteMonitor(
        (0, 1), ("zero", "rare", "other"), 0, ("hit",), [
            api.FiniteMonitorTransition(state, event, 1 if event == "zero" else 0,
                ("hit",) if state == 1 and event == "rare" else ())
            for state, event in product((0, 1), ("zero", "rare", "other"))]), (
        api.TransactionEvent("zero", "out", eq("y", 0)),
        api.TransactionEvent("rare", "out", rare_output),
        api.TransactionEvent("other", "out")), order=("out",))
    alphabet = tuple(event for size in range(1, 4)
                     for event in combinations(("input", "other", "output"), size))
    together = api.TransactionMonitorSpec("entry", api.FiniteMonitor(
        (0,), alphabet, 0, ("hit",), [
            api.FiniteMonitorTransition(0, event, 0,
                ("hit",) if event == ("input", "output") else ()) for event in alphabet]), (
        api.TransactionEvent("input", "in", rare_input),
        api.TransactionEvent("other", "in"),
        api.TransactionEvent("output", "out", rare_output),
        api.TransactionEvent("other", "out")), order=("in", "out"), sampling="entry-set")
    obligation = api.TransactionMonitorSpec("range", api.FiniteMonitor(
        (0,), ("bad", "other"), 0, ("violation",), (
            api.FiniteMonitorTransition(0, "bad", 0, ("violation",)),
            api.FiniteMonitorTransition(0, "other", 0, ()))), (
        api.TransactionEvent("bad", "out", eq("y", 256)),
        api.TransactionEvent("other", "out")), order=("out",), violations=("violation",))
    return api.TaskContract(base.endpoints, base.clocks,
        observation_goals=(
            api.TransactionGoal("rare", "out", rare_output),
            api.TransactionGoal("rise", "out", api.GoalCondition("and", rare_output,
                api.GoalCondition("eq", api.GoalField("y", previous=True), 0)))),
        source_read_rules=(api.SourceReadRule("a", "rare", eq("value", 137)),),
        operation_rules=(api.OperationRule("add", api.OperationGoal("rare", eq("result", 138))),),
        transaction_monitors=(sequence, together, obligation),
        latency_goals=(api.LatencyGoal("rare", api.LatencyEvent("in", condition=rare_input),
            api.LatencyEvent("out", condition=rare_output), (api.LatencyBin("paired", 0),),
            api.LatencyTimebase("transaction", "in")),))


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("kind", ["behavior", pytest.param("rtl", marks=pytest.mark.eda)])
def test_public_search_composes_custom_and_automatic_goals(tmp_path, kind, record_property):
    import json

    from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

    source = """class M:
    def reset(self): pass
    def process(self, a):
        if a == 137 and a > 0:
            return {"y": (a + 1) & 255}
        return {"y": a}
"""
    contract = _search_contract()
    assert api.TaskContract.from_dict(contract.as_dict()).as_dict() == contract.as_dict()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "behavior.py"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_behavior_source(source, contract, file=path.name),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name), bounded_analysis=False)
    else:
        path = tmp_path / "design.sv"
        path.write_text(RTL.replace("VALUE", "(a == 137 && a > 0) ? (a + 8'd1) : a"))
        task = api.admit(contract, api.realization_of_rtl_source(path.read_text(), top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    frozen_source = handle.frontend_catalogue.as_dict()
    providers = tuple(provider for provider in handle.generation_providers(reference, workdir=tmp_path / "queries")
                      if ("candidate:" in provider.name if kind == "behavior"
                          else provider.policy == RTL_LOCAL_GUIDANCE_POLICY))
    assert len(providers) == 1
    request = handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=24, debug=True), run_analysis=False)
    frozen_policy = request.goal_policy.as_dict()
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    assert result.evaluation.complete and result.analysis is None
    search, = result.generated.searches
    assert search.generated
    assert handle.frontend_catalogue.as_dict() == frozen_source
    assert request.goal_policy.as_dict() == frozen_policy
    wanted = {"observation:rare", "observation:rise", "monitor:sequence:hit",
              "monitor:entry:hit", "latency:rare:paired"}
    baseline_hits = set(search.telemetry["baseline_coverage"]["hits"])
    assert wanted.isdisjoint(baseline_hits)
    assert wanted <= set(search.telemetry["coverage"]["hits"])
    safety = "monitor:range:violation"
    assert result.evaluation.catalogue[safety].cls == api.SAFETY
    assert safety in search.telemetry["coverage"]["unhit"]
    assert safety not in result.evaluation.hits

    # Do not let the separate continuous workload supply missing search credit.
    new_ids = {stimulus.fingerprint for stimulus in search.generated}
    new_runs = {index for index, run in enumerate(result.evaluation.results)
                if run.stimulus.fingerprint in new_ids}
    assert new_runs
    native_hits = set()
    for index in new_runs:
        run = result.evaluation.results[index]
        assert run.ok and run.trace.accepted_count(("in",)) == 100
        native_hits.update(run.trace.goal_summary[1])
    native_hits.update(dict(hit)["goal"]
        for index, hit in dict(result.evaluation.telemetry)["monitor_hits"] if index in new_runs)
    assert wanted <= native_hits
    source_hits = {}
    for schema in ("value-observation", "operation-event", "control-choice", "boolean-expression"):
        # The frozen policy owns canonical schema names. Frontends retain their
        # own source identities/families (e.g. Python's "decision"), not copies
        # relabelled by the caller to resemble RTL.
        declared = {goal for provider in request.goal_policy.enabled_providers()
                    if provider.schema.name == schema for goal in provider.catalogue
                    if goal in handle.frontend_catalogue}
        newly_hit = (declared - baseline_hits) & native_hits
        assert newly_hit, schema
        source_hits[schema] = sorted(newly_hit)
    record_property("composed_goal_loop", json.dumps({
        "kind": kind, "baseline_hits": len(baseline_hits), "generated": len(search.generated),
        "custom_goals_confirmed": sorted(wanted), "safety_retained_unhit": safety,
        "source_goals_confirmed": source_hits,
        "new_isolated_runs": len(new_runs), "probes": search.telemetry["probes"]}))
