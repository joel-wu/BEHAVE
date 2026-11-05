"""Whole-domain confluence can specialize, but cannot replace, native checks."""

from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.observation_audit import SourceNativeComparison
from behave.sim.rtl.source_confluence import SourceOrderComparison, _proof_shape, _replica_coordinates

pytestmark = pytest.mark.solver


def _example(context=None, *, hidden=False):
    x, a, b = [z3.Int(name, ctx=context) for name in ('input', 'left', 'right')]
    false, true = z3.BoolVal(False, ctx=context), z3.BoolVal(True, ctx=context)
    def history(atom):
        return SimpleNamespace(constraints=(x >= 0, atom == x + 1), alive=true,
            unfinished=false, domain_faults={}, source=SimpleNamespace(memory=SimpleNamespace(conflicts=())))
    left, right = history(a), history(b)
    comparison = SourceOrderComparison(left, right, {'value': a != b})
    observations = ((a, b, true),)
    if hidden:
        p, q = z3.Bools('private_left private_right', ctx=context)
        observations += ((p, q, true),)
        comparison.differences['hidden'] = p != q
    object.__setattr__(comparison, '_observations', observations)
    return comparison, x, a, b


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('value', [0, 5, 17])
def test_admitted_confluence_still_checks_each_native_record(private_context, value):
    pair, x, a, _ = _example(z3.Context() if private_context else None)
    def solve(_phase, roots):
        return z3_check(roots, 1000)
    assert pair.audit(solve).status is AuditCheckStatus.AGREED
    fixed = (x == value,)
    for observed in (value + 1, value + 2):
        native = SourceNativeComparison(pair.left, (*pair.left.constraints, *fixed),
            {'value': a != observed}, {})
        calls = []
        def checked(phase, roots):
            calls.append(phase)
            return solve(phase, roots)
        answer = native.audit(checked, proved_literals=lambda model:
            pair.native_literals(pair.left, model, fixed, check=lambda: None))
        assert answer.status is (AuditCheckStatus.AGREED if observed == value + 1
                                 else AuditCheckStatus.INCONSISTENT)
        if observed == value + 1:
            assert calls == ['feasibility', 'native-source-certificate', 'native-certificate']


@pytest.mark.parametrize('cause', ['unfixed', 'bad-model', 'other-history', 'different-roots'])
def test_failed_preconditions_do_not_supply_literals(cause):
    pair, x, _, _ = _example()
    fixed = (x == 3,)
    model = z3_check((*pair.left.constraints, *fixed), 1000).model
    encoding = pair.left
    if cause == 'unfixed':
        fixed = ()
    elif cause == 'bad-model':
        model = z3.Model()
    elif cause == 'other-history':
        encoding = pair.right
    else:
        pair.right.constraints += (z3.BoolVal(True),)
    assert pair.native_literals(encoding, model, fixed, check=lambda: None) is None


@pytest.mark.parametrize('case', ['alias', 'swapped', 'literal', 'extract', 'function', 'quantifier'])
def test_alpha_replica_checks_reject_false_correspondence(case):
    a, b, c = z3.Ints('a b c')
    pairs = {'alias': ((a, b), (c, b)), 'swapped': ((a, b), (b, a)),
             'literal': ((z3.IntVal(1), z3.IntVal(2)),),
             'extract': ((z3.Extract(3, 0, z3.BitVec('a4', 8)),
                          z3.Extract(7, 4, z3.BitVec('b4', 8))),),
             'function': ((z3.Function('f', z3.IntSort(), z3.IntSort())(a), b),),
             'quantifier': ((z3.ForAll(a, a == a), z3.ForAll(b, b == b)),)}[case]
    assert _replica_coordinates(pairs, check=lambda: None) is None


@pytest.mark.parametrize('private', [False, True])
def test_universal_theorem_renaming_can_overlap_without_claiming_independence(private):
    ctx = z3.Context() if private else None
    x, y, z = z3.BitVecs('overlap_x overlap_y overlap_z', 8, ctx=ctx)
    left, right = z3.And(x == y, x != y), z3.And(y == z, y != z)
    pairs = ((left, right),)
    assert _replica_coordinates(pairs, check=lambda: None) is None
    assert _replica_coordinates(pairs, check=lambda: None, independent=False) is not None
    for term in (left, right):
        assert z3_check((term,), 1000).answer is SolverAnswer.UNSAT
    # A changed alias or literal is not a renaming, even for universal proofs.
    for defect in (z3.And(y == z, y != x), z3.And(y == z, y != 1)):
        assert _replica_coordinates(((left, defect),), check=lambda: None,
                                    independent=False) is None


@pytest.mark.parametrize('private', [False, True])
def test_proof_shape_is_only_a_renaming_invariant_prefilter(private):
    ctx = z3.Context() if private else None
    x, y, z = z3.BitVecs('shape_x shape_y shape_z', 8, ctx=ctx)
    shape = lambda term: _proof_shape(term, check=lambda: None)
    assert shape(z3.And(x == 3, x != 3)) == shape(z3.And(y == 3, y != 3))
    assert shape(x == x) != shape(y == z)
    assert _replica_coordinates(((x == x, y == z),), check=lambda: None,
                                independent=False) is None
    assert shape(z3.ForAll(x, x == x)) is None


