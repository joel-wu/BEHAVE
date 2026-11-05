"""Latency cells, independent native deques and actual query/replay controls."""

from dataclasses import replace
from itertools import product

import pytest
import z3

from behave.sim import api
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.latency_product import LatencyProduct
from behave.sim.core.observation_symbolic import condition_terms, logical_scalar
from behave.sim.core.ready_valid_product import ConcreteAlgebra
from behave.sim.engines.probing import Outcome
from behave.sim.rtl.task_symbolic import Z3Algebra

from .test_latency_goals import _declared, _hits, _record, _report, _rule
from .test_transaction_goal_queries import _lower, _probe


def _read(value):
    value = z3.simplify(value)
    return z3.is_true(value) if z3.is_bool(value) else value.as_long()


@pytest.mark.parametrize("keyed,order,basis", tuple(product((False, True), ("start-first", "end-first"),
    (api.LatencyTimebase("clock", "clk"), api.LatencyTimebase("transaction", "in")))))
def test_finite_cells_equal_native_pairing_over_short_histories(keyed, order, basis):
    rule = _rule(keyed=keyed, order=order, basis=basis)
    binding = LatencyProduct(_declared(rule), artifact_kind="rtl")
    symbolic_algebra = Z3Algebra()
    entries = ((None, None), (7, None), (9, None), (None, 7), (None, 9), (7, 7), (7, 9))
    for history in product(entries, repeat=3):
        native = _hits(_report(rule, [_record(a, b) for a, b in history]))
        states = [binding.initial(algebra) for algebra in (ConcreteAlgebra, symbolic_algebra)]
        found = [set(), set()]
        for slot, (a, b) in enumerate(history):
            for index, algebra in enumerate((ConcreteAlgebra, symbolic_algebra)):
                samples = {name: ((algebra.bool(value is not None),
                                   ((value or 0) if index == 0 else z3.BitVecVal(value or 0, 10),)),)
                           for name, value in (("in", a), ("out", b))}
                states[index], hits = binding.advance(states[index], samples, algebra,
                    condition=condition_terms if index else None, logical=logical_scalar if index else None,
                    ticks=("clk",))
                found[index].update((slot, goal.rsplit(":", 1)[1]) for goal, hit in hits.items()
                                    if (_read(hit) if index else hit))
            assert binding.image(states[1], _read) == states[0]
        assert found[0] == found[1] == native, history


@pytest.mark.parametrize("cancel", (False, True))
def test_unknown_cancel_and_control_only_global_time(cancel):
    rule = _rule(basis=api.LatencyTimebase("global-time"), reset_domains=("clk",) if cancel else ())
    binding = LatencyProduct(_declared(rule), artifact_kind="rtl")
    algebra = Z3Algebra()
    state = binding.initial(algebra)
    for stamp, a, b, reset in ((0, None, None, False), (1, 7, None, False),
                                (2, None, None, True), (3, None, 7, False), (8, 9, 9, False)):
        # Unknown is a sampled None only in the first entry; not a missing clock.
        samples = {"in": ((z3.BoolVal(stamp == 0 or a is not None), (a,)),),
                   "out": ((z3.BoolVal(b is not None), (b,)),)}
        state, hits = binding.advance(state, samples, algebra, condition=condition_terms,
            logical=logical_scalar, resets={"clk": z3.BoolVal(reset)}, time=stamp)
        reached = {name for name, value in hits.items() if _read(value)}
        assert reached == ({"latency:response:zero", "latency:response:any"} if stamp == 8 and cancel else set())


@pytest.mark.parametrize("identity", (7, 255))
def test_latency_local_search_uses_native_prefix_and_replay_confirms(identity):
    rule = _rule(basis=api.LatencyTimebase("transaction", "in"))
    _contract, instance, binding = _lower(
        f'        return {{"y": {identity}}} if a == 9 else None\n', latencies=(rule,), bound=2)
    goal = "latency:response:one"
    assert goal not in instance.replay([{"a": 0}, {"a": 0}]).hits
    assert _probe(instance, binding, {}, goal).outcome is Outcome.LOCAL_UNSAT
    previous = {}
    assert not binding.observe(previous, {"a": identity}, ())
    answer = _probe(instance, binding, previous, goal, position=1)
    assert answer.outcome is Outcome.SAT and answer.candidate == {"a": 9}
    witness = [{"a": identity}, answer.candidate]
    assert goal in instance.replay(witness).hits
    assert instance.audit((witness,)).status is AuditCheckStatus.AGREED
    binding.observe(previous, {"a": None}, ())
    assert _probe(instance, binding, previous, goal, position=2).outcome is Outcome.UNSUPPORTED


