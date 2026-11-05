"""An environment hint proves a full member, never an empty or smaller domain."""

from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.analysis_job import _SourceAnalysis
from behave.sim.rtl.protocol_relation import SymbolicRequest


class _Request(SimpleNamespace):
    atomic = False
    settled = False
    full_queue_hint = SymbolicRequest.full_queue_hint


def _exact():
    exact = _SourceAnalysis.__new__(_SourceAnalysis)
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=None))
    return exact


def _fixture():
    x, y = z3.Ints('hint_x hint_y')
    request = _Request(
        constraints=(x >= 0, x <= 1),
        lengths={}, queues={},
        rows=lambda model: [{'x': model.eval(x, model_completion=True).as_long()}],
        fix=lambda rows: (x == rows[0]['x'],),
    )
    exact = _exact()
    exact.request = request
    return exact, x, y


def _check(assertions):
    return z3_check_finite(assertions, 10_000)


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('changed', (None, 'rows', 'encoding', 'request', 'root', 'unknown-root', 'context'))
def test_query_member_handoff_revalidates_all_native_roots(private, changed):
    ctx = z3.Context() if private else None
    x, y = z3.Ints('handoff_x handoff_y', ctx=ctx)
    exact = _exact()
    exact.encoding = object()
    exact.constraints = (x == 3, y == x + 1)
    exact.request = SimpleNamespace(fix=lambda rows: (x == rows[0]['x'],))
    exact._request_members = {}
    rows = [{'x': 3}]
    model = _check(exact.constraints).model
    exact._retain_query_member(rows, model, lambda: None)
    assert len(exact._request_members) == 1
    encoding, roots = exact.encoding, exact.constraints
    if changed == 'rows':
        rows = [{'x': 4}]
    elif changed == 'encoding':
        encoding = object()
    elif changed == 'request':
        exact.request = SimpleNamespace(fix=lambda rows: (x == rows[0]['x'],))
    elif changed == 'root':
        roots = (*roots, y == 5)
    elif changed == 'unknown-root':
        roots = (*roots, z3.Bool('handoff_unknown', ctx=ctx))
    elif changed == 'context':
        roots = (z3.BoolVal(True, ctx=z3.Context()),)
    hint = exact._take_query_member(rows, encoding, roots, lambda: None)
    assert (hint is model) is (changed is None)
    assert exact._pending_query_member is None
    assert exact._take_query_member(rows, encoding, roots, lambda: None) is None


@pytest.mark.parametrize('invalid', ('source', 'environment', 'request', 'unbound', 'cancel'))
def test_query_member_handoff_never_completes_missing_values_or_keeps_failed_hints(invalid):
    exact, x, y = _fixture()
    exact.encoding = object()
    exact.constraints = (x == 0, y == 1)
    exact._request_members = {}
    exact._pending_query_member = object()
    roots = (x == int(invalid == 'source'),)
    if invalid != 'unbound':
        roots += (y == (2 if invalid == 'environment' else 1),)
    rows = [{'x': int(invalid == 'request')}]
    model = _check(roots).model
    def check():
        if invalid == 'cancel':
            raise InterruptedError('query member cancelled')
    if invalid == 'cancel':
        with pytest.raises(InterruptedError):
            exact._retain_query_member(rows, model, check)
    else:
        exact._retain_query_member(rows, model, check)
    assert exact._pending_query_member is None
    assert not exact._request_members


def test_query_member_is_consumed_even_when_native_validation_is_cancelled():
    exact, x, y = _fixture()
    exact.encoding = object()
    exact.constraints = (x == 0, y == 1)
    exact._request_members = {}
    rows = [{'x': 0}]
    exact._retain_query_member(rows, _check(exact.constraints).model, lambda: None)
    def cancel():
        raise InterruptedError('native handoff cancelled')
    with pytest.raises(InterruptedError):
        exact._take_query_member(rows, exact.encoding, exact.constraints, cancel)
    assert exact._pending_query_member is None


@pytest.mark.parametrize('changed', (None, 'rows', 'request', 'context', 'environment'))
def test_cross_projection_proposal_is_not_a_native_certificate(changed):
    exact, x, y = _fixture()
    exact.encoding = object()
    exact.constraints = (x == 0, y == 1)
    exact._request_members = {}
    rows = [{'x': 0}]
    model = _check(exact.constraints).model
    exact._retain_query_member(rows, model, lambda: None)
    # Deliberately contradictory native roots. Returning a proposal must NOT
    # claim these roots hold; the receiving constructor has to check them.
    roots = (y == 2,)
    if changed == 'rows':
        rows = [{'x': 1}]
    elif changed == 'request':
        exact.request = SimpleNamespace(constraints=(), fix=lambda rows: (x == rows[0]['x'],))
    elif changed == 'context':
        roots = (z3.BoolVal(True, ctx=z3.Context()),)
    elif changed == 'environment':
        exact.request.constraints = (x == 1,)
    proposal = exact._take_query_member(rows, object(), roots, lambda: None, proposal=True)
    assert (proposal is model) is (changed is None)
    assert exact._pending_query_member is None
    if proposal is not None:
        assert z3.is_false(proposal.eval(roots[0], model_completion=False))


