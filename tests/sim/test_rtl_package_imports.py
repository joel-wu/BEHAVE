"""Compiler-resolved package imports preserve function identity and truth tables."""

import pytest
from pathlib import Path

from behave.sim.rtl.source_imports import normalize_package_imports
from .test_rtl_rom_normalization import _typed
from .test_rtl_helper_returns import _truth_table
from .test_rtl_source_plan import _run
from behave.sim.rtl.shared_frontend import compile_source

pytestmark = pytest.mark.eda("slang", "yosys")


@pytest.mark.parametrize("imported", ["*", "f"])
def test_imported_function_matches_qualified_function(tmp_path, imported):
    source = f'''package P;
      function automatic [7:0] f(input [3:0] v); return v+3; endfunction
    endpackage
    module M(input [3:0] a, output [7:0] q);
      import P::{imported};
      assign q=f(a);
    endmodule
    '''
    cst, typed = _typed(tmp_path / "parse", source)
    plan = normalize_package_imports(source, cst, typed)
    assert "\\P::f (a)" in plan.text
    assert "import" not in plan.text
    assert plan.text.count("\n") == source.count("\n")
    _truth_table(tmp_path / "compiled", source, lambda a: a+3)


def test_local_function_shadows_wildcard_import(tmp_path):
    source = '''package P;
      function automatic [7:0] f(input [3:0] v); f=v+3; endfunction
    endpackage
    module M(input [3:0] a, output [7:0] q);
      import P::*;
      function automatic [7:0] f(input [3:0] v); f=v+7; endfunction
      assign q=f(a);
    endmodule
    '''
    _truth_table(tmp_path / "compiled", source, lambda a: a+7)


def test_unsupported_import_is_not_silently_dropped(tmp_path):
    source = '''package P; typedef logic [7:0] word; endpackage
    module M(input [3:0] a, output [7:0] q);
      import P::*; word data; assign data=a; assign q=data;
    endmodule'''
    cst, typed = _typed(tmp_path / "parse", source)
    assert normalize_package_imports(source, cst, typed).text == source


def test_missing_call_binding_preserves_the_import(tmp_path):
    source = '''package P;
      function automatic [7:0] f(input [3:0] v); f=v+3; endfunction
    endpackage
    module M(input [3:0] a, output [7:0] q); import P::*; assign q=f(a); endmodule'''
    cst, typed = _typed(tmp_path / "parse", source)

    def omit_calls(node):
        if isinstance(node, dict):
            return {} if node.get("kind") == "Call" else {k: omit_calls(v) for k, v in node.items()}
        if isinstance(node, list):
            return [omit_calls(v) for v in node]
        return node

    assert normalize_package_imports(source, cst, omit_calls(typed)).text == source


def test_imported_call_in_clocked_case_keeps_json_provenance(tmp_path):
    source = '''package nv_math;
function automatic [6:0] to_scale(input logic [31:0] a);
    logic [23:0] sig;
    logic [24:0] r;
    integer sh,code;
    begin
        sig={1'b1,a[22:0]}; sh=20; r=0; code=0;
        if(a==0) to_scale=7'd56;
        else if(a<=32'h3b000000) to_scale=7'd1;
        else begin r=sig>>sh; to_scale=r[6:0]; end
    end
endfunction
endpackage
module M(input clk, rst, input [31:0] a, output logic [6:0] q);
import nv_math::*;
reg [3:0] state;
always @(posedge clk) begin
    if(rst) begin state<=4; q<=0; end
    else case(state)
        4: begin q<=to_scale(a); state<=5; end
        5: begin q<=0; state<=4; end
    endcase
end
endmodule
'''
    (tmp_path / "design.sv").write_text(source)
    (tmp_path / "run.ys").write_text('read_verilog -sv design.sv\nhierarchy -top M\n'
        'proc\nflatten\nopt_expr -keepdc\nopt_clean\nmemory_collect\nopt_dff -keepdc\n'
        'write_json ownership.json\n')

    def run(where, *argv):
        _run(where, Path(argv[0]).name, *argv[1:])
        return True

    assert compile_source(tmp_path, "M", "run.ys", run=run)
