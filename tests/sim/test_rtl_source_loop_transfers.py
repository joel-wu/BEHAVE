"""Lexically scoped loop transfers: every goal, numeric oracle and native hit."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_bounded_source_loops import _assert_closure, _lower
from .test_rtl_source_ir import _encode, _run_activation

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("loop,expected", [
    ("for(i=0;i<4;i++) begin if(i==a) break; q=q+1; end q=q+8*i;",
     lambda a: 9*min(a,4)),
    ("for(i=0;i<a;i++) begin if(i==3) break; q=q+i; end q=q+8*i;",
     lambda a: sum(range(min(a,3)))+8*min(a,3)),
    ("for(i=0;i<4;i++) begin if(i==2) continue; q=q+i+1; end q=q+8*i;",
     lambda a: 39),
    ("for(i=0;i<6;i++) begin if(i==a) break; if(i&1) continue; q=q+i; end",
     lambda a: sum(i for i in range(min(a,6)) if not i&1)),
    ("repeat(a) begin i++; if(i==3) break; q=q+1; end q=q+16*i;",
     lambda a: min(a,2)+16*min(a,3)),
    ("repeat(a) begin i++; if(i&1) continue; q=q+i; end",
     lambda a: sum(i for i in range(1,a+1) if not i&1)),
    ("while(i<a) begin if(i==3) break; q=q+1; i++; end q=q+8*i;",
     lambda a: 9*min(a,3)),
    ("while(i<a) begin i++; if(i&1) continue; q=q+i; end",
     lambda a: sum(i for i in range(1,a+1) if not i&1)),
    ("do begin if(i==3) break; q=q+1; i++; end while(i<a);",
     lambda a: min(max(1,a),3)),
    ("do begin i++; if(i&1) continue; q=q+i; end while(i<a);",
     lambda a: sum(i for i in range(1,max(1,a)+1) if not i&1)),
    ("do begin q=7; continue; q=99; end while(0);", lambda a: 7),
    ("do begin q=7; break; q=99; end while(0);", lambda a: 7),
    (("for(i=0;i<3;i++) begin for(j=0;j<4;j++) begin "
      "if(j==a) break; if(j==1) continue; q=q+1; end q=q+8; end"),
     lambda a: 24+3*sum(j!=1 for j in range(min(a,4)))),
    (("repeat(3) begin i=0; while(i<a) begin i++; if(i==3) break; "
      "if(i==1) continue; q=q+1; end q=q+8; end"),
     lambda a: 24+3*int(a>=2)),
    ("while(i<a) begin repeat(2) begin if(a[0]) continue; q=q+1; end i++; end",
     lambda a: 0 if a&1 else 2*a),
    (("for(i=0;i<4;i++) begin case(i) 0: continue; 2: if(a[0]) break; "
      "default: q=q+1; endcase q=q+8; end"),
     lambda a: 9 if a&1 else 26),
])
def test_loop_transfer_whole_catalogue(tmp_path, record_property, loop, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      int i,j;
      always @(posedge clk) begin
        q=0; i=0; j=0; {loop} if(1'b0) q=99;
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("loop,tests", [
    ("for(i=0;i<bound(v);i++) begin if(i==2) break; if(i==0) continue; f=f+1; end",
     lambda a: min(a+1,3)),
    ("while(i<bound(v)) begin i++; if(i==2) break; if(i==1) continue; f=f+1; end",
     lambda a: min(a+1,2)),
    ("do begin i++; if(i==2) break; if(i==1) continue; f=f+1; end while(i<bound(v));",
     lambda a: 1),
    ("repeat(bound(v)) begin i++; if(i==2) break; if(i==1) continue; f=f+1; end",
     lambda a: 1),
])
def test_transfer_keeps_header_effects_and_function_frame(
        tmp_path, record_property, loop, tests):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      reg [3:0] calls;
      function automatic [3:0] bound(input [3:0] v); calls=calls+1; return v; endfunction
      function automatic [7:0] f(input [3:0] v);
        int i; i=0; f=0; {loop} if(1'b0) f=99;
      endfunction
      always @(posedge clk) begin calls=0; q=f(a); q=q+(calls<<4); end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 16*tests(a)+int(loop.startswith('for') and a>=2))


@pytest.mark.eda("verilator", "make")
def test_helper_break_continue_and_return_do_not_escape_to_caller(tmp_path, record_property):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int j; f=0;
        for(j=0;j<4;j++) begin
          if(j==v) break;
          if(j==1) continue;
          if(j==2) return 5;
          f=f+1;
        end
        if(1'b0) f=99;
      endfunction
      always @(posedge clk) begin
        q=0;
        for(int i=0;i<3;i++) begin q=q+f(a); q=q+8; end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 24+3*(0 if a==0 else 1 if a<=2 else 5))


@pytest.mark.parametrize("loop,defined", [
    ("for(i=0;i<2;i++) begin break; f=7; end", False),
    ("for(i=0;i<2;i++) begin continue; f=7; end", False),
    ("for(i=0;i<2;i++) begin if(v) break; f=7; end", False),
    ("for(i=0;i<2;i++) begin if(v) continue; f=7; end", False),
    ("for(i=0;i<2;i++) begin f=7; break; end", True),
    ("for(i=0;i<2;i++) begin f=7; continue; end", True),
    ("repeat(2) begin break; f=7; end", False),
    ("repeat(2) begin continue; f=7; end", False),
    ("repeat(2) begin f=7; break; end", True),
    ("repeat(2) begin f=7; continue; end", True),
    ("repeat(2) begin case(v) 0: break; default: f=7; endcase end", False),
    ("do begin break; f=7; end while(0);", False),
    ("do begin continue; f=7; end while(0);", False),
    ("do begin f=7; break; end while(0);", True),
    ("do begin f=7; continue; end while(0);", True),
    ("repeat(2) begin repeat(2) break; f=7; end", True),
    ("repeat(2) begin if(v) return 7; f=7; break; end", True),
    ("do begin break; end while(f & 0); f=7;", True),
    ("do begin continue; end while(f & 0); f=7;", False),
])
def test_return_storage_follows_abrupt_exits(tmp_path, loop, defined):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v); int i; i=0; {loop} endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    if not defined:
        with pytest.raises(Refused, match="return-undefined"):
            _lower(tmp_path, source)
        return
    _, lowered, names = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {n: a if n=="a" else 0 for n in names}, encoded)
        assert post[names['q']] == 7


@pytest.mark.parametrize("body", [
    "if(a[0]) continue; i++;",
    "case(a) 0: continue; default: q=q+1; endcase i++;",
    "if(a[0]) i++; else continue;",
    "i++; if(a[0]) i++;",
])
@pytest.mark.parametrize("posttest", [False, True])
def test_unproved_backedge_is_not_made_finite_by_a_cap(tmp_path, body, posttest):
    loop = f"do begin {body} end while(i<a);" if posttest else f"while(i<a) begin {body} end"
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      int i; always @(posedge clk) begin q=0; i=0; {loop} end
    endmodule"""
    with pytest.raises(Refused, match='rtl-source-ir-operation-unsupported'):
        _lower(tmp_path, source)