def test_proof_shape_walks_shared_dags_and_honors_cancellation():
    root = z3.Bool('shape_dag_leaf')
    for _ in range(50):
        root = z3.Or(root, root)
    count = 0

    def check():
        nonlocal count
        count += 1
        assert count < 200

    assert _proof_shape(root, check=check) is not None
    def cancel():
        raise InterruptedError('shape cancelled')
    with pytest.raises(InterruptedError, match='shape cancelled'):
        _proof_shape(root, check=cancel)


@pytest.mark.parametrize('wide', [False, True])
def test_proof_shape_sampling_cost_does_not_grow_with_history(wide):
    x = z3.BitVec('large_shape_x', 8)
    root = x == 0
    if wide:
        root = z3.And(*(x == i for i in range(4096)))
    else:
        for _ in range(4096):
            root = z3.Or(root, root)
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        assert calls <= 128

    assert _proof_shape(root, check=check) is not None


def test_proof_shape_collision_beyond_sample_still_requires_exact_check():
    x = z3.BitVec('unsampled_shape_x', 8)
    common = tuple(x == i for i in range(128))
    left, right = z3.And(x == 7, *common), z3.And(x == 8, *common)
    assert _proof_shape(left, check=lambda: None) == _proof_shape(right, check=lambda: None)
    assert _replica_coordinates(((left, right),), check=lambda: None,
                                independent=False) is None


def test_guarded_or_unreadable_observations_supply_no_fact():
    pair, x, a, b = _example()
    p, q = z3.Bools('left_guard right_guard')
    object.__setattr__(pair, '_observations', ((p, q, z3.BoolVal(True)), (a, b, p)))
    fixed = (x == 3,)
    model = z3_check((*pair.left.constraints, *fixed), 1000).model
    facts = pair.native_literals(pair.left, model, fixed, check=lambda: None)
    assert facts is not None
    assert not any(term.eq(a) or term.eq(p) for term, _ in facts.replacements)


def test_replica_plan_never_retains_values_and_honors_cancel():
    pair, x, a, _ = _example()
    for value in (2, 9):
        fixed = (x == value,)
        model = z3_check((*pair.left.constraints, *fixed), 1000).model
        facts = pair.native_literals(pair.left, model, fixed, check=lambda: None)
        assert z3.is_true(z3.simplify(facts.substitute(a == value + 1)))
    def cancel():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        pair.native_literals(pair.left, model, fixed, check=cancel)


def test_shared_hidden_coordinate_must_be_fixed_too():
    pair, x, a, b = _example()
    h = z3.Int('hidden')
    pair.left.constraints = (a == x + h,)
    pair.right.constraints = (b == x + h,)
    model = z3_check((*pair.left.constraints, x == 1), 1000).model
    assert pair.native_literals(pair.left, model, (x == 1,), check=lambda: None) is None


def test_unresolved_certificate_does_not_credit_a_matching_model():
    pair, x, a, _ = _example()
    fixed = (x == 3,)
    native = SourceNativeComparison(pair.left, (*pair.left.constraints, *fixed), {'value': a != 4}, {})
    def solve(phase, roots):
        if phase == 'feasibility':
            return z3_check(roots, 1000)
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason='cancelled check')
    result = native.audit(solve, proved_literals=lambda model:
        pair.native_literals(pair.left, model, fixed, check=lambda: None))
    assert result.status is AuditCheckStatus.REFUSED


def test_unobserved_difference_is_not_removed_by_confluence():
    pair, x, a, _ = _example()
    fixed = (x == 3,)
    hidden = z3.Bool('unobserved_difference')
    native = SourceNativeComparison(pair.left, (*pair.left.constraints, *fixed),
                                    {'value': a != 4, 'hidden': hidden}, {})
    result = native.audit(lambda _, roots: z3_check(roots, 1000), proved_literals=lambda model:
        pair.native_literals(pair.left, model, fixed, check=lambda: None))
    assert result.status is AuditCheckStatus.REFUSED


def test_dead_image_cannot_become_a_native_value_fact():
    pair, x, a, b = _example()
    dead = z3.BoolVal(False)
    object.__setattr__(pair, '_observations', ((a, b, dead),))
    fixed = (x == 3,)
    model = z3_check((*pair.left.constraints, *fixed), 1000).model
    facts = pair.native_literals(pair.left, model, fixed, check=lambda: None)
    assert facts is not None and not any(term.eq(a) for term, _ in facts.replacements)


def test_partially_built_replica_plan_is_not_published():
    pair, x, _, _ = _example()
    fixed = (x == 3,)
    model = z3_check((*pair.left.constraints, *fixed), 1000).model
    count = 0
    def cancel():
        nonlocal count
        count += 1
        if count == 5:
            raise InterruptedError('cancelled replica validation')
    with pytest.raises(InterruptedError):
        pair.native_literals(pair.left, model, fixed, check=cancel)
    assert not pair._replicas_checked and pair._shared_coordinates is None
    assert pair.native_literals(pair.left, model, fixed, check=lambda: None) is not None
