"""Per-schema public baseline -> local query -> independently checked native hit.

These small controls do not claim whole-domain proofs or real-task coverage.
"""

import json
from time import monotonic

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
from tests.support.atomic_behavior import SOURCE, _contract, _schedule

from .test_behavior_atomic_operation_rules import _rules
from .test_behavior_atomic_transactions import IDENTITY, _declarations

pytestmark = [pytest.mark.solver, pytest.mark.isolation]


def _stimulus(offers, queues, *, reset=()):
    schedule = GlobalSchedule(_schedule((2, 2), n_slots=len(offers)).timings, [
        GlobalEventSlot(2 * index, event=api.EventSlot(
            ticks=("a", "b"), offers=offered, resets=reset if index == 1 else (),
            controls={"a_rst": int(index == 1 and "a" in reset),
                      "b_rst": int(index == 1 and "b" in reset),
                      "oa_ready": 1, "ob_ready": 1}))
        for index, offered in enumerate(offers)])
    return api.Stimulus(queues=queues, slots=[slot.event for slot in schedule.slots],
                        clock_schedule=schedule)


def _prepare(tmp_path, source, contract, seed, *, reference_source=None):
    path = tmp_path / "behavior.py"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": (source if reference_source is None else reference_source).encode()},
        config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file=path.name), reference=reference)
    spec = api.GenerationSpec(len(seed.slots), max(len(q) for _, q in seed.queues), 0, 0, 0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"),
                                  spec, bounded_analysis=False)
    return task, spec, handle, reference


def _hit_slots(evaluation, goal):
    # Source events are recorded in the native vector. Transaction monitors
    # consume those actual records and retain their own attributed hit receipts.
    trace = evaluation.results[0].trace
    slots = {index for index, row in enumerate(trace.records) if dict(row.hits).get(goal)}
    slots.update(dict(hit)["slot"] for run, hit in dict(evaluation.telemetry)["monitor_hits"]
                 if run == 0 and dict(hit)["goal"] == goal)
    return sorted(slots)


class _PublicLoop:
    """Retain only this test invocation's real evaluations; no fabricated traces."""

    def __init__(self, tmp_path, source, contract, seed):
        self.started = monotonic()
        task, spec, self.handle, self.reference = _prepare(tmp_path, source, contract, seed)
        self.runs = {}
        self.prepared_at = monotonic()
        self.baseline = self.run(seed)
        assert self.baseline.passed, self.baseline.as_dict()
        self.baseline_at = monotonic()
        self.provider = api.prepare_behavior_generation(task, spec, self.reference,
            lambda item: self.run(item).evaluation.results[0].trace,
            timeout_s=60, timeout_ms=20_000)
        self.manifest = self.provider.catalogue_manifest()
        self.layout_at = monotonic()

    def run(self, seed):
        if seed.fingerprint not in self.runs:
            result = self.handle.replay((seed,), reference=self.reference)
            assert result.authoritative, json.dumps([
                (index, failure.as_dict()) for index, failure in result.evaluation.failures])
            self.runs[seed.fingerprint] = result
        return self.runs[seed.fingerprint]

    def search(self, seed, goal, record_property):
        assert goal in self.manifest
        assert goal not in self.baseline.evaluation.hits
        outcomes = []
        with self.provider.search_scope(probe_budget=len(seed.slots)):
            selected = self.provider.select(seed, targets=[goal])
            assert isinstance(selected, api.ProbeSelection), selected.as_dict()
            assert goal not in selected.replay.hits
            selected_at = monotonic()
            for request in selected.requests:
                assert request.targets == (goal,)
                answer = self.provider.probe(seed, request)
                outcomes.append(answer.outcome.value)
                if answer.outcome.value == "sat":
                    break
                assert answer.outcome.value == "local-unsat", answer.as_dict()
            else:
                pytest.fail(f"no local witness for {goal}: {outcomes}")
            probed_at = monotonic()
            assert answer.scope.value == "exact-local-hit"
            assert answer.goal == goal
            # SAT has not earned any new concrete credit yet.
            assert len(self.runs) == 1
            assert goal not in self.baseline.evaluation.hits
            confirmed = self.provider.select(answer.candidate, targets=[goal])
            assert isinstance(confirmed, api.ProbeSelection), confirmed.as_dict()
            assert goal in confirmed.replay.hits
            assert self.provider.catalogue_manifest() == self.manifest
        final = self.runs[answer.candidate.fingerprint]
        assert final.passed, final.as_dict()
        assert len(self.runs) == 2
        trace = final.evaluation.results[0].trace
        slots = _hit_slots(final.evaluation, goal)
        assert goal in final.evaluation.hits and slots
        record_property("goal_loop", json.dumps({
            "goal": goal, "declaration": self.manifest[goal].as_dict(),
            "baseline_hits": 0, "final_hits": 1, "native_slots": slots,
            "probe_outcomes": outcomes, "prepare_s": self.prepared_at - self.started,
            "baseline_s": self.baseline_at - self.prepared_at,
            "layout_s": self.layout_at - self.baseline_at,
            "select_s": selected_at - self.layout_at,
            "probe_s": probed_at - selected_at, "confirmation_s": monotonic() - probed_at,
            "total_s": monotonic() - self.started}))
        return answer.candidate, trace, outcomes


