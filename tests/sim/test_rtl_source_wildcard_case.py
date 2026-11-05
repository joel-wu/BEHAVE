"""Source wildcard matching agrees on values, each native goal and queries."""

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_case import CasePattern

from .test_rtl_bounded_source_loops import _assert_closure, _lower

pytestmark = [pytest.mark.eda('slang', 'verilator', 'make'), pytest.mark.solver]


@pytest.mark.parametrize('operator,labels', [
    ('casez', "4'b1???,4'b0011"),
    ('casex', "4'b1xxx,4'b0011"),
    ('casez', "4'b1zzz,4'h3"),
])
def test_wildcard_priority_keeps_native_values_and_hits(tmp_path, record_property, operator, labels):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        {operator}(a)
          {labels}:q=1;
          4'b?1??:q=2;
          default:q=3;
        endcase
        if(1'b0) q=99;
      end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 1 if a >= 8 or a == 3 else 2 if a & 4 else 3)


def test_casez_does_not_turn_x_into_a_wildcard(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        casez(a) 4'b1x??:q=1; 4'b01??:q=2; default:q=3; endcase
        if(1'b0) q=99;
      end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 2 if 4 <= a < 8 else 3)


@pytest.mark.parametrize('width', [4, 23])
def test_unique_partition_is_proved_without_enumerating_words(tmp_path, record_property, width):
    labels = '\n'.join(f"{width}'b{'0'*n}1{'?'*(width-n-1)}:q={n+1};" for n in range(width))
    source = f'''module M(input clk,input [{width-1}:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        unique casez(a)
          {labels}
          {width}'d0:q=0;
        endcase
        if(1'b0) q=99;
      end
    endmodule'''
    # Visit every leading-one class. The shared helper queries the interval
    # spanned by these inputs, so sparse samples must still cover every arm.
    _assert_closure(tmp_path, record_property, source, (0, *(1 << i for i in range(width))),
                    lambda a: width - a.bit_length() + 1 if a else 0)


@pytest.mark.parametrize('body,a,error', [
    ("unique casez(a) 4'b1???:q=1; 4'b?1??:q=2; default:q=3; endcase", 12, 'RTLCaseOverlap'),
    ("unique casez(a) 4'b1???:q=1; endcase", 0, 'RTLCaseNoMatch'),
])
def test_wildcard_failure_is_not_an_assumption(tmp_path, body, a, error):
    from .test_rtl_source_ir import _encode, _lower as lower, _run_activation

    lowered, names, _, _ = lower(tmp_path, f'''module M(input clk,input [3:0] a,output reg [7:0] q);
          always @(posedge clk) begin {body} end endmodule''')
    _run_activation(lowered, names, {'a': a, 'q': 0}, _encode(lowered), error=error)


def test_undefined_selector_is_not_assumed_defined(tmp_path):
    body = "automatic logic [3:0] s; if(a==1) s=1; casez(s) 4'b1???:q=1; default:q=2; endcase"
    with pytest.raises(Refused):
        _lower(tmp_path, f'''module M(input clk,input [3:0] a,output reg [7:0] q);
          always @(posedge clk) begin {body} end
        endmodule''')


def test_wide_pattern_overlap_and_size_are_exact():
    assert CasePattern(128, 1 << 127, 1 << 127).size == 1 << 127
    assert not CasePattern(128, 1 << 127, 1 << 127).overlaps(CasePattern(128, 0, 1 << 127))
    assert CasePattern(128, 1 << 127, 1 << 127).overlaps(CasePattern(128, 1, 1))
    assert CasePattern(128, 0, 0, True).size == 0
