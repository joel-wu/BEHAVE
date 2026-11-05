"""Dynamic array rows do not erase disjoint constant-column destinations."""

import pytest

from behave.sim.symbolic import ir, ranges
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_ir import BoundLValue, BoundSelection, _Lowerer
from behave.sim.rtl.source_values import ArrayType

from .test_rtl_bounded_source_loops import _assert_closure, _lower


@pytest.mark.parametrize('rows,columns,width', [(2,2,4), (3,5,1), (7,3,9)])
@pytest.mark.parametrize('column', [0,1])
def test_dynamic_array_mask_covers_exactly_each_possible_selected_cell(rows, columns, width, column):
    element = IntegralType(width, False)
    row = ArrayType(0, columns-1, element)
    array = ArrayType(0, rows-1, row)
    lowerer = object.__new__(_Lowerer)
    lowerer.loop_ranges, lowerer.snapshot_ranges = {}, {}
    lowerer.ranges = {'row': ranges.Range(0,rows-1)}
    bound = BoundLValue('x', array, (
        BoundSelection(ir.Var('row'), array, row),
        BoundSelection(ir.Lit(column), row, element)), {})
    expected = sum(((1<<width)-1) << ((r*columns+column)*width) for r in range(rows))
    assert lowerer.lvalue_mask(bound) == expected


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('layout', ['[0:1][0:1]', '[1:0][1:0]'])
@pytest.mark.parametrize('other_row', ['a&1', '(a>>1)&1'])
def test_nested_copyouts_keep_every_native_and_queried_goal(
        tmp_path, record_property, layout, other_row):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      logic [3:0] cells{layout};
      task automatic pair(input [3:0] v, output logic [3:0] x,y);
        x=v; y=15-v; if(v==7) y=v+1; if(1'b0) y=99;
      endtask
      always @(posedge clk) begin
        pair(a,cells[a&1][0],cells[{other_row}][1]);
        q={{cells[{other_row}][1],cells[a&1][0]}};
      end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: a+16*(15-a))


@pytest.mark.eda('slang')
@pytest.mark.solver
@pytest.mark.parametrize('second', ['cells[0][0]', 'cells[a&1][0]', 'cells[0][(a>>1)&1]'])
def test_possible_nested_alias_is_still_refused(tmp_path, second):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      logic [3:0] cells[0:1][0:1];
      task automatic pair(output logic [3:0] x,y); x=1; y=2; endtask
      always @(posedge clk) begin pair(cells[a&1][0],{second}); q=0; end
    endmodule'''
    with pytest.raises(Refused, match='operand effects'):
        _lower(tmp_path, source)
