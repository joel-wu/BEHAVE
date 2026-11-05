"""Joint native proofs preserve all six obligations, including non-vacuity."""

import itertools
from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check
from behave.sim.core.audit import AuditCheckStatus, AuditedWitness
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.observation_audit import NativeProofCache, SourceNativeComparison
from behave.sim.rtl.source_relation import SourceObservation

pytestmark = pytest.mark.solver
PHASES = ['feasibility', 'completeness', 'definedness', 'memory', 'membership', 'agreement']


def _proof_witness(value=0):
    return AuditedWitness(domain='test-domain', inputs=[{'x': value}],
        states=[{'x': 0}, {'x': value}], outputs=[[]], hit_sets=[set()])


@pytest.mark.parametrize('change', ['none', 'owner', 'root', 'root-order', 'name',
    'predicate', 'predicate-order', 'witness', 'extra', 'request', 'scope'])
def test_compact_native_proof_requires_all_original_evidence(change):
    original = _comparison()
    original.differences['second'] = z3.BoolVal(False)
    cache = NativeProofCache()
    witness = _proof_witness()
    first = cache.identity(original, witness, check=lambda: None)
    cache.remember(b'input', first, check=lambda: None)
    current = replace(original, constraints=tuple(original.constraints),
                      differences=dict(original.differences))
    key = b'input'
    if change == 'owner':
        current = replace(current, encoding=SimpleNamespace(**vars(original.encoding)))
    elif change == 'root':
        current = replace(current, constraints=(*current.constraints, z3.BoolVal(False)))
    elif change == 'root-order':
        current = replace(current, constraints=tuple(reversed(current.constraints)))
    elif change == 'name':
        current.differences['renamed'] = current.differences.pop('second')
    elif change == 'predicate':
        current.differences['second'] = z3.BoolVal(True)
    elif change == 'predicate-order':
        current = replace(current, differences=dict(reversed(tuple(current.differences.items()))))
    elif change == 'extra':
        current.differences['hidden'] = z3.BoolVal(False)
    elif change == 'witness':
        witness = _proof_witness(1)
    elif change == 'request':
        key = b'other-input'
    elif change == 'scope':
        current = replace(current, observation_scope=SourceObservation.GOAL_PREFIX)
    second = cache.identity(current, witness, check=lambda: None)
    assert cache.contains(key, second, check=lambda: None) is (change == 'none')


@pytest.mark.parametrize('bound', ['bytes', 'terms'])
def test_compact_native_proof_cache_is_bounded_and_exact_after_eviction(bound):
    comparison = _comparison()
    cache = NativeProofCache()
    identity = cache.identity(comparison, _proof_witness(), check=lambda: None)
    cost = 1 + len(identity[3]) + len(identity[4]) + 256
    if bound == 'bytes':
        cache.max_bytes = 2 * cost
    else:
        cache.max_terms = 2 * identity[5]
    for key in (b'a', b'b', b'a'):
        cache.remember(key, identity, check=lambda: None)
    assert set(cache.entries) == {b'a', b'b'}
    cache.remember(b'c', identity, check=lambda: None)
    assert set(cache.entries) == {b'a', b'c'}
    assert not cache.contains(b'b', identity, check=lambda: None)
    assert cache.contains(b'a', identity, check=lambda: None)
    assert cache.bytes == 2 * cost and cache.terms == 2 * identity[5]
    cache.clear()
    assert not cache.entries and cache.bytes == cache.terms == 0


def test_compact_native_proof_identity_honors_cancel_and_oversized_fallback():
    cache = NativeProofCache(max_bytes=1, max_terms=1)
    comparison = _comparison()
    witness = _proof_witness()
    identity = cache.identity(comparison, witness, check=lambda: None)
    cache.remember(b'a', identity, check=lambda: None)
    assert not cache.entries
    def cancel():
        raise InterruptedError('native proof cancelled')
    for action in (lambda: cache.identity(comparison, witness, check=cancel),
                   lambda: cache.contains(b'a', identity, check=cancel),
                   lambda: cache.remember(b'a', identity, check=cancel)):
        with pytest.raises(InterruptedError, match='native proof cancelled'):
            action()


def test_compact_native_proof_pool_keeps_fifty_distinct_obligations():
    cache = NativeProofCache()
    original = _comparison()
    coordinate = z3.Int('proof_pool_request')
    for value in range(50):
        comparison = replace(original, constraints=(*original.constraints, coordinate == value))
        identity = cache.identity(comparison, _proof_witness(value), check=lambda: None)
        cache.remember(str(value).encode(), identity, check=lambda: None)
    assert len(cache.entries) == 50
    for value in range(50):
        comparison = replace(original, constraints=(*original.constraints, coordinate == value))
        identity = cache.identity(comparison, _proof_witness(value), check=lambda: None)
        assert cache.contains(str(value).encode(), identity, check=lambda: None)
        assert not cache.contains(str((value + 1) % 50).encode(), identity, check=lambda: None)


