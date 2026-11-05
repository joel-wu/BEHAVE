"""Finite monitor queries carry real prefix state, not an invented hit history."""

from itertools import pairwise, product
from types import SimpleNamespace

import pytest
import z3

from behave.sim import api
from behave.sim.behavior import residual
from behave.sim.symbolic import interp
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.observation_symbolic import condition_terms, predicate_term
from behave.sim.core.ready_valid_product import ConcreteAlgebra
from behave.sim.core.stimulus import SlotRecord
from behave.sim.core.transaction_monitor import DeclaredTransactionMonitor
from behave.sim.engines.probing import Outcome
from behave.sim.rtl.task_symbolic import Z3Algebra

from .test_baseline_public_sequence import _contract
from .test_transaction_goal_queries import _lower, _probe


def _sequence(before=7, after=9, *, endpoint="out", field="y", safety=False):
    rows = [api.FiniteMonitorTransition(state, event, int(event == "first"),
                ("hit",) if state == 1 and event == "second" else ())
            for state, event in product((0, 1), ("first", "second", "other"))]
    return api.TransactionMonitorSpec("chain", api.FiniteMonitor(
        (0, 1), ("first", "second", "other"), 0, ("hit",), rows), (
            api.TransactionEvent("first", endpoint,
                api.GoalCondition("eq", api.GoalField(field), before)),
            api.TransactionEvent("second", endpoint,
                api.GoalCondition("eq", api.GoalField(field), after)),
            api.TransactionEvent("other", endpoint)), order=(endpoint,),
        violations=("hit",) if safety else ())


def _contract_with(*monitors):
    base = _contract()
    # Keep a scalar predicate alongside the FSM: both share the same product.
    return api.TaskContract(base.endpoints, base.clocks, transaction_monitors=monitors,
        observation_goals=(api.TransactionGoal("zero", "out",
            api.GoalCondition("eq", api.GoalField("y"), 0)),))


def _bool(term):
    return z3.is_true(z3.simplify(term))


def _read(term):
    if not isinstance(term, z3.ExprRef):
        return term
    return _bool(term) if z3.is_bool(term) else z3.simplify(term).as_long()


@pytest.mark.parametrize("after", (1, 2))
def test_product_matches_independent_native_monitor_and_keeps_silence(after):
    # after=1 overlaps the priority cases: only the first case may consume it.
    contract = _contract_with(_sequence(1, after))
    binding = TransactionProduct(contract)
    native = DeclaredTransactionMonitor(contract, behavior=True)
    algebra = Z3Algebra()
    for values in product((0, 1, 2), repeat=3):
        records = [SlotRecord(outputs={"out": ((value,),)}) for value in values]
        records.insert(1, SlotRecord())  # DUT reset/silence does not reset history.
        report = native.check(None, SimpleNamespace(records=records))
        expected = {hit.slot for hit in report.hits}
        concrete = binding.initial(ConcreteAlgebra)
        symbolic = binding.initial(algebra)
        for index, record in enumerate(records):
            samples = {"out": tuple((True, payload) for payload in dict(record.outputs).get("out", ()))}
            # An unsampled value must not enter either scalar or FSM history.
            if not samples.get("out"):
                samples["out"] = ((False, (2,)),)
            concrete, hits = binding.advance(concrete, samples, ConcreteAlgebra)
            symbolic, terms = binding.advance(symbolic, samples, algebra,
                predicate=predicate_term, condition=condition_terms)
            assert hits["monitor:chain:hit"] == _bool(terms["monitor:chain:hit"]) == (index in expected)
            assert binding.image(concrete) == binding.image(symbolic, _read)


