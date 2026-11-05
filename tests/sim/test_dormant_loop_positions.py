"""A loop inactive at one bounded position still has a sound encoding plan."""

from behave.sim.symbolic import closure_proof

import pytest
import z3

from behave.sim.behavior import instrumented, residual
from behave.sim.symbolic import encoder, interp
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

pytestmark = pytest.mark.solver


def _source(count, may_fail):
    return f'''class M:
    def reset(self):
        self.phase = 0
        self.value = 0
    def process(self, a):
        out = {{'y': self.value}}
        if self.phase == 0:
            value = {'0 // a' if may_fail else '0'}
            for i in range({count}):
                value += a
            self.value = value & 15
        self.phase = (self.phase + 1) & 3
        return out
'''


def _schema():
    return TransactionSchema(
        [Endpoint('a', 'input', 2), Endpoint('y', 'output', 4)],
        authority=Authority.TASK, reading=Reading.ELABORATED)


@pytest.mark.parametrize('count', ['3', 'a & 3'])
@pytest.mark.parametrize('bound', [2, 6])
@pytest.mark.parametrize('may_fail', [False, True])
def test_inactive_source_loop_at_later_position(count, bound, may_fail):
    source = _source(count, may_fail)
    obj = lower(source, _schema(), bound=bound)
    source_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=source_plan)
    native = namespace['M']()
    positions = closure_proof.prepare_positions(obj.proc, obj.env, bound)
    run = residual.unroll(obj.proc, obj.env, bound, plan=positions)
    solver = z3.Solver()
    solver.add(run.constraint)
    assert solver.check() == z3.sat
    for value in range(1 if may_fail else 0, 4):
        state = dict(obj.proc.initial)
        native.reset()
        solver.push()
        differences = []
        for step in run.encodings:
            native_hits.clear()
            expected = native.process(value)
            state, output, hits, error = interp.run_step(obj.proc, {'a': value}, state)
            assert not error
            assert output.transactions == [expected]
            assert hits == set(native_hits)
            solver.add(step.pre['a'] == value)
            differences.extend((
                z3.BV2Int(step.post['y'][0], is_signed=True) != output.transactions[0]['y'],
                *(term != (goal in hits) for goal, term in step.hits.items())))
        assert solver.check() == z3.sat
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        solver.pop()

    if may_fail:
        # A zero denominator ends the run, not just this loop. Later padded
        # steps must not manufacture either outputs or goal hits.
        native.reset()
        native_hits.clear()
        with pytest.raises(ZeroDivisionError) as caught:
            native.process(0)
        safety = instrumented.attribute_safety_failure(
            caught.value, source_plan.safety_sites)
        assert safety is not None
        expected_hits = set(native_hits) | {safety}
        _state, _output, hits, error = interp.run_step(obj.proc, {'a': 0})
        assert error and hits == expected_hits
        solver.push()
        solver.add(*(step.pre['a'] == 0 for step in run.encodings))
        assert solver.check() == z3.sat
        solver.add(z3.Or(run.completed, *(z3.Not(halt) for halt in run.halted[1:]),
                         *(run.goal_condition(goal) != (goal in expected_hits)
                           for goal in run.goals)))
        assert solver.check() == z3.unsat
        solver.pop()


def test_position_proof_is_not_applied_to_arbitrary_initial_terms():
    obj = lower(_source('3', True), _schema(), bound=2)
    positions = closure_proof.prepare_positions(obj.proc, obj.env, 2)
    initial = {name: z3.BitVec('external_' + name, positions.plan.scalar_width)
               for name in obj.proc.state}
    # The position proof starts at reset; arbitrary supplied state does not.
    # Reject an incomplete representation rather than assuming it equals reset.
    with pytest.raises(encoder.EncodingError):
        residual.unroll(obj.proc, obj.env, 2, initial=initial, plan=positions)


def test_reactivated_loop_failure_absorbs_only_its_actual_suffix():
    source = _source('a & 3', True)
    values = (1, 0, 2, 3, 0, 1)
    obj = lower(source, _schema(), bound=len(values))
    positions = closure_proof.prepare_positions(obj.proc, obj.env, len(values))
    run = residual.unroll(obj.proc, obj.env, len(values), plan=positions)
    source_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=source_plan)
    native = namespace['M']()
    native.reset()
    state = dict(obj.proc.initial)
    hits, differences = set(), []
    for index, value in enumerate(values[:5]):
        native_hits.clear()
        if index == 4:
            with pytest.raises(ZeroDivisionError) as caught:
                native.process(value)
            safety = instrumented.attribute_safety_failure(caught.value, source_plan.safety_sites)
            assert safety is not None
            native_hits.append(safety)
        else:
            expected = native.process(value)
        state, output, step_hits, error = interp.run_step(obj.proc, {'a': value}, state)
        assert bool(error) == (index == 4)
        assert step_hits == set(native_hits)
        hits.update(step_hits)
        if not error:
            assert output.transactions == [expected]
            differences.append(z3.BV2Int(run.encodings[index].post['y'][0],
                                        is_signed=True) != expected['y'])
    solver = z3.Solver()
    solver.add(run.constraint, *(step.pre['a'] == value
                                 for step, value in zip(run.encodings, values, strict=True)))
    assert solver.check() == z3.sat
    differences.extend(halted != (index >= 5) for index, halted in enumerate(run.halted))
    differences.extend(run.goal_condition(goal) != (goal in hits) for goal in run.goals)
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
