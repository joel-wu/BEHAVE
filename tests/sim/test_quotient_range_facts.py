"""Remainder precision uses Python floor semantics and live operand values."""
import itertools

import pytest

from behave.sim.symbolic import cfg, ir, ranges


def graph(instructions):
    return cfg.CFG({'entry': cfg.Block('entry', instructions, cfg.Return())}, 'entry')


def remainder(n='n', q='q', d=None):
    return ir.Op('sub', ir.Var(n), ir.Op('mul', ir.Var(q), d or ir.Var('d')))


@pytest.mark.parametrize('divisor', [-9, -3, -1, 1, 3, 9])
@pytest.mark.parametrize('named', [False, True])
@pytest.mark.parametrize('overwrite', [False, True])
def test_floor_remainder_encloses_every_signed_input(divisor, named, overwrite):
    denominator = ir.Var('d') if named else ir.Lit(divisor)
    target = 'n' if overwrite else 'r'
    program = graph([ir.Assign('q', ir.Op('fdiv', ir.Var('n'), denominator)),
                     ir.Assign(target, remainder(d=denominator))])
    got = ranges.graph_post(program, {'n': ranges.Range(-100, 100), 'd': ranges.Range(divisor, divisor)})[target]
    assert got == (ranges.Range(0, divisor - 1) if divisor > 0 else ranges.Range(divisor + 1, 0))
    assert all(got.lo <= n % divisor <= got.hi for n in range(-100, 101))


@pytest.mark.parametrize('name', ['n', 'q', 'd'])
def test_overwriting_an_operand_does_not_keep_an_old_remainder_relation(name):
    program = graph([ir.Assign('q', ir.Op('fdiv', ir.Var('n'), ir.Var('d'))),
                     ir.Assign(name, ir.Lit(11)), ir.Assign('r', remainder())])
    got = ranges.graph_post(program, {'n': ranges.Range(-12, 12), 'd': ranges.Range(3, 3)})['r']
    for n in range(-12, 13):
        values = {'n': n, 'q': n // 3, 'd': 3, name: 11}
        assert got.lo <= values['n'] - values['q'] * values['d'] <= got.hi


@pytest.mark.parametrize('denominator', [ranges.Range(-4, -1), ranges.Range(1, 4)])
def test_varying_divisor_does_not_acquire_a_constant_divisor_fact(denominator):
    program = graph([ir.Assign('q', ir.Op('fdiv', ir.Var('n'), ir.Var('d'))),
                     ir.Assign('r', remainder())])
    got = ranges.graph_post(program, {'n': ranges.Range(-12, 12), 'd': denominator})['r']
    for n, d in itertools.product(range(-12, 13), range(denominator.lo, denominator.hi + 1)):
        assert got.lo <= n % d <= got.hi


def test_quotient_fact_does_not_survive_a_disagreeing_predecessor():
    program = cfg.CFG({
        'entry': cfg.Block('entry', (), cfg.Branch(ir.Var('flag'), 'yes', 'no')),
        'yes': cfg.Block('yes', [ir.Assign('q', ir.Op('fdiv', ir.Var('n'), ir.Lit(3)))], cfg.Goto('join')),
        'no': cfg.Block('no', [ir.Assign('q', ir.Lit(100))], cfg.Goto('join')),
        'join': cfg.Block('join', [ir.Assign('r', remainder(d=ir.Lit(3)))], cfg.Return()),
    }, 'entry')
    got = ranges.graph_post(program, {'n': ranges.Range(-12, 12), 'flag': ranges.Range(0, 1)})['r']
    assert got.lo <= -312 and got.hi >= 2


def test_in_place_quotient_does_not_relate_new_dividend_to_its_old_value():
    program = graph([ir.Assign('n', ir.Op('fdiv', ir.Var('n'), ir.Lit(3))),
                     ir.Assign('r', ir.Op('sub', ir.Var('n'), ir.Op('mul', ir.Var('n'), ir.Lit(3))))])
    got = ranges.graph_post(program, {'n': ranges.Range(-12, 12)})['r']
    for n in range(-12, 13):
        assert got.lo <= -2 * (n // 3) <= got.hi


def test_non_floor_division_does_not_claim_python_remainder_bounds():
    from behave.sim.symbolic.range_relations import quotient

    env = {'n': ranges.Range(-12, 12)}
    assert quotient('q', ir.Op('tdiv', ir.Var('n'), ir.Lit(3)), env) is None
    assert quotient('q', ir.Op('fdiv', ir.Var('n'), ir.Lit(0)), env) is None


def test_exact_point_divisor_expression_and_affine_dividend():
    denominator = ir.Op('mul', ir.Var('d'), ir.Lit(2))
    numerator = ir.Op('add', ir.Op('mul', ir.Var('n'), ir.Lit(3)), ir.Lit(1))
    program = graph([ir.Assign('q', ir.Op('fdiv', numerator, denominator)),
                     ir.Assign('r', ir.Op('sub', numerator, ir.Op('mul', ir.Var('q'), denominator)))])
    got = ranges.graph_post(program, {'n': ranges.Range(-100, 100), 'd': ranges.Range(3, 3)})['r']
    assert got == ranges.Range(0, 5)
