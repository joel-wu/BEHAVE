"""Argument-bound tasks share the original implicit process read dependencies."""

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_observation_frontend import _installed
from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


@pytest.mark.parametrize("process", ["always_comb", "always @*"])
def test_nested_argument_bound_tasks_keep_values_and_entries(tmp_path, process):
    source = f'''module M(input [7:0] a,output logic [31:0] q);
      localparam OFFSET=3;
      logic [7:0] x=0;
      task automatic inner(input [7:0] value,output logic [7:0] result);
        case(value[1:0])
          0: result=value;
          1: result=value+OFFSET;
          default: result=value-1;
        endcase
      endtask
      task automatic outer(input [7:0] value,output logic [7:0] result);
        logic [7:0] temporary;
        inner(value,temporary);
        result=temporary;
      endtask
      {process} outer(a,x);
      always_comb q=x;
    endmodule'''
    _assert_entries(tmp_path, source, [1,1,4,5,7,0], ("q=x;",), settle_startup=True)


@pytest.mark.parametrize("body", ["result=value+a;", "x=value; result=value;"])
@pytest.mark.parametrize("process", ["always_comb", "always @*"])
def test_hidden_task_reads_or_writes_still_require_schedule_qualification(tmp_path, body, process):
    source = f'''module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0,y=0;
      task automatic hidden(input [7:0] value,output logic [7:0] result);
        {body}
      endtask
      {process} hidden(8'd1,y);
      always_comb q=x+y;
    endmodule'''
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), reads = parse_observation_source(tmp_path, "M", run=_installed)
    unqualified = [{key: value for key, value in row.items() if key != "inlined_task_dependencies"}
                   for row in reads]
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=unqualified)
    assert any("implicit task-call sensitivity" in issue for issue in plan.native_issues)
    qualified = plan_source(source, cst, semantic_document=ast, sensitivity_document=reads)
    if process == "always_comb":
        assert not qualified.native_issues
        assert plan.declarations == qualified.declarations
        qualified.native()
    else:
        assert any("implicit task-call sensitivity" in issue for issue in qualified.native_issues)