@pytest.mark.parametrize('change', ['none', 'encoding', 'root', 'image', 'predicate', 'name', 'extra', 'scope'])
def test_native_proof_reuse_requires_the_complete_identical_obligation(change):
    original = _comparison()
    other = replace(original, constraints=tuple(list(original.constraints)),
                    differences=dict(original.differences), native_image=dict(original.native_image))
    if change == 'encoding':
        other = replace(other, encoding=SimpleNamespace(**vars(original.encoding)))
    elif change == 'root':
        other = replace(other, constraints=(*other.constraints, z3.BoolVal(False)))
    elif change == 'image':
        other = replace(other, native_image={'initial_hits': ('forged',)})
    elif change == 'predicate':
        other.differences['observations'] = z3.Not(other.differences['observations'])
    elif change == 'name':
        other.differences['different-name'] = other.differences.pop('observations')
    elif change == 'extra':
        other.differences['earlier-hidden-state'] = z3.BoolVal(True)
    elif change == 'scope':
        other = replace(other, observation_scope=SourceObservation.GOAL_PREFIX)
    assert other.same_obligation(original, check=lambda: None) is (change == 'none')
    assert not other.same_obligation(None, check=lambda: None)
    def cancel():
        raise InterruptedError('cancelled proof identity comparison')
    with pytest.raises(InterruptedError):
        other.same_obligation(original, check=cancel)


def _comparison(*, feasible=True, defects=(False, False, False, False)):
    terms = z3.Bools('unfinished undefined conflict different')
    encoding = SimpleNamespace(
        alive=z3.BoolVal(True), unfinished=terms[0], domain_faults={'operation': terms[1]},
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=(terms[2],))))
    constraints = (z3.BoolVal(feasible), *(term == value for term, value in zip(terms, defects)
                                          if value is not None))
    return SourceNativeComparison(encoding, constraints, {'observations': terms[3]}, {})


def _run(comparison, *, joint=True, unknown=()):
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        assert all(a is b for a, b in zip(assertions, comparison.constraints))
        if phase in unknown:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='test cancellation')
        return z3_check(assertions, 1000)

    return comparison.audit(solve, joint=joint), calls


@pytest.mark.parametrize('feasible', [False, True])
@pytest.mark.parametrize('defects', tuple(itertools.product((False, True, None), repeat=4)))
def test_joint_and_separate_proofs_have_the_same_answer(feasible, defects):
    comparison = _comparison(feasible=feasible, defects=defects)
    joint, _ = _run(comparison)
    separate, _ = _run(comparison, joint=False)
    assert joint.as_dict() == separate.as_dict()


def test_success_needs_nonempty_domain_and_no_defect_in_any_member():
    result, calls = _run(_comparison())
    assert result.status is AuditCheckStatus.AGREED
    assert calls == ['feasibility', 'native-certificate']
    result, calls = _run(_comparison(feasible=False))
    assert result.status is AuditCheckStatus.REFUSED
    assert calls == ['feasibility']


def test_unknown_joint_proof_retains_individual_checks():
    result, calls = _run(_comparison(), unknown={'native-certificate'})
    assert result.status is AuditCheckStatus.AGREED
    assert calls == [PHASES[0], 'native-certificate', *PHASES[1:]]


@pytest.mark.parametrize('phase', PHASES)
def test_unknown_obligation_never_acquires_authority(phase):
    result, calls = _run(_comparison(), unknown={'native-certificate', phase})
    assert result.status is AuditCheckStatus.REFUSED
    assert calls[-1] == phase


def test_success_is_not_retained_for_another_observation():
    comparison = _comparison()
    assert _run(comparison)[0].status is AuditCheckStatus.AGREED
    comparison.differences['observations'] = z3.BoolVal(True)
    assert _run(comparison)[0].status is AuditCheckStatus.INCONSISTENT