def test_unreadable_classifier_poison_is_absorbing_not_default():
    contract = _contract_with(_sequence(1, 2))
    binding = TransactionProduct(contract)
    algebras = (ConcreteAlgebra, Z3Algebra())
    states = [binding.initial(algebra) for algebra in algebras]
    # None is an unreadable payload, never an alias for the default event.
    for value in (1, None, 1, 2):
        # This test isolates the FSM, whose state has no scalar payload carrier.
        for i, algebra in enumerate(algebras):
            state, hits = binding.monitors.advance(states[i]["automata"],
                {"out": ((True, (value,)),)}, algebra,
                condition=condition_terms if i else None)
            states[i]["automata"] = state
            assert not (_bool(hits["monitor:chain:hit"]) if i else hits["monitor:chain:hit"])
    assert states[0]["automata"]["chain"] == (False, False)
    assert tuple(map(_bool, states[1]["automata"]["chain"])) == (False, False)


@pytest.mark.parametrize("order", (("in", "out"), ("out", "in")))
def test_product_preserves_declared_same_record_order(order):
    fsm = api.FiniteMonitor((False, 0), (False, 0), 0, ("pair",), [
        api.FiniteMonitorTransition(state, event, event,
            ("pair",) if type(state) is bool and type(event) is int else ())
        for state in (False, 0) for event in (False, 0)])
    spec = api.TransactionMonitorSpec("order", fsm, (
        api.TransactionEvent(False, "in"), api.TransactionEvent(0, "out")), order=order)
    contract = _contract_with(spec)
    product_binding = TransactionProduct(contract).monitors
    native = DeclaredTransactionMonitor(contract, behavior=True)
    records = [SlotRecord(accepted={"in": (0,)}, outputs={"out": ((0,),)})] * 2
    expected = {hit.slot for hit in native.check(None, SimpleNamespace(records=records)).hits}
    assert expected == ({0, 1} if order[0] == "in" else {1})
    algebras = (ConcreteAlgebra, Z3Algebra())
    states = [product_binding.initial(algebra) for algebra in algebras]
    for slot in range(2):
        for i, algebra in enumerate(algebras):
            states[i], hits = product_binding.advance(states[i],
                {"in": ((True, (0,)),), "out": ((True, (0,)),)}, algebra,
                condition=condition_terms if i else None)
            assert (_bool(hits["monitor:order:pair"]) if i else hits["monitor:order:pair"]) == (slot in expected)


@pytest.mark.parametrize("safety", (False, True))
def test_local_question_uses_native_prefix_and_fresh_replay_confirms(safety):
    _c, instance, binding = _lower(
        '        if a == 0:\n            return None\n        return {"y": a}\n',
        monitors=(_sequence(safety=safety),))
    goal = "monitor:chain:hit"
    assert _probe(instance, binding, {}, goal).outcome is Outcome.LOCAL_UNSAT
    previous = {}
    for value in (7, 0):
        _state, outputs, _hits, failure = interp.run_step(instance.proc, {"a": value})
        assert not failure
        assert goal not in binding.observe(previous, {"a": value}, outputs.transactions)
    result = _probe(instance, binding, previous, goal, position=2)
    assert result.outcome is Outcome.SAT and result.candidate == {"a": 9}
    trace = [{"a": 7}, {"a": 0}, result.candidate]
    assert goal in instance.replay(trace).hits
    assert instance.audit((trace,)).status is AuditCheckStatus.AGREED
    assert instance.catalogue_manifest()[goal].cls == (api.SAFETY if safety else api.COVERAGE)


def test_input_monitor_local_query_does_not_expand_irrelevant_computation(monkeypatch):
    from behave.sim.engines import local_ir

    encode, cones = local_ir._encode_query_step, []

    def record(*args, **kwargs):
        cones.append(kwargs["keep"])
        return encode(*args, **kwargs)

    monkeypatch.setattr(local_ir, "_encode_query_step", record)
    _c, instance, binding = _lower('        return {"y": 1 // a}\n',
        monitors=(_sequence(7, 0, endpoint="in", field="a"),))
    previous = {}
    binding.observe(previous, {"a": 7}, ())
    result = _probe(instance, binding, previous, "monitor:chain:hit", position=1)
    assert result.outcome is Outcome.SAT and result.candidate == {"a": 0}
    assert cones == [frozenset()]
    assert "monitor:chain:hit" in instance.replay([{"a": 7}, {"a": 0}]).hits


