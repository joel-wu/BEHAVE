"""Direct-edge symbolic bindings must not guess computed signal transitions."""

import pytest

from behave.sim.rtl.source_declarations import declare_source
from behave.sim.rtl.source_triggers import check_source_triggers

from .test_rtl_source_declarations import _documents

pytestmark = pytest.mark.eda("slang")


def _declarations(tmp_path, source):
    _, ast = _documents(tmp_path, source)
    return declare_source(source, ast, filename="design.sv")


@pytest.mark.parametrize("body, expected", [
    ("always @(posedge clk) q<=a;", (("posedge", "clk"),)),
    ("always_ff @(posedge clk or negedge rst_n) q<=a;",
     (("posedge", "clk"), ("negedge", "rst_n"))),
    ("always_comb q=a;", (("always", ""),)),
    ("always @* q=a;", (("always", ""),)),
    ("always @(posedge (clk & a)) q<=a;", None),
    ("always @(a) q=a;", None),
    ("wire clock_alias=clk; always @(posedge clock_alias) q<=a;",
     (("posedge", "clk"),)),
])
def test_resolved_source_process_edges(tmp_path, body, expected):
    source = f"module M(input logic clk,rst_n,a,output logic q); {body} endmodule"
    declarations = _declarations(tmp_path, source)
    process, = (context for context in declarations.contexts
                if context.process_start in declarations.process_starts)
    assert process.triggers == expected
    if expected is None:
        with pytest.raises(ValueError, match="non-Driver-owned"):
            check_source_triggers(declarations, {("posedge", "clk"), ("always", "")})
    else:
        check_source_triggers(declarations, set(expected) | {("always", "")})


def test_shared_instance_bodies_keep_separate_port_environments(tmp_path):
    source = """module Leaf(input logic c,a,output logic q);
      always @(posedge c) q<=a;
    endmodule
    module Middle(input logic c,a,output wire q);
      wire wire_c; assign wire_c=c;
      Leaf leaf(wire_c,a,q);
    endmodule
    module M(input logic clk0,clk1,a,output wire [1:0] q);
      Middle one(clk0,a,q[0]);
      Middle two(clk1,a,q[1]);
    endmodule"""
    declarations = _declarations(tmp_path, source)
    edges = {context.scope: context.triggers for context in declarations.contexts
             if context.process_start in declarations.process_starts}
    assert edges == {
        ("M", "one", "leaf"): (("posedge", "clk0"),),
        ("M", "two", "leaf"): (("posedge", "clk1"),),
    }
    allowed = {("always", ""), ("posedge", "clk0")}
    with pytest.raises(ValueError, match="clk1"):
        check_source_triggers(declarations, allowed)
    check_source_triggers(declarations, allowed | {("posedge", "clk1")})


def test_generate_aliases_use_the_selected_instance(tmp_path):
    source = """module Leaf(input logic c,a,output logic q);
      always @(posedge c) q<=a;
    endmodule
    module M(input logic clk,a,output wire [1:0] q);
      for(genvar g=0;g<2;g++) begin: bank
        wire clock_alias=clk;
        Leaf leaf(clock_alias,a,q[g]);
      end
    endmodule"""
    declarations = _declarations(tmp_path, source)
    assert {context.scope: context.triggers for context in declarations.contexts
            if context.process_start in declarations.process_starts} == {
        ("M", "bank[0]", "leaf"): (("posedge", "clk"),),
        ("M", "bank[1]", "leaf"): (("posedge", "clk"),),
    }


def test_same_spelling_does_not_make_an_internal_register_a_clock_alias(tmp_path):
    source = """module Leaf(input logic a, output logic q);
      logic clk=0;
      always @(posedge a) clk<=!clk;
      always @(posedge clk) q<=a;
    endmodule
    module M(input logic clk,a,output wire q);
      Leaf leaf(a,q);
    endmodule"""
    declarations = _declarations(tmp_path, source)
    assert [context.triggers for context in declarations.contexts] == [
        (("posedge", "a"),), None]
    with pytest.raises(ValueError, match="non-Driver-owned"):
        check_source_triggers(declarations, {("posedge", "a"), ("posedge", "clk")})
