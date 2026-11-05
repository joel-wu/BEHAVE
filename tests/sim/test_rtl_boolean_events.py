"""Evaluated Boolean terms: native short circuit and original RTL predicates."""

import pytest

from behave.sim.rtl.native_events import prepare_native_events
from behave.sim.rtl.source_projection import project_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_encoding import _prepared
from .test_rtl_source_expressions import _driver

pytestmark = [pytest.mark.eda("slang", "yosys", "verilator", "make"), pytest.mark.solver]

# This suite checks Boolean/control predicates against the original RTLIL
# relation. Statement-entry goals have their own native/source-IR oracles in
# test_rtl_source_locations; they must not change this suite's denominator.
CONTROL_SCHEMAS = frozenset({"control-choice", "boolean-expression"})


def _terms(*values):
    return {(name, "true" if value else "false") for name, value in values}


def _mixed(a):
    hits = _terms(("a[0]", a & 1))
    if a & 1:
        hits |= _terms(("a[1]", a & 2))
    left = bool(a & 1 and a & 2)
    if not left:
        hits |= _terms(("a[2]", a & 4))
        if a & 4:
            hits |= _terms(("a[3]", a & 8))
    return int(left or bool(a & 4 and a & 8)), hits


CASES = {
    "and-vector": ("q=a[3:2] && a[1:0];", lambda a: (
        int(bool(a >> 2) and bool(a & 3)),
        _terms(("a[3:2]", a >> 2)) | (_terms(("a[1:0]", a & 3)) if a >> 2 else set()))),
    "or-vector": ("q=a[3:2] || a[1:0];", lambda a: (
        int(bool(a >> 2) or bool(a & 3)),
        _terms(("a[3:2]", a >> 2)) | (_terms(("a[1:0]", a & 3)) if not a >> 2 else set()))),
    "mixed": ("q=(a[0] && a[1]) || (a[2] && a[3]);", _mixed),
    "program-point": ("q=a+1; q=q[0] && a[1];", lambda a: (
        int(not a & 1 and bool(a & 2)),
        _terms(("q[0]", not a & 1)) | (_terms(("a[1]", a & 2)) if not a & 1 else set()))),
    "guarded": ("if(a[3]) q=a[0] && a[1]; else q=0;", lambda a: (
        int(bool(a & 8 and a & 1 and a & 2)),
        (_terms(("a[0]", a & 1)) | (_terms(("a[1]", a & 2)) if a & 1 else set()))
        if a & 8 else set())),
    "if-predicate": ("if(a[0] && a[1]) q=5; else q=3;", lambda a: (
        5 if a & 1 and a & 2 else 3,
        _terms(("a[0]", a & 1)) | (_terms(("a[1]", a & 2)) if a & 1 else set()))),
    "in-conditional": ("q=a[3] ? (a[0] && a[1]) : 0;", lambda a: (
        int(bool(a & 8 and a & 1 and a & 2)),
        (_terms(("a[0]", a & 1)) | (_terms(("a[1]", a & 2)) if a & 1 else set()))
        if a & 8 else set())),
    "conditional-in-rhs": ("q=a[0] && (a[1] ? a[2] : a[3]);", lambda a: (
        int(bool(a & 1 and a & (4 if a & 2 else 8))),
        _terms(("a[0]", a & 1)) | (
            _terms(("a[1] ? a[2] : a[3]", a & (4 if a & 2 else 8))) if a & 1 else set()))),
    "conditional-in-lhs": ("q=(a[0] ? a[1] : a[2]) && a[3];", lambda a: (
        int(bool(a & (2 if a & 1 else 4) and a & 8)),
        _terms(("a[0] ? a[1] : a[2]", a & (2 if a & 1 else 4))) | (
            _terms(("a[3]", a & 8)) if a & (2 if a & 1 else 4) else set()))),
    "nested-group": ("q=!(a[0] && a[1]) || a[2];", lambda a: (
        int(not (a & 1 and a & 2) or bool(a & 4)),
        _terms(("!(a[0] && a[1])", not (a & 1 and a & 2)), ("a[0]", a & 1))
        | (_terms(("a[1]", a & 2)) if a & 1 else set())
        | (_terms(("a[2]", a & 4)) if a & 1 and a & 2 else set()))),
}


@pytest.mark.parametrize("context", ["loop", "helper"])
def test_repeated_boolean_terms_keep_native_context_without_guessing_symbolic_copies(tmp_path, context):
    from behave.sim.core.refusal import Refusal

    helper = "function automatic logic f(input [3:0] v); f=v[0] && v[1]; endfunction"
    body = ("q=0; for(int i=0;i<4;i++) q=q+(a[i] && a[(i+1)%4]);" if context == "loop" else
            "if(a[3]) q=f(a); else q=f(~a);")
    source = f"""module M(input clk,rst,input [3:0] a,output reg [7:0] q);
      {helper if context == 'helper' else ''}
      always @(posedge clk) begin {body} end endmodule"""
    plan, original, annotated, execution = _prepared(tmp_path, source)
    project_source(plan, execution.native, filename="design.sv").require_complete()
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100, source_goals=True)
    plain = _trace(tmp_path / "plain", source, collector, _driver())
    native = _trace(tmp_path / "native", plan.native().text, collector, _driver(), native=True)
    call_sites = sorted({event.calls for event in plan.events if event.role.startswith("boolean-")})
    assert len(call_sites) == (2 if context == "helper" else 1)
    assert len(native) == len(plain) == 16
    for a, ((plain_q, _, _), (q, bits, callbacks)) in enumerate(zip(plain, native, strict=True)):
        v = a if a & 8 else (~a & 15)
        expected_q = (sum(bool(a & (1 << i) and a & (1 << ((i+1)%4))) for i in range(4))
                      if context == "loop" else int(bool(v & 1 and v & 2)))
        assert q == plain_q == expected_q
        # Every unrolled invocation executes at most once in this fixture.
        # Include location callbacks in the transport check, but retain the
        # independent Boolean/control count rather than hardcoding new IDs.
        assert callbacks == bits.bit_count()
        nonlocations = sum(bool(bits >> index & 1) for index, invocation in enumerate(plan.invocations)
                           if plan.events[invocation.source_event].role != "location")
        assert nonlocations == (6 + a.bit_count() if context == "loop" else 3 + bool(v & 1))
        for index, invocation in enumerate(plan.invocations):
            event = plan.events[invocation.source_event]
            if not event.role.startswith("boolean-"):
                continue
            if context == "loop":
                i, = invocation.iterations
                left, right = bool(a & (1 << i)), bool(a & (1 << ((i+1)%4)))
                active = True
            else:
                left, right = bool(v & 1), bool(v & 2)
                active = event.calls == call_sites[0 if a & 8 else 1]
            is_left = event.logical_path[-1].port == "A"
            expected_hit = active and (is_left or left) and ((left if is_left else right) == (event.choice == "true"))
            assert bool(bits >> index & 1) == expected_hit
            assert isinstance(execution.encoding.predicate_for(index, object()), Refusal)

