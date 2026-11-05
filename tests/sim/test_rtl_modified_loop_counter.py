"""A modified for counter needs a termination proof, never silent truncation."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_bounded_source_loops import _assert_closure, _lower

pytestmark = [pytest.mark.eda('slang', 'verilator', 'make'), pytest.mark.solver]


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_counter_exit_preserves_actual_steps_tests_and_all_goals(tmp_path, record_property, direction):
    up = direction == 'up'
    header = 'int i=0;i<4;i++' if up else 'int i=3;i>=0;i--'
    exit_value = 4 if up else -1
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for({header}) begin
          q=q+1;
          if(a[i]) i={exit_value};
          if(1'b0) q=99;
        end
      end
    endmodule'''

    def expected(a):
        indices = range(4) if up else range(3, -1, -1)
        return next((n for n, i in enumerate(indices, 1) if a & (1 << i)), 4)

    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize('transfer', ['', 'continue;', 'if(a==7) break;'])
def test_extra_forward_updates_preserve_transfers(tmp_path, record_property, transfer):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for(int i=0;i<8;i++) begin
          q=q+1;
          i=i+1;
          if(1'b0) q=99;
          {transfer}
        end
      end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 1 if 'break' in transfer and a == 7 else 4)


@pytest.mark.parametrize('mutation', ['i=0;', 'if(a==7) i=0;', 'if(a==7 && i==2) i=i-2;'])
def test_body_that_can_outlive_the_header_bound_is_not_truncated(tmp_path, mutation):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for(int i=0;i<4;i++) begin q=q+1; {mutation} end
      end
    endmodule'''
    with pytest.raises(Refused, match='body-modified for counter'):
        _lower(tmp_path, source)


def test_helper_return_does_not_need_a_final_false_test(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        logic [7:0] n=0;
        for(int i=0;i<4;i++) begin
          n=n+1;
          if(v==7) return n;
          if(v[i]) i=4;
          if(1'b0) n=99;
        end
        return n;
      endfunction
      always @(posedge clk) q=f(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 1 if a == 7 else next((i+1 for i in range(4) if a & (1 << i)), 4))
