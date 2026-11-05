"""Disjoint helper copy-outs need no intermediate same-cell write history."""

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_observation_frontend import _installed
from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


def _source(second):
    return f'''module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x[2];
      initial begin x[0]=0; x[1]=0; end
      task automatic split(input [7:0] value,output logic [7:0] left,right);
        left=value; right=value+1;
      endtask
      always @(posedge a[0]) split(a,x[0],x[{second}]);
      always_comb q={{x[1],x[0]}};
    endmodule'''


def test_disjoint_copyouts_keep_outputs_and_events(tmp_path):
    _assert_entries(tmp_path, _source("1"), [0,1,0,3,3,255], ("q={x[1],x[0]};",))


@pytest.mark.parametrize("second", ["0", "a[0]"])
def test_overlapping_or_unproved_copyouts_keep_primitive_order_requirement(tmp_path, second):
    source = _source(second)
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    assert any("repeated source writes" in issue for issue in plan.native_issues)


def _swap_source(low=0, second="j+1"):
    high = low + 3
    result = "q={" + ",".join(f"x[{i}]" for i in range(high, low - 1, -1)) + "};"
    source = f'''module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x[{low}:{high}];
      always_comb begin
        for (integer j={low}; j<={high}; j=j+1) x[j]=a+j;
        for (integer j={low}; j<{high}; j=j+1)
          {{x[j],x[{second}]}}={{x[{second}],x[j]}};
      end
      always_comb {result}
    endmodule'''
    return source, result


@pytest.mark.parametrize("low", [-2, 0])
def test_adjacent_variable_index_swaps_keep_outputs_and_native_events(tmp_path, low):
    source, result = _swap_source(low)
    _assert_entries(tmp_path, source, [0, 1, 0, 2, 2, 255], (result,))


@pytest.mark.parametrize("second", [
    "j", "j+(32'sh7fffffff+32'sh7fffffff+32'sd2)", "a[1:0]", "j++",
])
def test_aliasing_and_unrelated_dynamic_indices_remain_unproved(tmp_path, second):
    source, _ = _swap_source(second=second)
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    assert any("repeated source writes" in issue for issue in plan.native_issues)
