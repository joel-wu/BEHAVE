"""Use invariant values, not their universal type ceiling, for source ranking."""

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.rtl.source_ir import _Lowerer
from behave.sim.rtl.source_while import LoopProgress


@pytest.mark.parametrize('write', [
    ir.Assign('n', ir.Lit(3)),
    ir.Store('n', ir.Lit(0), ir.Lit(3)),
    ir.For('n', ir.Lit(3), ir.Skip()),
])
def test_any_limit_write_keeps_universal_cutoff(write):
    value = ir.Op('add', ir.Var('n'), ir.Lit(1))
    assert _Lowerer.invariant_loop_limit(value, [ir.If(ir.Var('a'), write, ir.Skip())]) is None
    assert _Lowerer.invariant_loop_limit(value, [ir.Assign('other', ir.Var('n'))]) is value


def test_condition_capture_is_not_an_invariant_source_value():
    value = ir.Var('captured')
    assert _Lowerer.invariant_loop_limit(value, [ir.Assign('captured', ir.Var('n'))]) is None


@pytest.mark.parametrize('delta', [1, 2, -1, -3])
@pytest.mark.parametrize('inclusive', [False, True])
def test_symbolic_ranking_keeps_zero_negative_stride_and_wrap_obligations(delta, inclusive):
    progress = LoopProgress({}, delta, 7 if delta > 0 else -8, -8, 7, {}, inclusive)
    start, limit = ir.Var('start'), ir.Var('limit')
    count = progress.trips(start, limit)
    for initial in range(-8, 8):
        for stop in range(-8, 8):
            values = interp._Interp({'start': initial, 'limit': stop})
            cutoff = stop + ((1 if delta > 0 else -1) if inclusive else 0)
            expected = len(range(initial, cutoff, delta))
            assert values.value(count) == expected
            end = initial + expected * delta
            assert values.boolean(progress.in_domain(start, count)) == (-8 <= end <= 7)


@pytest.mark.eda('slang', 'verilator', 'make')
@pytest.mark.solver
@pytest.mark.parametrize('loop', [
    'for(i=0;i<n;i++) begin BODY end',
    'for(i=0;i<=n;i+=2) begin BODY end',
    'for(i=4;i>=n;i-=2) begin BODY end',
    'i=0; while(i<n) begin BODY i++; end',
    'i=0; do begin BODY i++; end while(i<n);',
])
def test_helper_integer_limit_keeps_outputs_all_hits_and_native_search(tmp_path, loop):
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_interval
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower, _names, _settle, _state, _step

    condition = 'v==7 && i==2'
    body = f'f=f+1; if({condition}) f=f+10; if(1\'b0) f=99;'
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, input integer n);
        integer i; f=0; {loop.replace('BODY', body)}
      endfunction
      always @(posedge clk) q<=f(a,2);
    endmodule'''
    # The source-facing local search entry deliberately retains domain checks.
    machine, plan = _lower(tmp_path, source, defer_domains=True)
    names = _names(machine)
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    template = SourceIntervalTemplate(machine.procedure, env)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    inputs = (0, 7)
    outputs, observed = replay(inputs)
    for a, output, native_hits in zip(inputs, outputs, observed, strict=True):
        state = _state(machine, {'clk': 0, 'a': 0, 'q': 0})
        state, _, hits, _, _ = _step(machine, state, 0, clk=1, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        assert state[names['q']] == output
        assert hits == native_hits
    target = _goal(source, plan, condition, 'true', 'control-choice')
    assert target not in observed[0]
    initial = _state(machine, {'clk': 0, 'a': 0, 'q': 0})
    answer = query_source_interval(machine, env, initial,
        {names['clk']: 0, names['a']: 0}, [target], mutable_inputs=(names['a'],),
        following_inputs=({names['clk']: 1, names['a']: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000,
        _relation=template.relation(machine, [target]))
    reachable = target in observed[1]
    assert answer.outcome is (probing.Outcome.SAT if reachable else probing.Outcome.LOCAL_UNSAT), answer.detail
    if reachable:
        assert answer.candidate[names['a']] == 7
        _, confirmed = replay((answer.candidate[names['a']],))
        assert target in confirmed[0]
