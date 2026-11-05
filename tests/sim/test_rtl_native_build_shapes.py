"""Native observer rewrites retain HDL typing and helper call semantics."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


def test_helper_return_used_as_deferred_array_index(tmp_path):
    source = '''module M(input [7:0] a, output logic [31:0] q);
      logic [7:0] x[2];
      initial begin x[0]=0; x[1]=0; end
      function automatic integer index_of(input [7:0] value);
        if(value[1]) return 1; else return 0;
      endfunction
      always @(posedge a[0]) x[index_of(a)] <= a;
      always_comb q=x[0]+x[1];
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,0,3,0,5], ("q=x[0]+x[1];",))


def test_generate_indexed_concatenation_nba_keeps_sized_lvalue(tmp_path):
    source = '''module M(input [7:0] a, output logic [31:0] q);
      parameter W=4;
      logic [7:0] x[2]; logic carry[2];
      initial begin x[0]=0; x[1]=0; carry[0]=0; carry[1]=0; end
      for(genvar g=0;g<2;g++) begin: lanes
        always @(posedge a[0])
          {carry[g],x[g][g*W+:W]} <= {1'b0,a[W-1:0]}+a[7:W];
      end
      always_comb q={carry[1],x[1],carry[0],x[0]};
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,0,3,0,255], ("q={carry[1],x[1],carry[0],x[0]};",))


def test_case_of_nested_helper_keeps_native_frame_local(tmp_path):
    source = '''module M(input [7:0] a, output logic [31:0] q);
      function automatic integer code(input [7:0] v);
        if(v[0]) return 1; else return 2;
      endfunction
      function automatic integer choose(input [7:0] v);
        case(code(v))
          1: return 3;
          2: return 4;
          default: return 5;
        endcase
      endfunction
      always_comb q=choose(a)+choose(a+1);
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,1,2,3,255], ("q=choose(a)+choose(a+1);",))


@pytest.mark.parametrize("stages", [2, 3, 4])
def test_generate_pipeline_reads_previous_array_cell_and_final_scope(tmp_path, stages):
    source = '''module M(input [7:0] a, output logic [31:0] q);
      parameter N=3;
      logic [3:0] x[0:N-1];
      initial begin for(int i=0;i<N;i++) x[i]=0; end
      for(genvar g=0;g<N;g++) begin: lane
        wire [3:0] value;
        if(g==0) begin: head
          assign value=a[7:4];
        end else begin: tail
          assign value=x[g-1]+1;
        end
        if(g<N-1) begin: registers
          always @(posedge a[0]) x[g]<=value;
        end
      end
      always_comb q=lane[N-1].value;
    endmodule'''
    source = source.replace("parameter N=3;", f"parameter N={stages};")
    _assert_entries(tmp_path, source, [0,1,0,17,16,33,32,255], ("q=lane[N-1].value;",))
