"""Source lowering retains events, not redundant copies/control flags."""

import pytest

from behave.sim.symbolic import ir, typecheck as types

from .test_rtl_source_ir import _encode, _lower, _run_activation

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize("literal", ["4'd3", "-4'sd2", "4'hf"])
def test_literal_assignment_needs_no_value_snapshot(tmp_path, literal):
    source = f"""module M(input clk, output reg [3:0] q);
      always @(posedge clk) q = {literal};
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    post, _, hits = _run_activation(lowered, names, {"q": 0}, encoded)
    assert post[names['q']] == (3 if literal == "4'd3" else 14 if literal == "-4'sd2" else 15)
    assert any(hit.path[1] == 'location' for hit in hits)
    # Unary minus remains an evaluated operation, rather than a literal node.
    if not literal.startswith('-'):
        assignments = [cmd for cmd in ir.walk(lowered.activation) if isinstance(cmd, ir.Assign)]
        assert len(assignments) == 1
        assert assignments[0].var == names['q']


@pytest.mark.parametrize("early_return", [False, True])
def test_return_control_is_local_and_only_present_when_needed(tmp_path, early_return):
    body = 'if (v[0]) return v+2; f=v+1;' if early_return else 'f=v+1;'
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v); {body} endfunction
      function automatic [7:0] g(input [3:0] v);
        g=f(v); g=g+3;
      endfunction
      always @(posedge clk) q=g(a)+g(0);
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    if not early_return:
        assert not any(isinstance(cmd, ir.If) for cmd in ir.walk(lowered.activation))
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 0}, encoded)
        assert post[names['q']] == a + (9 if early_return and a & 1 else 8)
        assert hits


def test_constant_case_labels_need_no_row_scratch_and_keep_first_match_hits(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) case (a)
        1,2: q=7;
        2: q=6;
        2,3: q=8;
        4: q=9;
        default: q=0;
      endcase
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    assert types.BOOLEAN not in lowered.procedure.types.values()
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 5}, encoded)
        assert post[names['q']] == {1: 7, 2: 7, 3: 8, 4: 9}.get(a, 0)
        selected = [hit.role for hit in hits if hit.path[1] == 'control-choice']
        assert selected == ['arm_0' if a in {1, 2} else 'arm_2' if a == 3
                            else 'arm_3' if a == 4 else 'default']


def test_constant_helper_argument_does_not_override_a_ref_write(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic void bump(ref logic [3:0] v); v=v+1; endfunction
      function automatic [7:0] f(input logic [3:0] v,input logic change);
        if(change) bump(v);
        if(v==3) f=7; else f=9;
      endfunction
      always @(posedge clk) q=f(3,a[0]);
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 0}, encoded)
        assert post[names['q']] == (9 if a & 1 else 7)
        assert hits
