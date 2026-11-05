"""A greedy private execution is evidence only after complete-root validation."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_solve import z3_check
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.source_member import SourceMemberPlan, source_member_hint
from behave.sim.rtl.source_relation import SourceIntervalEncoding


def _interval(actions, constraints):
    return SourceIntervalEncoding(constraints, {}, {}, {}, {}, actions,
                                  None, None, {}, None, None)


def test_prefix_member_preserves_solved_order_and_checks_future_roots():
    from behave.sim.rtl.source_member import source_query_prefixes

    public, first, later = z3.BitVecs('prefix_public prefix_first prefix_later', 2)
    early = replace(_interval((first,), (z3.Or(first == 0, first == 1),)),
                    hits={'early': first == 1})
    late = replace(_interval((later,), (later == public,)), hits={'late': later == 2})
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (early, late)))
    prefixes = list(source_query_prefixes(history, (public == 2,), check=lambda: None))
    assert len(prefixes) == 2
    roots, hits, faults = prefixes[0]
    assert not faults
    assert set(hits) == {'early'}
    proposal = z3_check((*roots, hits['early']), 1000)
    complete = (public == 2, *early.constraints, *late.constraints, first == 1)
    model = source_member_hint(proposal.model, (history,), complete, check=lambda: None)
    assert model is not None
    assert all(z3.is_true(model.eval(term, model_completion=False)) for term in complete)
    assert model.eval(later).as_long() == 2
    # A later contradictory requirement must reject this otherwise valid
    # prefix. No truncated execution can become a full-domain witness.
    assert source_member_hint(proposal.model, (history,), (*complete, later == 3),
                              check=lambda: None) is None


def test_prefix_unsat_does_not_classify_a_later_reachable_goal():
    from behave.sim.rtl.source_member import source_query_prefixes

    action = z3.BitVec('prefix_late_hit', 2)
    early = replace(_interval((z3.BitVecVal(0, 2),), (z3.BoolVal(True),)),
                    hits={'goal': z3.BoolVal(False)})
    late = replace(_interval((action,), (action == 1,)), hits={'goal': action == 1})
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (early, late)))
    first, last = list(source_query_prefixes(history, (), check=lambda: None))
    assert z3_check((*first[0], first[1]['goal']), 1000).answer is SolverAnswer.UNSAT
    candidate = z3_check((*last[0], last[1]['goal']), 1000)
    assert candidate.answer is SolverAnswer.SAT
    assert source_member_hint(candidate.model, (history,), (*last[0], last[1]['goal']),
                              check=lambda: None) is not None


def test_prefixes_share_frames_and_preserve_cancellation():
    from behave.sim.rtl.source_member import source_query_prefixes

    actions = z3.BitVecs('prefix_a prefix_b prefix_c prefix_d prefix_e', 2)
    steps = tuple(replace(_interval((a,), (a == 1,)), hits={'goal': a == 1}) for a in actions)
    history = SimpleNamespace(member_plan=SourceMemberPlan((), steps))
    prefixes = list(source_query_prefixes(history, (), check=lambda: None))
    assert [len(roots) for roots, _, _ in prefixes] == [1, 2, 4, 5]
    assert prefixes[0][0][0] is steps[0].constraints[0]
    assert list(source_query_prefixes(SimpleNamespace(), (), check=lambda: None)) == []
    def cancelled():
        raise InterruptedError('prefix cancelled')
    with pytest.raises(InterruptedError):
        list(source_query_prefixes(history, (), check=cancelled))


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('kind', ('monitor', 'protocol', 'transaction', 'fault'))
def test_task_prefixes_include_completed_slot_events_and_validate_full_tail(private, kind):
    from behave.sim.rtl.source_member import source_query_prefixes

    context = z3.Context() if private else None
    public, first, later = z3.BitVecs('task_prefix_public first_monitor later_monitor', 4, ctx=context)
    # Startup plus three physical phases per slot. The first two proposal
    # boundaries are before the first complete slot and must expose no monitor.
    steps = tuple(_interval((z3.BitVecVal(0, 1, ctx=context),),
                            (z3.BoolVal(True, ctx=context),)) for _ in range(7))
    definitions = (first == public + 1, later == first + 1)
    false = z3.BoolVal(False, ctx=context)
    events = ({kind: first == 3}, {kind: later == 4})
    empty = ({}, {})
    history = SimpleNamespace(
        member_plan=SourceMemberPlan((), steps),
        source=SimpleNamespace(slots=(SimpleNamespace(phases=steps[1:4]),
                                      SimpleNamespace(phases=steps[4:]))),
        protocol=SimpleNamespace(hits=events if kind == 'protocol' else empty),
        monitor_hits=events if kind == 'monitor' else empty,
        transaction_hits=events if kind == 'transaction' else empty,
        monitor_faults=events if kind == 'fault' else empty,
        monitor_constraints=definitions, monitor_frame_sizes=(1, 1),
        terminal_hits={'terminal': false})
    prefixes = list(source_query_prefixes(history, (), check=lambda: None))
    assert len(prefixes) == 4
    assert all(not hits and not faults for _, hits, faults in prefixes[:2])
    roots, hits, faults = prefixes[2]
    assert 'terminal' not in hits
    assert (set(hits), set(faults)) == ((set(), {'fault'}) if kind == 'fault' else ({kind}, set()))
    assert roots[-1] is definitions[0]
    assert not any(root.eq(definitions[1]) for root in roots)
    target = (faults if kind == 'fault' else hits)[kind]
    proposed = z3_check((*roots, target), 1000)
    assert proposed.answer is SolverAnswer.SAT
    full = (*prefixes[-1][0], target)
    model = source_member_hint(proposed.model, (history,), full, check=lambda: None)
    assert model is not None and model.eval(later).as_long() == 4
    assert all(z3.is_true(model.eval(term, model_completion=False)) for term in full)
    assert source_member_hint(proposed.model, (history,), (*full, later == 9),
                              check=lambda: None) is None


@pytest.mark.parametrize('field', ('monitor_hits', 'monitor_faults', 'monitor_frame_sizes'))
@pytest.mark.parametrize('length', (1, 3))
def test_partial_monitor_images_do_not_guess_slot_alignment(field, length):
    from behave.sim.rtl.source_member import source_query_prefixes

    a, b = z3.Bools('aligned_a aligned_b')
    steps = tuple(replace(_interval((z3.BitVecVal(0, 1),), (z3.BoolVal(True),)),
                          hits={'source': a}) for _ in range(2))
    history = SimpleNamespace(
        member_plan=SourceMemberPlan((), steps),
        source=SimpleNamespace(slots=tuple(SimpleNamespace(phases=(step,)) for step in steps)),
        monitor_hits=({'monitor': a}, {'monitor': b}),
        monitor_faults=({'fault': a}, {'fault': b}),
        monitor_constraints=(a == b, b == a), monitor_frame_sizes=(1, 1))
    setattr(history, field, (1 if field == 'monitor_frame_sizes' else {'partial': a},) * length)
    rows = list(source_query_prefixes(history, (), check=lambda: None))
    assert len(rows) == 2
    assert all(set(hits) == {'source'} and not faults for _, hits, faults in rows)
    assert history.monitor_constraints == (a == b, b == a)


@pytest.mark.parametrize('prefix_answer', ('sat', 'unsat', 'unknown', 'wrong-order'))
@pytest.mark.parametrize('tail_conflict', (False, True))
def test_native_prefix_proposes_order_but_never_replaces_full_validation(prefix_answer, tail_conflict):
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.rtl.source_member import solve_fixed_source_member, source_query_prefixes

    public = z3.BitVec('native_prefix_public', 2)
    first, later = z3.BitVecs('native_prefix_first native_prefix_later', 1)
    early = _interval((first,), (z3.Or(first == 0, first == 1),))
    late = _interval((later,), (later == 0,))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (early, late)))
    environment = (public == 2,)
    roots = (*environment, *early.constraints, *late.constraints, first == 1,
             later == int(tail_conflict))
    hints = source_query_prefixes(history, environment, check=lambda: None,
        cut_constraints={id(early): (first == int(prefix_answer != 'wrong-order'),)})
    calls = []
    def solve(phase, assertions):
        calls.append(phase)
        if phase == 'native-prefix' and prefix_answer in ('unsat', 'unknown'):
            return ExactCheckResult(SolverAnswer.UNSAT if prefix_answer == 'unsat'
                else SolverAnswer.UNKNOWN,
                reason='injected prefix result' if prefix_answer == 'unknown' else '')
        return z3_check(assertions, 1000)
    answer = solve_fixed_source_member(roots, (history,), environment, solve,
        phase='native', check=lambda: None, prefixes=(row[0] for row in hints))
    assert answer.answer is (SolverAnswer.UNSAT if tail_conflict else SolverAnswer.SAT)
    assert calls[:2] == ['native-environment', 'native-prefix']
    assert ('native' in calls) is (tail_conflict or prefix_answer != 'sat')
    if not tail_conflict:
        assert all(z3.is_true(answer.model.eval(term, model_completion=False)) for term in roots)


@pytest.mark.parametrize('private', (False, True))
def test_fault_prefix_is_not_a_public_hit_and_needs_full_extension(private):
    from behave.sim.rtl.source_member import source_query_prefixes

    context = z3.Context() if private else None
    public, first, later = z3.BitVecs('fault_public fault_first fault_later', 2, ctx=context)
    fault = first == 1
    early = replace(_interval((first,), (first == public,)),
                    hits={'goal': z3.BoolVal(False, ctx=context)}, domain_faults={'xz': fault})
    late = _interval((later,), (later == public,))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (early, late)))
    prefix, complete = list(source_query_prefixes(history, (), check=lambda: None))
    roots, hits, faults = prefix
    assert set(hits) == {'goal'} and set(faults) == {'xz'}
    assert z3.is_false(z3.simplify(hits['goal']))
    proposal = z3_check((*roots, faults['xz']), 1000)
    assert proposal.answer is SolverAnswer.SAT
    full = (*complete[0], complete[2]['xz'])
    model = source_member_hint(proposal.model, (history,), full, check=lambda: None)
    assert model is not None
    assert all(z3.is_true(model.eval(term, model_completion=False)) for term in full)
    assert source_member_hint(proposal.model, (history,), (*full, later == 2),
                              check=lambda: None) is None


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('conflict', (False, True))
def test_greedy_source_member_checks_complete_original_formula(private, conflict):
    context = z3.Context() if private else None
    public, initial, sample, monitor = z3.BitVecs('public initial sample monitor', 8, ctx=context)
    action, later = z3.BitVecs('action later', 2, ctx=context)
    first = action == 1
    last = later == z3.If(sample == 8, z3.BitVecVal(2, 2, ctx=context),
                         z3.BitVecVal(3, 2, ctx=context))
    definition = monitor == sample + 1
    plan = SourceMemberPlan(((initial, public),), (
        _interval((action,), (first,)), ((sample, initial + 1),),
        _interval((later,), (last,)),
    ))
    history = SimpleNamespace(member_plan=plan, monitor_constraints=(definition,), monitor_frame_sizes=(1,))
    roots = (public == 7, initial == public, first, sample == initial + 1,
             last, definition, monitor == (10 if conflict else 9))
    environment = z3_check((public == 7,), 1000).model
    result = source_member_hint(environment, (history,), roots, check=lambda: None)
    assert z3.eq(environment.eval(action), action)  # Caller model remains unchanged.
    if conflict:
        assert result is None
    else:
        assert all(z3.is_true(result.eval(term)) for term in roots)
        assert result.eval(later).as_long() == 2
        assert z3_check(roots, 1000).answer is SolverAnswer.SAT


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('conflict', (None, 0, 15, 31))
def test_fixed_order_validates_shared_roots_once_not_each_prefix(monkeypatch, private, conflict):
    context = z3.Context() if private else None
    public, initial = z3.BitVecs('fixed_public fixed_initial', 8, ctx=context)
    prior, steps, predicates = initial, [], []
    for i in range(32):
        after = z3.BitVec(f'fixed_step_{i}', 8, ctx=context)
        definition = after == prior + 1
        predicate = z3.And(z3.UGE(prior, 7), definition,
                           after != (i + 8 if i == conflict else 200))
        steps.append(replace(_interval((z3.BitVecVal(0, 2, ctx=context),), (predicate,)),
                             step_definitions=((definition,),)))
        predicates.append(predicate)
        prior = after
    history = SimpleNamespace(member_plan=SourceMemberPlan(((initial, public),), tuple(steps)))
    roots = (public == 7, initial == public, *predicates, prior == 39)
    proposal = z3_check((public == 7,), 1000).model
    original = z3.ModelRef.eval
    predicate_ids = {term.get_id() for term in predicates}
    repeated = []

    def counted(model, term, model_completion=False):
        if term.get_id() in predicate_ids:
            repeated.append(term.get_id())
        return original(model, term, model_completion=model_completion)

    with monkeypatch.context() as patch:
        patch.setattr(z3.ModelRef, 'eval', counted)
        result = source_member_hint(proposal, (history,), roots, check=lambda: None)
    assert not repeated  # Fixed choices need no repeated prefix feasibility.
    assert z3.eq(proposal.eval(initial), initial)  # Do not mutate the proposal.
    if conflict is not None:
        assert result is None  # Early, middle, and final violations all survive.
        assert z3_check(roots, 1000).answer is SolverAnswer.UNSAT
    else:
        assert result is not None
        assert all(z3.is_true(result.eval(term)) for term in roots)


@pytest.mark.parametrize('failure', ('action', 'drive', 'missing-plan', 'bad-binding', 'missing-event', 'cancel'))
def test_failed_member_hint_never_establishes_nonemptiness(failure):
    action = z3.BitVec('failed_member_action', 2)
    target = action == 1
    interval = _interval((action,), (target,))
    plan = SourceMemberPlan((), (interval,))
    check = lambda: None
    if failure == 'action':
        interval = replace(interval, constraints=(z3.BoolVal(False),))
    elif failure == 'drive':
        interval = replace(interval, actions=(z3.BitVecVal(0, 2),), constraints=(z3.BoolVal(False),))
    elif failure == 'missing-event':
        interval = replace(interval, actions=())
    if failure == 'bad-binding':
        plan = SourceMemberPlan(((action + 1, action),), ())
    elif failure == 'missing-plan':
        plan = None
    else:
        plan = replace(plan, steps=(interval,))
    history = SimpleNamespace(member_plan=plan)
    if failure == 'cancel':
        def check():
            raise InterruptedError('cancelled member construction')
        with pytest.raises(InterruptedError):
            source_member_hint(z3.Model(), (history,), (target,), check=check)
    else:
        assert source_member_hint(z3.Model(), (history,), (target,), check=check) is None


def test_two_histories_cannot_overwrite_a_conflicting_shared_binding():
    shared = z3.BitVec('shared_member', 2)
    first = SimpleNamespace(member_plan=SourceMemberPlan(((shared, z3.BitVecVal(1, 2)),), ()))
    second = SimpleNamespace(member_plan=SourceMemberPlan(((shared, z3.BitVecVal(2, 2)),), ()))
    assert source_member_hint(z3.Model(), (first, second), (shared == 1, shared == 2),
                              check=lambda: None) is None


@pytest.mark.parametrize('fallback', ('none', 'greedy-miss', 'unknown', 'unsat', 'no-model', 'no-plan'))
def test_fixed_request_member_keeps_full_formula_fallback(fallback):
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.rtl.source_member import solve_fixed_source_member

    public, value = z3.BitVecs('fixed_member_public fixed_member_value', 4)
    action = z3.BitVec('fixed_member_action', 1)
    step = _interval((action,), (z3.Or(action == 0, action == 1),))
    history = SimpleNamespace(member_plan=SourceMemberPlan(((value, public),), (step,)))
    if fallback == 'no-plan':
        history.member_plan = None
    environment = (public == 3,)
    roots = (*environment, value == public, action == int(fallback == 'greedy-miss'))
    calls = []

    def solve(phase, assertions):
        calls.append((phase, assertions))
        if phase == 'fixed-environment':
            assert assertions is environment
            if fallback in ('unknown', 'unsat', 'no-model'):
                if fallback == 'no-model':
                    # Deliberately malformed boundary double: the closed
                    # ExactCheckResult itself forbids a SAT without a model.
                    return SimpleNamespace(answer=SolverAnswer.SAT, model=None)
                return ExactCheckResult({'unknown': SolverAnswer.UNKNOWN,
                    'unsat': SolverAnswer.UNSAT}[fallback],
                    reason='injected environment unknown' if fallback == 'unknown' else '')
        else:
            assert phase == 'fixed' and assertions is roots
        return z3_check(assertions, 1000)

    answer = solve_fixed_source_member(roots, (history,), environment, solve,
                                      phase='fixed', check=lambda: None)
    assert answer.answer is SolverAnswer.SAT
    assert all(z3.is_true(answer.model.eval(term)) for term in roots)
    assert [phase for phase, _ in calls] == (
        ['fixed-environment'] if fallback == 'none' else
        ['fixed'] if fallback == 'no-plan' else ['fixed-environment', 'fixed'])


def test_fixed_request_hint_does_not_hide_unsat_or_cancellation():
    from behave.sim.rtl.source_member import solve_fixed_source_member

    public = z3.BitVec('fixed_member_impossible', 1)
    history = SimpleNamespace(member_plan=SourceMemberPlan((), ()))
    environment, roots = (public == 0,), (public == 0, public == 1)
    calls = []
    cancelled = False

    def solve(phase, assertions):
        calls.append(phase)
        return z3_check(assertions, 1000)

    def check():
        if cancelled and calls:
            raise InterruptedError('owner cancelled after environment solve')

    answer = solve_fixed_source_member(roots, (history,), environment, solve,
                                      phase='fixed', check=check)
    assert answer.answer is SolverAnswer.UNSAT
    assert calls == ['fixed-environment', 'fixed']
    calls.clear()
    cancelled = True
    with pytest.raises(InterruptedError, match='owner cancelled'):
        solve_fixed_source_member(roots, (history,), environment, solve,
                                  phase='fixed', check=check)
    assert calls == ['fixed-environment']


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('invalid', (None, 'request', 'native', 'context', 'cancel'))
def test_fixed_request_reuses_proposal_only_after_every_native_root(private, invalid):
    from behave.sim.rtl.source_member import solve_fixed_source_member

    ctx = z3.Context() if private else None
    public, output = z3.BitVecs('member_proposal_public member_proposal_output', 4, ctx=ctx)
    action = z3.BitVec('member_proposal_action', 1, ctx=ctx)
    predicate = z3.Or(action == 0, action == 1)
    history = SimpleNamespace(member_plan=SourceMemberPlan(
        ((output, public + 1),), (_interval((action,), (predicate,)),)))
    environment = (public == 3,)
    roots = (*environment, output == public + 1, predicate, action == 1,
             output == (5 if invalid == 'native' else 4))
    proposal = z3_check((public == (2 if invalid == 'request' else 3), action == 1), 1000).model
    if invalid == 'context':
        proposal = proposal.translate(z3.Context())
    before = proposal.sexpr()
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        return z3_check(assertions, 1000)

    def check():
        if invalid == 'cancel':
            raise InterruptedError('proposal cancelled')

    if invalid == 'cancel':
        with pytest.raises(InterruptedError, match='proposal cancelled'):
            solve_fixed_source_member(roots, (history,), environment, solve,
                phase='native', check=check, proposal=proposal)
        assert not calls
    else:
        answer = solve_fixed_source_member(roots, (history,), environment, solve,
            phase='native', check=check, proposal=proposal)
        if invalid == 'native':
            assert answer.answer is SolverAnswer.UNSAT
        else:
            assert answer.answer is SolverAnswer.SAT
            assert all(z3.is_true(answer.model.eval(term, model_completion=False)) for term in roots)
        assert bool(calls) is (invalid is not None)
        if calls:
            assert calls == ['native-environment', 'native']
    assert proposal.sexpr() == before  # Extension never mutates retained evidence.


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('direct', (False, True))
@pytest.mark.parametrize('invalid', (None, 'tail', 'request', 'owner', 'context', 'cancel'))
def test_decode_reuses_query_proposal_without_consuming_native_hint(private, direct, invalid):
    from behave.sim.rtl.analysis_job import _SourceAnalysis

    context = z3.Context() if private else None
    public, output = z3.BitVecs('decode_public decode_output', 4, ctx=context)
    action = z3.BitVec('decode_action', 1, ctx=context)
    predicate = z3.Or(action == 0, action == 1)
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (
        ((output, public + 1),), _interval((action,), (predicate,)),
    )))
    roots = (output == public + 1, predicate, action == 1)
    if invalid == 'tail':
        roots += (output == 5,)  # Absent from the projected query, but mandatory here.
    request = SimpleNamespace(constraints=(), fix=lambda rows: (public == rows[0]['x'],))
    exact = _SourceAnalysis.__new__(_SourceAnalysis)
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=object() if direct else None))
    exact.request, exact.encoding, exact.constraints = request, history, roots
    if direct:
        exact.domain = SimpleNamespace(bound=1)
        history.constraints, history.environment_assumptions = roots, {}
        def bind(horizon, **kwargs):
            assert horizon == 1 and kwargs == {'goals': (), 'fixed_rows': rows}
            return None, exact.request, history
        exact._membership_source = SimpleNamespace(bind_request=bind)
    exact._request_members = {}
    exact._pending_query_member = None
    projected = SimpleNamespace(constraints=(public == 3, action == 1), environment_assumptions={})
    proposal = z3_check(projected.constraints, 1000).model
    rows = [{'x': 3}]
    exact._retain_query_member(rows, proposal, lambda: None, encoding=projected)
    assert exact._pending_query_member is not None
    if invalid == 'request':
        rows = [{'x': 2}]
    elif invalid == 'owner':
        exact.request = SimpleNamespace(constraints=(), fix=request.fix)
    elif invalid == 'context':
        old = exact._pending_query_member
        exact._pending_query_member = (*old[:3], proposal.translate(z3.Context()))
    retained = exact._pending_query_member
    before = retained[3].sexpr()
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        return z3_check(assertions, 1000)

    def check():
        if invalid == 'cancel':
            raise InterruptedError('decode cancelled')

    if invalid == 'cancel':
        with pytest.raises(InterruptedError, match='decode cancelled'):
            exact._request_failure(rows, solve=solve, check=check)
        assert not calls and not exact._request_members
    else:
        result = exact._request_failure(rows, solve=solve, check=check)
        if invalid == 'tail':
            assert result.answer is SolverAnswer.UNSAT
            assert not exact._request_members
        else:
            assert result is None and exact._request_members
        assert bool(calls) is (invalid is not None)
    assert exact._pending_query_member is retained
    assert retained[3].sexpr() == before
    # The next native consumer still owns the one-shot handoff; only its
    # own full-root check can turn the cross-projection proposal into evidence.
    exact._take_query_member(rows, history, roots, lambda: None, proposal=True)
    assert exact._pending_query_member is None


@pytest.mark.parametrize('value', (0, 1))
def test_hdl_fixed_initial_bits_are_checked_not_reassigned(value):
    public = z3.BitVec('fixed_initial_public', 1)
    plan = SourceMemberPlan(((z3.BitVecVal(0, 1), public),), ())
    history = SimpleNamespace(member_plan=plan)
    environment = z3_check((public == value,), 1000).model
    result = source_member_hint(environment, (history,), (public == 0,), check=lambda: None)
    assert (result is not None) is (value == 0)


@pytest.mark.parametrize('private', (False, True))
def test_event_successor_definitions_are_recomputed_for_each_choice(private):
    context = z3.Context() if private else None
    action = z3.BitVec('defined_action', 2, ctx=context)
    successor = z3.BitVec('defined_successor', 2, ctx=context)
    equation = successor == action + 1
    predicate = z3.And(equation, successor == 3)
    interval = replace(_interval((action,), (predicate,)), step_definitions=((equation,),))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (interval,)))
    result = source_member_hint(z3.Model(ctx=context), (history,), (predicate,), check=lambda: None)
    assert result is not None
    assert result.eval(action).as_long() == 2
    assert result.eval(successor).as_long() == 3


@pytest.mark.parametrize('alive', (False, True))
@pytest.mark.parametrize('succeeds', (False, True))
@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('shared', (False, True))
def test_control_successors_preserve_failure_and_dead_prefixes(alive, succeeds, private, shared):
    from behave.sim.rtl.source_relation import _define_control, _define_controls

    context = z3.Context() if private else None
    entered, ok = z3.Bools('control_entered control_ok', ctx=context)
    prior, new = z3.BitVecs('control_prior control_new', 3, ctx=context)
    definitions = []
    terms = (z3.And(entered, ok), z3.If(entered, new, prior))
    tags = ('running', 'error')
    if shared:
        running, error = _define_controls(terms, definitions, tags)
    else:
        running, error = (_define_control(term, definitions, tag)
                          for term, tag in zip(terms, tags, strict=True))
    roots = (entered == alive, ok == succeeds, prior == 2, new == 5, *definitions)
    solver = z3.Solver(ctx=context)
    solver.add(*roots)
    assert solver.check() == z3.sat
    solver.add(z3.Or(running != (alive and succeeds), error != (5 if alive else 2)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', (False, True))
def test_unchanged_source_storage_does_not_accumulate_hold_history(private):
    from behave.sim.rtl.source_relation import _hold_value

    context = z3.Context() if private else None
    state = z3.BitVec('held_unchanged', 8, ctx=context)
    original = state
    for i in range(100):
        state = _hold_value(z3.Bool(f'held_enabled_{i}', ctx=context), state, state)
    assert state.eq(original)
    enable = z3.Bool('held_changed_enable', ctx=context)
    changed = _hold_value(enable, original + 1, original)
    solver = z3.Solver(ctx=context)
    solver.add(changed != z3.If(enable, original + 1, original))
    assert solver.check() == z3.unsat


@pytest.mark.eda('slang')
@pytest.mark.solver
@pytest.mark.parametrize('depth', (0, 2))
def test_compiler_source_member_validates_complete_history_without_solver_fallback(tmp_path, depth):
    from behave.sim.rtl.source_goals import manifest_of
    from behave.sim.rtl.source_task import SourceTaskRelation
    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
    from .test_rtl_source_memory_relation import _prepare
    from .test_rtl_source_monitors import _monitors
    from .test_rtl_source_request_domain import _request

    machine, plan, _, template = _prepare(tmp_path, HISTORY_SOURCE, initialization=True)
    contract = _simple_contract(reset_policy='clears')
    relation = SourceTaskRelation(template, machine, contract,
        tuple(manifest_of(plan.declarations, filename='design.sv')),
        complete_state=True, monitors=_monitors(contract))
    request, _, _ = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    pair = relation.compare_request(request)
    environment = z3_check((*request.constraints, request.lengths['in'] == depth), 10000)
    assert environment.answer is SolverAnswer.SAT
    fixed = request.fix(request.rows(environment.model))
    roots = (*pair.constraints, *fixed)
    result = source_member_hint(environment.model, (pair.left, pair.right), roots, check=lambda: None)
    assert result is not None
    assert all(z3.is_true(result.eval(term, model_completion=True)) for term in roots)
