"""The closed query view preserves all scheduler choices, not one trace."""

import pytest
import z3

from behave.sim.symbolic import ranges
from behave.sim.symbolic.step_template import StepTemplate, _leaves

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('body,target', [
    (('reg [2:0] cells[0:1]; '
     'always @(posedge clk) begin cells[a[0]]=a; if(cells[0]==3) q<=a; end '
     'always @(negedge clk) r<=q;'), 'cells[0]==3'),
    (('always @(posedge clk) begin if(a==3) $fatal(1,"stop"); q<=a; end '
     'always @(negedge clk) if(q==2) r=1; else r=0;'), 'q==2'),
    (('reg pulse; always @(posedge clk) begin pulse=a[0]; pulse=0; end '
     'always @(posedge pulse) if(a==3) q<=1; else q<=0; '
     'always @(negedge clk) r<=q;'), 'a==3'),
    (('function automatic [2:0] f(input [2:0] v); '
     'if(v==3) f=1; else f=0; endfunction '
     'always_comb q=f(a)+f(a+1); always @(posedge clk) r<=q;'), 'v==3'),
])
def test_inductive_scheduler_miter(tmp_path, body, target):
    source = 'module M(input clk,input [2:0] a,output reg [2:0] q,r);' + body + ' endmodule'
    machine, plan = _lower(tmp_path, source)
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    template = StepTemplate(proc, env)
    goal = _goal(source, plan, target)
    groups = {goal: [mark for mark, _, name in machine.occurrences if name == goal]}
    full = template.project(groups)
    view = template.project(groups, outputs=('accepted', 'quiescent'),
                            state_reads=dict(machine.drive_inputs))
    assert len(view._layout['state_post']) < len(full._layout['state_post'])
    for field in ('constraint', 'ok', 'error'):
        assert full._image.arg(full._layout[field]).eq(view._image.arg(view._layout[field]))
    solver = z3.Solver()
    # Arbitrary legal pre-state and action, with only retained state/inputs
    # shared. Dropped storage is independently symbolic on the other side.
    # Equality of observations *and* retained successors establishes induction
    # over any number/order of steps, not just one enumerated driver trace.
    left = full.encode()
    kept = set(view._layout['state_post']) | set(proc.inputs)
    right = view.encode({name: value for name, value in left.pre.items() if name in kept})
    solver.add(left.constraint, right.constraint)
    differences = [left.ok != right.ok, left.error != right.error,
                   left.hits[goal] != right.hits[goal]]
    for name in right.state_post:
        differences.extend(a != b for a, b in zip(
            _leaves(left.state_post[name]), _leaves(right.state_post[name]), strict=True))
    for name in ('accepted', 'quiescent'):
        differences.extend(a != b for a, b in zip(
            _leaves(left.post[name]), _leaves(right.post[name]), strict=True))
    assert solver.check() == z3.sat
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