def test_projected_query_proposal_does_not_admit_the_full_request_domain():
    exact, x, y = _fixture()
    exact._request_members = {}
    encoding = SimpleNamespace(constraints=(x == 0, y == 1), environment_assumptions={})
    rows = [{'x': 0}]
    model = _check(encoding.constraints).model
    exact._retain_query_member(rows, model, lambda: None, encoding=encoding)
    assert exact._pending_query_member[0] is encoding
    assert not exact._request_members
    assert '_base_history' not in exact.__dict__
    assert exact._take_query_member(rows, object(), (y == 2,), lambda: None,
                                    proposal=True) is model


@pytest.mark.parametrize('violation', [False, True, None])
def test_native_membership_requires_complete_query_environment(violation):
    exact, _, _ = _fixture()
    fault = z3.Bool('query_environment_fault')
    encoding = SimpleNamespace(environment_assumptions={'fault': fault})
    exact.encoding = encoding
    model = z3.Model() if violation is None else _check((fault == violation,)).model
    assert exact._member_in_query_domain(encoding, model, lambda: None) is (violation is False)
    assert not exact._member_in_query_domain(SimpleNamespace(), model, lambda: None)
    def cancel():
        raise InterruptedError('cancel member validation')
    with pytest.raises(InterruptedError):
        exact._member_in_query_domain(encoding, model, cancel)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('invalid', [None, 'source', 'environment', 'unknown', 'not-admitted'])
def test_admission_request_requires_full_roots_and_query_environment(private, invalid):
    ctx = z3.Context() if private else None
    x = z3.Int('admission_request', ctx=ctx)
    fault = z3.Bool('admission_environment_fault', ctx=ctx)
    exact = _exact()
    exact.request = SimpleNamespace(rows=lambda model: [{'x': model.eval(x).as_long()}],
                                    fix=lambda rows: (x == rows[0]['x'],))
    history = SimpleNamespace(constraints=(x == 7,), environment_assumptions={'fault': fault})
    exact.encoding = history
    exact._admitted_comparison = None if invalid == 'not-admitted' else SimpleNamespace(left=history)
    exact._request_members = {}
    roots = (x == (8 if invalid == 'source' else 7),)
    if invalid != 'unknown':
        roots += (fault == (invalid == 'environment'),)
    solver = z3.Solver(ctx=ctx)
    solver.add(*roots)
    assert solver.check() == z3.sat
    answer = ExactCheckResult(SolverAnswer.SAT, model=solver.model())
    exact._retain_admission_request(answer, lambda: None)
    if invalid:
        assert exact._admitted_request is None
        assert exact._admission_witness(lambda: None) is None
        return
    assert exact._admission_witness(lambda: None) == [{'x': 7}]
    # No model survives; callers receive fresh rows, not the retained request.
    assert exact._admitted_request[2] == ((('x', 7),),)
    witness = exact._admission_witness(lambda: None)
    witness[0]['x'] = 123
    assert exact._admission_witness(lambda: None) == [{'x': 7}]
    assert len(exact._request_members) == 1
    original = exact.encoding
    exact.encoding = SimpleNamespace()
    assert exact._admission_witness(lambda: None) is None
    exact.encoding = original
    request = exact.request
    exact.request = SimpleNamespace()
    assert exact._admission_witness(lambda: None) is None
    exact.request = request
    exact._admitted_comparison = SimpleNamespace(left=history)
    assert exact._admission_witness(lambda: None) is None


def test_admission_request_cancellation_and_failed_answers_do_not_supply_evidence():
    exact = _exact()
    history = SimpleNamespace(constraints=(z3.BoolVal(True),), environment_assumptions={})
    exact._admitted_comparison = SimpleNamespace(left=history)
    for answer in (None, ExactCheckResult(SolverAnswer.UNSAT),
                   ExactCheckResult(SolverAnswer.UNKNOWN, reason='timeout')):
        exact._admitted_request = object()
        exact._retain_admission_request(answer, lambda: None)
        assert exact._admitted_request is None
    def cancelled():
        raise InterruptedError('member deadline')
    with pytest.raises(InterruptedError, match='member deadline'):
        exact._retain_admission_request(ExactCheckResult(SolverAnswer.SAT, model=z3.Model()), cancelled)
    assert exact._admitted_request is None
    with pytest.raises(InterruptedError, match='member deadline'):
        exact._admission_witness(cancelled)