@pytest.mark.parametrize("bound", (1, 2))
def test_latency_whole_history_does_not_promote_local_unsat(bound):
    rule = _rule(basis=api.LatencyTimebase("transaction", "in"))
    _contract, instance, _binding = _lower(
        '        return {"y": 7} if a == 9 else None\n', latencies=(rule,), bound=bound)
    goal = "latency:response:one"
    result = instance.query((goal,), bound, timeout_ms=10_000, workers=1)
    assert not result.undecided
    if bound == 1:
        assert result.unreachable == {goal}
    else:
        witness = result.reachable[goal]
        assert goal in instance.replay(witness).hits
        assert instance.audit((witness,)).status is AuditCheckStatus.AGREED


def test_latency_audit_rejects_invented_symbolic_hit():
    from behave.sim.behavior import residual

    rule = _rule(basis=api.LatencyTimebase("transaction", "in"))
    _contract, instance, binding = _lower('        return {"y": a}\n', latencies=(rule,), bound=1)
    run = residual.unroll(instance.proc, instance.env, 1, plan=instance._position_plan, transactions=binding)
    run.transaction_components["latency:response:one"] = (z3.BoolVal(True),)
    instance._prepared_transaction_audit = run
    assert instance.audit(([{"a": 7}],)).status is AuditCheckStatus.INCONSISTENT


def test_b_reset_latency_stays_native_instead_of_inventing_query_context():
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.core.goal_provider import EXACT_BOUNDED_HIT, LOCAL_GUIDANCE
    from behave.sim.goal_policy import evaluation_goal_policy

    rule = _rule(basis=api.LatencyTimebase("transaction", "in"), reset_domains=("clk",))
    assert rule.applicable("behavior") and not rule.queryable("behavior")
    assert not LatencyProduct(_declared(rule), artifact_kind="behavior").goals
    assert LatencyProduct(_declared(replace(rule, reset_domains=())), artifact_kind="behavior").goals
    policy = evaluation_goal_policy(_declared(rule), CatalogueManifest(()), artifact_kind="behavior")
    provider = next(p for p in policy.enabled_providers() if p.schema.name == "latency")
    assert len(provider.catalogue) == 5
    assert not provider.has(LOCAL_GUIDANCE) and not provider.has(EXACT_BOUNDED_HIT)


def test_atomic_behavior_latency_uses_transactions_not_slot_numbers():
    from behave.sim.behavior.atomic_exact import build_atomic_behavior
    from tests.support.atomic_behavior import _contract, _schedule

    from .test_behavior_atomic_exact import _facts
    from .test_behavior_atomic_transactions import IDENTITY

    base = _contract()
    rule = api.LatencyGoal("cross", api.LatencyEvent("a_out", ("oa",)), api.LatencyEvent("b_out", ("ob",)),
        (api.LatencyBin("zero", 0, 0), api.LatencyBin("one", 1, 1)), api.LatencyTimebase("transaction", "a_in"))
    contract = api.TaskContract(base.endpoints, base.clocks, latency_goals=(rule,))
    instance = build_atomic_behavior(IDENTITY, "atomic_latency.py", contract, _schedule(n_slots=1),
                                     task_contract=contract.digest, construction=_facts({}))
    traces = []
    for a, b in product(range(4), repeat=2):
        rows = [{"a.__invoke__": 1, "a.a": a, "b.__invoke__": 1, "b.b": b}]
        hits = set(instance.replay(rows).hits) & set(instance.observations.catalogue)
        assert hits == ({"latency:cross:zero"} if a == b else set())
        traces.append(rows)
    assert instance.audit(traces).status is AuditCheckStatus.AGREED
    answer = instance.query(("latency:cross:zero", "latency:cross:one"), 1, timeout_ms=10_000, workers=1)
    assert not answer.undecided and answer.unreachable == {"latency:cross:one"}
    assert "latency:cross:zero" in instance.replay(answer.reachable["latency:cross:zero"]).hits


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
def test_public_baseline_missed_latency_is_solved_and_replayed(tmp_path, kind, record_property):
    import json

    from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

    from .test_baseline_public_sequence import REFERENCE, RTL

    rule = api.LatencyGoal("rare", api.LatencyEvent("in", condition=api.GoalCondition(
        "eq", api.GoalField("a"), 173)), api.LatencyEvent("out"), (api.LatencyBin("instant", 0, 0),),
        api.LatencyTimebase("transaction", "in") if kind == "behavior" else api.LatencyTimebase("clock", "clk"))
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
        providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                          if "candidate:" in p.name)
    else:
        path = tmp_path / "M.sv"
        path.write_text(RTL.replace("VALUE", "a"))
        task = api.admit(contract, api.realization_of_rtl_source(path.read_text(), top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
        providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                          if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
    assert len(providers) == 1
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, debug=True), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    record_property("search", json.dumps(result.generated.searches[0].as_dict()))
    assert result.evaluation.complete and result.decision.authoritative and result.decision.passed
    goal = "latency:rare:instant"
    assert goal in result.generated.searches[0].telemetry["baseline_coverage"]["unhit"]
    assert goal in result.evaluation.hits
    assert any(goal in dict(row)["targets"] for rows in result.generated.searches[0].provenance.values() for row in rows)
