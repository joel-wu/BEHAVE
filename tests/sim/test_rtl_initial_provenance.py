"""Initialization belongs to the initial image, not the runtime goal catalogue."""

import pytest

from behave.sim.rtl.catalogue import occurrence_key_of, sites_of
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_origins import compiler_origins
from behave.sim.rtl.source_plan import plan_source
from behave.sim.rtl.shared_frontend import compile_source

from .test_rtl_frontend_compatibility import _run
from .test_rtl_source_plan import _parse


pytestmark = pytest.mark.eda("slang", "yosys")


def _documents(tmp_path, source):
    plan = plan_source(source, _parse(tmp_path, source))
    documents = []
    for name, text in (("original", source), ("annotated", plan.compiler.text)):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "design.sv").write_text(text)
        (directory / "run.ys").write_text(
            "read_verilog -sv design.sv\nhierarchy -top M\nflatten\nwrite_rtlil design.rtlil\n")
        assert compile_source(directory, "M", "run.ys", run=_run)
        assert (directory / "design.sv").read_text() == text
        documents.append((directory / "design.rtlil").read_text())
    return plan, *documents


@pytest.mark.parametrize("initializer", [
    "initial memory[0]=8'h35;",
    "initial for(integer i=0;i<4;i=i+1) memory[i]=8'h35;",
    "initial begin : boot integer i; q=0; "
    "for(i=0;i<4;i=i+1) memory[i]=8'h35; end",
])
@pytest.mark.parametrize("separator", [" ", "\n", " /* π */\n"])
def test_initialized_memory_has_no_runtime_occurrence(tmp_path, initializer, separator):
    initializer = initializer.replace("initial ", "initial" + separator, 1)
    source = f"""module M(input clk, we, input [1:0] a, input [7:0] d,
        output reg [7:0] q);
        reg [7:0] memory[0:3];
        {initializer}
        always @(posedge clk) begin
            if(we) memory[a]<=d;
            q<=memory[a];
        end
    endmodule"""
    plan, original, annotated = _documents(tmp_path, source)
    controls = compiler_origins(plan, original, annotated, filename="design.sv")
    origins = {origin.key: origin for origin in plan.controls}
    assert all(origins[control.origin].kind != "InitialBlock" for control in controls)
    sites = sites_of(original)
    assert sites and all(site.triggers == (("posedge", "\\clk"),) for site in sites)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert {item.occurrence for item in bound.occurrences} == {
        occurrence_key_of(site) for site in sites}


def test_initial_only_memory_without_init_sync_is_not_a_runtime_process(tmp_path):
    source = """module M(input [1:0] a, output [7:0] q);
        reg [7:0] memory[0:3];
        initial begin : boot integer i;
            for(i=0;i<4;i=i+1) memory[i]=8'h35;
        end
        assign q=memory[a];
    endmodule"""
    plan, original, annotated = _documents(tmp_path, source)
    assert "sync always" in original and "sync init" not in original
    assert sites_of(original) == []
    assert compiler_origins(plan, original, annotated, filename="design.sv") == ()


def test_submitted_initial_attribute_cannot_hide_runtime_actions(tmp_path):
    source = """module M(input clk, a, output reg q);
        (* behave_source_initial=1 *) always @(posedge clk) if(a) q<=1; else q<=0;
    endmodule"""
    _, original, _ = _documents(tmp_path, source)
    assert "behave_source_initial" not in original
    sites = sites_of(original)
    assert sites and all(site.triggers == (("posedge", "\\clk"),) for site in sites)