def test_adopting_admitted_history_discards_only_old_encoding_proofs():
    from behave.sim.rtl.observation_audit import NativeProofCache

    exact, x, _ = _fixture()
    previous = SimpleNamespace(constraints=(x == 1,))
    admitted = SimpleNamespace(constraints=(x >= 0,), environment_assumptions={'fault': x > 2})
    exact.encoding, exact.constraints = previous, previous.constraints
    exact._admitted_comparison = SimpleNamespace(left=admitted)
    exact._request_members = {b'old': None}
    exact._source_solver = old_solver = object()
    exact._native.encoding, exact._native.agreement = previous, object()
    exact._native.proofs = NativeProofCache()
    exact._native.proofs.entries[b'old'] = object()
    def cancel():
        raise InterruptedError('cancel adoption')
    with pytest.raises(InterruptedError):
        exact._use_admitted_history(cancel)
    assert exact.encoding is previous and exact._request_members == {b'old': None}
    assert exact._source_solver is old_solver
    exact._use_admitted_history(lambda: None)
    assert exact.encoding is admitted and not exact._request_members
    assert exact._source_solver is None
    assert exact._native.encoding is exact._native.agreement is None
    assert not exact._native.proofs.entries
    assert _check((*exact.constraints, x == 3)).answer is SolverAnswer.UNSAT
    exact._request_members[b'new'] = None
    exact._use_admitted_history(lambda: None)
    assert exact._request_members == {b'new': None}


@pytest.mark.parametrize('change', ['rows', 'trace'])
def test_native_replacement_releases_old_proof_owners_before_building(monkeypatch, change):
    import weakref
    from behave.sim.core import analysis
    from behave.sim.rtl.analysis_job import _membership_key
    from behave.sim.rtl.observation_audit import NativeProofCache

    class History:
        pass

    exact = _exact()
    exact.source.contract = None
    monkeypatch.setattr(analysis, 'audit_environment_assumptions', lambda *_: None)
    exact.source.template.direct = SimpleNamespace(covers_native_orders=False)
    exact.domain = SimpleNamespace(bound=6)
    exact.request = exact.observation_scope = object()
    exact._native.witness = exact._native.member_miss = None
    exact._native.propose_member = lambda *args, **kwargs: None
    rows = [{'x': 0}]
    history = History()
    owner = weakref.ref(history)
    exact._native.encoding = history
    exact._native.relation = SimpleNamespace(history=history)
    exact._native.agreement = SimpleNamespace(encoding=history)
    exact._native.proofs = NativeProofCache()
    exact._native.proofs.entries[b'old'] = (history,)
    exact._native.trace = (_membership_key(rows, lambda: None), 'old-trace')
    exact._pending_query_member = proposal = object()
    del history
    if change == 'rows':
        rows = [{'x': 1}]
    trace = SimpleNamespace(fingerprint='new-trace' if change == 'trace' else 'old-trace',
                            stimulus=None)
    trace.as_dict = lambda: {'fingerprint': trace.fingerprint}

    def construct(*args, **kwargs):
        assert owner() is None
        assert not exact._native.proofs.entries
        assert exact._native.agreement is exact._native.encoding is exact._native.relation is None
        assert exact._pending_query_member is proposal
        raise InterruptedError('stop before new history')

    exact.source.bind_request = construct
    with pytest.raises(InterruptedError, match='stop before new history'):
        exact._witness(rows, trace, solve=None, check=lambda: None)
    assert exact._native.trace is None


def test_source_preparation_requires_admission_and_never_serves_goal_or_native_proofs():
    exact, x, y = _fixture()
    monitor = y == x + 1
    history = SimpleNamespace(constraints=(x >= 0, monitor, x <= 1),
                              monitor_constraints=(monitor,))
    exact.encoding = history
    exact._admitted_comparison = None
    exact._source_solver = None
    assert exact._member_preparation('feasibility-source') is None
    exact._admitted_comparison = SimpleNamespace(left=history)
    prepared = exact._member_preparation('feasibility-source')
    assert prepared.base == (x >= 0, x <= 1)
    assert exact._member_preparation('fixed-request-source') is prepared
    for phase in ('query', 'environment-member-source', 'native-certificate',
                  'native-source-certificate', 'completeness', 'definedness'):
        assert exact._member_preparation(phase) is None
    exact.encoding = SimpleNamespace()
    assert exact._member_preparation('feasibility-source') is None