def _monitored(private, defect):
    ctx = z3.Context() if private else None
    x, sampled, first, second = z3.Ints('input sampled first second', ctx=ctx)
    flag, held = z3.Bools('flag held', ctx=ctx)
    false = z3.BoolVal(False, ctx=ctx)
    free = z3.Bool('order', ctx=ctx)
    definitions = (first == sampled + 1, second == first + 2, held == flag)
    source = (x == 3, sampled == x + 1 + (z3.If(free, 1, 0) if defect == 'order' else 0),
              flag == True)
    encoding = SimpleNamespace(alive=z3.BoolVal(True, ctx=ctx),
        unfinished=z3.BoolVal(defect == 'unfinished', ctx=ctx),
        domain_faults={'monitor': z3.BoolVal(defect == 'fault', ctx=ctx)},
        monitor_constraints=definitions,
        source=SimpleNamespace(domain_faults={}, memory=SimpleNamespace(conflicts=(
            z3.BoolVal(defect == 'memory', ctx=ctx),))))
    differences = {'slot[0].sample.x': sampled != (5 if defect == 'sample' else 4),
                   'slot[0].transfer': flag != True,
                   'monitor[1].first': first != (6 if defect == 'monitor' else 5),
                   'monitor[2].second': second != 7,
                   'monitor[1].held': held != (defect != 'held'),
                   'future-observation': z3.BoolVal(defect == 'extra', ctx=ctx)}
    constraints = (*source, *definitions, *((false,) if defect == 'empty' else ()))
    return SourceNativeComparison(encoding, constraints, differences, {})


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('defect', ['none', 'sample', 'monitor', 'held', 'order',
                                   'unfinished', 'fault', 'memory', 'extra', 'empty'])
def test_staged_native_certificate_matches_full_obligations(private, defect):
    comparison = _monitored(private, defect)
    calls = []

    def solve(phase, roots):
        calls.append((phase, roots))
        return z3_check(roots, 1000)

    staged = comparison.audit(solve)
    separate = comparison.audit(solve, joint=False)
    assert staged.as_dict() == separate.as_dict()
    if defect == 'none':
        assert staged.status is AuditCheckStatus.AGREED
        assert [phase for phase, _ in calls[:3]] == [
            'feasibility', 'native-source-certificate', 'native-certificate']
        assert len(calls[2][1]) == 1 and z3.is_false(calls[2][1][0])
    elif defect != 'empty':
        assert staged.status is not AuditCheckStatus.AGREED


@pytest.mark.parametrize('answer', [SolverAnswer.SAT, SolverAnswer.UNKNOWN])
def test_native_staged_proof_cannot_use_unproved_observations(answer):
    comparison = _monitored(False, 'sample')
    calls = []

    def solve(phase, roots):
        calls.append((phase, roots))
        if phase == 'native-source-certificate' and answer is SolverAnswer.UNKNOWN:
            return ExactCheckResult(answer, reason='source not proved')
        return z3_check(roots, 1000)

    result = comparison.audit(solve)
    assert result.status is AuditCheckStatus.INCONSISTENT
    _, fallback = next(call for call in calls if call[0] == 'native-certificate')
    assert len(fallback) == len(comparison.constraints) + 1
    assert all(a is b for a, b in zip(fallback, comparison.constraints))


def test_native_staged_proof_does_not_publish_after_cancellation():
    comparison = _monitored(False, 'none')
    cancelled = False

    def solve(phase, roots):
        nonlocal cancelled
        result = z3_check(roots, 1000)
        cancelled = phase == 'native-source-certificate'
        return result

    def check():
        if cancelled:
            raise InterruptedError('cancelled native proof')

    with pytest.raises(InterruptedError, match='cancelled native proof'):
        comparison.audit(solve, check=check)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('guard', ['true', 'false', 'unconstrained', 'unknown'])
def test_completed_phase_hint_is_proved_or_uses_original_fallback(private, guard):
    comparison = _monitored(private, 'none')
    encoding = comparison.encoding
    ctx = encoding.alive.ctx
    complete = (z3.BoolVal(guard == 'true', ctx=ctx) if guard in {'true', 'false'}
                else z3.Bool('phase_complete', ctx=ctx))
    encoding.source.slots = (SimpleNamespace(phases=(SimpleNamespace(continuable=complete),)),)
    calls = []

    def solve(phase, roots):
        calls.append((phase, roots))
        if phase == 'native-source-certificate' and guard == 'unknown':
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='unproved completion')
        return z3_check(roots, 1000)

    # The completion guard is an optimization premise, not a new mandatory
    # obligation. An unproved hint must not reject an otherwise valid record.
    result = comparison.audit(solve)
    assert result.status is AuditCheckStatus.AGREED
    _, certificate = next(call for call in calls if call[0] == 'native-certificate')
    if guard == 'true':
        assert len(certificate) == 1 and z3.is_false(certificate[0])
    else:
        assert len(certificate) == len(comparison.constraints) + 1
        assert all(a is b for a, b in zip(certificate, comparison.constraints))


def test_completed_phase_hint_cannot_hide_a_native_monitor_defect():
    comparison = _monitored(False, 'monitor')
    comparison.encoding.source.slots = (
        SimpleNamespace(phases=(SimpleNamespace(continuable=z3.BoolVal(False)),)),)
    solve = lambda _phase, roots: z3_check(roots, 1000)
    assert comparison.audit(solve).status is AuditCheckStatus.INCONSISTENT
    assert comparison.audit(solve).as_dict() == comparison.audit(solve, joint=False).as_dict()
