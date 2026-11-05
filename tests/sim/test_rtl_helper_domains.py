"""Universal helper lemmas must not inherit a particular call's arguments."""

import pytest

from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.rtl import source_domains

from .test_rtl_source_ir import _lower, _native_agreement

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]

SAFE = """function automatic [3:0] f(input [3:0] n,d);
  if(d==0) return 0;
  return n/d;
endfunction"""


def _module(body, helper=SAFE, declarations=""):
    return ("module M(input clk,input [3:0] a,output logic [7:0] q);\n"
            + declarations + helper + "\nalways @(posedge clk) begin " + body + " end endmodule")


@pytest.mark.eda("verilator", "make")
def test_one_all_argument_proof_keeps_distinct_native_call_occurrences(tmp_path, monkeypatch):
    calls = []
    original = source_domains.prove_source_domains

    def record(procedure, state_ranges, *args, **kwargs):
        calls.append(dict(state_ranges))
        return original(procedure, state_ranges, *args, **kwargs)

    monkeypatch.setattr(source_domains, "prove_source_domains", record)
    source = _module("q=f(a,3)+f(a,0)+f(15,a);")
    _native_agreement(tmp_path, source)
    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert all(bound == ranges.Range(0, 15) for bound in calls[0].values())


@pytest.mark.eda("verilator", "make")
def test_unproved_universal_helper_keeps_valid_caller_precondition(tmp_path):
    helper = "function automatic [3:0] f(input [3:0] n,d); return n/d; endfunction"
    _native_agreement(tmp_path, _module("if(a!=0) q=f(9,a); else q=0;", helper))


@pytest.mark.parametrize("body,declarations,helper", [
    ("q=f(a,3)+f(a,0);", "", "function automatic [3:0] f(input [3:0] n,d); return n/d; endfunction"),
    ("d=3; q=f(a); d=0; q=f(a);", "logic [3:0] d;",
     "function automatic [3:0] f(input [3:0] n); return n/d; endfunction"),
    ("q=f(a/(a-a),3);", "", SAFE),
])
def test_caller_values_globals_and_argument_failures_are_not_assumed_away(
        tmp_path, body, declarations, helper):
    with pytest.raises(Refused) as got:
        _lower(tmp_path, _module(body, helper, declarations))
    assert got.value.refusal.kind == "rtl-source-ir-divisor-domain-unproved"
    assert got.value.refusal.detail["domain_proof"] == "sat"


@pytest.mark.eda("verilator", "make")
def test_nested_helpers_keep_their_original_outputs_and_event_contexts(tmp_path):
    helper = SAFE + "\nfunction automatic [3:0] g(input [3:0] n); return f(n,3)+f(n,0); endfunction"
    _native_agreement(tmp_path, _module("q=g(a)+f(a,2);", helper))


@pytest.mark.eda("verilator", "make")
def test_correlated_helper_accesses_use_the_same_domain_path(tmp_path):
    helper = """function automatic [3:0] f(input [3:0] n,d);
      integer x,y;
      logic [3:0] v;
      v=0;
      if(n[0]) begin x=3; y=0; end else begin x=0; y=3; end
      v[x+y]=n[1];
      return v;
    endfunction"""
    _native_agreement(tmp_path, _module("q=f(a,1)+f(a,2);", helper))


def test_proofs_are_not_reused_between_source_compilations(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _lower(first, _module("q=f(a,0);"))
    with pytest.raises(Refused, match="divisor"):
        _lower(second, _module("q=f(a,0);", SAFE.replace("if(d==0) return 0;", "")))


def test_first_guarded_loop_call_does_not_qualify_later_calls(tmp_path):
    helper = """function automatic [3:0] f(input [2:0] n);
      integer i;
      f=0;
      for(i=0;i<3;i=i+1) if(i<n) f=f+9/(2-i);
    endfunction"""
    with pytest.raises(Refused) as got:
        _lower(tmp_path, _module("q=f(1)+f(3);", helper))
    assert got.value.refusal.kind == "rtl-source-ir-divisor-domain-unproved"
    assert got.value.refusal.detail["domain_proof"] == "sat"


def test_helper_and_activation_attempts_share_the_existing_time_allowance(monkeypatch):
    from types import SimpleNamespace

    now, calls = [0.0], []
    monkeypatch.setattr(source_domains, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def spend(*args, timeout_ms, **kwargs):
        calls.append(timeout_ms)
        now[0] += 0.01

    monkeypatch.setattr(source_domains, "prove_source_domains", spend)
    budget = source_domains.DomainProofBudget(30)
    budget.prove(None, {}, {})
    budget.prove(None, {}, {})
    budget.prove(None, {}, {})
    assert calls == [30, 20, 10]