def test_unreadable_local_prefix_is_not_a_local_unsat_claim():
    _c, instance, binding = _lower('        return {"y": a}\n', monitors=(_sequence(),))
    previous = {}
    binding.observe(previous, {"a": 7}, ({"y": None},))
    assert _probe(instance, binding, previous, "monitor:chain:hit", position=1).outcome is Outcome.UNSUPPORTED


@pytest.mark.parametrize("bound", (1, 2))
def test_full_history_has_a_different_question_from_fixed_prefix(bound):
    _c, instance, _binding = _lower('        return {"y": a}\n',
        monitors=(_sequence(),), bound=bound)
    goal = "monitor:chain:hit"
    verdict = instance.query((goal,), bound, timeout_ms=10_000, workers=1)
    assert not verdict.undecided
    if bound == 1:
        assert verdict.unreachable == {goal}
    else:
        assert goal in verdict.reachable
        trace = verdict.reachable[goal]
        assert list(trace) == [{"a": 7}, {"a": 9}]
        assert goal in instance.replay(trace).hits
        assert instance.audit((trace,)).status is AuditCheckStatus.AGREED
    assert goal in instance.catalogue_manifest()  # Never delete an unhit goal.


def test_one_batch_can_advance_twice_and_audit_detects_wrong_symbolic_hit():
    _c, instance, binding = _lower('        return [{"y": 7}, {"y": 9}]\n',
        monitors=(_sequence(),), bound=1)
    trace = [{"a": 0}]
    assert "monitor:chain:hit" in instance.replay(trace).hits
    assert instance.audit((trace,)).status is AuditCheckStatus.AGREED
    run = residual.unroll(instance.proc, instance.env, 1,
        plan=instance._position_plan, transactions=binding)
    run.transaction_components["monitor:chain:hit"] = (z3.BoolVal(False),)
    instance._prepared_transaction_audit = run
    assert instance.audit((trace,)).status is AuditCheckStatus.INCONSISTENT


