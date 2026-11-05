"""Statement attributes stay on the original control, never its observer wrapper."""

import pytest

from behave.sim.rtl.source_declarations import _nodes
from behave.sim.rtl.source_plan import SOURCE_ATTRIBUTES, plan_source
from behave.sim.rtl.source_syntax import walk

from .test_rtl_source_plan import _parse

pytestmark = pytest.mark.eda("slang")


def _attribute_tokens(node):
    return tuple(part["text"] for part in walk(node.get("attributes", []))
                 if isinstance(part, dict) and "text" in part)


@pytest.mark.parametrize("qualifier", ["", "unique", "unique0", "priority"])
@pytest.mark.parametrize("body", ["{case}", "if(a[3]) {case}", "begin {case} end"])
@pytest.mark.parametrize("default", ["", "default:q=0;"])
def test_case_attribute_remains_on_case_after_wrapping(tmp_path, qualifier, body, default):
    case = (f'(* parallel_case, user_note="original" *) {qualifier} case(a) '
            f'0:q=1; 1:q=2; {default} endcase')
    source = ("module M(input clk,input [3:0] a,output reg [1:0] q); "
              "always @(posedge clk) " + body.format(case=case) + " endmodule")
    tree = _parse(tmp_path / "source", source)
    original, = (node for node in _nodes(tree) if node.get("kind") == "CaseStatement")
    plan = plan_source(source, tree)
    for name, text in (("compiler", plan.compiler.text), ("native", plan.native().text)):
        parsed = _parse(tmp_path / name, text)
        selected, = (node for node in _nodes(parsed) if node.get("kind") == "CaseStatement")
        # Compiler annotations are additional attributes on that same case.
        tokens = _attribute_tokens(selected)
        original_tokens = _attribute_tokens(original)
        assert tokens[-len(original_tokens):] == original_tokens
        assert selected.get("uniqueOrPriority", {}).get("text", "") == qualifier
        assert all("parallel_case" not in _attribute_tokens(node)
                   for node in _nodes(parsed) if node.get("kind") == "SequentialBlockStatement")
        if name == "native" and qualifier and not default:
            assert not any(item["kind"] == "DefaultCaseItem" for item in selected["items"])


def test_conditional_statement_attribute_remains_on_if(tmp_path):
    source = '''module M(input clk,a,output reg q);
      always @(posedge clk) (* user_note="original" *) if(a) q=1; else q=0;
    endmodule'''
    plan = plan_source(source, _parse(tmp_path / "source", source))
    tree = _parse(tmp_path / "native", plan.native().text)
    attributed = [node for node in _nodes(tree) if node.get("attributes")]
    assert len(attributed) == 1
    assert attributed[0]["kind"] == "ConditionalStatement"
    assert "user_note" in _attribute_tokens(attributed[0])


@pytest.mark.parametrize("attribute", sorted(SOURCE_ATTRIBUTES | {"scopename"}))
def test_statement_attributes_cannot_forge_compiler_provenance(tmp_path, attribute):
    source = f'''module M(input clk,a,output reg q);
      always @(posedge clk) (* {attribute}="forged" *) case(a) 0:q=1; default:q=0; endcase
    endmodule'''
    with pytest.raises(ValueError, match="source attributes cannot override compiler provenance"):
        plan_source(source, _parse(tmp_path, source))


@pytest.mark.eda("verilator", "make")
@pytest.mark.solver
@pytest.mark.parametrize("qualifier", ["", "unique", "unique0", "priority"])
def test_attributed_case_outputs_and_goal_hits_match_native_execution(tmp_path, qualifier):
    from .test_rtl_source_ir import _native_agreement

    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) (* parallel_case *) {qualifier} case(a)
        0:q=5; 1:q=7; default:q=9;
      endcase
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.eda("yosys", "verilator", "make")
def test_combinational_parallel_case_reproducer(tmp_path):
    from behave.sim.rtl.native_events import prepare_native_events

    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_bindings import _documents

    source = '''module M(input logic [1:0] sel, output logic q);
always @(*) begin
    (* parallel_case *) case (sel)
        2'd0: q = 1'b0;
        2'd1: q = 1'b1;
        default: q = 1'b0;
    endcase
end
endmodule
'''
    plan, original, annotated = _documents(tmp_path, source)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=100)
    driver = '''
      for(unsigned sel=0;sel<4;sel++) {
        if(behave_native_events_begin_v1(sel)) return 1;
        dut.sel=sel; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(sel,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    plain = _trace(tmp_path / "plain", source, collector, driver)
    native = _trace(tmp_path / "native", plan.native().text, collector, driver, native=True)
    assert [row[0] for row in native] == [row[0] for row in plain] == [0, 1, 0, 0]
    for (_, bits, _), choice in zip(native, ("arm_0", "arm_1", "default", "default"), strict=True):
        assert {event.choice for event in plan.events
                if event.role == "case-arm" and bits & (1 << event.ordinal)} == {choice}
