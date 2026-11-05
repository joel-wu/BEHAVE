"""Typed RTL division: native values/events, symbolic values and local witnesses."""

from itertools import product

import pytest

from behave.sim.symbolic import interp
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_batch

from .test_rtl_source_batch import _lower as _lower_batch
from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation
from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _boundary, _env, _goal

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


def _quotient(a, b):
    magnitude = abs(a) // abs(b)
    return -magnitude if (a < 0) != (b < 0) else magnitude


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("operator", ["/", "%"])
def test_all_small_operands_preserve_truncation_width_and_overflow(tmp_path, signed, operator):
    sign = "signed" if signed else ""
    source = f"""module M(input clk,input {sign} [3:0] a,b,
                         output reg {sign} [3:0] q);
      always @(posedge clk) if(b!=0) q=a {operator} b; else q=0;
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    domain = range(-8, 8) if signed else range(16)
    for a, b in product(domain, repeat=2):
        quotient = _quotient(a, b) if b else 0
        expected = (quotient if operator == "/" else a - quotient*b) if b else 0
        expected &= 15
        if signed and expected >= 8:
            expected -= 16
        post, _, _ = _run_activation(lowered, names, {"a": a, "b": b, "q": 0}, encoded)
        assert post[names["q"]] == expected


@pytest.mark.parametrize("operator", ["/", "%"])
@pytest.mark.parametrize("body", ["q=a OP b;", "q=a OP b; b=1;",
                                  "q=a OP b; assert(0) else $fatal;"])
def test_reachable_zero_divisor_is_not_assumed_away(tmp_path, operator, body):
    source = "module M(input clk,input [3:0] a,output reg [3:0] q); reg [3:0] b; " \
             "always @(posedge clk) begin " + body.replace("OP", operator) + " end endmodule"
    with pytest.raises(Refused) as got:
        _lower(tmp_path, source)
    assert got.value.refusal.kind == "rtl-source-ir-divisor-domain-unproved"
    assert got.value.refusal.detail["domain_proof"] == "sat"


def test_constant_nonzero_divisors_need_no_solver_proof(tmp_path, monkeypatch):
    from behave.sim.rtl import source_domains

    def unexpected(*args):
        pytest.fail("a fixed nonzero divisor needs no symbolic domain proof")

    monkeypatch.setattr(source_domains, "_prove_exact", unexpected)
    _lower(tmp_path, "module M(input clk,input signed [3:0] a,output reg [3:0] q); "
                     "always @(posedge clk) q=(a / -3) + (a % 3); endmodule")


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("operator", ["/", "%"])
@pytest.mark.parametrize("expression", ["$signed(a) OP -3", "$signed(a) OP 3",
                                       "$signed(a) OP -1", "$signed(a) OP 4'd3"])
def test_native_values_and_occurrence_hits_match_contextual_signs(tmp_path, operator, expression):
    source = "module M(input clk,input [3:0] a,output reg [7:0] q); " \
             "always @(posedge clk) begin q=" + expression.replace("OP", operator) + \
             "; if(q==1) q=19; end endmodule"
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_division_operand_helpers_execute_once_at_their_program_points(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic integer denominator(input [3:0] v);
        q=q+1;
        if(v[0]) return -3;
        return 3;
      endfunction
      always @(posedge clk) begin
        q=0;
        q=q+($signed(a)/denominator(a));
      end
    endmodule"""
    # The left q read and helper write are unsequenced: admitting the division
    # must not bypass the existing operand-order qualification.
    with pytest.raises(Refused, match="operand effects"):
        _lower(tmp_path, source)
    source = source.replace("q=q+($signed(a)/denominator(a));", "q=$signed(a)/denominator(a);")
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("operator", ["/", "%"])
def test_baseline_missed_division_goal_has_native_confirmed_local_witness(tmp_path, operator):
    expression = f"a {operator} 4'd3"
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) if(({expression})==2) q<=9; else q<=0;
    endmodule"""
    batch, plan = _lower_batch(tmp_path, source)
    goal = _goal(source, plan, expression)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    _, baseline = replay((0, 1))
    assert all(goal not in hits for hits in baseline)
    state, inputs, names = _boundary(batch, {"q": 0, "a": 0}, (True,))
    answer = query_source_batch(batch, _env(batch), state, inputs, (goal,),
                                mutable_inputs=(names["a"],), timeout_ms=1000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    candidate = answer.candidate[names["a"]]
    assert (candidate // 3 if operator == "/" else candidate % 3) == 2
    marks = interp.run_step(batch.procedure, {**inputs, **answer.candidate}, state)[2]
    assert goal in {public for mark, _, public in batch.occurrences if mark in marks}
    _, after = replay((candidate,))
    assert goal in after[0]


@pytest.mark.cosim
@pytest.mark.parametrize("operator,kind,divisor", [("/", "divide", 97), ("%", "remainder", 104729)])
@pytest.mark.parametrize("wrong", [False, True], ids=["correct", "searched-mismatch"])
def test_public_search_division_witness_is_replayed_and_scored(tmp_path, operator, kind, divisor, wrong):
    import json

    from behave.sim import api
    from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

    target = 4660
    integer = api.FiniteType(32)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", integer, "a")], api.settled("in"),
                     None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", integer, "y")], api.settled("out"),
                     None, kind=api.Kind.TRANSACTION, answers_per_transaction=1)], [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))],
        operation_rules=(api.OperationRule(kind, api.OperationGoal("target",
            api.GoalCondition("eq", api.GoalField("result"), target))),))
    python_op = "//" if operator == "/" else "%"
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": ("class M:\n def reset(self): pass\n"
                         f" def process(self,a): return {{'y': a {python_op} {divisor}}}\n").encode()},
        config={"class": "M"})
    source = f"""module M(input [31:0] a,output logic [31:0] y);
      always_comb begin
        y=a {operator} 32'd{divisor};
        if(y==32'd{target}) y=y+32'd{int(wrong)};
      end
    endmodule"""
    path = tmp_path / "design.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    baseline = api.build_baseline(contract)
    assert baseline.policy.case_limit == 50
    assert all(seed.depth("in") == 100 for seed in baseline.stimuli)
    for seed in baseline.stimuli:
        for _, rows in seed.queues:
            for a, in rows:
                assert (a // divisor if operator == "/" else a % divisor) != target
    providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "search")
                      if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=baseline, reference=reference,
        providers=providers, search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True),
        run_analysis=False))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed is not wrong
    goal, = [g.id for g in handle.frontend_catalogue.values() if g.family == "operation-event"]
    search, = result.generated.searches
    feedback = search.telemetry["baseline_coverage"]
    assert goal in feedback["remaining"] and goal not in feedback["hits"]
    assert search.telemetry["probes"] > 0 and not search.telemetry["refusals"]
    baseline_ids = {seed.identity_bytes for seed in baseline.stimuli}
    witnesses = [run for run in result.evaluation.results if run.stimulus.identity_bytes not in baseline_ids
                 and any(name == goal and hit is True for row in run.trace.records for name, hit in row.hits)]
    assert witnesses
    if wrong:
        assert result.evaluation.failure_witness and result.evaluation.mismatch_count
    else:
        assert result.evaluation.complete
