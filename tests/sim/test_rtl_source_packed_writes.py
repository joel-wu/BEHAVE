"""Typed packed updates: actual RTL values/events and exact queue histories."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize("declaration,selection", [
    ("[3:0]", "q[1]"), ("[3:0]", "q[2:1]"),
    ("[3:0]", "q[a[0]*2+:2]"), ("[3:0]", "q[3-a[0]*2-:2]"),
    ("[0:3]", "q[a[0]*2+:2]"), ("[0:3]", "q[3-a[0]*2-:2]"),
    ("[7:4]", "q[4+a[0]*2+:2]"), ("[4:7]", "q[7-a[0]*2-:2]"),
    ("[-2:1]", "q[-2+int'(a[0])*2+:2]"),
    ("[1:0][3:0]", "q[a[0]][a[2:1]]"),
])
@pytest.mark.eda("verilator", "make")
def test_packed_coordinates_match_native(tmp_path, declaration, selection):
    source = f"""module M(input clk,input [3:0] a,output logic {declaration} q);
      always @(posedge clk) begin q='1; {selection}=a[1:0]; end
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize("body", [
    "q[a[2:1]*2+:2]<=a[1:0]; q=8'hA5;",
    "q[7-a[2:1]*2-:2]<=a[1:0]; q[4:2]<=a[2:0];",
    "q<=8'h5A; q[a[2:1]*2+:2]<=a[1:0]; q=8'hF0;",
    "q[a[2:1]*2+:2]<=a[1:0]; q<=8'h5A;",
    "q[3:0]<=a; q[3:0]<=0; q[7:4]=4'hF;",
])
@pytest.mark.eda("verilator", "make")
def test_partial_nba_matches_native_commit_order_and_late_blocking_writes(tmp_path, body):
    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      always @(posedge clk) begin {body} end
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_selected_rhs_helper_executes_once_with_native_hits(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      function automatic [1:0] f(input [3:0] x);
        q=q+1;
        if(x[0]) return 2; else return 1;
      endfunction
      always @(posedge clk) q[a[2:1]*2+:2]=f(a);
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize("selection", ["q[a[2:0]+:2]", "q[a[2:0]-:2]",
                                      "q[$signed({1'b0,a})-3+:2]"])
def test_out_of_range_writes_do_not_claim_native_equivalence(tmp_path, selection):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) {selection}=a[1:0];
    endmodule"""
    with pytest.raises(Refused, match="packed-write-domain-unproved"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_write_domain_uses_actual_loop_and_conditional_counter_flow(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      always @(posedge clk) begin
        int n;
        n=0; q=0;
        for(int i=0;i<2;i++) begin
          q[7-n*2-:2]=a[1:0]; n=n+1;
          if(a[0]) begin q[7-n*2-:2]=a[3:2]; n=n+1; end
        end
      end
    endmodule"""
    # n is reset before use, so deliberately give the IR a nonzero entry.
    _native_agreement(tmp_path, source, initial={"n": 11})


def test_later_loop_write_must_also_have_a_proved_domain(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) begin
        int n;
        n=0;
        for(int i=0;i<3;i++) begin q[n*2+:2]=a[1:0]; n=n+1; end
      end
    endmodule"""
    with pytest.raises(Refused, match="packed-write-domain-unproved"):
        _lower(tmp_path, source)


def test_selected_lvalue_cannot_reorder_a_conflicting_rhs(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      integer i;
      function automatic [1:0] f(input [3:0] x); i=i+1; return x[1:0]; endfunction
      always @(posedge clk) begin i=0; q[i+:2]=f(a); end
    endmodule"""
    with pytest.raises(Refused, match="qualified evaluation order"):
        _lower(tmp_path, source)


def test_blocking_packed_update_retains_signed_storage_type(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic signed [3:0] q);
      always @(posedge clk) begin q=0; q[3]=a[0]; end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        assert post[names["q"]] == (-8 if a & 1 else 0)


def test_deferred_packed_update_retains_signed_storage_at_commit(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic signed [3:0] q);
      always @(posedge clk) begin q[3]<=a[0]; q=3; end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    write, = lowered.deferred
    assert write.signed and write.width == 4
    for a in range(16):
        post, outputs, _ = _run_activation(lowered, names, {"a": a, "q": -1}, enc)
        assert post[write.target] == 3
        assert outputs[write.mask] == 8
        bits = ((post[write.target] & ~outputs[write.mask])
                | (outputs[write.data] & outputs[write.mask])) & 15
        assert bits == (11 if a & 1 else 3)


@pytest.mark.eda("verilator", "make")
def test_packed_write_query_adds_a_native_hit_after_baseline(tmp_path):
    from behave.sim.symbolic import ranges
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_interval

    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower as lower_machine, _names, _state

    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [7:0] x;
      always @(posedge clk) begin x[7:4]<=a; x[3:0]=3; end
      always_comb if(x==8'h93) q=1; else q=0;
    endmodule"""
    machine, plan = lower_machine(tmp_path, source)
    names = _names(machine)
    goal = _goal(source, plan, "x==8'h93")
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values, hits = replay((0, 1, 2))
    assert values == [0, 0, 0] and all(goal not in observed for observed in hits)
    state = _state(machine, {"clk": 0, "a": 0, "x": 0, "q": 0})
    env = ranges.fixpoint(machine.procedure,
                         {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, state,
        {names["clk"]: 0, names["a"]: 0}, [goal], mutable_inputs=(names["a"],),
        following_inputs=({names["clk"]: 1, names["a"]: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer
    assert answer.candidate[names["a"]] == 9
    values, hits = replay((answer.candidate[names["a"]],))
    assert values == [1] and goal in hits[0]
