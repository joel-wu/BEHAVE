"""Later abstract loop visits cannot use an already-passed iteration index."""

import pytest

from behave.sim.symbolic import interp, ir, ranges


def _series():
    return ir.For('i', ir.Var('count'), ir.Seq(
        ir.Assign('k', ir.Op('add', ir.Var('i'), ir.Lit(1))), ir.Seq(
            ir.Assign('term', ir.Op('fdiv',
                ir.Op('mul', ir.Var('term'), ir.Lit(8)),
                ir.Op('mul', ir.Var('k'), ir.Var('k')))),
            ir.Assign('total', ir.Op('add', ir.Var('total'), ir.Var('term'))))))


@pytest.mark.parametrize('minimum,maximum', [(0, 32), (1, 32), (32, 32), (0, 0)])
def test_finite_nonlinear_recurrence_keeps_its_index_progress(minimum, maximum):
    seed = 1 << 40
    after = ranges.post(_series(), dict(count=ranges.Range(minimum, maximum),
                                      term=ranges.Range(seed, seed),
                                      total=ranges.Range(seed, seed)))
    for count in range(minimum, maximum + 1):
        term = total = seed
        for k in range(1, count + 1):
            term = term * 8 // (k * k)
            total += term
        assert after['term'].lo <= term <= after['term'].hi
        assert after['total'].lo <= total <= after['total'].hi
    assert after['total'].hi < 1 << 64


def test_suffix_index_does_not_disable_sound_fixed_point_exit():
    visits = []
    command = ir.Assign('value', ir.Lit(7))

    def observe(current, env):
        if current is command:
            visits.append(env['i'])

    after = ranges._post(ir.For('i', ir.Lit(32), command),
                         {'value': ranges.Range(7, 7)}, observe)
    assert after['value'] == ranges.Range(7, 7)
    assert visits == [ranges.Range(0, 31)]


def test_a_target_copied_from_the_index_does_not_disable_the_fixed_point_exit():
    # A source loop target is assigned from the index before any read. Its
    # suffix image changes every turn but never reaches the next turn. Nested
    # loops of this shape with 2**20 trips each used to run once per trip.
    visits = []
    command = ir.Assign('value', ir.Lit(7))
    body = ir.Seq(ir.Assign('target', ir.Var('i')), command)

    def observe(current, env):
        if current is command:
            visits.append(env['i'])

    after = ranges._post(ir.For('i', ir.Lit(32), body),
                         {'value': ranges.Range(7, 7)}, observe)
    assert after['value'] == ranges.Range(7, 7)
    assert after['target'] == ranges.Range(0, 31)
    assert visits == [ranges.Range(0, 31)]


def test_a_target_read_before_its_assignment_still_runs_each_turn():
    visits = []
    command = ir.Assign('target', ir.Var('i'))
    body = ir.Seq(ir.Assign('previous', ir.Var('target')), command)

    def observe(current, env):
        if current is command:
            visits.append(env['i'])

    after = ranges._post(ir.For('i', ir.Lit(32), body),
                         {'target': ranges.Range(0, 0)}, observe)
    # The 32nd turn saves the 31st turn's target.
    assert after['previous'].lo <= 30 <= after['previous'].hi
    assert len(visits) == 32


@pytest.mark.parametrize('nested', [False, True])
def test_suffix_ranges_enclose_variable_trips_and_nested_index_reads(nested):
    index = ir.Var('j' if nested else 'i')
    divisor = ir.Op('add', index, ir.Lit(1))
    update = ir.Assign('value', ir.Op('fdiv',
        ir.Op('add', ir.Op('mul', ir.Var('value'), ir.Lit(3)), ir.Var('i')),
        divisor))
    body = ir.For('j', ir.Lit(5), update) if nested else update
    command = ir.For('i', ir.Var('count'), body)
    after = ranges.post(command, {'value': ranges.Range(-4, 4),
                                  'count': ranges.Range(0, 20)})
    for count in range(21):
        for value in range(-4, 5):
            machine = interp._Interp({'count': count, 'value': value})
            machine.run(command, set())
            assert after['value'].lo <= machine.rho['value'] <= after['value'].hi


def test_suffix_fixed_point_does_not_exclude_later_failure_or_normal_exit():
    body = ir.If(ir.Op('eq', ir.Var('i'), ir.Lit(18)),
                 ir.Fail('stop', 'failure'), ir.Assign('value', ir.Lit(7)))
    command = ir.For('i', ir.Var('count'), body)
    after = ranges.post(command, {'value': ranges.Range(7, 7),
                                  'count': ranges.Range(0, 32)})
    assert after['value'] == ranges.Range(7, 7)
    for count in range(33):
        machine = interp._Interp({'count': count, 'value': 7})
        if count >= 19:
            with pytest.raises(interp.Halt):
                machine.run(command, set())
        else:
            machine.run(command, set())
            assert after['value'].lo <= machine.rho['value'] <= after['value'].hi
