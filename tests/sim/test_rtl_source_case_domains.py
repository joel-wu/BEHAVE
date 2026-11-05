"""Qualified cases keep their check unless the original activation proves it inert."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('qualifier', ['unique', 'unique0', 'priority'])
@pytest.mark.parametrize('kind,items', [
    ('case', '0,0:q=3; 1,1:q=7; default:q=9;'),
    ('casez', "4'b0???,4'b00??:q=3; 4'b1???:q=7;"),
    ('casex', "4'b0xxx,4'b00xx:q=3; 4'b1xxx:q=7;"),
])
@pytest.mark.eda('verilator', 'make')
def test_multiple_labels_in_one_item_do_not_violate_uniqueness(tmp_path, qualifier, kind, items):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) {qualifier} {kind}(a) {items} endcase
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('qualifier', ['unique', 'unique0'])
@pytest.mark.parametrize('kind,guard,items', [
    ('case', 'a!=0', '0:q=3; 0:q=7; default:q=9;'),
    ('casez', 'a>=8', "4'b0???:q=3; 4'b00??:q=7; default:q=9;"),
])
@pytest.mark.eda('verilator', 'make')
def test_actual_caller_can_exclude_an_overlapping_item(tmp_path, qualifier, kind, guard, items):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        if({guard}) {qualifier} {kind}(a) {items} endcase
      end
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('kind,items', [
    ('case', '0:q=3; 0:q=7; default:q=9;'),
    ('casez', "4'b0???:q=3; 4'b00??:q=7; default:q=9;"),
])
def test_default_does_not_excuse_a_live_overlap(tmp_path, kind, items):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) unique {kind}(a) {items} endcase
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    post, _, hits = _run_activation(lowered, names, {'a': 0, 'q': 0}, _encode(lowered),
                                    error='RTLCaseOverlap')
    assert post[names['q']] == 3
    assert any(hit.path[1] == 'artifact-failure' for hit in hits)


def test_multiple_item_predicate_is_exact_and_linear_size():
    from itertools import product

    from behave.sim.symbolic import ir
    from behave.sim.symbolic.interp import _Interp
    from behave.sim.rtl.source_case import _multiple_items

    for count in range(7):
        terms = [ir.Var(f'm{i}') for i in range(count)]
        predicate = _multiple_items(terms)
        for values in product((False, True), repeat=count):
            assert _Interp(dict(zip((term.name for term in terms), values))).value(predicate) == (sum(values) > 1)
    for count in (16, 128, 1024):
        predicate = _multiple_items([ir.Var(f'm{i}') for i in range(count)])
        pending, seen, depth = [(predicate, 0)], set(), 0
        while pending:
            node, level = pending.pop()
            depth = max(depth, level)
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, ir.Op):
                pending.extend((child, level + 1) for child in node.args)
        assert len(seen) <= 6 * count
        assert depth <= 3 * count.bit_length()


@pytest.mark.parametrize('qualifier', ['unique', 'priority'])
@pytest.mark.parametrize('setup,selector', [
    ('s=a&1;', 's'),
    ('s=a;', 's-s'),
])
@pytest.mark.eda('verilator', 'make')
def test_case_check_uses_preceding_assignments_and_relations(tmp_path, qualifier, setup, selector):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        logic [7:0] s;
        {setup}
        {qualifier} case({selector}) 0:q=3; 1:q=7; endcase
      end
    endmodule'''
    _native_agreement(tmp_path, source, initial={'s': 0})


@pytest.mark.eda('verilator', 'make')
def test_case_helper_can_require_an_actual_caller_precondition(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        unique case(v) 0:return 3; 1:return 7; endcase
        return 0;
      endfunction
      always @(posedge clk) begin q=0; if(a<2) q=f(a); end
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('body', [
    'unique case(a) 0:q=3; 1:q=7; endcase',
    'unique case(a) 0:q=3; 1:q=7; endcase q=a&1;',
    'q<=a&1; unique case(q) 0:r=3; 1:r=7; endcase',
])
def test_live_check_failures_are_not_removed_or_assumed_away(tmp_path, body):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      reg [7:0] r;
      always @(posedge clk) begin {body} end
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    post, _, hits = _run_activation(lowered, names, {'a': 3, 'q': 3, 'r': 0}, _encode(lowered),
                                    error='RTLCaseNoMatch')
    assert post[names['q']] == 3
    assert any(hit.path[1] == 'artifact-failure' for hit in hits)


def test_native_post_body_check_cannot_use_a_stale_selector_proof(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=a&1;
        unique case(q) 0:q=3; 1:q=7; endcase
      end
    endmodule'''
    with pytest.raises(Refused, match='stable native selector'):
        _lower(tmp_path, source)


@pytest.mark.eda('verilator', 'make')
def test_priority_no_match_action_does_not_reread_a_modified_selector(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=a&1;
        priority case(q) 0:q=3; 1:q=7; endcase
      end
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.eda('verilator', 'make')
def test_case_domain_proof_keeps_a_residual_goal_searchable(tmp_path):
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_batch

    from .test_rtl_source_batch import _lower as lower_batch
    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _boundary, _env, _goal

    source = '''module M(input clk,input [3:0] a,output reg [3:0] q);
      initial q=0;
      always @(posedge clk) begin
        logic [3:0] s;
        s=a&1;
        unique case(s) 0:q=3; 1:q=7; endcase
      end
    endmodule'''
    batch, plan = lower_batch(tmp_path, source)
    target = _goal(source, plan, 'case(s)', 'arm_1')
    baseline, hits, replay = _prepare_clocked_replay(tmp_path, source, plan)
    assert target not in hits[0]
    state, inputs, names = _boundary(batch, {'q': baseline[-1], 'a': 0, 's': 0}, (True,))
    answer = query_source_batch(batch, _env(batch), state, inputs, (target,),
                                mutable_inputs=(names['a'],), timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer
    assert answer.candidate[names['a']] & 1
    outputs, hits = replay([0, answer.candidate[names['a']]])
    assert outputs == [3, 7]
    assert target not in hits[0] and target in hits[1]
