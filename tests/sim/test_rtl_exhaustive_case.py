"""Complete constant tables keep their goals without a live default query."""

import pytest

from behave.sim.symbolic import ir
from behave.sim.behavior.goal_projection import literal_false_goals
from behave.sim.rtl.source_case import constant_labels_exhaustive
from behave.sim.rtl.source_integral import IntegralType


@pytest.mark.parametrize('width,signed', [(1, False), (2, False), (3, True), (8, False)])
def test_coverage_uses_all_typed_values_and_deduplicates_labels(width, signed):
    kind = IntegralType(width, signed)
    low, high = kind.interval
    values = list(range(low, high + 1))
    labels = lambda xs: tuple((([], ir.Lit(x)),) for x in xs)
    assert constant_labels_exhaustive(kind, labels(values))
    assert constant_labels_exhaustive(kind, labels(values + [low, high, high + 1]))
    assert not constant_labels_exhaustive(kind, labels(values[:-1] + [low, high + 1]))
    assert not constant_labels_exhaustive(kind, ())


def test_nonconstant_or_effectful_labels_do_not_supply_a_coverage_proof():
    kind = IntegralType(1, False)
    assert not constant_labels_exhaustive(kind, ((([], ir.Var('x')),),))
    assert not constant_labels_exhaustive(kind, (
        (([ir.Assign('written', ir.Lit(1))], ir.Lit(0)),), (([], ir.Lit(1)),)))


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('signed,complete', [(False, True), (True, True), (False, False)])
@pytest.mark.parametrize('with_default', [False, True])
def test_native_outputs_hits_and_retained_fallback_agree(tmp_path, signed, complete, with_default):
    from .test_rtl_source_ir import _lower, _native_agreement

    selector = "$signed(a[1:0])" if signed else 'a[1:0]'
    values = range(4 if complete else 3)
    labels = '\n'.join(f"2'{('s' if signed else '')}b{n:02b}:q=8'd{n+1};" for n in values)
    fallback = 'default:q=9;' if with_default else ''
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) case({selector})
        {labels}
        {fallback}
      endcase
    endmodule'''
    lowered, _, _, _ = _lower(tmp_path, source)
    role = 'default' if with_default else 'no-match'
    marks = {mark for mark, key, _ in lowered.occurrences
             if key.path[1] == 'control-choice' and key.role == role}
    assert len(marks) == 1
    assert (marks <= literal_false_goals(lowered.procedure)) is complete
    # The declaration and its occurrence still exist in the IR, even when no
    # allowed native value can reach them. This is not catalogue pruning.
    assert marks <= {cmd.goal for cmd in ir.walk(lowered.procedure.body) if isinstance(cmd, ir.Mark)}
    _native_agreement(tmp_path, source)


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_unsized_labels_do_not_assume_the_narrower_spelling_is_the_comparison_type(tmp_path):
    from .test_rtl_source_ir import _lower

    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) case(a)
        0:q=1; 1:q=2; 2:q=3; 3:q=4;
        default:q=9;
      endcase
    endmodule'''
    lowered, _, _, _ = _lower(tmp_path, source)
    marks = {mark for mark, key, _ in lowered.occurrences
             if key.path[1] == 'control-choice' and key.role == 'default'}
    assert marks and not marks & literal_false_goals(lowered.procedure)


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('selector,values', [
    ('a[1:0]', (0, 1, 2, 3)), ('$signed(a[1:0])', (-2, -1, 0, 1)),
    ('a & 3', (0, 1, 2, 3)),
])
@pytest.mark.parametrize('complete', [False, True])
def test_widened_selector_keeps_universal_expression_range(tmp_path, selector, values, complete):
    from .test_rtl_source_ir import _lower, _native_agreement

    labels = ' '.join(f'{value}:q={index};' for index, value in
                      enumerate(values if complete else values[:-1]))
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) case({selector}) {labels} default:q=9; endcase
    endmodule'''
    lowered, _, _, _ = _lower(tmp_path, source)
    default = {mark for mark, key, _ in lowered.occurrences
               if key.path[1] == 'control-choice' and key.role == 'default'}
    assert default and (default <= literal_false_goals(lowered.procedure)) is complete
    _native_agreement(tmp_path, source)


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_widened_exhaustive_selector_keeps_unreachable_unknown_default(tmp_path):
    from .test_rtl_source_ir import _lower

    source = '''module M(input clk,input [1:0] a,output reg q);
      always @(posedge clk) case(a)
        0:q=0; 1:q=1; 2:q=0; 3:q=1; default:q='x;
      endcase
    endmodule'''
    lowered, _, _, _ = _lower(tmp_path, source)
    default = {mark for mark, key, _ in lowered.occurrences
               if key.path[1] == 'control-choice' and key.role == 'default'}
    assert default and default <= literal_false_goals(lowered.procedure)


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_exhaustive_bits_do_not_bypass_selector_definedness(tmp_path):
    from behave.sim.core.refusal import Refused

    from .test_rtl_source_ir import _lower

    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        automatic logic [1:0] selector;
        if(a[0]) selector=0;
        case(selector)
          2'd0:q=1; 2'd1:q=2; 2'd2:q=3; 2'd3:q=4;
          default:q=9;
        endcase
      end
    endmodule'''
    with pytest.raises(Refused):
        _lower(tmp_path, source)
