"""Residue-distance certificates retain all source events and native hits."""

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_while import ModuloProgress

from .test_rtl_bounded_source_loops import _assert_closure, _lower


@pytest.mark.parametrize('modulus', [1, 2, 3, 7, 16])
@pytest.mark.parametrize('delta', [-1, 1])
@pytest.mark.parametrize('minimum', [0, 1])
def test_exact_distance_to_first_zero_residue(modulus, delta, minimum):
    progress = ModuloProgress({}, modulus, delta, -128, 127)
    for start in range(-128, 128):
        runtime = interp._Interp({'i': start})
        count = runtime.value(progress.trips(ir.Var('i'), minimum=minimum))
        assert minimum <= count <= modulus
        assert (start + delta*count) % modulus == 0
        assert all((start + delta*k) % modulus for k in range(minimum, count))
        assert runtime.boolean(progress.in_domain(ir.Var('i'), ir.Lit(count))) == (
            -128 <= start + delta*count <= 127)


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('form', ['while', 'do', 'for'])
@pytest.mark.parametrize('delta', [-1, 1])
def test_all_goals_query_and_native_replay(tmp_path, record_property, form, delta):
    step = 'i++' if delta > 0 else 'i--'
    body = "q++; if(a==3 && i==1) q=q; if(1'b0) q=99;"
    if form == 'while':
        loop = f'i=a; while(i%3!=0) begin {body} {step}; end'
    elif form == 'do':
        loop = f'i=a; do begin {body} {step}; end while(i%3!=0);'
    else:
        loop = f'for(i=a;i%3!=0;{step}) begin {body} end'
    source = f'''module M(input clk,input signed [3:0] a,output reg [7:0] q);
      integer i;
      always @(posedge clk) begin q=0; {loop} end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(-8, 8),
                    lambda a: (-delta*a) % 3 or (3 if form == 'do' else 0))


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('transfer', ['', 'if(i==3) break;'])
def test_alignment_keeps_short_circuit_condition_effects_and_early_exit(
        tmp_path, record_property, transfer):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      integer i;
      function automatic bit more(); q=q+10; return 1; endfunction
      always @(posedge clk) begin
        q=0; i=a;
        while(more() && i%4!=0) begin
          q++; if(a==7) q=q; if(1'b0) q=99; {transfer} i++;
        end
      end
    endmodule'''

    def expected(a):
        count = (-a) % 4
        broken = bool(transfer) and 1 <= a <= 3
        return 11*count + (0 if broken else 10)

    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda('slang')
@pytest.mark.solver
@pytest.mark.parametrize('declaration,initial,condition,body', [
    ('integer', 'i=1;', 'i%4!=0', 'i+=2;'),  # never reaches a zero residue
    ('integer', 'i=a;', 'i%4!=0', 'if(a==7) continue; i++;'),
    ('integer', 'i=a;', 'i%4!=0', 'i++; if(a==7) i=a;'),
    ('integer', 'i=a;', 'i%4!=0', 'if(a==7) i++;'),
    ('integer', 'i=a;', '(i++)%4!=0', 'i++;'),
    ('integer', 'i=a;', 'i%(a+1)!=0', 'i++;'),
    ('logic [3:0]', 'i=15;', 'i%2!=0', 'i++;'),  # wraps before mathematical exit
])
def test_unproved_progress_is_not_truncated(tmp_path, declaration, initial, condition, body):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration} i;
      always @(posedge clk) begin
        q=0; {initial} while({condition}) begin q++; {body} end
      end
    endmodule'''
    with pytest.raises(Refused):
        _lower(tmp_path, source)
