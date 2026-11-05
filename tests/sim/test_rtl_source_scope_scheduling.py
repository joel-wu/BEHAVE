"""Subscriber scope bindings survive generate instances and lexical shadowing."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


def test_generated_consumers_receive_their_own_pending_notifications(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0;
      always_comb begin x=a; x=0; end
      for(genvar k=0;k<2;k++) begin: consumer
        always_comb q[k*16+:16]=x;
      end
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ("q[k*16+:16]=x;",))
    assert [len(entries) for _, entries in rows] == [2,2,0,2,0,0,2]


@pytest.mark.parametrize("writer", [
    """always_comb begin: writer
         logic [7:0] consumer;
         consumer=a; storage.x=consumer; storage.x=0;
       end""",
    """function automatic [7:0] pulse(input [7:0] consumer);
         storage.x=consumer; storage.x=0; return consumer;
       endfunction
       logic [7:0] ignored;
       always_comb ignored=pulse(a);""",
])
def test_consumer_scope_is_not_a_same_named_local_value(tmp_path, writer):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      if (1) begin: storage logic [7:0] x=0; end
      {writer}
      if (1) begin: consumer always_comb q=storage.x; end
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ("q=storage.x;",))
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,False,True]


def test_parameterized_instances_observe_only_their_selected_bits(tmp_path):
    source = """module leaf #(parameter int INDEX=0)(input [7:0] a,output logic [7:0] q);
      always_comb q=a[INDEX];
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      leaf #(.INDEX(0)) low(a,q[7:0]);
      leaf #(.INDEX(7)) high(a,q[15:8]);
      assign q[31:16]=0;
    endmodule"""
    # Icarus widens implicit vector selects. The independent reference exposes
    # a separate one-bit input to each instance, without Sim's dependency code.
    reference = source.replace("input [7:0] a,output logic", "input a,output logic")
    reference = reference.replace("q=a[INDEX];", "q=a;")
    reference = reference.replace("low(a,", "low(a[0],").replace("high(a,", "high(a[7],")
    rows = _assert_entries(tmp_path, source, [0,1,1,3,131,130,2], ("q=a[INDEX];",),
                           reference_source=reference, reference_statements=("q=a;",))
    assert [len(entries) for _, entries in rows] == [2,1,0,0,1,1,1]


@pytest.mark.parametrize("process", ["always_comb", "always @*"])
@pytest.mark.parametrize("base", [0, -2])
def test_nested_genvar_instances_keep_exact_activation_sets(tmp_path, process, base):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      for(genvar i={base};i<{base+2};i++) begin: outer
        for(genvar j=0;j<2;j++) begin: inner
          localparam int INDEX=(i-({base}))*2+j;
          {process} q[INDEX*8+:8]=a[INDEX];
        end
      end
    endmodule"""
    statement = "q[INDEX*8+:8]=a[INDEX];"
    # @* has a whole-vector dependency. A shift reads that same whole vector;
    # always_comb supplies the locked native backend's startup settling too.
    # Conversely always_comb needs split inputs to avoid Icarus widening selects.
    reference_statement = ("q[INDEX*8+:8]=(a >> INDEX) & 1;" if process == "always @*"
                           else "q[INDEX*8+:8]=selected;")
    reference = source.replace(f"{process} {statement}",
        ("" if process == "always @*" else "wire selected=a[INDEX]; ")
        + "always_comb " + reference_statement)
    rows = _assert_entries(tmp_path, source, [0,1,1,3,131,130,2],
        ("q[INDEX*8+:8]=a[INDEX];",), reference_source=reference,
        reference_statements=(reference_statement,))
    assert [len(entries) for _, entries in rows] == (
        [4,4,0,4,4,4,4] if process == "always @*" else [4,1,0,1,0,1,0])
