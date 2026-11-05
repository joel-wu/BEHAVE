"""The contract comes from the task, and the task is the specification.

The behavior side read its input widths out of the candidate design's header, so a
candidate declaring a port narrower than the task narrowed the contract, and
narrowing the contract can only enlarge the set of goals reported unreachable:
every verdict was relative to an input space the thing under test had chosen. The
schema belongs to the task, and the specification is where the task states it.

These tests are about the reading, not about the population. What the population
says is in the commit that added this: 836 of 836 tasks declare an interface that
elaborates, and no candidate declares a port the task does not.
"""
import pytest

from behave.sim import task
from behave.sim.core.schema import Authority, InterfaceError, Reading

SPEC = """# A Thing

## Interface

### RTL interface

```systemverilog
module a_thing #(
    parameter int N = 4,
    parameter int W = 8
) (
    input  logic                 clk,
    input  logic                 rst,
    input  logic [N-1:0]         in_sel,
    input  logic signed [W-1:0]  in_a, in_b,
    output logic [N*W-1:0]       out_wide,
    output logic [$clog2(N)-1:0] out_which
);
```
"""


def _elaborates():
    from behave.sim.rtl import elaborate

    return elaborate.available()


_elaborator_absent = pytest.mark.skipif(
    not _elaborates(), reason="the elaborator is not installed")


def needs_elaborator(test):
    """Mark a schema test with both of the capabilities it actually consumes."""

    return pytest.mark.cosim(_elaborator_absent(test))


def test_the_interface_is_taken_from_the_heading_not_the_first_block():
    """A specification has other code in it. The block that matters is the one
    under the heading that says it is the interface."""
    spec = ("## Something else\n\n```systemverilog\nmodule other ();\n"
            "endmodule\n```\n" + SPEC)
    assert "module a_thing" in task.interface_source(spec)
    assert "module other" not in task.interface_source(spec)


def test_a_specification_with_no_interface_is_refused():
    """Rather than falling back to the candidate, which is the thing this
    exists to stop."""
    with pytest.raises(InterfaceError):
        task.interface_source("# A Thing\n\nno interface here\n")


@needs_elaborator
def test_a_width_over_the_parameters_is_a_number():
    """What the header reader refuses. 91 designs in the population declare a
    port whose width is written over the parameters the module declares, and
    substituting them here rather than asking the elaborator would have been a
    second SystemVerilog evaluator for a language whose evaluator is installed."""
    schema = task.schema_of_spec(SPEC)
    assert schema["in_sel"].width == 4               # N
    assert schema["out_wide"].width == 32            # N*W
    assert schema["out_which"].width == 2            # $clog2(N)


@needs_elaborator
def test_a_comma_continues_the_declaration():
    """Both names of `input logic signed [W-1:0] in_a, in_b` are eight bits and
    both are signed. The range and the signedness belong to the declaration, not
    to its first name."""
    schema = task.schema_of_spec(SPEC)
    for name in ("in_a", "in_b"):
        assert schema[name].width == 8, name
        assert schema[name].signed is True, name


@needs_elaborator
def test_the_schema_says_it_came_from_the_task():
    """The provenance note is descriptive; authority is a separate field."""
    assert "task" in task.schema_of_spec(SPEC).source
    assert "read from" not in task.schema_of_spec(SPEC).source


MODEL = ('class M:\n'
         '    def reset(self): pass\n'
         '    def process(self, in_a, in_b, in_sel):\n'
         '        return {"out_wide": (in_a + in_b) & 0xFFFFFFFF, '
         '"out_which": in_sel & 1}\n')

DESIGN = ("module a_thing (\n"
          "    input  logic       clk,\n"
          "    input  logic       rst,\n"
          "    input  logic [3:0] in_sel,\n"
          "    input  logic signed [7:0] in_a, in_b,\n"
          "    output logic [31:0] out_wide,\n"
          "    output logic [1:0]  out_which\n"
          ");\n"
          "endmodule\n")


@needs_elaborator
def test_a_contract_from_the_task_records_task_authority_and_elaboration():
    """The frozen schema records task authority and elaborated reading directly."""
    from behave.sim.behavior import instance as behavior_instance

    declared = behavior_instance.lower(MODEL, task.schema_of_spec(SPEC))
    assert declared.schema.authority is Authority.TASK
    assert declared.schema.reading is Reading.ELABORATED


