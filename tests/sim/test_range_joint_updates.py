"""Loop replacement facts must not mix selected values with their sentinels."""

import pytest

from behave.sim.symbolic import ir, ranges, range_relations
from behave.sim.behavior.lowering import lower_procedure


def _source(*, after='', guard='        checked = 1 << pick',
            update='                    score = value\n                    pick = j',
            trip='4', extra='', result='score'):
    return f'''class Model:
    def reset(self): pass
    def process(self, a):
        score = 2147483647
        pick = -1
{extra}
        for j in range({trip}):
            if ((a >> (j + 2)) & 1) == 0:
                value = ((a & 3) + j) & 3
                if value < score:
{update}
{after}
{guard}
        return {{"y": {result}}}
'''


def _check_native(source):
    proc = lower_procedure(source, outputs=['y'], input_widths=(6,))
    after = ranges.procedure_post(proc, {'a': ranges.Range(0, 63)}, source_paths=True)
    bound = after[proc.output_vars['y']]
    native = {}
    exec(compile(source, 'joint_updates.py', 'exec'), native)  # noqa: S102
    outputs = []
    for value in range(64):
        try:
            result = native['Model']().process(value)['y']
        except ValueError:  # The actual Python negative-shift failure.
            continue
        outputs.append(result)
        assert bound.lo <= result <= bound.hi
    assert outputs
    return bound


@pytest.mark.parametrize('trip', ['4', '(a & 3)'])
@pytest.mark.parametrize('guard', [
    '        checked = 1 << pick',
    '        if pick < 0:\n            return {"y": 0}',
    '        if score == 2147483647:\n            return {"y": 0}',
])
def test_joint_update_is_refined_only_after_its_entry_case_is_excluded(trip, guard):
    bound = _check_native(_source(trip=trip, guard=guard))
    assert bound.lo >= 0 and bound.hi <= 3


@pytest.mark.parametrize('changes', [
    {'guard': ''},
    {'after': '        pick = 0'},
    {'after': '        score = 2147483647'},
    {'after': '        if a & 1:\n            pick = 0'},
    {'after': '        if a & 1:\n            score = 2147483647'},
    {'update': '                    score = value\n                pick = j'},
])
def test_a_missing_or_invalidated_joint_update_never_hides_a_bad_output(changes):
    bound = _check_native(_source(**changes))
    assert bound.hi == 2147483647


def test_saved_values_receive_the_same_refinement_as_their_live_copies():
    source = _source(after='        saved = score', result='saved')
    assert _check_native(source).hi <= 3


def test_a_read_before_write_is_not_a_per_iteration_alias():
    source = _source(extra='        previous = 100', update=(
        '                    score = previous\n'
        '                    pick = j\n'
        '                    previous = value'))
    assert _check_native(source).hi >= 100


def test_a_second_readonly_array_participates_in_the_same_update_group():
    source = _source(extra='        payloads = [0, 2, 1, 3]\n        identity = 63',
                     update=('                    score = value\n'
                             '                    identity = payloads[j]\n'
                             '                    pick = j'), result='identity')
    assert _check_native(source).hi <= 3


def test_alias_expansion_handles_deep_shared_dags_and_cycles():
    names = [f'x{i}' for i in range(4000)]
    body = ir._sequence([ir.Assign(names[0], ir.Var('a'))] + [
        ir.Assign(name, ir.Op('add', ir.Var(names[index - 1]), ir.Lit(1)))
        for index, name in enumerate(names[1:], 1)])
    loop = ir.For('index', ir.Lit(3), body)
    stable, _definitions, normalize = ranges._readonly_loop_expressions(
        loop, {'a': ranges.Range(0, 1)}, 3, frozenset(names))
    shared = ir.Var(names[-1])
    expanded = normalize(ir.Op('add', shared, shared))
    assert expanded.args[0] is expanded.args[1]
    assert ranges.of_expression(expanded, stable) == ranges.Range(7998, 8000)
    cyclic = ir.For('index', ir.Lit(3), ir._sequence([
        ir.Assign('x', ir.Var('y')), ir.Assign('y', ir.Var('x'))]))
    _stable, _definitions, normalize = ranges._readonly_loop_expressions(
        cyclic, {}, 3, {'x', 'y'})
    assert normalize(ir.Var('x')) is None


def test_conditions_require_all_incoming_paths_and_invalidate_marker_writes():
    markers = (('pick', -1),)
    facts = {markers: {(('score', 1),): ranges.Bounds(0, 3)}}
    env = {'pick': ranges.Range(0, 3), 'score': ranges.Range(0, 99)}
    assert range_relations.refine_conditions(env, facts)['score'].hi == 3
    assert not range_relations.join_conditions(facts, {})
    assert not range_relations.forget_conditions(facts, {'pick'})
    assert range_relations.refine_conditions(
        env, range_relations.forget_conditions(facts, {'score'}))['score'].hi == 99