def test_public_operation_uses_noninitial_old_state_not_serial_handler_order(tmp_path, record_property):
    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks, operation_rules=_rules())
    seed = _stimulus([("a_in", "b_in")] * 2,
                     {"a_in": ((0,), (0,)), "b_in": ((0,), (0,))})
    loop = _PublicLoop(tmp_path, SOURCE, contract, seed)
    line = next(i for i, text in enumerate(SOURCE.splitlines(), 1) if "self.y = self.x + b" in text)
    goals = [goal for goal, declaration in loop.manifest.items()
             if declaration.family == "operation-event"
             and dict(declaration.where("site"))["line"] == line
             and dict(declaration.where("predicate"))["name"] == "target"]
    assert len(goals) == 1
    candidate, trace, outcomes = loop.search(seed, goals[0], record_property)
    assert outcomes == ["local-unsat", "sat"]
    assert dict(candidate.queues)["b_in"] == ((0,), (2,))
    # At slot 1, old x=8 gives 8+2=10. Serially committing A first would
    # incorrectly make B see x=0; the original native output must be 10.
    assert dict(trace.records[1].outputs)["b_out"] == ((10,),)
    assert [i for i, row in enumerate(trace.records) if dict(row.hits).get(goals[0])] == [1]


def test_public_structural_search_keeps_short_circuit_and_loop_entry_semantics(
        tmp_path, record_property):
    source = '''class M:
    def reset(self):
        pass

    def process_a(self, a):
        total = 0
        if a and a == 3:
            for k in range(2):
                total = total + 1
        return {"oa": total}

    def process_b(self, b):
        return {"ob": b}
'''
    seed = _stimulus([("a_in", "b_in")] * 4,
        {"a_in": ((0,), (1,), (2,), (0,)), "b_in": ((0,),) * 4})
    loop = _PublicLoop(tmp_path, source, _contract(), seed)

    def goal_at(family, text, event):
        start = source.index(text)
        line = source.count("\n", 0, start) + 1
        col = start - source.rfind("\n", 0, start) - 1
        goal, = [name for name, goal in loop.manifest.items()
                 if goal.family == family and goal.event == event
                 and goal.where("line") == line and goal.where("col") == col]
        return goal

    right_true = goal_at("condition", "a == 3", "T")
    right_false = goal_at("condition", "a == 3", "F")
    branch = goal_at("decision", "a and a == 3", "T")
    body = goal_at("location", "total = total + 1", "entry")
    nonzero = goal_at("loop", "range(2)", "T")
    zero = goal_at("loop", "range(2)", "F")
    baseline = loop.baseline.evaluation
    assert not {right_true, branch, body, nonzero, zero} & set(baseline.hits)
    declared = loop.manifest.as_dict()
    candidate, trace, _outcomes = loop.search(seed, right_true, record_property)

    # An independent truth table over actual accepted inputs, not the query's
    # projected events. A skipped RHS is absent, not a false condition hit.
    for actual in (baseline.results[0].trace, trace):
        for row in actual.records:
            value, = dict(row.accepted)["a_in"]
            hits = {name for name, reached in row.hits if reached}
            entered = value == 3
            assert (right_true in hits) is entered
            assert (right_false in hits) is (value in (1, 2))
            assert all((name in hits) is entered for name in (branch, body, nonzero))
            assert zero not in hits
            assert dict(row.outputs)["a_out"] == ((2 if entered else 0,),)

    final = loop.run(candidate)
    assert {right_true, branch, body, nonzero} <= set(final.evaluation.hits)
    assert loop.provider.catalogue_manifest().as_dict() == declared
    assert zero in loop.manifest and zero not in final.evaluation.hits
    record_property("structural_categories", json.dumps({
        "confirmed": ["boolean-expression", "control-choice", "location", "loop-activation"],
        "baseline_rhs_false_slots": _hit_slots(baseline, right_false),
        "new_rhs_true_slots": _hit_slots(final.evaluation, right_true),
        "retained_zero_loop": zero,
    }))