@needs_elaborator
def test_channel_payload_names_do_not_create_clock_domains():
    from behave.sim.contract_of import contract_of_spec
    from behave.sim.core.contract import Kind

    spec = """\
## RTL interface

```systemverilog
module channel_names (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic in_reset,
    output logic out_valid, input logic out_ready,
    output logic out_clk
);
```
"""
    contract = contract_of_spec(
        spec, "channel names", assume_kind=Kind.TRANSACTION)

    assert [(clock.clock, clock.reset) for clock in contract.clocks] == [
        ("clk", "rst")]
    assert {
        field.port for endpoint in contract.endpoints for field in endpoint.fields
    } == {"in_reset", "out_clk"}


@needs_elaborator
def test_a_contract_read_off_the_candidate_still_says_so():
    """Both halves of it, and they are two things. Whose declaration it is
    decides whether the artifact under test defined the space its own verdicts
    are about; how it was read decides whether a reader that cannot elaborate was
    believed."""
    from behave.sim.behavior import instance as behavior_instance

    guessed = behavior_instance.lower(MODEL, DESIGN)
    assert guessed.schema.authority is not Authority.TASK
    assert guessed.schema.reading is Reading.HEADER_REGEX

    from behave.sim.rtl import elaborate

    built = behavior_instance.lower(
        MODEL, elaborate.schema_of_module(DESIGN, "the candidate"))
    assert built.schema.authority is not Authority.TASK, \
        "still the candidate's declaration"
    assert built.schema.reading is Reading.ELABORATED, "but not read by regex"


@needs_elaborator
def test_rewording_provenance_cannot_change_schema_authority():
    """B5's acceptance, and the reason B5 is not cosmetic.

    Authority and reading were once inferred from English prose.  They are fields now,
    and changing the note cannot alter them.
    """
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.core.schema import Authority, Reading
    from behave.sim.rtl import elaborate

    schema = elaborate.schema_of_module(DESIGN, "the candidate",
                                        authority=Authority.TASK)
    assert schema.reading is Reading.ELABORATED
    before = (schema.authority, schema.reading)

    for note in ("read from the candidate's header", "declared by the task",
                 "", None, "candidate"):
        schema.source = note
        after_instance = behavior_instance.lower(MODEL, schema)
        after = (after_instance.schema.authority, after_instance.schema.reading)
        assert after == before, note


@needs_elaborator
def test_a_design_built_from_helpers_gives_the_design_s_ports():
    """A design built from helpers is ordinary -- one in the population is an
    adder written as add1, add2, add4, add8 and then the sixteen-bit one -- and
    reading every module in the file gives the ports of whichever came first,
    which is a helper. The module nothing instantiates is the one wanted."""
    from behave.sim.rtl import elaborate

    source = ("module helper (input logic a, output logic y);\n"
              "  assign y = a;\n"
              "endmodule\n"
              "module top (input logic clk, output logic [3:0] q);\n"
              "  logic y;\n"
              "  helper h (.a(clk), .y(y));\n"
              "  assign q = {3'b0, y};\n"
              "endmodule\n")
    assert set(elaborate.schema_of_module(source).endpoints) == {"clk", "q"}


@needs_elaborator
def test_which_module_can_be_named_where_a_file_holds_two():
    """`top` is for choosing among modules that are tops, not for reaching into a
    hierarchy: the elaborator inlines what it can, so a helper it dissolved is not
    a module with ports any more. Two designs in one file are both tops, and
    without a name the choice between them is arbitrary."""
    from behave.sim.rtl import elaborate

    source = ("module one (input logic a, output logic [1:0] x);\n"
              "  assign x = {2{a}};\n"
              "endmodule\n"
              "module two (input logic [3:0] b, output logic y);\n"
              "  assign y = |b;\n"
              "endmodule\n")
    assert set(elaborate.schema_of_module(source, top="one").endpoints) \
        == {"a", "x"}
    assert set(elaborate.schema_of_module(source, top="two").endpoints) \
        == {"b", "y"}
    with pytest.raises(InterfaceError):
        elaborate.schema_of_module(source, top="three")


@needs_elaborator
def test_a_function_argument_is_not_a_port():
    """It carries a direction, so collecting every directed variable of a module
    collected the arguments of every function in it."""
    from behave.sim.rtl import elaborate

    source = ("module m (input logic [3:0] d, output logic [3:0] q);\n"
              "  function automatic [3:0] twice(input [3:0] a);\n"
              "    twice = a << 1;\n"
              "  endfunction\n"
              "  assign q = twice(d);\n"
              "endmodule\n")
    assert set(elaborate.schema_of_module(source).endpoints) == {"d", "q"}
