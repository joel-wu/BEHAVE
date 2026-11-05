"""Recomputed pure operand snapshots retain loop-invariant source bounds."""

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.rtl.source_ir import _Lowerer


def test_expand_only_registered_snapshots_written_in_this_loop():
    first = ir.Assign('first', ir.Op('add', ir.Var('n'), ir.Lit(1)))
    second = ir.Assign('second', ir.Op('fdiv', ir.Var('first'), ir.Lit(2)))
    snapshots = {c.var: c for c in (first, second)}
    value = ir.Var('second')
    expanded = _Lowerer.invariant_loop_limit(value, [first, second], snapshots)
    assert expanded is not None
    assert interp._Interp({'n': 5}).value(expanded) == 3
    # An earlier capture is a value, not permission to read today's n again.
    assert _Lowerer.invariant_loop_limit(value, [ir.Assign('n', ir.Lit(0))], snapshots) is value
    # Ordinary mutable assignments are not compiler-owned operand captures.
    assert _Lowerer.invariant_loop_limit(value, [first, second]) is None


@pytest.mark.parametrize('write', [
    ir.Assign('n', ir.Lit(9)),
    ir.Store('n', ir.Lit(0), ir.Lit(9)),
    ir.For('n', ir.Lit(3), ir.Skip()),
    ir.Assign('captured', ir.Lit(9)),
])
def test_snapshot_with_mutable_dependency_keeps_universal_cutoff(write):
    capture = ir.Assign('captured', ir.Var('n'))
    body = ir.If(ir.Var('enabled'), write, ir.Skip())
    assert _Lowerer.invariant_loop_limit(
        ir.Var('captured'), [capture, body], {'captured': capture}) is None


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_dynamic_helper_domain_obligations_are_still_checked_at_actual_call(tmp_path):
    from behave.sim.core.refusal import Refused

    from .test_rtl_bounded_source_loops import _lower

    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, input integer n);
        integer i; f=0;
        for(i=0;i<(n+1)/2;i++) f=f+v/(n-5);
      endfunction
      always @(posedge clk) q=f(a,5);
    endmodule'''
    with pytest.raises(Refused, match='rtl-source-ir-divisor-domain-unproved'):
        _lower(tmp_path, source)


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('form', ['for', 'while', 'do'])
@pytest.mark.parametrize('observed_test', [False, True])
def test_compound_bound_keeps_all_goals_and_condition_effects(
        tmp_path, record_property, form, observed_test):
    from .test_rtl_bounded_source_loops import _assert_closure

    condition = 'i<(n+1)/2'
    if observed_test:
        condition = f'(seen=seen+1)>0 && {condition}'
    body = "f=f+1; if(v==7 && i==2) f=f+10; if(1'b0) f=99;"
    if form == 'for':
        loop = f'for(i=0;{condition};i++) begin {body} end'
    elif form == 'while':
        loop = f'i=0; while({condition}) begin {body} i++; end'
    else:
        loop = f'i=0; do begin {body} i++; end while({condition});'
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, input integer n);
        integer i,seen; f=0; seen=0; {loop} f=f+16*seen;
      endfunction
      always @(posedge clk) q=f(a,5);
    endmodule'''
    tests = (3 if form == 'do' else 4) if observed_test else 0
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 3 + (10 if a == 7 else 0) + 16*tests)
