"""Case equality is exact on proved-defined domains, not X-as-zero semantics."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('expression,expected', [
    ("a === 4'd3", lambda a: int(a == 3)),
    ("a !== 4'd3", lambda a: int(a != 3)),
    ("|a === 1'bx", lambda a: 0),
    ("1'bz !== |a", lambda a: 1),
    ("a === 4'b0x01", lambda a: 0),
    ("1'bx === 1'bx", lambda a: 1),
    ("1'bx === 1'bz", lambda a: 0),
])
def test_case_equality_keeps_boolean_hits_and_exact_values(tmp_path, expression, expected):
    lowered, names, _, _ = _lower(tmp_path, f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) if({expression}) q=1; else q=0;
    endmodule''')
    encoded = _encode(lowered)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 99}, encoded)
        assert post[names['q']] == expected(a)
        assert {hit.role for hit in hits if hit.path[1] == 'control-choice'} == {
            'true' if expected(a) else 'false'}


@pytest.mark.parametrize('body', [
    "automatic logic t; if(a==1) t=1; q=(t === 1'bx);",
    "automatic logic t; t=a==1 ? 1'bx : 1'b0; q=(t === 1'bx);",
    "q=(a[7] === 1'bx);",
])
def test_case_equality_cannot_assume_undefined_values_known(tmp_path, body):
    with pytest.raises(Refused):
        _lower(tmp_path, f'''module M(input clk,input [3:0] a,output reg [7:0] q);
          always @(posedge clk) begin {body} end
        endmodule''')


@pytest.mark.eda('verilator', 'make')
def test_comparison_to_x_retains_helper_effects_and_native_goals(tmp_path):
    _native_agreement(tmp_path, '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x);
        q=q+1;
        if(x==3) return 7;
        return x;
      endfunction
      always @(posedge clk) begin
        q=a;
        if(f(a) === 4'bxxxx) q=99;
      end
    endmodule''')
