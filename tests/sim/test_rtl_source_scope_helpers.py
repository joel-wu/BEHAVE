"""A helper's source events belong to the invoking process's instance scope."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


def test_helper_events_keep_the_calling_generate_scope(tmp_path):
    source = """module cell_model(input [3:0] a,output logic [15:0] q);
      function automatic [3:0] identity(input [3:0] v);
        return v;
      endfunction
      if (1) begin: consumer always_comb q=identity(a); end
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      cell_model low(a[3:0],q[15:0]),high(a[7:4],q[31:16]);
    endmodule"""
    # This straight-line helper is called exactly once at each caller entry.
    # Mark that caller in the independent oracle so its %m identifies the
    # invocation, not the helper's declaration scope.
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17], ("return v;",),
                           reference_statements=("q=identity(a);",))
    assert [len(entries) for _, entries in rows] == [2,1,1,1,0,1,2]


def test_nested_helper_copies_scope_through_generated_calls(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      function automatic [15:0] inner(input [7:0] v,input int lane);
        int result=v ? v+lane : 0;
        return result;
      endfunction
      function automatic [15:0] outer(input [7:0] v,input int lane);
        return inner(v,lane);
      endfunction
      for(genvar k=0;k<2;k++) begin: consumer
        always_comb q[k*16+:16]=outer(a,k);
      end
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17], ("return result;",),
                           reference_statements=("q[k*16+:16]=outer(a,k);",))
    assert [len(entries) for _, entries in rows] == [2,2,2,2,0,2,2]


def test_unrelated_function_local_does_not_shadow_a_helper_call(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      function automatic [31:0] select_value(input [7:0] x);
        if (x[0]) return x+1;
        else return x+2;
      endfunction
      function automatic [31:0] other(input [7:0] x);
        int select_value;
        select_value=x;
        return select_value;
      endfunction
      always_comb q=select_value(a)+other(a);
    endmodule"""
    # A helper's events carry its caller's instance scope, not the function's
    # lexical %m. This straight-line return executes once per caller entry.
    rows = _assert_entries(tmp_path, source, [0,1,2,2,3,0],
                           ("return select_value;",),
                           reference_statements=("q=select_value(a)+other(a);",))
    assert [len(entries) for _, entries in rows] == [1,1,1,0,1,1]


def test_package_helper_keeps_typed_call_and_caller_event_scope(tmp_path):
    source = """package Values;
      function automatic [31:0] adjust(input [7:0] v);
        if(v[0]) return v+1;
        return v+2;
      endfunction
    endpackage
    module M(input [7:0] a,output logic [31:0] q);
      function automatic [31:0] wrap(input [7:0] v);
        return Values::adjust(v);
      endfunction
      always_comb q=wrap(a);
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,2,2,3,0],
                           ("return Values::adjust(v);",),
                           reference_statements=("q=wrap(a);",))
    assert [len(entries) for _, entries in rows] == [1,1,1,0,1,1]


def test_bare_task_call_keeps_native_entries_and_original_outputs(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q=0);
      task automatic put;
        q=a;
      endtask
      always @(a) begin put; end
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,2,2,3,0], ("q=a;",),
                           reference_statements=("begin put; end",), settle_startup=True)
    assert [len(entries) for _, entries in rows] == [0,1,1,0,1,1]


def test_implicit_task_sensitivity_does_not_silently_change_outputs(tmp_path):
    from behave.sim.rtl.source_plan import plan_source
    from .test_rtl_source_declarations import _documents

    source = """module M(input [7:0] a,output logic [31:0] q);
      task automatic put; q=a; endtask
      always_comb put;
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    # The source frontend excludes task bodies from implicit sensitivity, while
    # the current uninstrumented Verilator includes their reads. Do not enable a
    # producer that would turn that backend difference into a changed DUT output.
    with pytest.raises(ValueError, match="implicit task-call sensitivity"):
        plan.native()
