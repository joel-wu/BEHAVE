"""Report transitions in the existing scheduler; not deferred source admission.

The fixture lowers ordinary source storage/events, then supplies a report-bearing
typed activation explicitly. Source declaration/control binding is a separate
gate. This exercises the actual query relation, not a fixture event scheduler.
"""

from dataclasses import replace

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_reports import ReportCapture, SourceReport
from behave.sim.rtl.source_scheduler import _compose_source_scheduler

from .test_rtl_source_ir import _value_leaves
from .test_rtl_source_scheduler import _lower, _names, _state

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver,
              pytest.mark.usefixtures("search_lifetime")]


def _replace(command, target, replacement):
    if command is target:
        return replacement
    if isinstance(command, ir.Seq):
        return ir.Seq(_replace(command.first, target, replacement), _replace(command.second, target, replacement))
    if isinstance(command, ir.If):
        return ir.If(command.guard, _replace(command.then, target, replacement),
                     _replace(command.orelse, target, replacement))
    if isinstance(command, ir.For):
        return ir.For(command.index, command.trip_count, _replace(command.body, target, replacement))
    return command


def _machine(tmp_path, *, resume=False, regions=('observed',), terminal=False):
    trigger = 'posedge clk or b' if resume else 'posedge clk'
    machine, _ = _lower(tmp_path, f'''module M(input clk,input [3:0] a,output logic [3:0] q,r);
      logic [3:0] b;
      always @({trigger}) begin b<=a; q=a; end
      always @(q) r=q;
    endmodule''')
    program, names = machine.program, _names(machine)
    process = program.processes[0]
    store, = [cmd for cmd in ir.walk(process.activation) if isinstance(cmd, ir.Assign) and cmd.var == names['q']]
    reports = []
    for index, region in enumerate(regions):
        reports.append(SourceReport(0, (0, index), region, f'outcome{index}',
            (ReportCapture('held', types.INTEGER, ranges.Range(0, 15)),),
            ir._sequence([ir.Mark(f'entry{index}'),
                ir.Fail('RTLAssertion', f'failure{index}') if terminal else ir.Skip(),
                ir.Assign(names['q'], ir.Op('uwrap',
                    ir.Op('add', ir.Var('held'), ir.Var(names['b'])), ir.Lit(4)))]),
            ir.Assign(f'emit{index}', ir.Lit(0))))
    body = _replace(process.activation, store,
        ir._sequence([ir.Assign('held', ir.Var(names['a'])), *(report.emission for report in reports)]))
    process.procedure.types['held'] = types.INTEGER
    occurrences = tuple((f'{kind}{index}', None, f'goal:{kind}{index}')
                        for index in range(len(reports)) for kind in ('outcome', 'entry'))
    program.processes = (replace(process, activation=body,
        occurrences=(*process.occurrences, *occurrences)), *program.processes[1:])
    return _compose_source_scheduler(program, machine.subscriptions, machine.startup,
        reports=reports, transition_bound=12)


def _execute(machine, state, action, *, a=None, clk=None, symbolic=True):
    import z3

    names = _names(machine)
    pins = {name: state[name] for name, _ in machine.drive_inputs}
    if a is not None:
        pins[names['a']] = a
    if clk is not None:
        pins[names['clk']] = clk
    inputs = {driven: pins[name] for name, driven in machine.drive_inputs}
    inputs['action'] = action
    post, out, hits, error = interp.run_step(machine.procedure, inputs, state)
    if symbolic:
        env = {**machine.procedure.state_ranges, **machine.input_ranges}
        encoded = encoder.encode_step(machine.procedure, env=env)
        solver = z3.Solver()
        solver.add(encoded.constraint)
        for name, value in {**state, **inputs}.items():
            solver.add(*(term == cell for term, cell in _value_leaves(encoded.pre[name], value)))
        assert solver.check() == z3.sat
        model = solver.model()
        assert encoder.error_name(model.eval(encoded.error), encoded.error_codes) == error
        assert hits == {goal for goal, term in encoded.hits.items() if z3.is_true(model.eval(term))}
        for name, value in post.items():
            for term, cell in _value_leaves(encoded.post[name], value):
                actual = model.eval(term, model_completion=True)
                assert (z3.is_true(actual) == cell if z3.is_bool(actual)
                        else actual.as_long() == cell % (1 << actual.size()))
    return post, out.transactions, hits, error


