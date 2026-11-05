"""Bit-width termination proofs preserve source execution and every goal."""

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_while import ShiftProgress

from .test_rtl_bounded_source_loops import _assert_closure, _lower

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('width', [1, 3, 5, 8, 32, 64])
@pytest.mark.parametrize('amount', [1, 2, 3, 65])
def test_type_bound_is_sufficient_without_assuming_all_iterations_execute(width, amount):
    largest = (1 << width)-1
    progress = ShiftProgress({}, amount, -1 << width, largest)
    for initial in {0, 1, largest, largest//2, -1}:
        values = interp._Interp({'i': initial})
        bound = values.value(progress.trips(ir.Var('i')))
        assert values.boolean(progress.in_domain(ir.Var('i'), ir.Lit(bound))) == (initial >= 0)
        if initial >= 0:
            assert initial >> (bound*amount) == 0


@pytest.mark.eda('verilator', 'make')
@pytest.mark.parametrize('loop,expected', [
    ('while(i>0) begin BODY i=i>>1; end', lambda a: a.bit_length()),
    ('while(i!=0) begin BODY i>>=2; end', lambda a: (a.bit_length()+1)//2),
    ('while(i>=2) begin BODY i=i>>>1; end', lambda a: max(0,a.bit_length()-1)),
    ('do begin BODY i>>=1; end while(i>0);', lambda a: max(1,a.bit_length())),
    ('for(i=a;i>0;i>>=1) begin BODY end', lambda a: a.bit_length()),
    ('while(i>0) begin BODY if(i==3) break; i>>=1; end',
     lambda a: next((j+1 for j in range(a.bit_length()) if a>>j==3), a.bit_length())),
])
def test_all_goals_query_and_native_replay(tmp_path, record_property, loop, expected):
    body = "q++; if(a==7 && i==1) q=q; if(1'b0) q=99;"
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      integer i;
      always @(posedge clk) begin q=0; i=a; {loop.replace('BODY', body)} end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda('verilator', 'make')
def test_signed_negative_values_skip_arithmetic_shift_loop(tmp_path, record_property):
    source = '''module M(input clk,input signed [3:0] a,output reg [7:0] q);
      integer i;
      always @(posedge clk) begin
        q=0; i=a;
        while(i>0) begin q++; if(a==7) q=q; if(1'b0) q=99; i>>>=1; end
      end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(-8,8),
                    lambda a: max(0,a).bit_length())


@pytest.mark.eda('verilator', 'make')
@pytest.mark.parametrize('declaration,step', [
    ('logic [4:0]', 'i>>=1'),
    ('logic signed [5:0]', 'i=$signed(8\'(i))>>>2'),
])
def test_typed_conversion_and_final_condition_effects(tmp_path, record_property, declaration, step):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration} i;
      function automatic bit more(); q=q+10; return 1; endfunction
      always @(posedge clk) begin
        q=0; i=a;
        while(more() && i>0) begin q++; if(a==7) q=q; if(1'b0) q=99; {step}; end
      end
    endmodule'''
    amount = 2 if '>>>2' in step else 1
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 10+11*((a.bit_length()+amount-1)//amount))


@pytest.mark.parametrize('initial,condition,body', [
    ('i=a;', 'i>=0', 'i>>=1;'),  # zero never exits
    ('i=a;', 'i>0', 'i>>=0;'),  # no progress
    ('i=a;', 'i>0', 'i<<=1;'),  # not a decreasing shift
    ('i=a;', 'i>0', 'if(a==7) continue; i>>=1;'),
    ('i=a;', 'i>0', 'i>>=1; if(a==7) i=a;'),
    ('i=-1;', 'i!=0', 'i>>>=1;'),  # negative arithmetic shift stays negative
])
def test_no_guessed_truncation_of_nonterminating_or_mutated_counter(
        tmp_path, initial, condition, body):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      integer i;
      always @(posedge clk) begin q=0; {initial} while({condition}) begin q++; {body} end end
    endmodule'''
    with pytest.raises(Refused):
        _lower(tmp_path, source)