@pytest.mark.parametrize("reset", [(), ("a", "b"), ("a",)])
def test_public_sequence_preserves_history_across_silent_and_reset_slots(tmp_path, record_property, reset):
    seed = _stimulus([("a_in",), (), ("b_in",)],
                     {"a_in": ((1,),), "b_in": ((0,),)}, reset=reset)
    source = IDENTITY.replace("    def process_a", "    def reset_a(self):\n        pass\n    def process_a")
    loop = _PublicLoop(tmp_path, source, _declarations(), seed)
    candidate, trace, outcomes = loop.search(seed, "monitor:cross:hit", record_property)
    assert dict(candidate.queues)["a_in"] == ((1,),)
    assert dict(candidate.queues)["b_in"] == ((2,),)
    # Independent transaction oracle: only a=1 then b=2 completes the sequence;
    # the silent/reset entry produces neither a transaction nor a hit.
    assert dict(trace.records[0].outputs) == {"a_out": ((1,),)}
    assert not trace.records[1].accepted and not trace.records[1].outputs
    assert dict(trace.records[2].outputs) == {"b_out": ((2,),)}
    assert _hit_slots(loop.run(candidate).evaluation, "monitor:cross:hit") == [2]
    assert outcomes == ["local-unsat", "sat"]


@pytest.mark.parametrize("schema", ["value", "activity", "latency"])
def test_public_typed_history_goals_have_actual_sampling_evidence(tmp_path, record_property, schema):
    base = _contract()
    if schema == "value":
        contract = _declarations()
        seed = _stimulus([("b_in",)], {"a_in": (), "b_in": ((0,),)})
        goal, endpoint, expected, slot = "observation:two-output", "b_out", 2, 0
    elif schema == "activity":
        contract = _declarations()
        seed = _stimulus([("a_in",), (), ("a_in",)],
                         {"a_in": ((1,), (1,)), "b_in": ()})
        goal, endpoint, expected, slot = "observation:a-changed", "a_out", None, 2
    else:
        rule = api.LatencyGoal("cross", api.LatencyEvent("a_out", ("oa",)),
            api.LatencyEvent("b_out", ("ob",)), (api.LatencyBin("one", 1, 1),),
            api.LatencyTimebase("transaction", "a_out"))
        contract = api.TaskContract(base.endpoints, base.clocks, latency_goals=(rule,))
        seed = _stimulus([("a_in",), ("a_in",), ("b_in",)],
                         {"a_in": ((1,), (2,)), "b_in": ((0,),)})
        goal, endpoint, expected, slot = "latency:cross:one", "b_out", 1, 2
    loop = _PublicLoop(tmp_path, IDENTITY, contract, seed)
    candidate, trace, _outcomes = loop.search(seed, goal, record_property)
    assert _hit_slots(loop.run(candidate).evaluation, goal) == [slot]
    current = dict(trace.records[slot].outputs)[endpoint][0][0]
    if schema == "activity":
        previous = dict(trace.records[0].outputs)[endpoint][0][0]
        assert current != previous
    else:
        assert current == expected
    if schema == "latency":
        assert [dict(row.outputs)["a_out"][0][0] for row in trace.records[:2]] == [1, 2]