def _zero_boundary(machine):
    return machine.boundary({name: 0 for name in machine.program.fields})


def _action(machine, region, owner):
    return next(code for part, who, code in machine.report_actions if (part, who) == (region, owner))


@pytest.mark.parametrize('resume', [False, True])
def test_report_waits_for_nba_and_late_write_notifies_source(tmp_path, resume):
    machine = _machine(tmp_path, resume=resume)
    names = _names(machine)
    state, _, _, _ = _execute(machine, _zero_boundary(machine), 0, a=3, clk=1)
    state, _, _, _ = _execute(machine, state, 1)
    before = state
    state, out, hits, _ = _execute(machine, state, _action(machine, 'observed', None))
    assert state == before and not out[0]['accepted'] and not hits
    state, _, _, _ = _execute(machine, state, machine.commit_action)
    if resume:
        assert state[machine.pending[0]]
        state, _, _, _ = _execute(machine, state, 1)
        assert list(state[machine.reports.status])[:2] == [0, 1]
        state, _, _, _ = _execute(machine, state, machine.commit_action)
    state, out, hits, _ = _execute(machine, state, _action(machine, 'observed', None))
    assert out[0]['accepted'] and 'outcome0' in hits and 'entry0' not in hits
    before = state
    state, out, hits, _ = _execute(machine, state, 0, a=9, clk=0)
    assert state == before and not out[0]['accepted'] and not hits
    state, out, hits, _ = _execute(machine, state, _action(machine, 'observed', 0))
    assert out[0]['accepted'] and 'entry0' in hits
    assert state[names['q']] == 6 and state[machine.pending[1]]
    assert not out[0]['quiescent']
    state, out, _, _ = _execute(machine, state, 2)
    assert state[names['r']] == 6 and out[0]['quiescent'] and machine.boundary_is_quiet(state)
    assert machine.event_bound is None
    assert machine.phase_event_bounds(state, ({},), (), 12) == (12,)


def test_final_report_cannot_run_before_reactive_and_its_notifications(tmp_path):
    machine = _machine(tmp_path, regions=('observed', 'final'))
    state, _, _, _ = _execute(machine, _zero_boundary(machine), 0, a=3, clk=1)
    for action in (1, machine.commit_action, _action(machine, 'observed', None)):
        state, _, _, _ = _execute(machine, state, action)
    for action in (_action(machine, 'final', None), _action(machine, 'final', 0)):
        after, out, hits, _ = _execute(machine, state, action)
        assert after == state and not out[0]['accepted'] and not hits
    state, _, _, _ = _execute(machine, state, _action(machine, 'observed', 0))
    after, out, _, _ = _execute(machine, state, _action(machine, 'final', None))
    assert after == state and not out[0]['accepted']
    state, _, _, _ = _execute(machine, state, 2)
    state, _, hits, _ = _execute(machine, state, _action(machine, 'final', None))
    assert hits == {'outcome1'}
    state, out, hits, _ = _execute(machine, state, _action(machine, 'final', 0))
    assert hits == {'entry1'} and out[0]['quiescent']


def test_local_query_uses_report_transitions_and_rejects_nonquiet_boundary(tmp_path):
    machine = _machine(tmp_path)
    names, initial = _names(machine), _zero_boundary(machine)
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    pins = {name: int(name == names['clk']) for name, _ in machine.drive_inputs}
    answer = query_source_interval(machine, env, initial, pins, ('goal:entry0',),
        mutable_inputs=(names['a'],), event_bound=12, timeout_ms=30000)
    assert answer.outcome is probing.Outcome.SAT
    answer = query_source_interval(machine, env, initial, pins, ('goal:entry0',),
        mutable_inputs=(names['a'],), event_bound=13)
    assert answer.outcome is probing.Outcome.UNSUPPORTED and 'report-frame' in answer.detail
    pending = dict(initial)
    pending[machine.reports.status] = [1, *([0] * (machine.reports.capacity-1))]
    answer = query_source_interval(machine, env, pending, pins, ('goal:entry0',),
        mutable_inputs=(names['a'],), event_bound=12)
    assert answer.outcome is probing.Outcome.UNSUPPORTED and 'quiescent' in answer.detail