def test_prepared_source_member_still_validates_every_original_monitor_constraint():
    from behave.sim.symbolic.exact_solve import SolverPreparation
    from behave.sim.rtl.monitor_template import solve_monitor_member

    ctx = z3.Context()
    x, y = z3.BitVecs('input monitor', 8, ctx=ctx)
    base = (z3.ULE(x, 10),)
    definition = y == x + 1
    history = SimpleNamespace(monitor_constraints=(definition,), monitor_frame_sizes=(1,))
    prepared = SolverPreparation(base)
    phases = []

    def solve(phase, roots):
        phases.append(phase)
        return z3_check_finite(roots, 1000, _owned_context=ctx,
            _preparation=prepared if phase.endswith('-source') else None)

    for value, observed in ((3, 4), (7, 8), (7, 9), (3, 4)):
        roots = (*base, definition, x == value, y == observed)
        phases.clear()
        result = solve_monitor_member(roots, (history,), solve,
            phase='feasibility', check=lambda: None)
        if observed == value + 1:
            assert result.answer is SolverAnswer.SAT
            assert phases == ['feasibility-source']
            assert all(z3.is_true(result.model.eval(root)) for root in roots)
        else:
            # A source-only SAT model can assign the wrong monitor value.
            # Completion cannot endorse it; the untouched original query wins.
            assert result.answer is SolverAnswer.UNSAT
            assert phases == ['feasibility-source', 'feasibility']


@pytest.mark.parametrize('private', (False, True))
def test_immutable_monitor_plan_reuses_only_syntax_across_inputs(private, monkeypatch):
    from dataclasses import replace
    from behave.sim.rtl.monitor_template import monitor_frames, reduce_monitor_defect, solve_monitor_member
    from behave.sim.rtl.source_task import SourceTaskEncoding
    from behave.sim.rtl.symbolic_state import FixedBindings

    ctx = z3.Context() if private else None
    x, first, second = z3.Ints('prepared_x prepared_first prepared_second', ctx=ctx)
    definitions = (first == x + 1, second == first * 3)
    history = SourceTaskEncoding(None, None, (), monitor_constraints=definitions,
                                 monitor_frame_sizes=(1, 1))
    prepared = monitor_frames(history, check=lambda: None)
    assert monitor_frames(history, check=lambda: None) is prepared
    changed = replace(history, monitor_constraints=(first == x + 2, second == first * 3))
    assert monitor_frames(changed, check=lambda: None) is not prepared

    def no_repacking(*args, **kwargs):
        raise AssertionError('immutable frame syntax was rebuilt')

    monkeypatch.setattr(z3, 'TupleSort', no_repacking)
    for value in (2, 7, -4):
        roots = (x == value, *definitions)
        result = solve_monitor_member(roots, (history,), lambda _, terms: _check(terms),
                                      phase='feasibility', check=lambda: None)
        assert result.answer is SolverAnswer.SAT
        assert all(z3.is_true(result.model.eval(term)) for term in roots)
        assert result.model.eval(second).as_long() == (value + 1) * 3
        fixed = FixedBindings.of((x == value,))
        for delta in (0, 1):
            reduced = reduce_monitor_defect(second != (value + 1) * 3 + delta,
                definitions, fixed, check=lambda: None, _frames=prepared)
            assert z3.is_false(reduced) is (delta == 0)
    def cancel():
        raise InterruptedError('cancelled even on a prepared plan')
    with pytest.raises(InterruptedError):
        monitor_frames(history, check=cancel)


def test_cancelled_monitor_preparation_is_not_retained():
    from behave.sim.rtl.source_task import SourceTaskEncoding

    x, y = z3.Ints('cancelled_plan_x cancelled_plan_y')
    history = SourceTaskEncoding(None, None, (), monitor_constraints=(y == x + 1,))
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise InterruptedError('cancel parsing')
    with pytest.raises(InterruptedError):
        history._monitor_frames(check=cancel)
    assert history._monitor_frames_cache is None
    assert history._monitor_frames(check=lambda: None).frames is not None


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('conflict', (False, True))
def test_fixed_record_member_checks_full_roots_after_monitor_completion(private, conflict):
    from behave.sim.rtl.monitor_template import solve_monitor_member

    ctx = z3.Context() if private else None
    x, state = z3.Ints('native_member_input native_member_state', ctx=ctx)
    definitions = (state == x + 1,)
    history = SimpleNamespace(monitor_constraints=definitions, monitor_frame_sizes=(1,))
    roots = (x == 4, *definitions, state == (6 if conflict else 5))
    calls = []

    def solve(label, assertions):
        calls.append((label, assertions))
        return _check(assertions)

    result = solve_monitor_member(roots, (history,), solve, phase='feasibility', check=lambda: None)
    assert calls[0][0] == 'feasibility-source'
    assert not any(term.eq(definitions[0]) for term in calls[0][1])
    if conflict:
        assert calls[-1] == ('feasibility', roots)
        assert result.answer is SolverAnswer.UNSAT
    else:
        assert len(calls) == 1 and result.answer is SolverAnswer.SAT
        assert all(z3.is_true(result.model.eval(term)) for term in roots)