@pytest.mark.parametrize("simultaneous", [True, False])
def test_public_entry_set_does_not_confuse_same_slot_with_consecutive_slots(tmp_path, record_property, simultaneous):
    from .test_schedule_entry_goals import _rule

    base = _contract()
    monitor = _rule((
        api.TransactionEvent("a", "a_out", api.GoalCondition("eq", api.GoalField("oa"), 1)),
        api.TransactionEvent("other", "a_out"),
        api.TransactionEvent("b", "b_out", api.GoalCondition("eq", api.GoalField("ob"), 2)),
        api.TransactionEvent("other", "b_out")), order=("a_out", "b_out"))
    contract = api.TaskContract(base.endpoints, base.clocks, transaction_monitors=(monitor,))
    seed = _stimulus([("a_in", "b_in")] if simultaneous else [("a_in",), ("b_in",)],
                     {"a_in": ((0,),), "b_in": ((0,),)})
    loop = _PublicLoop(tmp_path, IDENTITY, contract, seed)
    goal = "monitor:entry:together"
    if simultaneous:
        candidate, trace, _outcomes = loop.search(seed, goal, record_property)
        assert dict(trace.records[0].outputs) == {"a_out": ((1,),), "b_out": ((2,),)}
        assert _hit_slots(loop.run(candidate).evaluation, goal) == [0]
    else:
        with loop.provider.search_scope(probe_budget=2):
            selected = loop.provider.select(seed, targets=[goal])
            assert isinstance(selected, api.ProbeSelection), selected.as_dict()
            assert goal not in selected.replay.hits
            answers = [loop.provider.probe(seed, request) for request in selected.requests]
        assert len(answers) == 2
        assert all(answer.outcome.value == "local-unsat" for answer in answers)
        assert goal in loop.provider.catalogue_manifest()
        assert len(loop.runs) == 1  # No invented candidate or hit from UNSAT.
        record_property("local_unsat", json.dumps({"goal": goal, "probes": 2,
            "scope": "fixed separate entries only", "catalogue_retained": True}))