def test_failed_invocation_observes_input_but_absorbed_suffix_does_not():
    _c, instance, _binding = _lower('        return {"y": 1 // a}\n',
        monitors=(_sequence(0, 7, endpoint="in", field="a"),), bound=3)
    trace = [{"a": 0}, {"a": 7}, {"a": 9}]
    assert instance.replay(trace).error
    assert "monitor:chain:hit" not in instance.replay(trace).hits
    assert instance.audit((trace,)).status is AuditCheckStatus.AGREED


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("goal_kind", ("sequence", "activity", "obligation"))
def test_public_search_targets_missing_finite_goal_not_just_scalar_predicates(tmp_path, kind, goal_kind):
    from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

    from .test_baseline_public_sequence import REFERENCE, RTL

    base = _contract()
    safety = goal_kind == "obligation"
    activity = goal_kind == "activity"
    contract = api.TaskContract(base.endpoints, base.clocks,
        observation_goals=(api.TransactionGoal("changed", "out", api.GoalCondition("and",
            api.GoalCondition("eq", api.GoalField("y", previous=True), 0),
            api.GoalCondition("eq", api.GoalField("y"), 173))),) if activity else (),
        transaction_monitors=() if activity else (_sequence(0, 173, safety=safety),))
    # The reference never emits the violating second symbol. Other nonzero
    # inputs must still agree: source-location search can propose them before
    # the monitor request, and a valid early mismatch would stop this category
    # control before it ever exercises the intended obligation.
    reference_source = (REFERENCE.replace('return {"y": a}',
        'return {"y": 0 if a == 173 else a}') if safety else REFERENCE)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(REFERENCE)
        task = api.admit(contract, api.realization_of_behavior_source(
            REFERENCE, contract, file="candidate.py"), reference=reference)
        handle = api.prepare_behavior(task,
            api.BehaviorConfig(path, file="candidate.py", timeout_s=90), bounded_analysis=False)
        providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                          if "candidate:" in p.name)
    else:
        rtl = RTL.replace("VALUE", "a")
        path = tmp_path / "M.sv"
        path.write_text(rtl)
        task = api.admit(contract, api.realization_of_rtl_source(rtl, top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
        providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                          if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
    assert len(providers) == 1
    # Under load a local query can spend one probe constructing before it
    # answers; leave room for two questions in each goal family.
    request = handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=12, debug=True), run_analysis=False)
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.status
    assert result.decision.authoritative
    if safety:
        # A replay-confirmed violation may stop scoring before the remaining
        # pool is exhausted. Failure evidence, not pool completion, owns zero.
        assert result.evaluation.failure_witness is not None, [
            search.as_dict() for search in result.generated.searches]
    else:
        assert result.evaluation.complete
    assert result.decision.passed is not safety
    goal = "observation:changed" if activity else "monitor:chain:hit"
    assert goal in result.evaluation.hits, [
        search.as_dict() for search in result.generated.searches]
    assert result.evaluation.catalogue[goal].family == {
        "activity": "state-activity", "sequence": "sequence", "obligation": "monitor-obligation",
    }[goal_kind]
    assert (goal in result.evaluation.safety_hits) is safety
    baseline_ids = {seed.identity_bytes for seed in request.baseline.stimuli}
    assert all(seed.depth("in") == 100 for seed in request.baseline.stimuli)
    assert any(run.stimulus.identity_bytes not in baseline_ids for run in result.evaluation.results)
    # Transaction goals are judged over native transfers, not source-event
    # callback bits. Independently reconstruct the expected second-observation
    # slots and compare with the saved, per-run monitor evidence.
    reports = dict(result.evaluation.telemetry)["monitor_violations" if safety else "monitor_hits"]
    native_hits = []
    for index, run in enumerate(result.evaluation.results):
        assert run.ran
        samples = []
        for slot, record in enumerate(run.trace.records):
            if kind == "behavior":
                samples.extend((slot, payload[0]) for payload in dict(record.outputs).get("out", ()))
            elif (payload := dict(record.accepted).get("out")) is not None:
                samples.append((slot, payload[0]))
        expected = {slot for (_, before), (slot, after) in pairwise(samples)
                    if before == 0 and after == 173}
        reported = {dict(hit)["slot"] for position, hit in reports
                    if position == index and dict(hit)["goal"] == goal}
        assert reported == expected
        native_hits.append((run.stimulus.identity_bytes, bool(expected)))
    assert not any(hit for identity, hit in native_hits if identity in baseline_ids)
    assert any(hit for identity, hit in native_hits if identity not in baseline_ids)
    search = result.generated.searches[0]
    assert goal in search.telemetry["baseline_coverage"]["unhit"]
    assert goal in search.telemetry["baseline_coverage"]["targetable"]
    assert any(goal in dict(row)["targets"] for rows in search.provenance.values() for row in rows)


@pytest.mark.parametrize("bound,expected", ((1, "bounded-unreachable"), (2, "reached")))
def test_public_bounded_analysis_keeps_baseline_score_and_adds_history_witness(tmp_path, bound, expected):
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.core.query_cache import clear_analysis_answer_cache

    from .test_behavior_public_exact_construction import _reference, _schema, _task
    from .test_command_handler import _BaselineOnly

    clear_analysis_answer_cache()
    source = 'class M:\n    def reset(self):\n        pass\n    def process(self, a):\n        return {"y": a}\n'
    path = tmp_path / "candidate.py"
    path.write_text(source)
    identity = behavior_instance.lower(source, _schema(), file="stable/finite.py", bound=bound).identity
    reference = _reference(source)
    base = _task(identity, reference=reference)
    contract = api.TaskContract(base.contract.endpoints, base.contract.clocks,
        transaction_monitors=(_sequence(0, 1),))
    task = api.admit(contract, base.realization, reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="stable/finite.py", timeout_s=90),
        api.GenerationSpec(horizon=2, queue_bound=2, reset_hold_cycles=0, drain=0),
        bounded_analysis=True, analysis_bound=bound)
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1), providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=True, reference=reference,
        feasibility_timeout_ms=10_000, query_timeout_ms=10_000,
        analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.evaluation.passed
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    outcome = result.analysis.per_goal["monitor:chain:hit"]
    assert outcome["status"] == expected
    assert result.analysis.concrete == result.evaluation.fingerprint
    if bound == 2:
        witness = next(row for row in result.analysis.evidence["witnesses"]
                       if row["witness_ref"] == outcome["support_ref"])
        assert witness["origin"] == "bounded-query"
        from behave.sim.core.audit import AuditedWitness
        assert AuditedWitness.from_record(witness["trace"]).inputs == ({"a": 0}, {"a": 1})