@pytest.mark.parametrize('answer', (SolverAnswer.UNSAT, SolverAnswer.UNKNOWN))
def test_failed_record_member_hint_does_not_replace_original_query(answer):
    from behave.sim.rtl.monitor_template import solve_monitor_member

    x, state = z3.Ints('fallback_input fallback_state')
    definitions = (state == x + 1,)
    history = SimpleNamespace(monitor_constraints=definitions, monitor_frame_sizes=(1,))
    roots = (x == 4, *definitions)
    calls = []

    def solve(label, assertions):
        calls.append((label, assertions))
        if label.endswith('-source'):
            return ExactCheckResult(answer, reason='hint failed' if answer is SolverAnswer.UNKNOWN else '')
        return _check(assertions)

    result = solve_monitor_member(roots, (history,), solve, phase='feasibility', check=lambda: None)
    assert calls[-1] == ('feasibility', roots)
    assert result.answer is SolverAnswer.SAT


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('unknown', ['none', 'coordinate', 'function', 'forward'])
def test_monitor_literal_proof_never_completes_unknown_values(private, unknown):
    from behave.sim.rtl.monitor_template import reduce_monitor_defect
    from behave.sim.rtl.symbolic_state import FixedBindings

    ctx = z3.Context() if private else None
    x, first, second = z3.Ints('literal_input literal_first literal_second', ctx=ctx)
    flag = z3.Bool('unassigned_flag', ctx=ctx)
    function = z3.Function('unassigned_function', z3.IntSort(ctx), z3.BoolSort(ctx))
    definitions = (first == x + 1, second == first * 3)
    defect = second != 9
    if unknown == 'coordinate':
        defect = z3.Or(defect, flag)
    elif unknown == 'function':
        defect = z3.Or(defect, function(x))
    elif unknown == 'forward':
        definitions = (first == second + 1, second == x + 1)
        defect = first != 4
    fixed = FixedBindings.of((x == 2,))
    result = reduce_monitor_defect(defect, definitions, fixed,
        frame_sizes=(1, 1), check=lambda: None)
    assert z3.is_false(result) is (unknown == 'none')
    # Reduction is an entailment under the asserted definitions and facts,
    # not one arbitrary satisfying assignment to the untouched coordinates.
    solver = z3.Solver(ctx=ctx)
    solver.add(*fixed.equations, *definitions, defect != result)
    assert solver.check() == z3.unsat


def test_monitor_literal_proof_cuts_only_proved_compound_observations():
    from behave.sim.rtl.monitor_template import reduce_monitor_defect
    from behave.sim.rtl.symbolic_state import FixedBindings

    x, first, second = z3.Ints('compound_input compound_first compound_second')
    observed = x * x
    fact = observed == 9
    fixed = FixedBindings((fact,), (), ((observed, z3.IntVal(9)),))
    definitions = (first == observed + 1, second == first + 2)
    target = second != 12
    result = reduce_monitor_defect(target, definitions, fixed, check=lambda: None)
    assert z3.is_false(result)
    assert _check((fact, *definitions, target)).answer is SolverAnswer.UNSAT
    # This ground interpretation may not invent the sign of x from x*x=9.
    untouched = reduce_monitor_defect(x < 0, definitions, fixed, check=lambda: None)
    assert not z3.is_false(untouched)


def test_environment_member_keeps_every_full_formula_constraint():
    exact, x, y = _fixture()
    full = (*exact.request.constraints, y == x + 1)
    calls = []

    def solve(phase, assertions):
        calls.append((phase, assertions))
        return _check(assertions)

    member = exact._domain_feasibility(solve, full)
    assert member.answer is SolverAnswer.SAT
    assert all(z3.is_true(member.model.eval(term)) for term in full)
    assert [phase for phase, _ in calls] == ['environment-hint', 'environment-member']
    fixed = exact.request.fix(exact.request.rows(member.model))
    assert _check((z3.Xor(z3.And(*calls[1][1]), z3.And(*full, *fixed)),)).answer is SolverAnswer.UNSAT


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('conflict', [False, True])
def test_monitor_completion_validates_every_original_root(private, conflict):
    ctx = z3.Context() if private else None
    x, first, second = z3.Ints('member_input member_first member_second', ctx=ctx)
    definitions = (first == x + 1, second == first * 3)
    exact = _exact()
    exact.request = _Request(constraints=(x == 2,), lengths={}, queues={},
        rows=lambda model: model.eval(x, model_completion=True).as_long(), fix=lambda row: (x == row,))
    exact._comparison = SimpleNamespace(left=SimpleNamespace(monitor_constraints=definitions),
                                        right=SimpleNamespace(monitor_constraints=()))
    full = (x == 2, *definitions, second == (8 if conflict else 9))
    calls = []

    def solve(label, roots):
        calls.append(label)
        return _check(roots)

    answer = exact._domain_feasibility(solve, full)
    if conflict:
        assert answer.answer is SolverAnswer.UNSAT
        assert calls[-2:] == ['environment-member', 'feasibility']
    else:
        assert answer.answer is SolverAnswer.SAT
        assert all(z3.is_true(answer.model.eval(term)) for term in full)
        assert calls == ['environment-hint', 'environment-member-source']