@pytest.mark.parametrize("schema", ["partial-operation", "output-mismatch", "obligation"])
def test_public_candidate_search_discovers_safety_and_returns_witnessed_zero(tmp_path, record_property, schema):
    started = monotonic()
    if schema == "partial-operation":
        source = IDENTITY.replace('{"oa": a}', '{"oa": 1 // (3 - a)}')
        reference_source = IDENTITY.replace('{"oa": a}', '{"oa": 0}')
        contract = _contract()
        seed = _stimulus([("a_in", "b_in")], {"a_in": ((0,),), "b_in": ((0,),)})
    else:
        source = IDENTITY.replace('{"oa": a}',
            '{"oa": 1}' if schema == "obligation" else '{"oa": a + 1}')
        reference_source = source.replace('{"ob": b}', '{"ob": 3 if b == 2 else b}')
        contract = _declarations(safety=True)
        seed = _stimulus([("a_in", "b_in")] * 2,
                         {"a_in": ((0,), (0,)), "b_in": ((0,), (0,))})
    contract = api.TaskContract([
        endpoint if endpoint.direction is api.Direction.IN else api.Endpoint(
            endpoint.name, endpoint.direction, endpoint.fields, endpoint.adapter,
            endpoint.clock, kind=endpoint.kind, answers_per_transaction=1)
        for endpoint in contract.endpoints], contract.clocks,
        observation_goals=contract.observation_goals,
        transaction_monitors=contract.transaction_monitors,
        protocol_instances=[api.ProtocolInstance("a", ("a_in", "a_out")),
                            api.ProtocolInstance("b", ("b_in", "b_out"))])
    _task, _spec, handle, reference = _prepare(
        tmp_path, source, contract, seed, reference_source=reference_source)
    # This is the candidate-to-candidate public route. The hidden reference
    # never violates the obligation; reference errors must not supply a zero.
    providers = handle.generation_providers(reference, workdir=tmp_path / "guidance")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=2),
        providers=(providers[1], providers[0]),
        reference=reference, search_policy=api.SearchPolicy(time_budget_s=60, max_probes=12)))
    record_property("command_s", monotonic() - started)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and not result.decision.passed, result.as_dict()
    assert result.analysis is None
    searches = {item.provider: item for item in result.generated.searches}
    search, skipped = searches[providers[1].name], searches[providers[0].name]
    record_property("search", json.dumps(search.as_dict()))
    assert result.generated.search_summary["stop_reason"] == "candidate-failed"
    assert search.telemetry["termination"] == skipped.telemetry["termination"] == "candidate-failed"
    assert skipped.telemetry["probes"] == 0
    assert result.evaluation.failure_witness is not None
    assert search.generated, search.as_dict()
    assert result.evaluation.results[0].ok
    hits = set(result.evaluation.hits)
    if schema in ("obligation", "output-mismatch"):
        assert search.telemetry["probes"] < 12
        assert "observation:outside" in search.telemetry["coverage"]["remaining"]
        assert "monitor:cross:hit" not in search.telemetry["baseline_coverage"]["hits"]
        if schema == "obligation":
            assert "monitor:cross:hit" in hits
        else:
            # This counterexample precedes the old control's later safety hit.
            # Do not keep searching just to finish a category demonstration.
            assert result.evaluation.mismatch_count > 0
            assert "monitor:cross:hit" not in hits
    else:
        assert result.evaluation.safety_hits, sorted(hits)
        failed = result.evaluation.results[result.evaluation.failure_witness].trace
        safety = set(result.evaluation.safety_hits)
        failure_rows = [row for row in failed.records
                        if any(reached and goal in safety for goal, reached in row.hits)]
        assert failure_rows
        assert all(dict(row.accepted)["a_in"] == (3,) for row in failure_rows)
        assert all(not row.outputs for row in failure_rows)
        assert failed.completion is api.Completion.PADDED
    # The baseline replays its own continuous workload before Search, and that
    # retained run is reported. No continuous workload with the failing test runs.
    evaluation = result.evaluation
    failing = evaluation.results[evaluation.failure_witness].stimulus.fingerprint
    workloads = dict(dict(dict(evaluation.telemetry)["replay_suite"])["members"])
    expanded = {workload for workload, tests in workloads.items() if failing in tests}
    assert expanded and expanded <= {s.fingerprint for s in evaluation.unexecuted}
    record_property("negative_witness", json.dumps({
        "index": result.evaluation.failure_witness,
        "safety_hits": result.evaluation.safety_hits,
        "completed_runs": len(result.evaluation.results),
        "unexecuted_runs": len(result.evaluation.unexecuted)}))


def test_reference_safety_search_cannot_supply_a_candidate_failure(tmp_path, record_property):
    source = IDENTITY.replace('{"oa": a}', '{"oa": 0}')
    reference_source = IDENTITY.replace('{"oa": a}', '{"oa": 1 // (3 - a)}')
    base = _contract()
    contract = api.TaskContract([
        endpoint if endpoint.direction is api.Direction.IN else api.Endpoint(
            endpoint.name, endpoint.direction, endpoint.fields, endpoint.adapter,
            endpoint.clock, kind=endpoint.kind, answers_per_transaction=1)
        for endpoint in base.endpoints], base.clocks,
        protocol_instances=[api.ProtocolInstance("a", ("a_in", "a_out")),
                            api.ProtocolInstance("b", ("b_in", "b_out"))])
    seed = _stimulus([("a_in", "b_in")], {"a_in": ((0,),), "b_in": ((0,),)})
    _task, _spec, handle, reference = _prepare(
        tmp_path, source, contract, seed, reference_source=reference_source)
    providers = handle.generation_providers(reference, workdir=tmp_path / "guidance")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=2),
        providers=(providers[0],), reference=reference,
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=4)))
    record_property("result", json.dumps(result.as_dict()))
    assert result.evaluation is not None, result.as_dict()
    assert result.evaluation.results[0].ok
    assert result.generated.searches[0].generated
    assert not result.decision.authoritative
    assert result.evaluation.failure_witness is None
    assert any(failure.origin.value == "reference" for _, failure in result.evaluation.failures)
    assert not result.evaluation.safety_hits
    assert result.generated.search_summary["stop_reason"] != "candidate-failed"
