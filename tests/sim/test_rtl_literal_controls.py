"""Typed constant folding retains source goals and exact bit-vector values."""

import pytest

from behave.sim.symbolic import ir
from behave.sim.engines.rtl.source_session import _constant_false_source_goals
from behave.sim.rtl.source_integral import IntegralType

from .test_rtl_net_views import _check_native_and_queries
from .test_rtl_source_query import _goal


@pytest.mark.parametrize('width', [1, 2, 4, 8, 32])
@pytest.mark.parametrize('signed', [False, True])
def test_literal_wrap_matches_fixed_width_arithmetic(width, signed):
    kind = IntegralType(width, signed)
    for value in (-513, -256, -129, -128, -17, -1, 0, 1, 15, 127, 128, 255, 256, 513):
        bits = value % (2 ** width)
        expected = bits - 2 ** width if signed and bits >= 2 ** (width - 1) else bits
        literal = kind.wrap(ir.Lit(value))
        assert isinstance(literal, ir.Lit)
        assert type(literal.value) is int and literal.value == expected
    assert isinstance(kind.wrap(ir.Var('a')), ir.Op)


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('expression,truth', [
    ('0', False), ('32\'d0', False), ("2'(4'b0100)", False),
    ('K', False), ("4'sh8", True), ('1', True), ('a[0]', None),
])
def test_typed_constant_control_is_excluded_without_erasing_its_goal(tmp_path, expression, truth):
    source = f'''module M(input clk,input [3:0] a,output logic [3:0] q);
      localparam signed [3:0] K=0;
      always @(posedge clk) if(a && {expression}) q<=1; else q<=0;
    endmodule'''
    machine, plan = _check_native_and_queries(tmp_path, source)
    yes = _goal(source, plan, expression, family='boolean-expression')
    no = _goal(source, plan, expression, event='false', family='boolean-expression')
    blocked = _constant_false_source_goals(machine)
    assert (yes in blocked) is (truth is False)
    assert (no in blocked) is (truth is True)
    # Selection facts do not delete occurrences or manufacture native hits.
    assert {yes, no} <= {goal for _mark, _key, goal in machine.occurrences}