def test_monitor_completion_is_cancellable_and_does_not_mutate_the_source_model():
    from behave.sim.rtl.monitor_template import complete_monitor_model

    x, alias = z3.Ints('completion_input completion_alias')
    answer = _check((x == 2,))
    original = str(answer.model)
    definitions = (alias == x + 1,)
    completed = complete_monitor_model(answer.model, definitions, (x == 2, *definitions), check=lambda: None)
    assert completed.eval(alias).as_long() == 3
    assert str(answer.model) == original
    assert complete_monitor_model(answer.model, (alias == alias + 1,), (alias == alias + 1,),
                                  check=lambda: None) is None

    def stop():
        raise InterruptedError('analysis stopped')

    with pytest.raises(InterruptedError):
        complete_monitor_model(answer.model, definitions, definitions, check=stop)


@pytest.mark.parametrize('private', (False, True))
def test_monitor_frames_evaluate_shared_images_once_and_order_dependent_batching(private, monkeypatch):
    from behave.sim.rtl.monitor_template import complete_monitor_model

    ctx = z3.Context() if private else None
    x, first, second = z3.Ints('frame_input frame_first frame_second', ctx=ctx)
    flag, later = z3.Bools('frame_flag frame_later', ctx=ctx)
    definitions = (first == x + 1, flag == (x > 0), second == first * 3, later == z3.Not(flag))
    full = (x == 2, *definitions)
    source = _check((x == 2,))
    calls, evaluate = [], z3.ModelRef.eval

    def counted(model, term, *args, **kwargs):
        calls.append(term)
        return evaluate(model, term, *args, **kwargs)

    monkeypatch.setattr(z3.ModelRef, 'eval', counted)
    completed = complete_monitor_model(source.model, definitions, full, check=lambda: None, frame_sizes=(2, 2))
    assert completed is not None
    assert len(calls) == 3  # Two successor images and all original assertions.
    assert all(z3.is_true(evaluate(completed, term)) for term in full)
    # A source action can append boundary definitions depending on its named
    # successors. Derive two layers from the original equations, not stale
    # model values. The full-root check remains mandatory.
    calls.clear()
    combined = complete_monitor_model(source.model, definitions, full, check=lambda: None, frame_sizes=(4,))
    assert combined is not None and len(calls) == 3
    assert all(z3.is_true(evaluate(combined, term)) for term in full)
    assert complete_monitor_model(source.model, definitions, (*full, second != 9),
                                  check=lambda: None, frame_sizes=(4,)) is None
    assert complete_monitor_model(source.model, definitions, full, check=lambda: None, frame_sizes=(3,)) is None


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('required', [0, 3])
@pytest.mark.parametrize('atomic', [False, True])
def test_queue_hint_preserves_complete_domain_and_original_fallback(private, required, atomic):
    context = z3.Context() if private else None
    length = z3.Int('preferred_queue_length', ctx=context)
    exact = _exact()
    exact.request = _Request(
        constraints=(length >= 0, length <= 3), lengths={'in': length},
        queues={'in': ((), (), ())}, release_capacity={'in': z3.IntVal(3, ctx=context)}, atomic=atomic,
        rows=lambda model: model.eval(length, model_completion=True).as_long(),
        fix=lambda row: (length == row,),
    )
    # Prefer a full fixed sampler or an idle handshake member. Deliberately
    # require the opposite in half the cases to exercise the original domain.
    full = (*exact.request.constraints, length == required)
    calls = []

    def solve(phase, roots):
        result = _check(roots)
        calls.append((phase, roots, result))
        return result

    result = exact._domain_feasibility(solve, full)
    assert result.answer is SolverAnswer.SAT
    assert result.model.eval(length).as_long() == required
    preferred = 3 if atomic else 0
    assert calls[0][2].model.eval(length).as_long() == preferred
    assert [name for name, *_ in calls] == (
        ['environment-hint', 'environment-member'] if required == preferred else
        ['environment-hint', 'environment-member', 'feasibility'])
    if required != preferred:
        assert calls[-1][1] is full
    # The public domain itself was never fixed to the preferred length.
    assert _check((*exact.request.constraints, length == 1)).answer is SolverAnswer.SAT


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('conflict', [False, True])
def test_member_hint_keeps_original_formula_and_complete_model(private, conflict):
    context = z3.Context() if private else None
    choose = z3.Int('member_choice', ctx=context)
    data, state, monitor = z3.BitVecs('member_data member_state member_monitor', 32, ctx=context)
    exact = _exact()
    exact.request = _Request(
        constraints=(choose >= 0, choose <= 1), lengths={}, queues={},
        rows=lambda model: model.eval(choose, model_completion=True).as_long(),
        fix=lambda row: (choose == row,),
    )
    definition = monitor == state + 1
    history = SimpleNamespace(monitor_constraints=(definition,), monitor_frame_sizes=(1,))
    exact._comparison = SimpleNamespace(left=history, right=None)
    inactive = data * 3
    full = (*exact.request.constraints, state == z3.If(choose == 0, 7, inactive),
            definition, monitor == (9 if conflict else 8))
    phases = []

    def solve(phase, roots):
        phases.append(phase)
        if phase == 'environment-hint':
            return _check((*roots, choose == 0))
        if phase == 'environment-member-source':
            # Keep the shared source formula instead of copying its whole DAG
            # per fixed hint; the explicit equality still selects the idle path.
            assert any(term.eq(full[2]) for term in roots)
            assert _check((*roots, choose != 0)).answer is SolverAnswer.UNSAT
        return _check(roots)

    result = exact._domain_feasibility(solve, full)
    if conflict:
        assert phases[-1] == 'feasibility'
    else:
        assert phases == ['environment-hint', 'environment-member-source']
    assert result.answer is SolverAnswer.SAT
    assert all(z3.is_true(result.model.eval(root, model_completion=True)) for root in full)
    assert result.model.eval(choose).as_long() == int(conflict)
    # A hint neither mutates nor narrows the original source domain.
    assert _check((*full, choose == 1)).answer is SolverAnswer.SAT