@pytest.mark.parametrize('at_header', [False, True])
def test_for_proof_checks_helper_writes_to_the_bound_counter(tmp_path, at_header):
    bound = 'f(a)' if at_header else 'a'
    body = 'q=q+1;' if at_header else 'q=f(a);'
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      int i;
      function automatic [3:0] f(input [3:0] v); i=i-1; return v; endfunction
      always @(posedge clk) begin q=0; for(i=0;i<{bound};i++) begin {body} end end
    endmodule'''
    # Header operands already conflict: reading i and changing it in f has no
    # qualified SV evaluation order. A body call reaches the exit proof: its
    # decrement cancels the header increment, so the header bound is invalid.
    reason = ('operand effects need a qualified evaluation order' if at_header
              else 'body-modified for counter needs proof of exit within its header bound')
    with pytest.raises(Refused, match=reason):
        _lower(tmp_path, source)


def test_transfer_scan_obeys_loop_scopes_in_lists_and_tuples():
    from behave.sim.rtl.source_loops import loop_transfers

    body = [{'kind': 'Conditional', 'ifTrue': {'kind': 'Continue'}},
            {'kind': 'RepeatLoop', 'body': {'kind': 'Break'}}]
    assert loop_transfers(body) == loop_transfers(tuple(body)) == {'Continue'}
