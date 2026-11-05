"""Compilation-unit helpers retain caller-owned native event identities."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


@pytest.mark.parametrize("lifetime", ["", "automatic "])
def test_unit_helper_keeps_instance_qualified_entries(tmp_path, lifetime):
    source = f'''function {lifetime}integer choose(input [3:0] v);
      if(v>3) choose=v+1;
      else choose=v;
    endfunction
    module leaf(input [3:0] a,output logic [15:0] q);
      always_comb q=choose(a);
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      leaf low(a[3:0],q[15:0]), high(a[7:4],q[31:16]);
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,4,64,68,68,0], ("q=choose(a);",))


def test_unit_and_module_helpers_with_same_name_keep_compiler_binding(tmp_path):
    source = '''function automatic integer adjust(input [7:0] v);
      if(v[0]) return v+1; else return v;
    endfunction
    module local_model(input [7:0] a,output logic [15:0] q);
      function automatic integer adjust(input [7:0] v);
        if(v[0]) return v+2; else return v;
      endfunction
      always_comb q=adjust(a);
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      local_model local_inst(a,q[15:0]);
      logic [15:0] high;
      assign q[31:16]=high;
      always_comb high=adjust(a);
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,4,5,5,255],
                    ("q=adjust(a);", "high=adjust(a);"))