@pytest.mark.cosim
@pytest.mark.parametrize("kind", ("behavior", "rtl"))
@pytest.mark.parametrize("goal_kind", ("sequence", "activity", "obligation"))
@pytest.mark.parametrize("bound", (1, 2))
def test_public_analysis_classifies_transaction_history_with_native_evidence(
        tmp_path, kind, goal_kind, bound):
    """One transfer cannot hit a two-transfer goal; two may, without score edits."""
    import json
    from pathlib import Path

    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.outcome import AnalysisOutcome

    from .test_baseline_public_sequence import REFERENCE, RTL
    from .test_command_handler import _BaselineOnly

    base = _contract()
    activity = goal_kind == "activity"
    safety = goal_kind == "obligation"
    contract = api.TaskContract(base.endpoints, base.clocks,
        observation_goals=(api.TransactionGoal("changed", "out", api.GoalCondition("and",
            api.GoalCondition("eq", api.GoalField("y", previous=True), 0),
            api.GoalCondition("eq", api.GoalField("y"), 173))),) if activity else (),
        transaction_monitors=() if activity else (_sequence(0, 173, safety=safety),))
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    generation = api.GenerationSpec(horizon=7, queue_bound=bound,
                                    reset_hold_cycles=1, drain=4)
    baseline = api.build_baseline(contract, generation,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=bound))
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(REFERENCE)
        task = api.admit(contract, api.realization_of_behavior_source(
            REFERENCE, contract, file="candidate.py"), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py"),
            generation, bounded_analysis=True, analysis_bound=bound)
    else:
        source = RTL.replace("VALUE", "a")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_rtl_source(
            source, top="M", contract=contract), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), generation)
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=baseline, providers=(_BaselineOnly(),), probe_budget=0, reference=reference,
        run_analysis=True, analysis_wall_timeout_seconds=600))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.passed
    target = "observation:changed" if activity else "monitor:chain:hit"
    assert target not in result.evaluation.hits
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    image = result.analysis.as_dict()
    assert AnalysisOutcome.from_dict(image).as_dict() == image
    assert set(image["per_goal"]) == set(result.evaluation.catalogue)
    goal = image["per_goal"][target]
    assert goal["status"] == ("reached" if bound == 2 else "bounded-unreachable")
    assert result.evaluation.catalogue[target].cls == ("safety" if safety else "coverage")
    if bound == 2:
        row, = (row for row in image["evidence"]["witnesses"]
                if row["witness_ref"] == goal["support_ref"])
        assert row["origin"] == "bounded-query"
        witness = AuditedWitness.from_record(row["trace"])
        assert target in witness.hits
        # Transaction Behavior returns payloads; RTL histories retain endpoint tags.
        values = ([payload["y"] for batch in witness.outputs for payload in batch]
                  if kind == "behavior" else
                  [payload["y"] for batch in witness.outputs
                   for endpoint in batch for name, payload in endpoint.items() if name == "out"])
        assert any(before == 0 and after == 173 for before, after in pairwise(values))
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["passed"] is True
    assert persisted["authority"]["evaluation_fingerprint"] == image["concrete"]
    assert persisted["report"]["analysis"] == image
