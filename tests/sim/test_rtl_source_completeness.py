"""Local sufficient proofs cannot replace a full SAT/UNKNOWN with success."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.source_completeness import local_completeness
from behave.sim.rtl.source_confluence import SourceOrderComparison, _replica_coordinates
from behave.sim.rtl.source_relation import SourceIntervalEncoding


def _pair(*, bad=False, restricted=False, private=False):
    context = z3.Context() if private else None
    true, false = z3.BoolVal(True, ctx=context), z3.BoolVal(False, ctx=context)
    histories = []
    for side in range(2):
        parts = []
        for index in range(4):
            x, q = z3.Bools(f'complete_{side}_{index}_x complete_{side}_{index}_q', ctx=context)
            part = SourceIntervalEncoding(
                constraints=(q == (x if bad and index == 3 else true),), hits={},
                occurrences={}, state={}, inputs={}, actions=(), alive=true, error=false,
                domain_faults={}, quiescent=q, entered=true, entry_terms=())
            parts.append(part)
        roots = tuple(term for part in parts for term in part.constraints)
        if restricted:
            roots += (x,)
        histories.append(SimpleNamespace(alive=true, constraints=roots,
            unfinished=z3.Or(*(p.unfinished for p in parts)), domain_faults={}, startup=None,
            source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=tuple(
                SimpleNamespace(phases=(part,)) for part in parts))))
    return SourceOrderComparison(*histories, {})


@pytest.mark.parametrize('private', [False, True])
def test_only_all_proved_local_groups_can_discharge_completeness(private):
    pair = _pair(private=private)
    calls = []

    def solve(label, roots):
        calls.append(label)
        return z3_check(roots, 10_000, _owned_context=pair.left.alive.ctx)

    assert local_completeness(pair, solve, check=lambda: None).answer is SolverAnswer.UNSAT
    assert len(calls) < 8  # Real structural equivalence, not a per-index waiver.
    calls.clear()
    assert pair.audit(solve, prefix_proofs=True).status is AuditCheckStatus.AGREED
    assert calls[0] == 'feasibility'
    assert 'completeness' not in calls and 'completeness:prefix' not in calls
    assert 'definedness' in calls and 'memory' in calls


@pytest.mark.parametrize('capacity', [0, 1])
def test_completeness_cache_eviction_only_repeats_proofs(monkeypatch, capacity):
    from behave.sim.rtl import source_completeness

    monkeypatch.setattr(source_completeness.defaults, 'SIM_ANALYSIS_ANSWER_CACHE_ENTRIES', capacity)
    calls = []

    def solve(label, roots):
        calls.append(label)
        return z3_check(roots, 10_000)

    assert local_completeness(_pair(), solve, check=lambda: None).answer is SolverAnswer.UNSAT
    assert len(calls) == (8 if capacity == 0 else 4)
    assert local_completeness(_pair(bad=True), solve, check=lambda: None) is None


@pytest.mark.parametrize('restricted', [False, True])
def test_local_sat_must_use_original_domain_before_any_refusal(restricted):
    pair = _pair(bad=True, restricted=restricted)
    calls = []

    def solve(label, roots):
        calls.append((label, roots))
        return z3_check(roots, 10_000)

    result = pair.audit(solve, prefix_proofs=True)
    assert (result.status is AuditCheckStatus.AGREED) is restricted
    full = next(roots for label, roots in calls if label == 'completeness')
    assert all(a.eq(b) for a, b in zip(full, pair.constraints))
    assert any(label.startswith('completeness:local:') for label, _ in calls)


def test_local_unknown_does_not_claim_unreachable_or_change_full_fallback():
    pair = _pair()
    calls = []

    def solve(label, roots):
        calls.append(label)
        if label.startswith('completeness:local:'):
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='local test budget')
        return z3_check(roots, 10_000)

    assert pair.audit(solve, prefix_proofs=True).status is AuditCheckStatus.AGREED
    assert 'completeness' in calls


def test_missing_metadata_and_cancellation_never_publish_a_partial_proof():
    pair = _pair()
    pair.right.source.slots[-1].phases = (
        replace(pair.right.source.slots[-1].phases[0], entry_terms=None),)
    def unexpected(*_args):
        pytest.fail('incomplete metadata must fall back before solving')
    assert local_completeness(pair, unexpected, check=lambda: None) is None
    pair = _pair()
    pair.right.source.slots[-1].phases = ()
    assert local_completeness(pair, unexpected, check=lambda: None) is None
    def cancel():
        raise InterruptedError('owning analysis stopped')
    with pytest.raises(InterruptedError, match='owning analysis stopped'):
        local_completeness(_pair(), unexpected, check=cancel)


def test_literal_bound_changes_cannot_reuse_an_unrelated_local_proof():
    assert local_completeness(_pair(bad=True),
        lambda _label, roots: z3_check(roots, 10_000), check=lambda: None) is None


def test_shared_function_reuse_keeps_declarations_aliases_and_native_default():
    x, y = z3.Ints('function_x function_y')
    f, g = [z3.Function(name, x.sort(), x.sort()) for name in ('complete_f', 'complete_g')]
    pair = ((f(x) == x, f(y) == y),)
    assert _replica_coordinates(pair, check=lambda: None) is None
    assert _replica_coordinates(pair, check=lambda: None, shared_functions=True) is not None
    assert _replica_coordinates(((f(x) == x, g(y) == y),), check=lambda: None,
                                shared_functions=True) is None
    assert _replica_coordinates(((x == x, x == y),), check=lambda: None,
                                shared_functions=True) is None
