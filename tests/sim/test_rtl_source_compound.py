"""One evaluated lvalue for typed source updates, native hits and local search."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize("operator", ["+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>", "<<<", ">>>"])
def test_compound_uses_compiler_sized_rhs(tmp_path, operator):
    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      always @(posedge clk) begin q=8'hd3; q {operator}= (a | 4'b1); end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        b = a | 1
        expected = {"+": 211+b, "-": 211-b, "*": 211*b, "/": 211//b, "%": 211 % b,
                    "&": 211 & b, "|": 211 | b, "^": 211 ^ b,
                    "<<": 211 << b, "<<<": 211 << b,
                    ">>": 211 >> b, ">>>": 211 >> b}[operator] & 255
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        assert post[names["q"]] == expected


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("body,initial", [
    ("q=8'h83; q += $signed(a); q >>>= a[1:0];", None),
    ("q=8'h83; q /= $signed(a | 4'b1); q %= -8'sd3;", None),
    ("q=8'h93; q[a[0]*4+:4] ^= a; q[1:0] += a;", None),
    ("q=0; i=0; q[i++]=a[0]; q[i+++:2] += a[1:0]; q[7:4]=i;", {"i": 9}),
    ("q=0; i=0; q[i+:2]++; i++; ++q[i+:2]; i++; q[7:4]=i;", {"i": 9}),
    ("q=a; r=1; q += (r *= a+1); q ^= r;", {"r": 0}),
])
def test_read_modify_write_matches_native_values_and_occurrences(tmp_path, body, initial):
    source = f"""module M(input clk,input [3:0] a,output logic signed [7:0] q);
      integer i; logic [7:0] r;
      always @(posedge clk) begin {body} end
    endmodule"""
    _native_agreement(tmp_path, source, initial=initial)


@pytest.mark.parametrize("body", [
    "q += f(a);",
    "q[i+++:2] += i;",
    "q[i+++:2] = i;",
])
def test_conflicting_operand_effects_are_not_assigned_an_arbitrary_order(tmp_path, body):
    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      integer i;
      function automatic [7:0] f(input [3:0] x); q=q+1; return x; endfunction
      always @(posedge clk) begin i=0; {body} end
    endmodule"""
    with pytest.raises(Refused, match="qualified evaluation order"):
        _lower(tmp_path, source)


def test_compound_read_keeps_out_of_range_refusal(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      always @(posedge clk) q[a+:2] += 1;
    endmodule"""
    with pytest.raises(Refused, match="packed-read-domain-unproved"):
        _lower(tmp_path, source)


def test_selected_increment_binds_effectful_index_once_in_ir_and_solver(tmp_path):
    # The installed simulator crashes on this uninstrumented nested ++ form.
    # Keep an independent value oracle, without calling this native qualification.
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      integer i;
      always @(posedge clk) begin
        q=0; i=0; q[i+++:2]++; ++q[i+++:2]; q[7:4]=i;
      end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    post, _, _ = _run_activation(lowered, names, {"a": 0, "q": 0, "i": 9}, enc)
    assert post[names["i"]] == 2 and post[names["q"]] == 35


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("assignment", ["+=", "="])
def test_array_address_and_helper_are_each_evaluated_once(tmp_path, assignment):
    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_scheduler import _lower as lower_machine
    from .test_rtl_source_scheduler import _names, _settle, _state, _step

    other_cell = "cells[3]+=2;" if assignment == "=" else ""
    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [7:0] cells[2:3]; integer i; logic [7:0] calls;
      function automatic [7:0] f(input [3:0] x);
        calls++; {other_cell} if(x[0]) return x+1; else return x+2;
      endfunction
      always @(posedge clk) begin
        cells[2]=10; cells[3]=20; i=2; calls=0;
        cells[i++] {assignment} f(a);
        cells[i++] += 1;
        q=cells[2]+cells[3]+i+calls;
      end
    endmodule"""
    machine, plan = lower_machine(tmp_path, source)
    names = _names(machine)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    inputs = (0, 1, 7, 14, 15)
    actual, native_hits = replay(inputs)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "cells": [0, 0], "i": 0, "calls": 0})
    for a, output, reached in zip(inputs, actual, native_hits, strict=True):
        state, _, hits, _, _ = _step(machine, state, 0, clk=0, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        state, _, _, _, _ = _step(machine, state, 0, clk=1, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        assert state[names["i"]] == 4 and state[names["calls"]] == 1
        expected = [(10 if assignment == "+=" else 0)+a+(1 if a & 1 else 2),
                    21 if assignment == "+=" else 23]
        assert state[names["cells"]] == expected
        assert state[names["q"]] == output == sum(expected)+5
        assert hits == reached


@pytest.mark.cosim
def test_public_search_compound_branch_requires_native_confirmation(tmp_path):
    from behave.sim import api

    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower as lower_machine

    source = """module M(input [15:0] a,output logic [15:0] y);
      logic [15:0] cells[2:3]; integer i;
      always_comb begin
        i=2; cells[2]=1; cells[3]=7;
        cells[i++] += a;
        if(cells[2]==16'hc35c && i==3) y=a; else y=a;
      end
    endmodule"""
    scalar = api.FiniteType(16)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", scalar, "a")], api.settled("in"), None,
                     kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", scalar, "y")], api.settled("out"), None,
                     kind=api.Kind.TRANSACTION, answers_per_transaction=1)], [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n    def reset(self):\n        pass\n'
                        b'    def process(self,a):\n        return {"y":a}\n'}, config={"class": "M"})
    path = tmp_path / "design.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    _, plan = lower_machine(tmp_path, source)
    target = _goal(source, plan, "cells[2]==16'hc35c && i==3", "true", "control-choice")
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", baseline=baseline, reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path / "search")[1:],
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=6), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    before, after, added = set(), set(), []
    baseline_ids = {seed.identity_bytes for seed in baseline.stimuli}
    for run in result.evaluation.results:
        hits = {goal for row in run.trace.records for goal, hit in row.hits if hit is True}
        after.update(hits)
        if run.stimulus.identity_bytes in baseline_ids:
            before.update(hits)
        elif target in hits:
            added.append(run)
    assert target not in before and target in after and added, result.generated.as_dict()
    assert result.analysis is None