@pytest.mark.parametrize('failed_phase', ['environment-hint', 'environment-member'])
@pytest.mark.parametrize('answer', [SolverAnswer.UNKNOWN, SolverAnswer.UNSAT])
def test_failed_hint_falls_back_without_fixed_coordinates(failed_phase, answer):
    exact, x, y = _fixture()
    full = (*exact.request.constraints, y == x + 1)
    calls = []

    def solve(phase, assertions):
        calls.append((phase, assertions))
        if phase == failed_phase:
            return ExactCheckResult(answer, reason='hint unavailable' if answer is SolverAnswer.UNKNOWN else '')
        return _check(assertions)

    result = exact._domain_feasibility(solve, full)
    assert result.answer is SolverAnswer.SAT
    assert calls[-1][0] == 'feasibility' and calls[-1][1] is full


def test_legal_environment_may_be_infeasible_for_the_artifact():
    exact, x, y = _fixture()
    full = (*exact.request.constraints, x == 1, y == x + 1)
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        # A legal environment need not satisfy the artifact's HDL initialization.
        return _check((*assertions, x == 0) if phase == 'environment-hint' else assertions)

    result = exact._domain_feasibility(solve, full)
    assert calls == ['environment-hint', 'environment-member', 'feasibility']
    assert result.answer is SolverAnswer.SAT and result.model.eval(x).as_long() == 1


def test_empty_domain_cannot_be_admitted_from_an_environment_model():
    exact, x, _ = _fixture()
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        return _check(assertions)

    assert exact._domain_feasibility(solve, (*exact.request.constraints, x == 2)).answer is SolverAnswer.UNSAT
    assert calls == ['environment-hint', 'environment-member', 'feasibility']


def test_hint_does_not_restrict_the_next_universal_proof():
    exact, x, y = _fixture()
    full = (*exact.request.constraints, y == x + 1)
    calls = []

    class Comparison:
        def audit(self, solve, *, prefix_proofs, check):
            from behave.sim.core.audit import AuditCheck

            check()
            member = solve('feasibility', full)
            assert member.answer is SolverAnswer.SAT
            another = (*full, x != member.model.eval(x))
            assert solve('confluence', another).answer is SolverAnswer.SAT
            return AuditCheck.refused('test-different-members', 'Both inputs remain in the domain')

    exact._comparison, exact._admitted_comparison = Comparison(), None

    def solve(phase, assertions):
        calls.append((phase, assertions))
        return _check(assertions)

    from behave.sim.core.refusal import Refused
    with pytest.raises(Refused, match='test-different-members'):
        exact._admit_domain(solve, lambda: None)
    assert calls[-1][0] == 'confluence'
    assert exact._admitted_comparison is None


