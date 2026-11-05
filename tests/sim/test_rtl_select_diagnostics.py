"""Frontend select warnings must not replace native execution or exact proof."""

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.rtl.shared_frontend import parse_source
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_frontend_compatibility import _run
from .test_rtl_source_locations import _prepare_clocked_replay

pytestmark = pytest.mark.eda("slang", "verilator", "make")


@pytest.mark.parametrize("declaration,expression,diagnostic", [
    ("", "a[4]", "index-oob"),
    ("", "a[4:3]", "range-oob"),
    ("logic [3:0] mem[0:1]; initial begin mem[0]=3; mem[1]=5; end",
     "mem[2]", "index-oob"),
])
def test_select_diagnostic_keeps_native_values_and_activation(
        tmp_path, capfd, declaration, expression, diagnostic):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      {declaration}
      always @(posedge clk) if(a[0]) q={expression}; else q=7;
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    parsed = parse_source(tmp_path, "M", run=_run, typed=True)
    assert parsed is not None
    assert diagnostic in capfd.readouterr().err
    assert (tmp_path / "design.sv").read_text() == source
    cst, ast = parsed
    plan = plan_source(source, cst, semantic_document=ast)
    baseline, baseline_hits, replay = _prepare_clocked_replay(tmp_path, source, plan)
    assert baseline == [7]
    from behave.sim.rtl.source_goals import manifest_of

    manifest = manifest_of(plan.declarations, filename="design.sv")
    selected = {goal.id for goal in manifest.values() if goal.family == "location"
                and source[goal.where("start"):goal.where("end")] == f"q={expression};"}
    assert selected and selected.isdisjoint(baseline_hits[0])
    values, hits = replay((0, 1, 2, 3))
    assert values[0] == values[2] == 7
    assert all(selected <= hits[index] for index in (1, 3))
    assert all(selected.isdisjoint(hits[index]) for index in (0, 2))


def test_warned_select_does_not_silently_extend_exact_domain(tmp_path):
    from .test_rtl_source_ir import _lower

    source = """module M(input clk,input [3:0] a,output logic q);
      always @(posedge clk) q=a[4]; endmodule"""
    with pytest.raises(Refused):
        _lower(tmp_path, source)


def test_reversed_select_remains_an_elaboration_error(tmp_path, capfd):
    (tmp_path / "design.sv").write_text("""module M(input [3:0] a,output [1:0] q);
        assign q=a[1:2]; endmodule""")
    assert parse_source(tmp_path, "M", run=_run, typed=True) is None
    assert "range-select-reversed" in capfd.readouterr().err
