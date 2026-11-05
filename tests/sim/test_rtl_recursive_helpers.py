"""Recursive frames repeat source events; they do not create infinite goals."""

import pytest

from behave.sim.rtl.source_declarations import declare_source
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_source_declarations import _documents
from .test_rtl_source_locations import _prepare_clocked_replay

pytestmark = pytest.mark.eda('slang', 'verilator', 'make')

SELF = '''function automatic [7:0] f(input [3:0] v);
  if(v[3]) f=f({1'b0,v[2:0]})+8; else f=v;
endfunction'''
MUTUAL = '''function automatic [7:0] f(input [3:0] v);
  if(v[3]) f=g({1'b0,v[2:0]}); else f=v;
endfunction
function automatic [7:0] g(input [3:0] v);
  g=f(v)+1;
endfunction'''


def _source(helpers, body):
    return ('module M(input clk, input [3:0] a, output logic [7:0] q);'
            + helpers + ' always @(posedge clk) begin ' + body + ' end endmodule')


@pytest.mark.parametrize('helpers,body,expected', [
    (SELF, "q=f(a)+f(a^4'h8);", lambda a: 8+2*(a & 7)),
    (MUTUAL, "q=f(a)+g({1'b0,a[2:0]});", lambda a: 2*(a & 7)+1+(a >> 3)),
])
def test_recursive_automatic_frames_preserve_outputs_and_source_hits(tmp_path, helpers, body, expected):
    source = _source(helpers, body)
    cst, ast = _documents(tmp_path, source)
    declarations = declare_source(source, ast, filename='design.sv')
    plan = plan_source(source, cst, semantic_document=ast, enumerate_iterations=False)
    assert plan.declarations == declarations
    assert not plan.native_issues
    assert len(declarations.occurrences) < 100
    manifest = manifest_of(declarations, filename='design.sv')
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    outputs, hits = replay(range(16))
    assert outputs == [expected(a) for a in range(16)]
    seen = set().union(*hits)
    choices = [goal for goal in manifest.values() if goal.family == 'control-choice']
    assert {goal.event for goal in choices} == {'true', 'false'}
    assert all(goal.id in seen for goal in choices)
    assert any('recursive source calls' in issue for issue in plan.binding_issues)


def test_static_recursive_frame_is_not_silently_rewritten(tmp_path):
    source = _source(SELF.replace('automatic', 'static'), 'q=f(a);')
    _, ast = _documents(tmp_path, source)
    with pytest.raises(ValueError, match='automatic helper frames'):
        declare_source(source, ast, filename='design.sv')


def test_base_arm_hit_comes_from_recursive_frame_not_another_root_call(tmp_path):
    source = _source(SELF, 'q=f(a);')
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast, enumerate_iterations=False)
    manifest = manifest_of(plan.declarations, filename='design.sv')
    choices = {goal.event: goal.id for goal in manifest.values() if goal.family == 'control-choice'}
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    outputs, hits = replay([0, 8, 3])
    assert outputs == [0, 8, 3]
    assert [choices['true'] in row for row in hits] == [False, True, False]
    assert all(choices['false'] in row for row in hits)