def test_cancelled_hint_does_not_restart_or_fallback():
    exact, x, _ = _fixture()
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        raise InterruptedError('shared deadline')

    with pytest.raises(InterruptedError, match='shared deadline'):
        exact._domain_feasibility(solve, (*exact.request.constraints, x == 1))
    assert calls == ['environment-hint']


def _partial_abstract_proof(*, real_fault=False):
    from behave.sim.rtl.source_confluence import SourceOrderComparison

    exact, _x, v = _fixture()
    true, false = z3.BoolVal(True), z3.BoolVal(False)

    def comparison(restriction):
        side = SimpleNamespace(alive=true, unfinished=false,
            constraints=(*exact.request.constraints, restriction),
            domain_faults={'test-operation': v == 1},
            source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=()))
        return SourceOrderComparison(side, side, {'protocol.output': false})

    exact._comparison = comparison(v == int(real_fault))
    abstract = comparison(z3.Or(v == 0, v == 1))
    exact._admitted_comparison = None
    exact.relation = SimpleNamespace(abstract_order_comparison=lambda _request, **_kw: abstract)
    return exact


@pytest.mark.parametrize('real_fault', (False, True))
@pytest.mark.parametrize('abstract_failure', (SolverAnswer.SAT, SolverAnswer.UNKNOWN))
def test_abstract_unsat_is_reused_per_obligation_without_trusting_other_answers(real_fault, abstract_failure):
    from behave.sim.core.refusal import Refused

    exact = _partial_abstract_proof(real_fault=real_fault)
    concrete_calls, proof_calls = [], []

    def solve(phase, assertions):
        concrete_calls.append(phase)
        return _check(assertions)

    def prove(phase, assertions):
        proof_calls.append(phase)
        if phase == 'abstract:definedness' and abstract_failure is SolverAnswer.UNKNOWN:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='abstract proof not established')
        return _check(assertions)

    if real_fault:
        with pytest.raises(Refused, match='rtl-source-operation-domain-unencoded'):
            exact._admit_domain(solve, lambda: None, prove=prove)
        assert exact._admitted_comparison is None
    else:
        assert exact._admit_domain(solve, lambda: None, prove=prove) == 'proved'
    assert proof_calls == ['abstract:completeness', 'abstract:definedness']
    assert 'completeness' not in concrete_calls  # Its universal UNSAT survived.
    assert 'definedness' in concrete_calls  # SAT/UNKNOWN never became exact proof.
    assert 'environment-member' in concrete_calls  # Nonemptiness is always exact.


def test_partial_abstract_proofs_do_not_survive_failed_admission():
    from behave.sim.core.refusal import Refused

    exact = _partial_abstract_proof(real_fault=True)
    proofs = []

    def prove(phase, assertions):
        proofs.append(phase)
        return _check(assertions)

    for _ in range(2):
        with pytest.raises(Refused, match='rtl-source-operation-domain-unencoded'):
            exact._admit_domain(lambda _phase, roots: _check(roots), lambda: None, prove=prove)
        assert exact._admitted_comparison is None
    assert proofs.count('abstract:completeness') == 2


def test_abstract_unknown_does_not_skip_the_corresponding_exact_obligation():
    exact = _partial_abstract_proof()
    concrete = []

    def solve(phase, assertions):
        concrete.append(phase)
        return _check(assertions)

    assert exact._admit_domain(solve, lambda: None,
        prove=lambda _phase, _roots: ExactCheckResult(SolverAnswer.UNKNOWN, reason='unproved')) == 'proved'
    assert 'completeness' in concrete and 'definedness' in concrete


def test_complete_abstract_proof_cannot_discharge_a_relaxed_prefix():
    from behave.sim.rtl.source_confluence import SourceOrderComparison

    exact, x, v = _fixture()
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    suffix = x == 0

    def comparison(restriction):
        side = SimpleNamespace(alive=true, unfinished=false, domain_faults={},
            constraints=(suffix, restriction), source=SimpleNamespace(
                memory=SimpleNamespace(conflicts=()), slots=(SimpleNamespace(
                    phases=(SimpleNamespace(constraints=(suffix,)),)),)))
        return SourceOrderComparison(side, side, {'startup.x': x != 0, 'protocol.v': v == 1})

    exact._comparison = comparison(v == 0)
    exact._admitted_comparison = None
    abstract = comparison(z3.Or(v == 0, v == 1))
    exact.relation = SimpleNamespace(abstract_order_comparison=lambda _request, **_kw: abstract)
    concrete_calls = []

    def solve(phase, assertions):
        concrete_calls.append(phase)
        return _check(assertions)

    assert exact._admit_domain(solve, lambda: None,
        prove=lambda _phase, roots: _check(roots)) == 'proved'
    assert 'confluence:startup:prefix' in concrete_calls
    assert 'confluence:startup' not in concrete_calls
