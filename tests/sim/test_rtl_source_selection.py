"""Only instantiated source scopes own execution dependencies and events."""

from dataclasses import replace

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_declarations import declare_source
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_locations import _collector
from .test_rtl_source_net_drivers import _prepare


@pytest.mark.eda("slang")
@pytest.mark.parametrize("body,kinds,loops", [
    ("always @(posedge clk) q<=a;", ["Always"], 0),
    ("always_comb q=a;", ["AlwaysComb"], 0),
    ("assign q=a;", ["ContinuousAssign"], 0),
    ("wire w=a; assign q=w;", ["NetInitializer", "ContinuousAssign"], 0),
    ("always_comb begin q=a; for(int i=0;i<2;i++) q=q+1; end", ["AlwaysComb"], 1),
])
def test_dependencies_skip_uninstantiated_modules_and_generate_arms(tmp_path, body, kinds, loops):
    source = f"""module Unused(input clk,input [3:0] a,output logic [3:0] q);
      {body}
    endmodule
    module M(input clk,input [3:0] a,output logic [3:0] q);
      if(0) begin: absent {body} end
      else begin: selected {body} end
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), rows = parse_observation_source(tmp_path, "M", run=_installed)
    assert {row["scope"] for row in rows} == {"M.selected"}
    assert sorted(row["procedure"] for row in rows) == sorted(kinds)
    assert len(ast["loop_headers"]) == loops
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=rows)
    assert not plan.native_issues
    assert plan.declarations == declare_source(source, ast, filename="design.sv")
    assert {context.scope for context in plan.declarations.contexts} == {("M", "selected")}
    assert (tmp_path / "design.sv").read_text() == source


@pytest.mark.eda("slang")
def test_uninstantiated_writes_do_not_subscribe_to_selected_package_reads(tmp_path):
    source = """package P; logic [3:0] x; endpackage
    module Unused(input [3:0] a); always_comb P::x=a; endmodule
    module M(input clk,input [3:0] a,output logic [3:0] q);
      if(0) begin: absent always @(posedge clk) P::x=4; end
      always @(posedge clk) P::x=a;
      always_comb q=P::x;
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (_, _), rows = parse_observation_source(tmp_path, "M", run=_installed)
    assert {row["scope"] for row in rows} == {"M"}
    consumer, = [row for row in rows if row["procedure"] == "AlwaysComb"]
    read, = consumer["reads"]
    assert read["symbol"] == "P::x"
    write, = read["write_references"]
    assert write["start"] == source.index("P::x=a", source.index("module M"))
    assert source[write["start"]:write["end"]] == "P::x"
    assert write["scope_reference"] == ""


@pytest.mark.eda("slang", "verilator", "make")
def test_selected_instances_and_runtime_dead_arms_keep_native_events(tmp_path):
    source = """module Part(input [3:0] a,output logic [3:0] q);
      if(0) begin: absent always_comb q=0; end
      else begin: selected
        always_comb begin if(1'b0) q=15; else q=a; end
      end
    endmodule
    module Unused(input [3:0] a,output logic [3:0] q);
      always_comb q=a;
    endmodule
    module M(input [3:0] a,output wire [7:0] q);
      Part left(a,q[3:0]);
      Part right(a,q[7:4]);
    endmodule"""
    plan, _, rows = _prepare(tmp_path, source)
    scopes = {"M.left.selected", "M.right.selected"}
    assert {row["scope"] for row in rows} == scopes
    assert {".".join(context.scope) for context in plan.declarations.contexts} == scopes
    assert plan.declarations.sites_for("control-choice")[0].outcomes == ("true", "false")
    collector, _ = _collector(plan)
    driver = """
      for(unsigned i=0;i<3;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    for index, (before, after) in enumerate(zip(original, native, strict=True)):
        assert before == (index * 17, 0, 0)
        assert after[0] == before[0]
        seen = {bit for bit in range(collector.bindings.event_count) if after[1] >> bit & 1}
        hits = collector.bindings.decode(seen).occurrences
        # Two distinct instances execute the false runtime arm. The absent
        # generate arm has no occurrence; the dead runtime arm stays declared.
        assert sum(hit.role == "false" for hit in hits) == 2
        assert not any(hit.role == "true" for hit in hits)
