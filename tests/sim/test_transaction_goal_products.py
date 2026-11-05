"""Finite transaction history is present in query state, replay and audit."""

from functools import partial
from types import SimpleNamespace

import pytest
import z3

from behave.sim import api
from behave.sim.core.observation_monitor import TransactionMonitor
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.observation_symbolic import history_image, logical_payload, logical_zero, predicate_term
from behave.sim.core.ready_valid_product import ConcreteAlgebra
from behave.sim.core.stimulus import SlotRecord
from behave.sim.rtl.symbolic_state import read_component
from behave.sim.rtl.task_symbolic import Z3Algebra

from .test_baseline_public_sequence import REFERENCE, _contract

RTL = '''module M(input wire clk,rst,in_valid,out_ready,input wire [7:0] a,
                 output wire in_ready,out_valid,output wire [7:0] y);
    assign in_ready=out_ready;
    assign out_valid=in_valid;
    assign y=a ^ 8'd85;
endmodule'''


def _query_contract(*monitors):
    base = _contract()
    return api.TaskContract(base.endpoints, base.clocks, transaction_monitors=monitors, observation_goals=[
        api.TransactionGoal("seven", "out", api.GoalCondition("eq", api.GoalField("y"), 7)),
        api.TransactionGoal("pair", "out", api.GoalCondition("and",
            api.GoalCondition("eq", api.GoalField("y", previous=True), 7),
            api.GoalCondition("eq", api.GoalField("y"), 9))),
        api.TransactionGoal("outside", "out", api.GoalCondition("eq", api.GoalField("y"), 256)),
    ])


@pytest.mark.eda
@pytest.mark.isolation
def test_public_search_uses_native_rtl_predicates_without_reference_guidance(tmp_path):
    import json

    from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

    contract = _query_contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.replace('return {"y": a}', 'return {"y": a ^ 85}').encode()},
        config={"class": "M"})
    path = tmp_path / "M.sv"
    path.write_text(RTL)
    task = api.admit(contract, api.realization_of_rtl_source(RTL, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
    providers = tuple(provider for provider in handle.generation_providers(reference,
        workdir=tmp_path / "queries") if provider.policy == RTL_LOCAL_GUIDANCE_POLICY)
    assert len(providers) == 1
    request = handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True), run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict()))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.evaluation.complete and result.decision.authoritative and result.decision.passed
    assert "observation:seven" in result.evaluation.hits
    assert "observation:outside" not in result.evaluation.hits
    baseline = {seed.identity_bytes for seed in request.baseline.stimuli}
    assert all(run.stimulus.depth("in") == 100 for run in result.evaluation.results
               if run.stimulus.identity_bytes in baseline)
    assert any(run.stimulus.identity_bytes not in baseline for run in result.evaluation.results)
    feedback = result.generated.searches[0].telemetry["baseline_coverage"]
    assert "observation:seven" in feedback["unhit"]
    assert "observation:seven" in feedback["targetable"]


@pytest.mark.parametrize("width,signed", ((1, False), (1, True), (4, False), (4, True), (64, True)))
def test_physical_signedness_and_history_match_native_predicates(width, signed):
    kind = api.FiniteType(width, signed=signed)
    endpoint = api.Endpoint("out", "out", [api.Field("x", kind, "x")], api.settled("out"), None)
    rule = api.TransactionGoal("rise", "out", api.GoalCondition("gt",
        api.GoalField("x"), api.GoalField("x", previous=True)))
    contract = api.TaskContract([endpoint], [], observation_goals=[rule])
    product = TransactionProduct(contract)
    concrete = product.initial(ConcreteAlgebra)
    algebra = Z3Algebra()
    symbolic = product.initial(algebra, zero=logical_zero)
    low, high = kind.bounds
    for value, fires in ((low, True), (high, False), (high, True), (low, True)):
        concrete, hits = product.advance(concrete, {"out": ((fires, (value,)),)}, ConcreteAlgebra)
        physical = z3.BoolVal(bool(value)) if width == 1 else z3.BitVecVal(value, width)
        symbolic, terms = product.advance(symbolic,
            {"out": ((z3.BoolVal(fires), logical_payload(endpoint, (physical,))),)},
            algebra, predicate=predicate_term)
        assert {name: z3.is_true(z3.simplify(term)) for name, term in terms.items()} == hits
        solver = z3.Solver()
        assert solver.check() == z3.sat
        assert product.image(history_image(symbolic), partial(read_component, solver.model())) == concrete


def test_finite_history_matches_real_transfers_across_stall_and_reset():
    endpoint = api.Endpoint("out", "out", [api.Field("x", api.FiniteType(8), "x")],
                            api.ready_valid("out", "v", "r"), "clk")
    rule = api.TransactionGoal("pair", "out", api.GoalCondition("and",
        api.GoalCondition("eq", api.GoalField("x", previous=True), 5),
        api.GoalCondition("eq", api.GoalField("x"), 9)))
    contract = api.TaskContract([endpoint], [api.ClockDomain("clk", "clk", "rst")],
                                observation_goals=[rule])
    records = (SlotRecord(accepted={"out": (5,)}),
               SlotRecord(accepted={"out": None}, outputs={"x": 9, "v": 1, "rst": 1}),
               SlotRecord(accepted={"out": (9,)}))
    report = TransactionMonitor(contract, behavior=False).check(None, SimpleNamespace(records=records))
    product, observed = TransactionProduct(contract), []
    state = product.initial(ConcreteAlgebra)
    for slot, record in enumerate(records):
        state, hits = product.advance(state, product.record_samples(record), ConcreteAlgebra)
        observed.extend((name, slot) for name, hit in hits.items() if hit)
    assert observed == [(hit.goal, hit.slot) for hit in report.hits] == [("observation:pair", 2)]

