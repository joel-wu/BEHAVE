"""Helper NBAs keep call-site order, old reads and conditional execution."""

import pytest

from .test_rtl_bounded_source_loops import _assert_closure
from .test_rtl_source_activation_scheduling import _assert_entries
from .test_rtl_source_ir import _native_agreement

pytestmark = pytest.mark.eda('slang', 'verilator', 'make', 'iverilog', 'vvp')


@pytest.mark.parametrize('statement,reference_statement', [
    ('q<=f(a[4:1]);', 'f(a[4:1],value); q<=value;'),
    ('q[3:0]<=f(a[4:1]);', 'f(a[4:1],value); q[3:0]<=value;'),
    ('q[f(a[4:1])&7]<=a[2];', 'f(a[4:1],value); q[value&7]<=a[2];'),
    ('q<=a[1] && f(a[4:1]);', 'if(a[1]) begin f(a[4:1],value); q<=(value!=0); end else q<=0;'),
    ('q<=a[1] ? f(a[4:1]) : q+2;',
     'if(a[1]) begin f(a[4:1],value); q<=value; end else q<=q+2;'),
    ('case(a[4:1]) f(1),f(2):q<=q+16; f(5):q<=q+32; default:q<=q+64; endcase',
     '''f(1,value);
        if(a[4:1]==value) q<=q+16;
        else begin
          f(2,value);
          if(a[4:1]==value) q<=q+16;
          else begin
            f(5,value);
            if(a[4:1]==value) q<=q+32;
            else q<=q+64;
          end
        end'''),
])
def test_helper_nbas_match_independent_task_oracle(tmp_path, statement, reference_statement):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int stamp=0;
      function automatic [31:0] f(input [3:0] v);
        q<=q+1;
        if(v==7) return q+v;
        return v;
      endfunction
      always @(posedge a[0]) begin {statement} stamp=stamp+1; end
    endmodule'''
    # Icarus does not support NBAs in functions. This independent procedural
    # oracle spells out the calls, old-value reads and lazy evaluation using a
    # task. Both Verilator builds still compile the unchanged function source.
    reference = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int stamp=0;
      logic [31:0] value;
      task automatic f(input [3:0] v,output logic [31:0] result);
        q<=q+1;
        result=(v==7) ? q+v : v;
      endtask
      always @(posedge a[0]) begin {reference_statement} stamp=stamp+1; end
    endmodule'''
    values = [value for a in range(16) for value in (a << 1, (a << 1) | 1)]
    _assert_entries(tmp_path, source, values, ('stamp=stamp+1;',), reference_source=reference)


@pytest.mark.solver
@pytest.mark.parametrize('nested', [False, True])
def test_helper_nbas_keep_source_ir_and_native_event_order(tmp_path, nested):
    inner = 'g(v)' if nested else 'v'
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] g(input [3:0] v);
        q<=q+2;
        if(v==7) return q+v;
        return v;
      endfunction
      function automatic [7:0] f(input [3:0] v);
        q<={inner};
        if(v==5) return q+1;
        return q;
      endfunction
      always @(posedge clk) begin
        if(a[0]) q<=f(a);
        else q<=q+3;
      end
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.solver
@pytest.mark.parametrize('statement,expected', [
    ('q<=f(a);', lambda a: a),
    ('q<=a[0] && f(a);', lambda a: int(bool(a & 1))),
    ('q<=a[0] ? f(a) : 9;', lambda a: a if a & 1 else 9),
    ('q[f(a)&7]<=1;', lambda a: 1 | (1 << (a & 7))),
    ('case(a) f(1),f(2):q<=q+16; f(5):q<=q+32; default:q<=q+64; endcase',
     lambda a: 16 if a in (1, 2) else 32 if a == 5 else 64),
])
def test_every_helper_nba_goal_has_a_native_confirmed_local_answer(
        tmp_path, record_property, statement, expected):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        q<=q+1;
        if(1'b0) return 0;
        if(v==7) return q+v;
        return v;
      endfunction
      always @(posedge clk) begin {statement} end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), expected, fresh_inputs=True)


@pytest.mark.parametrize('statement,reference_statement', [
    ('cells[0]<=f(a[4:1]);', 'f(a[4:1],value); cells[0]<=value;'),
    ('case(a[4:1]) f(1):cells[0]<=17; f(2):cells[0]<=19; default:cells[0]<=23; endcase',
     '''f(1,value);
        if(a[4:1]==value) cells[0]<=17;
        else begin f(2,value);
          if(a[4:1]==value) cells[0]<=19;
          else cells[0]<=23;
        end'''),
])
def test_array_helper_nbas_commit_in_call_site_order(tmp_path, statement, reference_statement):
    shared = '''logic [31:0] cells[0:1];
      initial begin cells[0]=0; cells[1]=0; end
      int stamp=0;
      assign q=cells[0]+cells[1];'''
    source = f'''module M(input [7:0] a,output wire [31:0] q);
      {shared}
      function automatic [31:0] f(input [3:0] v);
        cells[0]<=cells[0]+1;
        return v;
      endfunction
      always @(posedge a[0]) begin {statement} stamp=stamp+1; end
    endmodule'''
    reference = f'''module M(input [7:0] a,output wire [31:0] q);
      {shared}
      logic [31:0] value;
      task automatic f(input [3:0] v,output logic [31:0] result);
        cells[0]<=cells[0]+1;
        result=v;
      endtask
      always @(posedge a[0]) begin {reference_statement} stamp=stamp+1; end
    endmodule'''
    values = [value for a in range(16) for value in (a << 1, (a << 1) | 1)]
    _assert_entries(tmp_path, source, values, ('stamp=stamp+1;',), reference_source=reference)
