"""Removing future constraints can prove UNSAT, never authorize a SAT finding."""

from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.source_confluence import SourceOrderComparison, _ProofPrefixes
from behave.sim.rtl.source_relation import SourceObservation, source_event_images


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('pattern', range(32))
def test_native_first_hit_requirements_equal_every_original_prefix(private, pattern):
    from behave.sim.rtl.observation_audit import _event_requirements

    ctx = z3.Context() if private else z3.main_ctx()
    old, new, seen, cumulative = [], [], set(), {}
    for phase in range(5):
        # Independent occurrence/goal bits need not have matching first hits.
        terms = {name: z3.Bool(f'{name}_{phase}', ctx=ctx) for name in ('goal', 'occurrence')}
        observed = {name for i, name in enumerate(terms) if pattern & (1 << ((phase + i) % 5))}
        for name, term, expected in _event_requirements(terms, observed, seen):
            new.append(term == expected)
        for name, term in terms.items():
            cumulative[name] = z3.Or(cumulative.get(name, z3.BoolVal(False, ctx)), term)
            old.append(cumulative[name] == (name in seen))
        solver = z3.Solver(ctx=ctx)
        # No circuit, trace correctness, or known input is assumed here.
        solver.add(z3.Xor(z3.And(*old), z3.And(*new)))
        assert solver.check() == z3.unsat


def test_phase_event_requirements_keep_repeated_hits_and_later_disappearance():
    from behave.sim.rtl.observation_audit import _event_requirements

    term = z3.Bool('phase_event')
    for observed in ({'g'}, {'g'}, set(), {'g'}):
        entries = list(_event_requirements({'g': term}, observed))
        assert len(entries) == 1
        name, value, expected = entries[0]
        assert name == 'g' and value.eq(term) and expected == ('g' in observed)


@pytest.mark.parametrize('phases', [4, 128])
def test_first_hit_reduction_does_not_repeat_settled_bits_or_drop_never_hits(phases):
    from behave.sim.rtl.observation_audit import _event_requirements

    seen, counts = set(), {'early': 0, 'late': 0, 'never': 0}
    for phase in range(phases):
        terms = {name: z3.Bool(f'{name}_{phase}') for name in counts}
        observed = {'early'} | ({'late'} if phase == phases - 1 else set())
        for name, _term, _expected in _event_requirements(terms, observed, seen):
            counts[name] += 1
    assert counts == {'early': 1, 'late': phases, 'never': phases}
    assert seen == {'early', 'late'}
    # The next history owns a fresh observation state, not the previous hits.
    assert len(list(_event_requirements(terms, set(), set()))) == 3


def _inductive_pair(*, private=False):
    ctx = z3.Context() if private else None
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)
    sides = []
    for side in ('left', 'right'):
        q = z3.Int(f'inductive_{side}', ctx=ctx)
        parts = tuple(SimpleNamespace(constraints=(q + i >= 0,), state={'q': q + i},
            alive=true, entered=true, error=false, quiescent=true) for i in range(3))
        protocol = SimpleNamespace(constraints=(q >= -1, q >= -2, q >= -3),
            constraint_slots=(-1, 0, 1),
            **{name: tuple(q + 10 + i for i in range(3))
               for name in ('adapters', 'pointers', 'released', 'framing')})
        frames = ((q >= -4,), (q >= -5,))
        sides.append(SimpleNamespace(alive=true, unfinished=false, domain_faults={},
            startup=parts[0], source=SimpleNamespace(memory=SimpleNamespace(conflicts=(), arrays={}),
                slots=tuple(SimpleNamespace(phases=(part,)) for part in parts[1:])),
            protocol=protocol, sample_frames=frames, monitor_frame_sizes=(1, 1, 1),
            constraints=(*protocol.constraints, *(term for frame in frames for term in frame),
                         *(term for part in parts for term in part.constraints))))
    differences = {'startup.q': false, 'slot[0].phase[0].q': false,
        'slot[1].phase[0].q': false, 'protocol.adapters[1].q': sides[0].protocol.adapters[1] != sides[1].protocol.adapters[1],
        'terminal.error': false}
    return SourceOrderComparison(*sides, differences)


@pytest.mark.parametrize('private', [False, True])
def test_induction_is_a_stronger_view_without_domain_or_native_mutation(private):
    pair = _inductive_pair(private=private)
    old_roots, old_groups = pair.constraints, dict(pair._confluence_obligations())
    view = pair.inductive(check=lambda: None)
    assert view is not None and view is not pair
    assert view.left is pair.left and view.right is pair.right
    assert view.differences is pair.differences and view._observations == ()
    assert view.constraints == old_roots and pair._induction is None
    groups = dict(view._confluence_obligations())
    for name, predicate in old_groups.items():
        solver = z3.Solver(ctx=pair.left.alive.ctx)
        solver.add(predicate, z3.Not(groups[name]))
        assert solver.check() == z3.unsat
    protocol = pair.differences['protocol.adapters[1].q']
    assert any(term.eq(protocol) for term in view._induction['slot[0]'])
    assert groups['confluence:protocol'].eq(old_groups['confluence:protocol'])
    assert view.inductive(check=lambda: None) is None


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('kind', ['scalar', 'nested', 'array'])
def test_private_state_equality_is_recursive_and_guarded(private, kind):
    from behave.sim.symbolic.functional_array import FunctionalArray

    pair = _inductive_pair(private=private)
    ctx = pair.left.alive.ctx
    x, y = z3.BitVecs('private_x private_y', 8, ctx=ctx)
    live, entered = z3.Bools('private_live private_entered', ctx=ctx)
    for history, value in zip((pair.left, pair.right), (x, y), strict=True):
        if kind == 'array':
            value = FunctionalArray(3, value)
        elif kind == 'nested':
            value = {'nested': ([value], (value,))}
        history.startup.state = {'private': value}
        history.startup.alive = live
        history.startup.entered = entered
    group = dict(pair.inductive(check=lambda: None)._confluence_obligations())['confluence:startup']
    solver = z3.Solver(ctx=ctx)
    solver.add(group, z3.Or(z3.Not(live), x == y))
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=ctx)
    solver.add(group, live, x != y)
    assert solver.check() == z3.sat
    # Quiescence is only observable at an entered, live phase.
    pair.right.startup.quiescent = z3.BoolVal(False, ctx=ctx)
    group = dict(pair.inductive(check=lambda: None)._confluence_obligations())['confluence:startup']
    solver = z3.Solver(ctx=ctx)
    solver.add(group, x == y, live, z3.Not(entered))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('missing', ['memory', 'frames', 'owners', 'entry'])
def test_induction_requires_complete_boundary_metadata(missing):
    pair = _inductive_pair()
    if missing == 'memory':
        pair.left.source.memory.arrays['ram'] = object()
    elif missing == 'frames':
        pair.left.sample_frames = ()
    elif missing == 'owners':
        pair.left.protocol.constraint_slots = (-1, 0, 9)
    else:
        pair.left.protocol.adapters = ()
    assert pair.inductive(check=lambda: None) is None
    assert pair._induction is None


def test_induction_construction_obeys_owner_cancellation():
    pair = _inductive_pair()
    def cancel():
        raise InterruptedError('cancel induction')
    with pytest.raises(InterruptedError, match='cancel induction'):
        pair.inductive(check=cancel)
    assert pair._induction is None


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('bounds', [(0, 1), (-2, 3), (-1000, 1000), (200, 201), (-200, -129)])
def test_inductive_ranges_are_signed_guarded_obligations_not_assumptions(private, bounds):
    from behave.sim.symbolic.ranges import Range

    pair = _inductive_pair(private=private)
    ctx = pair.left.alive.ctx
    value = z3.BitVec('range_candidate', 8, ctx=ctx)
    live = z3.Bool('range_candidate_alive', ctx=ctx)
    for side in (pair.left, pair.right):
        side.startup.state = {'q': value}
        side.startup.alive = live
        side.startup.state_range_candidates = {'q': Range(*bounds)}
    old_roots = pair.constraints
    view = pair.inductive(check=lambda: None)
    assert view.constraints == old_roots
    defect = dict(view._confluence_obligations())['confluence:startup']
    expected = z3.And(live, z3.Or(z3.BV2Int(value, is_signed=True) < bounds[0],
                                  z3.BV2Int(value, is_signed=True) > bounds[1]))
    solver = z3.Solver(ctx=ctx)
    solver.add(defect != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
def test_proved_predecessor_range_rules_out_spurious_wide_boundary(private):
    from behave.sim.symbolic.ranges import Range

    pair = _inductive_pair(private=private)
    ctx = pair.left.alive.ctx
    x = z3.BitVec('one_bit_in_wide_carrier', 8, ctx=ctx)
    value = x & 1
    false = z3.BoolVal(False, ctx=ctx)
    for side in (pair.left, pair.right):
        for part in (side.startup, *(slot.phases[0] for slot in side.source.slots)):
            part.state = {'q': value}
            part.state_range_candidates = {'q': Range(0, 1)}
    pair.differences.clear()
    pair.differences.update({'startup.q': false, 'slot[0].phase[0].q': false,
                            'slot[1].phase[0].q': value != (value & 1)})
    view = pair.inductive(check=lambda: None)
    for current, expected in ((pair, z3.sat), (view, z3.unsat)):
        groups = dict(current._confluence_obligations())
        previous = 'confluence:slot[0]'
        phase = 'confluence:slot[1]'
        # Prove the extra premise against the original expressions first.
        solver = z3.Solver(ctx=ctx)
        solver.add(groups[previous])
        assert solver.check() == z3.unsat
        roots = current._prefix_query(phase, current.constraints,
            current._prefix_premises(phase, {previous: z3.Not(groups[previous])}, ()), groups[phase])
        solver = z3.Solver(ctx=ctx)
        solver.add(*roots)
        assert solver.check() == expected


@pytest.mark.parametrize('private', [False, True])
def test_wrong_range_candidate_fails_base_case_without_restricting_original(private):
    from behave.sim.symbolic.ranges import Range

    pair = _inductive_pair(private=private)
    ctx = pair.left.alive.ctx
    for side in (pair.left, pair.right):
        side.startup.state = {'q': z3.BitVecVal(3, 8, ctx=ctx)}
        side.startup.state_range_candidates = {'q': Range(0, 1)}
    original = dict(pair._confluence_obligations())['confluence:startup']
    stronger = dict(pair.inductive(check=lambda: None)._confluence_obligations())['confluence:startup']
    solver = z3.Solver(ctx=ctx)
    solver.add(original)
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=ctx)
    solver.add(stronger)
    assert solver.check() == z3.sat


@pytest.mark.parametrize('private', [False, True])
def test_range_preservation_is_checked_not_assumed_on_the_current_transition(private):
    from behave.sim.symbolic.ranges import Range

    pair = _inductive_pair(private=private)
    ctx = pair.left.alive.ctx
    for side in (pair.left, pair.right):
        for at, part in enumerate((side.startup, *(slot.phases[0] for slot in side.source.slots))):
            part.state = {'q': z3.BitVecVal(3 if at == 2 else 0, 8, ctx=ctx)}
            part.state_range_candidates = {'q': Range(0, 1)}
    pair.differences.clear()
    pair.differences.update((name, z3.BoolVal(False, ctx=ctx))
        for name in ('startup.q', 'slot[0].phase[0].q', 'slot[1].phase[0].q'))
    view = pair.inductive(check=lambda: None)
    groups = dict(view._confluence_obligations())
    previous, phase = 'confluence:slot[0]', 'confluence:slot[1]'
    assert z3.is_false(z3.simplify(groups[previous]))
    roots = view._prefix_query(phase, view.constraints,
        view._prefix_premises(phase, {previous: z3.Not(groups[previous])}, ()), groups[phase])
    solver = z3.Solver(ctx=ctx)
    solver.add(*roots)
    assert solver.check() == z3.sat


def test_induction_cuts_adapter_entries_but_preserves_external_constraints():
    pair = _inductive_pair()
    view = pair.inductive(check=lambda: None)
    external = z3.Bool('external_domain_restriction')
    predicate = dict(view._confluence_obligations())['confluence:slot[1]']
    view._prefix_query('confluence:slot[1]', (*view.constraints, external), (), predicate)
    removed, bindings = view._source_prefixes[1]
    replaced = {term.get_id() for term, _ in bindings.replacements}
    for history in (view.left, view.right):
        assert all(term.get_id() in replaced for term in history.protocol.adapters[:2])
        assert history.protocol.constraints[1].get_id() in removed
        assert history.sample_frames[0][0].get_id() in removed
        assert history.protocol.constraints[0].get_id() not in removed
        assert history.protocol.constraints[2].get_id() not in removed
    assert external.get_id() not in removed
    view._prefix_query('confluence:slot[0]', view.constraints, (), predicate)
    assert not view._source_prefixes[0][0]


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('bad', [False, True])
def test_local_source_transition_rechecks_removed_predecessor_definitions(private, bad):
    ctx = z3.Context() if private else None
    x, y = z3.Ints('local_source_x local_source_y', ctx=ctx)
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)
    initial, transition = x == 1, y == x + 1
    startup = SimpleNamespace(constraints=(initial,), state={'q': x + 1},
                              alive=true, error=false, quiescent=true)
    interval = SimpleNamespace(constraints=(transition,))
    predecessor = SimpleNamespace(constraints=(), state={'q': x + 1},
                                  alive=true, error=false, quiescent=true)
    side = SimpleNamespace(alive=true, unfinished=false, domain_faults={}, startup=startup,
        constraints=(initial, transition, true), monitor_constraints=(true,),
        monitor_frame_sizes=(1,), source=SimpleNamespace(memory=SimpleNamespace(conflicts=()),
            slots=(SimpleNamespace(phases=(predecessor,)), SimpleNamespace(phases=(interval,)))))
    pair = SourceOrderComparison(side, side, {'startup.q': false,
        'slot[0].phase[0].q': false, 'slot[1].phase[0].q': y != (3 if bad else 2)})
    calls = []

    def solve(label, roots):
        answer = z3_check_finite(roots, 10_000)
        calls.append((label, roots, answer.answer))
        return answer

    result = pair.audit(solve, prefix_proofs=True)
    assert (result.status is AuditCheckStatus.REFUSED) is bad
    assert any(label == 'confluence:slot[1]:prefix' and answer is SolverAnswer.SAT
               for label, _, answer in calls)
    complete = next(roots for label, roots, _ in calls if label == 'confluence:slot[1]')
    assert all(a.eq(b) for a, b in zip(complete, pair.constraints))
    # The speculative and ordered consumers use the same immutable cut plan.
    jobs = next(jobs for phase, jobs in pair.order_queries(prefix_proofs=True)
                if phase == 'confluence:slot[1]')
    planned = dict(jobs)['confluence:slot[1]:prefix']
    consumed = next(roots for label, roots, _ in calls if label == 'confluence:slot[1]:prefix')
    assert len(planned) == len(consumed) == 1 and planned[0].eq(consumed[0])


def test_first_source_step_retains_its_exact_startup_boundary():
    x, y = z3.Ints('startup_exact_x startup_exact_y')
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    initial, transition = x == 1, y == x + 1
    startup = SimpleNamespace(constraints=(initial,), state={'q': x + 1},
                              alive=true, error=false, quiescent=true)
    side = SimpleNamespace(monitor_frame_sizes=(1,), startup=startup,
        source=SimpleNamespace(slots=(SimpleNamespace(phases=(
            SimpleNamespace(constraints=(transition,)),)),)))
    pair = SourceOrderComparison(side, side, {})
    roots = pair._prefix_query('confluence:slot[0]', (initial, transition), (), y != 2)
    assert z3_check_finite(roots, 1000).answer is SolverAnswer.UNSAT
    removed, bindings = pair._source_prefixes[0]
    assert not removed and not bindings.replacements


def test_source_prefixes_share_names_not_proofs_across_history_boundaries(monkeypatch):
    x = z3.Int('shared_prefix_coordinate')
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    parts = tuple(SimpleNamespace(constraints=(), state={'q': x + i},
                  alive=true, error=false, quiescent=true) for i in range(4))
    side = SimpleNamespace(monitor_frame_sizes=(1,), startup=parts[0],
        source=SimpleNamespace(slots=tuple(SimpleNamespace(phases=(part,)) for part in parts[1:])))
    pair = SourceOrderComparison(side, side, {})
    created = []
    fresh = z3.FreshConst
    def track(*args, **kwargs):
        term = fresh(*args, **kwargs)
        created.append(term)
        return term
    monkeypatch.setattr(z3, 'FreshConst', track)
    pair._prefix_query('confluence:slot[1]', (), (), false)
    count = len(created)
    pair._prefix_query('confluence:slot[2]', (), (), false)
    assert count == len(created) == 4
    before = dict((a.get_id(), b) for a, b in pair._source_prefixes[1][1].replacements)
    after = dict((a.get_id(), b) for a, b in pair._source_prefixes[2][1].replacements)
    assert before.keys() < after.keys()
    assert all(term.eq(after[key]) for key, term in before.items())


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('bad', [False, True])
def test_goal_prefix_carry_cut_keeps_original_fallback(private, bad):
    from dataclasses import replace

    pair = replace(_inductive_pair(private=private), observation_scope=SourceObservation.GOAL_PREFIX)
    images = []
    for index, history in enumerate((pair.left, pair.right)):
        monitor = z3.Bool(f'carry_monitor_{index}', ctx=history.alive.ctx)
        history.monitor_constraints = (monitor,)
        history.monitor_frame_sizes = (1, 0, 0)
        parts = [history.startup, *(slot.phases[0] for slot in history.source.slots)]
        initial = parts[0].state['q'] == (5 if bad and index else 0)
        parts[0].constraints = (*parts[0].constraints, initial)
        history.constraints = (*history.constraints, initial, monitor)
        for part in parts:
            part.hits = {'goal': part.state['q'] == 2}
            part.occurrences = {'site': part.hits['goal']}
        images.append(tuple(source_event_images(parts, SourceObservation.GOAL_PREFIX)))
    for index, (left, right) in enumerate(zip(*images, strict=True)):
        name = 'startup' if index == 0 else f'slot[{index - 1}].phase[0]'
        pair.differences[name + '.hits.goal'] = left[0]['goal'] != right[0]['goal']
    calls = []
    def solve(label, roots):
        answer = z3_check_finite(roots, 10_000, _owned_context=pair.left.alive.ctx)
        calls.append((label, roots, answer.answer))
        return answer

    result = pair.audit(solve, prefix_proofs=True)
    assert (result.status is AuditCheckStatus.REFUSED) is bad
    assert any(label == 'confluence:slot[1]:prefix' and answer is SolverAnswer.SAT
               for label, _, answer in calls), [(label, answer) for label, _, answer in calls]
    complete = next(roots for label, roots, _ in calls if label == 'confluence:slot[1]')
    assert all(a.eq(b) for a, b in zip(complete, pair.constraints, strict=False))

    removed, bindings = pair._source_prefixes[1]
    replaced = {term.get_id() for term, _ in bindings.replacements}
    assert all(term.get_id() in removed for history in (pair.left, pair.right)
               for term in history.startup.constraints)
    assert all(term.get_id() in replaced for side in images for image in side[:2]
               for table in image for term in table.values() if not z3.is_const(term))
    assert not pair._source_prefixes[0][1].replacements  # Keep real startup.
    # Every original model extends to the cut formula. The fresh carry bits
    # are not asserted equal/false/hit; only a complete UNSAT proves agreement.
    for label, query, _ in calls:
        if label != 'confluence:slot[1]:prefix':
            continue
        full = pair._prefix_constraints('confluence:slot[1]', pair.constraints)
        facts = tuple(z3.Not(term) for _, term, *_ in pair._domain_obligations() if term is not None)
        lemmas = {name: z3.Not(predicate) for name, predicate in pair._confluence_obligations()
                  if name in {'confluence:startup', 'confluence:slot[0]'}}
        predicate = dict(pair._confluence_obligations())['confluence:slot[1]']
        old = z3.And(*full, *pair._prefix_premises('confluence:slot[1]', lemmas, facts), predicate)
        solver = z3.Solver(ctx=pair.left.alive.ctx)
        solver.add(old, *(fresh == term for term, fresh in bindings.replacements), z3.Not(query[0]))
        assert solver.check() == z3.unsat


def test_phase_events_do_not_allocate_prefix_carry():
    pair = _inductive_pair()
    assert pair._event_boundaries() == ()
    assert pair._source_event_boundaries is None


def test_goal_prefix_boundary_index_obeys_cancellation(monkeypatch):
    from dataclasses import replace

    from behave.sim import analysis_budget

    pair = replace(_inductive_pair(), observation_scope=SourceObservation.GOAL_PREFIX)
    def cancel():
        raise InterruptedError('cancel event boundary index')
    monkeypatch.setattr(analysis_budget, 'check_analysis', cancel)
    with pytest.raises(InterruptedError, match='event boundary'):
        pair._event_boundaries()
    assert pair._source_event_boundaries is None


def test_source_prefix_cuts_predecessor_entry_not_only_survival():
    old, alive, quiet, later = z3.Bools('entry_old entry_alive entry_quiet entry_later')
    entered = z3.And(old, alive, quiet)
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    part = SimpleNamespace(constraints=(old,), state={}, alive=alive,
                           error=false, quiescent=quiet, entered=entered)
    current = SimpleNamespace(constraints=(later,), state={}, alive=true,
                              error=false, quiescent=true, entered=later)
    side = SimpleNamespace(monitor_frame_sizes=(1,), startup=None,
        source=SimpleNamespace(slots=(SimpleNamespace(phases=(part,)),
                                     SimpleNamespace(phases=(current,)))))
    pair = SourceOrderComparison(side, side, {})
    roots = pair._prefix_query('confluence:slot[1]', (old, later), (), entered)
    removed, bindings = pair._source_prefixes[1]
    replacements = dict((a.get_id(), b) for a, b in bindings.replacements)
    assert entered.get_id() in replacements
    boundary = replacements[entered.get_id()]
    assert roots[0].eq(z3.And(later, boundary))
    # The new coordinate is free, not assumed true/equal/successfully entered.
    for value in (True, False):
        solver = z3.Solver()
        solver.add(later, bindings.substitute(entered) == value)
        assert solver.check() == z3.sat


def test_local_entry_predicate_does_not_expand_the_entire_previous_history():
    from z3.z3util import get_vars

    true, false = z3.BoolVal(True), z3.BoolVal(False)
    parts, entered = [], true
    for index in range(100):
        ready, live = z3.Bools(f'history_ready_{index} history_live_{index}')
        entered = z3.And(entered, ready, live)
        parts.append(SimpleNamespace(constraints=(), state={}, alive=live,
            error=false, quiescent=ready, entered=entered))
    side = SimpleNamespace(monitor_frame_sizes=(1,), startup=None,
        source=SimpleNamespace(slots=tuple(SimpleNamespace(phases=(part,)) for part in parts)))
    pair = SourceOrderComparison(side, side, {})
    for index in (10, 99):
        roots = pair._prefix_query(f'confluence:slot[{index}]', (), (), parts[index].entered)
        variables = get_vars(roots[0])
        assert len(variables) == 3  # Previous entry, current ready and current live.
        assert any(term.eq(parts[index].alive) for term in variables)
        assert any(term.eq(parts[index].quiescent) for term in variables)


@pytest.mark.parametrize('nonconstant', [False, True])
def test_constant_domain_defects_do_not_load_the_complete_history(nonconstant):
    false, true = z3.BoolVal(False), z3.BoolVal(True)
    defect = z3.Bool('optional_domain_defect') if nonconstant else false
    constraints = (z3.Not(defect), true)
    side = SimpleNamespace(alive=true, unfinished=false,
        domain_faults={'test': defect}, constraints=constraints,
        source=SimpleNamespace(slots=(), memory=SimpleNamespace(conflicts=(defect,))))
    pair = SourceOrderComparison(side, side, {'startup.x': false})
    calls = {}

    def solve(phase, roots):
        calls[phase] = roots
        return z3_check_finite(roots, 1000)

    assert pair.audit(solve, prefix_proofs=True).status is AuditCheckStatus.AGREED
    for phase in ('definedness', 'memory'):
        roots = calls[phase]
        if nonconstant:
            assert len(roots) > 1 and roots[:len(pair.constraints)] == pair.constraints
        else:
            assert len(roots) == 1 and z3.is_false(roots[0])


def test_source_prefix_uses_only_proved_immediate_predecessor_and_domain_facts():
    side = SimpleNamespace(monitor_frame_sizes=(1,))
    pair = SourceOrderComparison(side, side, {})
    old, previous, fact = z3.Bools('source_old source_previous source_domain_fact')
    lemmas = {'confluence:startup': old, 'confluence:slot[0]': previous}
    assert pair._prefix_premises('confluence:slot[1]', lemmas, (fact,)) == (previous, fact)
    assert pair._prefix_premises('confluence:slot[2]', lemmas, (fact,)) == (fact,)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('index', [-1, 0, 1, 2])
def test_source_facts_only_project_proved_local_absence(private, index):
    ctx = z3.Context() if private else None
    unfinished = z3.Bools('initial_work work0 work1 work2', ctx=ctx)
    faults = z3.Bools('initial_fault fault0 fault1 fault2', ctx=ctx)
    other = z3.Bool('unrecognized_fact', ctx=ctx)
    parts = tuple(SimpleNamespace(unfinished=work, domain_faults={'test': fault})
                  for work, fault in zip(unfinished, faults, strict=True))
    side = SimpleNamespace(monitor_frame_sizes=(1,), startup=parts[0],
        source=SimpleNamespace(slots=tuple(SimpleNamespace(phases=(part,)) for part in parts[1:])))
    pair = SourceOrderComparison(side, side, {})
    facts = (z3.Not(z3.Or(unfinished[0], z3.Or(*unfinished[1:]))),
             z3.Not(z3.Or(*faults)), other)
    phase = 'confluence:startup' if index < 0 else f'confluence:slot[{index}]'
    local = pair._prefix_premises(phase, {}, facts)
    selected = parts[:index + 2] if index <= 0 else parts[index:index + 2]
    expected = (other, *(term for part in selected for term in
                        (z3.Not(part.unfinished), z3.Not(part.domain_faults['test']))))
    assert len(local) == len(expected)
    assert all(a.eq(b) for a, b in zip(local, expected, strict=True))
    solver = z3.Solver(ctx=ctx)
    solver.add(*facts, z3.Not(z3.And(*local)))
    assert solver.check() == z3.unsat
    # Reusing this index cannot reuse facts from another admission attempt.
    replacement = (z3.Not(z3.Or(faults[index + 1])),)
    assert pair._prefix_premises(phase, {}, replacement) == (
        z3.Not(faults[index + 1]),)
    assert pair._prefix_premises(phase, {}, ()) == ()


def test_local_absence_projection_does_not_claim_an_unproved_invariant():
    missing, positive = z3.Bools('missing_local_fact positive_local_fact')
    part = SimpleNamespace(unfinished=missing, domain_faults={'test': missing})
    side = SimpleNamespace(monitor_frame_sizes=(1,), startup=part,
        source=SimpleNamespace(slots=(SimpleNamespace(phases=(part,)),)))
    pair = SourceOrderComparison(side, side, {})
    facts = (z3.Or(missing, positive), z3.Not(positive))
    assert pair._prefix_premises('confluence:slot[0]', {}, facts) == facts


def test_absence_projection_walks_shared_boolean_dag_once(monkeypatch):
    leaf = z3.Bool('shared_absence_leaf')
    fact = leaf
    for _ in range(40):
        fact = z3.Or(fact, fact)
    calls = []

    def check():
        calls.append(None)
        assert len(calls) < 100  # A tree expansion would take 2**40 visits.

    monkeypatch.setattr('behave.sim.analysis_budget.check_analysis', check)
    part = SimpleNamespace(unfinished=leaf, domain_faults={})
    side = SimpleNamespace(monitor_frame_sizes=(1,), startup=part,
        source=SimpleNamespace(slots=(SimpleNamespace(phases=(part,)),)))
    pair = SourceOrderComparison(side, side, {})
    assert pair._prefix_premises('confluence:slot[0]', {}, (z3.Not(fact),)) == (z3.Not(leaf),)


def test_external_memory_keeps_its_history_in_source_prefix_proofs():
    side = SimpleNamespace(monitor_frame_sizes=(1,),
        source=SimpleNamespace(memory=SimpleNamespace(arrays={'ram': object()})))
    pair = SourceOrderComparison(side, side, {})
    old, previous, fact, predicate = z3.Bools('ram_old ram_previous ram_fact ram_defect')
    lemmas = {'confluence:startup': old, 'confluence:slot[0]': previous}
    assert pair._prefix_premises('confluence:slot[1]', lemmas, (fact,)) == (old, previous, fact)
    roots = (old, previous)
    assert pair._prefix_query('confluence:slot[1]', roots, (fact,), predicate) == (
        *roots, fact, predicate)
    assert not pair._source_prefixes


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('memory,metadata', [(False, True), (True, True), (False, False)])
def test_source_prefix_only_cuts_owned_future_protocol_and_sample_roots(private, memory, metadata):
    ctx = z3.Context() if private else None
    global_root, p0, p1, s0, s1, external = z3.Bools('domain p0 p1 s0 s1 external', ctx=ctx)
    roots = (global_root, p0, p1, s0, s1, p0, external)
    side = SimpleNamespace(constraints=roots, monitor_frame_sizes=(0, 0), monitor_constraints=(),
        protocol=SimpleNamespace(constraints=(global_root, p0, p1),
                                 constraint_slots=(-1, 0, 1) if metadata else ()),
        sample_frames=((s0,), (s1,)) if metadata else (),
        source=SimpleNamespace(memory=SimpleNamespace(arrays={'ram': 1} if memory else {}),
            slots=tuple(SimpleNamespace(phases=()) for _ in range(2))))
    pair = SourceOrderComparison(side, side, {})
    prefixes = _ProofPrefixes(pair, roots)
    if metadata and not memory:
        assert prefixes('confluence:startup') == (global_root, external)
        assert prefixes('confluence:slot[0]') == (global_root, p0, s0, p0, external)
    else:
        assert prefixes('confluence:slot[0]') == roots
    assert prefixes('confluence:slot[1]') == roots
    assert prefixes('confluence:protocol') == roots
    assert prefixes('confluence:product') == roots
    assert pair.constraints == (*roots, *roots)


@pytest.mark.parametrize('bad', [False, True])
def test_future_protocol_sat_is_rechecked_on_the_complete_original_relation(bad):
    x = z3.Bool('future_protocol_x')
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    restriction = true if bad else z3.Not(x)
    side = SimpleNamespace(alive=true, unfinished=false, domain_faults={},
        constraints=(restriction,), monitor_frame_sizes=(0, 0), monitor_constraints=(),
        protocol=SimpleNamespace(constraints=(restriction,), constraint_slots=(1,)),
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=()),
            slots=tuple(SimpleNamespace(phases=()) for _ in range(2))))
    pair = SourceOrderComparison(side, side, {'slot[0].phase[0].value': x})
    calls = []

    def solve(label, roots):
        answer = z3_check_finite(roots, 1000)
        calls.append((label, roots, answer.answer))
        return answer

    audit = pair.audit(solve, prefix_proofs=True)
    assert (audit.status is AuditCheckStatus.REFUSED) is bad
    assert any(label == 'confluence:slot[0]:prefix' and answer is SolverAnswer.SAT
               for label, _, answer in calls)
    full = next(roots for label, roots, _ in calls if label == 'confluence:slot[0]')
    assert all(a.eq(b) for a, b in zip(full, pair.constraints, strict=False))


def _pair(predicate, suffix):
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    side = SimpleNamespace(
        alive=true, unfinished=false, domain_faults={}, constraints=(true, suffix),
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=(
            SimpleNamespace(phases=(SimpleNamespace(constraints=(suffix,)),)),)))
    return SourceOrderComparison(side, side, {'startup.x': predicate,
                                              'future-unknown-label': false})


@pytest.mark.parametrize('real_counterexample', [False, True])
def test_prefix_sat_must_be_rechecked_with_every_original_constraint(real_counterexample):
    x = z3.Bool('prefix_x')
    pair = _pair(x, z3.BoolVal(True) if real_counterexample else z3.Not(x))
    calls = []

    def solve(phase, assertions):
        calls.append((phase, tuple(assertions)))
        return z3_check_finite(assertions, 10_000)

    audit = pair.audit(solve, prefix_proofs=True)
    assert [phase for phase, _ in calls[:6]] == [
        'feasibility', 'completeness', 'definedness', 'memory',
        'confluence:startup:prefix', 'confluence:startup']
    complete = calls[5][1]
    assert all(a is b for a, b in zip(complete, pair.constraints))
    if real_counterexample:
        assert audit.status is AuditCheckStatus.REFUSED
        assert audit.as_dict()['refusal']['kind'] == 'rtl-source-order-sensitive'
        assert 'first differing observation: startup.x' in audit.why
        assert 'not an artifact failure' in audit.why
        assert len(calls) == 6
    else:
        assert audit.status is AuditCheckStatus.AGREED
        assert calls[-1][0] == 'confluence:product'


@pytest.mark.parametrize('answer', [SolverAnswer.UNSAT, SolverAnswer.UNKNOWN])
def test_only_proved_prefix_equalities_can_become_later_lemmas(answer):
    pair = _pair(z3.BoolVal(False), z3.Bool('later'))
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        if phase.endswith(':prefix') and answer is SolverAnswer.UNKNOWN:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='owner exhausted')
        return z3_check_finite(assertions, 10_000)

    audit = pair.audit(solve, prefix_proofs=True)
    assert 'confluence:startup' not in calls
    if answer is SolverAnswer.UNKNOWN:
        assert audit.status is AuditCheckStatus.REFUSED
        assert audit.as_dict()['refusal']['kind'] == 'rtl-source-domain-check-unresolved'
        assert calls[-1] == 'confluence:startup:prefix'
    else:
        assert audit.status is AuditCheckStatus.AGREED
        assert calls[-1] == 'confluence:product'


@pytest.mark.parametrize('bad', [False, True])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('phase', ['confluence:protocol', 'confluence:product'])
def test_product_cut_uses_proved_premises_and_rechecks_spurious_sat(bad, private, phase):
    ctx = z3.Context() if private else None
    x, y = z3.Ints('product_cut_x product_cut_y', ctx=ctx)
    a, b = x * x + 1, y * y + 1
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)

    def side(term):
        interval = SimpleNamespace(state={'q': term}, alive=true, error=false, quiescent=true, constraints=())
        return SimpleNamespace(alive=true, unfinished=false, domain_faults={}, startup=None,
            constraints=(x == y, true), monitor_constraints=(true,), monitor_frame_sizes=(1,),
            source=SimpleNamespace(memory=SimpleNamespace(conflicts=()),
                slots=(SimpleNamespace(phases=(interval,)),)))

    predicate = a + 1 != b + 1 + int(bad)
    name = 'protocol.accepted[0].in' if phase == 'confluence:protocol' else 'terminal_hits.value'
    pair = SourceOrderComparison(side(a), side(b), {name: predicate})
    prefix = pair._prefix_constraints(phase, pair.constraints)
    query = pair._prefix_query(phase, prefix, (a == b,), predicate)
    cuts = pair._monitor_step_cuts[phase]
    recovered = z3.substitute(query[0], *((new, old) for old, new in cuts.replacements))
    expected = (*prefix, a == b, predicate) if phase == 'confluence:protocol' else (a == b, predicate)
    assert z3.is_true(z3.simplify(recovered == z3.And(*expected)))
    assert z3_check_finite(query, 1000, _owned_context=ctx).answer is (
        SolverAnswer.SAT if bad else SolverAnswer.UNSAT)
    calls = []

    def solve(label, roots):
        result = z3_check_finite(roots, 1000, _owned_context=ctx)
        calls.append((label, roots, result.answer))
        return result

    audit = pair.audit(solve, prefix_proofs=True)
    assert (audit.status is AuditCheckStatus.AGREED) is not bad
    assert calls[-2][0::2] == (phase + ':prefix', SolverAnswer.SAT)
    assert calls[-1][0::2] == (phase, SolverAnswer.SAT if bad else SolverAnswer.UNSAT)
    assert all(a.eq(b) for a, b in zip(calls[-1][1], pair.constraints))


def test_prefix_filter_never_changes_non_source_or_any_current_slot_phase_constraints():
    a, b, c, domain = z3.Bools('a b c domain')
    side = SimpleNamespace(source=SimpleNamespace(slots=(SimpleNamespace(phases=(
        SimpleNamespace(constraints=(a,)), SimpleNamespace(constraints=(b,))),),
        SimpleNamespace(phases=(SimpleNamespace(constraints=(c,)),)))))
    pair = SourceOrderComparison(side, side, {})
    roots = (a, b, c, domain, a)
    prefix = pair._prefix_constraints('confluence:slot[0]', roots)
    assert prefix == (a, b, domain, a)
    assert pair._prefix_constraints('confluence:product', roots) is roots
    assert pair._prefix_constraints('confluence:future-label', roots) is roots


def test_monitor_aliases_remain_in_full_recheck_and_product_obligation():
    x, alias = z3.Ints('original_value monitor_alias')
    definition = alias == x + 1
    pair = _pair(alias != x + 1, z3.BoolVal(True))
    pair.left.constraints = (definition,)
    pair.left.monitor_constraints = (definition,)
    calls = []

    def solve(label, roots):
        calls.append((label, roots))
        return z3_check_finite(roots, 10_000)

    assert pair.audit(solve, prefix_proofs=True).status is AuditCheckStatus.AGREED
    relaxed = next(roots for label, roots in calls if label == 'confluence:startup:prefix')
    assert not any(term.eq(definition) for term in relaxed)
    for label in ('confluence:startup', 'confluence:product'):
        full = next(roots for phase, roots in calls if phase == label)
        assert any(term.eq(definition) for term in full)


@pytest.mark.parametrize('phase', ['completeness', 'memory'])
@pytest.mark.parametrize('bad', [False, True])
def test_source_domain_prefix_cannot_accept_or_refuse_from_a_relaxed_sat(phase, bad):
    alias = z3.Bool('domain_alias')
    definition = alias == z3.BoolVal(bad)
    pair = _pair(z3.BoolVal(False), z3.BoolVal(True))
    pair.left.constraints = (definition,)
    pair.left.monitor_constraints = (definition,)
    if phase == 'completeness':
        pair.left.unfinished = alias
    else:
        pair.left.source.memory.conflicts = (alias,)
    calls = []

    def solve(label, roots):
        result = z3_check_finite(roots, 10_000)
        calls.append((label, result.answer))
        return result

    result = pair.audit(solve, prefix_proofs=True)
    assert (phase + ':prefix', SolverAnswer.SAT) in calls
    assert (phase, SolverAnswer.SAT if bad else SolverAnswer.UNSAT) in calls
    assert (result.status is AuditCheckStatus.AGREED) is not bad


def _framed_pair(*, bad=False, private=False):
    ctx = z3.Context() if private else None
    x, l1, l2, r1, r2 = z3.Ints('frame_x frame_l1 frame_l2 frame_r1 frame_r2', ctx=ctx)
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)

    def side(first, second, fault):
        definitions = (first == x + 1, second == first * 2 + int(fault))
        return SimpleNamespace(alive=true, unfinished=false, domain_faults={},
            constraints=definitions, monitor_constraints=definitions, monitor_frame_sizes=(1, 1),
            source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=()))

    # Deliberately shuffled: initial state and predecessors must be proved first.
    images = {'monitor.hits[1].bad': (l1 > 10) != (r1 > 10),
              'monitor.states[2].counter': l2 != r2,
              'monitor.states[0].counter': false,
              'protocol.accepted[0].in': false,
              'monitor.states[1].counter': l1 != r1,
              'future-unknown-image': l1 != x + 1}
    return SourceOrderComparison(side(l1, l2, False), side(r1, r2, bad), images)


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('bad', (False, True))
def test_monitor_boundary_cut_preserves_whole_query_and_never_grants_sat(private, bad):
    ctx = z3.Context() if private else None
    x, y, left, right = z3.Ints('cut_x cut_y cut_left cut_right', ctx=ctx)
    function = z3.Function('cut_datapath', x.sort(), x.sort())
    a, b = function(x) * 100 + 1, function(y) * 100 + 1
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)

    def side(source, state, delta):
        definition = state == source + delta
        interval = SimpleNamespace(state={'sample': source}, alive=true, error=false,
                                   quiescent=true, constraints=())
        return SimpleNamespace(alive=true, unfinished=false, domain_faults={}, startup=None,
            constraints=(x == y, definition), monitor_constraints=(definition,),
            monitor_frame_sizes=(1,), source=SimpleNamespace(memory=SimpleNamespace(conflicts=()),
                slots=(SimpleNamespace(phases=(interval,)),)))

    pair = SourceOrderComparison(side(a, left, 2), side(b, right, 3 if bad else 2),
        {'monitor.states[0].value': false, 'monitor.states[1].value': left != right})
    phase = 'confluence:monitor[1]'
    prefix = pair._prefix_constraints(phase, pair.constraints)
    query = pair._prefix_query(phase, prefix, (), left != right)
    assert len(query) == 1
    assert query[0].eq(pair._prefix_query(phase, prefix, (), left != right)[0])
    cuts = pair._monitor_step_cuts[0]
    assert query[0].eq(z3.substitute(z3.And(*prefix, left != right), *cuts.replacements))
    assert pair._prefix_query(phase, prefix, (), left != right)[0].eq(query[0])
    assert pair._monitor_step_cuts[0] is cuts
    recovered = z3.substitute(query[0], *((cut, term) for term, cut in cuts.replacements))
    solver = z3.Solver(ctx=ctx)
    solver.add(recovered != z3.And(*prefix, left != right))
    assert solver.check() == z3.unsat
    calls = []

    def solve(label, roots):
        result = z3_check_finite(roots, 10_000, _owned_context=ctx)
        calls.append((label, roots, result.answer))
        return result

    audit = pair.audit(solve, prefix_proofs=True)
    assert (audit.status is AuditCheckStatus.AGREED) is not bad
    assert calls[-2][0::2] == (phase + ':prefix', SolverAnswer.SAT)
    assert calls[-1][0::2] == (phase, SolverAnswer.SAT if bad else SolverAnswer.UNSAT)
    assert all(a.eq(b) for a, b in zip(calls[-1][1], pair.constraints))
    # With a proved boundary equality, congruence suffices; arithmetic is not
    # re-established from the complete source prefix for every monitor field.
    sufficient = pair._prefix_query(phase, prefix, (a == b,), left != right)
    assert z3_check_finite(sufficient, 10_000, _owned_context=ctx).answer is (
        SolverAnswer.SAT if bad else SolverAnswer.UNSAT)


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('bad', (False, True))
def test_framed_monitor_proofs_preserve_every_image_and_validate_the_full_fallback(private, bad):
    pair = _framed_pair(bad=bad, private=private)
    ctx = pair.left.alive.ctx
    groups = tuple(pair._confluence_obligations())
    assert [name for name, _ in groups] == [
        'confluence:protocol', 'confluence:monitor[0]', 'confluence:monitor[1]',
        'confluence:monitor[2]', 'confluence:product']
    solver = z3.Solver(ctx=ctx)
    solver.add(pair.different != z3.Or(*(term for _, term in groups)))
    assert solver.check() == z3.unsat
    calls = []

    def solve(phase, roots):
        result = z3_check_finite(roots, 10_000, _owned_context=ctx)
        calls.append((phase, roots, result.answer))
        return result

    result = pair.audit(solve, prefix_proofs=True)
    assert (result.status is AuditCheckStatus.AGREED) is not bad
    step = next(roots for name, roots, _ in calls if name == 'confluence:monitor[2]:prefix')
    for history in (pair.left, pair.right):
        first, second = history.monitor_constraints
        assert not any(term.eq(first) for term in step)
        assert any(term.eq(second) for term in step)
    if bad:
        assert result.kind == 'rtl-source-order-sensitive'
        assert calls[-1][0] == 'confluence:monitor[2]'
        assert 'monitor.states[2].counter' in result.why
    else:
        # The tail's arbitrary image is not implied by predecessor agreement
        # alone. A relaxed SAT must retain the original defining equations.
        assert calls[-2][0::2] == ('confluence:product:prefix', SolverAnswer.SAT)
        assert calls[-1][0::2] == ('confluence:product', SolverAnswer.UNSAT)
    for name, roots, _ in calls:
        if name.startswith('confluence:') and not name.endswith(':prefix'):
            assert all(a.eq(b) for a, b in zip(roots, pair.constraints))


def test_monitor_step_needs_the_proved_predecessor_and_unknown_cannot_be_skipped():
    pair = _framed_pair()
    predicates = dict(pair._confluence_obligations())
    roots = pair._prefix_constraints('confluence:monitor[2]', pair.constraints)
    assert z3_check_finite((*roots, predicates['confluence:monitor[2]']), 10_000).answer is SolverAnswer.SAT
    assert z3_check_finite((*roots, z3.Not(predicates['confluence:monitor[1]']),
                            predicates['confluence:monitor[2]']), 10_000).answer is SolverAnswer.UNSAT
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        if phase == 'confluence:monitor[1]:prefix':
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='predecessor not proved')
        return z3_check_finite(assertions, 10_000)

    result = pair.audit(solve, prefix_proofs=True)
    assert result.kind == 'rtl-source-domain-check-unresolved'
    assert calls[-1] == 'confluence:monitor[1]:prefix'
    assert not any(name.startswith('confluence:monitor[2]') for name in calls)


def test_local_monitor_premises_are_consequences_of_only_consumed_groups():
    pair = _framed_pair()
    a, b, future, entry = z3.Bools('local_alive_a local_alive_b unproved_image local_entry')
    pair.differences.update({'slot[0].phase[0].alive': a != b,
                             'startup.entered': entry,
                             'slot[1].phase[0].alive': future,
                             'protocol.emitted[1].out': a != b})
    groups = dict(pair._confluence_obligations())
    proved = {name: z3.Not(groups[name]) for name in (
        'confluence:startup', 'confluence:slot[0]', 'confluence:protocol', 'confluence:monitor[1]')}
    local = pair._prefix_premises('confluence:monitor[2]', proved, ())
    assert any(term.eq(z3.Not(a != b)) for term in local)
    assert not any(term.eq(z3.Not(entry)) for term in local)
    assert not any(term.eq(z3.Not(future)) for term in local)
    solver = z3.Solver()
    solver.add(*proved.values(), z3.Not(z3.And(*local)))
    assert solver.check() == z3.unsat  # No new assumption entered the proof.
    assert pair._prefix_premises('confluence:monitor[2]', {}, ()) == ()


@pytest.mark.parametrize('private', [False, True])
def test_monitor_image_index_matches_original_selection_and_current_owners(private):
    pair = _framed_pair(private=private)
    ctx = pair.left.alive.ctx
    # Interleave rows and include similar names that the original prefixes do
    # not select. Prefix matching allows arbitrary suffixes after a closed row.
    names = [f'{family}[{row}]{suffix}'
             for suffix in ('.value', '[2]', 'extra', '')
             for row in ('0', '1', '10', '01', '-1', '-0')
             for family in ('slot', 'monitor.states', 'monitor.hits',
                            'protocol.accepted', 'protocol.emitted', 'protocol.adapters',
                            'protocol.pointers', 'protocol.released', 'protocol.framing',
                            'protocol.hits', 'unrelated')]
    pair.differences.clear()
    pair.differences.update((name, z3.Bool(f'image_{i}', ctx=ctx)) for i, name in enumerate(names))
    for index in (10, -1, 0, 1):
        prefixes = {f'slot[{index}].': f'confluence:slot[{index}]',
                    f'monitor.states[{index}]': f'confluence:monitor[{index}]'}
        prefixes.update((f'protocol.{name}[{index}]', 'confluence:protocol') for name in
                        ('accepted', 'emitted', 'adapters', 'pointers', 'released', 'framing'))
        for owners in (tuple(prefixes.values()), ('confluence:protocol',), (),
                       (f'confluence:monitor[{index}]', f'confluence:slot[{index}]')):
            proved = dict.fromkeys(owners, z3.BoolVal(True, ctx=ctx))
            expected = tuple(z3.Not(term) for name, term in pair.differences.items()
                             if next((owner for prefix, owner in prefixes.items()
                                      if name.startswith(prefix)), None) in proved)
            actual = pair._prefix_premises(f'confluence:monitor[{index + 1}]', proved, ())
            assert len(actual) == len(expected)
            assert all(a.eq(b) for a, b in zip(actual, expected, strict=True))
    # Values, renamed keys (even at unchanged size), insertion order and the
    # current set of consumed lemmas must not be frozen into cached evidence.
    pair.differences.clear()
    pair.differences['protocol.accepted[0].in'] = z3.Bool('new_image', ctx=ctx)
    owner = {'confluence:protocol': z3.BoolVal(True, ctx=ctx)}
    first = pair._prefix_premises('confluence:monitor[1]', owner, ())
    pair.differences['protocol.accepted[0].in'] = z3.Bool('replacement', ctx=ctx)
    second = pair._prefix_premises('confluence:monitor[1]', owner, ())
    assert len(first) == len(second) == 1 and not first[0].eq(second[0])
    pair.differences['protocol.accepted[1].in'] = pair.differences.pop('protocol.accepted[0].in')
    assert pair._prefix_premises('confluence:monitor[1]', owner, ()) == ()
    assert len(pair._prefix_premises('confluence:monitor[2]', owner, ())) == 1
    assert pair._prefix_premises('confluence:monitor[2]', {}, ()) == ()


def test_monitor_image_index_does_not_reparse_history_after_first_call(monkeypatch):
    import behave.sim.rtl.source_confluence as module

    pair = _framed_pair()
    expected = pair._monitor_image_names(0)

    def repeated_scan(*_args, **_kwargs):
        raise AssertionError('unchanged images must use the index')

    monkeypatch.setattr(module.re, 'match', repeated_scan)
    for index in (0, 1, 10, 0):
        selected = pair._monitor_image_names(index)
        if index == 0:
            assert selected is expected


def test_monitor_image_index_cancellation_leaves_no_partial_index(monkeypatch):
    from behave.sim import analysis_budget

    pair = _framed_pair()
    calls = []

    def cancelled():
        calls.append(None)
        if len(calls) == 2:
            raise TimeoutError('test deadline')

    monkeypatch.setattr(analysis_budget, 'check_analysis', cancelled)
    with pytest.raises(TimeoutError, match='test deadline'):
        pair._monitor_image_names(0)
    assert pair._monitor_images is None
    monkeypatch.setattr(analysis_budget, 'check_analysis', lambda: None)
    pair._monitor_image_names(0)
    assert pair._monitor_images is not None


@pytest.mark.parametrize('private', [False, True])
def test_monitor_premises_do_not_accumulate_old_or_future_source_history(private):
    ctx = z3.Context() if private else None
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)
    parts, images, lemmas = [], {}, {}
    for index in range(100):
        entered, alive, work = z3.Bools(f'monitor_entry{index} monitor_alive{index} monitor_work{index}', ctx=ctx)
        parts.append(SimpleNamespace(unfinished=work, domain_faults={}))
        images.update({f'slot[{index}].phase[0].entered': entered,
                       f'slot[{index}].phase[0].alive': alive})
        lemmas[f'confluence:slot[{index}]'] = z3.Not(z3.Or(entered, alive))
    images['monitor.states[49].counter'] = false
    lemmas['confluence:monitor[49]'] = true
    side = SimpleNamespace(monitor_frame_sizes=(1,) * 100, startup=None,
        source=SimpleNamespace(slots=tuple(SimpleNamespace(phases=(part,)) for part in parts)))
    pair = SourceOrderComparison(side, side, images)
    facts = (z3.Not(z3.Or(*(part.unfinished for part in parts))),)
    local = pair._prefix_premises('confluence:monitor[50]', lemmas, facts)
    assert len(local) == 5  # Current entry/alive, prior monitor, two local absence facts.
    solver = z3.Solver(ctx=ctx)
    solver.add(*lemmas.values(), *facts, z3.Not(z3.And(*local)))
    assert solver.check() == z3.unsat
    from z3.z3util import get_vars
    names = {str(term) for term in get_vars(z3.And(*local))}
    assert names == {'monitor_entry49', 'monitor_alive49', 'monitor_work48', 'monitor_work49'}


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('image', ['adapters', 'pointers', 'released', 'framing'])
def test_monitor_drives_use_only_proved_current_protocol_boundary(private, image):
    ctx = z3.Context() if private else None
    left, right, out_left, out_right = z3.Ints('drive_left drive_right drive_out_left drive_out_right', ctx=ctx)
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)
    a, b = left * left + 17, right * right + 17
    def side(entry, output):
        part = SimpleNamespace(state={}, alive=true, error=false, quiescent=true,
                               entered=true, constraints=(), unfinished=false, domain_faults={})
        return SimpleNamespace(alive=true, monitor_frame_sizes=(1,), startup=None,
            protocol=SimpleNamespace(**{image: ({'value': entry},)}),
            source=SimpleNamespace(slots=(SimpleNamespace(phases=(part,)),)),
            constraints=(output == entry + 1,), monitor_constraints=(output == entry + 1,))
    mismatch = a != b
    pair = SourceOrderComparison(side(a, out_left), side(b, out_right),
        {f'protocol.{image}[0].value': mismatch, f'protocol.{image}[1].value': left != right})
    phase = 'confluence:monitor[1]'
    prefix = pair._prefix_constraints(phase, pair.constraints)
    # No protocol proof means no assumption of equal adapter entries.
    assert pair._prefix_premises(phase, {}, ()) == ()
    unknown = pair._prefix_query(phase, prefix, (), out_left != out_right)
    assert z3_check_finite(unknown, 1000, _owned_context=ctx).answer is SolverAnswer.SAT
    lemma = z3.Not(z3.Or(*pair.differences.values()))
    premises = pair._prefix_premises(phase, {'confluence:protocol': lemma}, ())
    assert premises == (z3.Not(mismatch),)
    query = pair._prefix_query(phase, prefix, premises, out_left != out_right)
    assert z3_check_finite(query, 1000, _owned_context=ctx).answer is SolverAnswer.UNSAT
    from z3.z3util import get_vars
    variables = get_vars(query[0])
    assert not any(term.eq(left) or term.eq(right) for term in variables)
    cuts = pair._monitor_step_cuts[0].replacements
    recovered = z3.substitute(query[0], *((new, old) for old, new in cuts))
    assert z3.is_true(z3.simplify(recovered == z3.And(*prefix, *premises, out_left != out_right)))


def test_local_monitor_prefix_retains_domain_and_never_drops_full_fallback():
    pair = _framed_pair()
    history, domain = z3.Bools('historical_source_constraint explicit_domain')
    pair.left.constraints = (*pair.left.constraints, history)
    complete = (*pair.constraints, domain)
    prefix = pair._prefix_constraints('confluence:monitor[2]', complete)
    assert any(term.eq(domain) for term in prefix)
    assert not any(term.eq(history) for term in prefix)
    assert all(any(term.eq(original) for original in complete) for term in prefix)
    plans = dict(pair.order_queries(domain=(domain,), prefix_proofs=True))
    full = plans['confluence:monitor[2]'][-1][1]
    assert all(a.eq(b) for a, b in zip(full, complete))


def test_protocol_prefix_keeps_sample_links_and_external_domain_not_source_steps():
    pair = _framed_pair()
    startup, step, link, domain = z3.Bools('protocol_startup protocol_step sample_link external_domain')
    for history in (pair.left, pair.right):
        history.startup = SimpleNamespace(constraints=(startup,))
        history.source.slots = (SimpleNamespace(phases=(SimpleNamespace(constraints=(step,)),)),)
        history.constraints = (*history.constraints, startup, step, link)
    full = (*pair.constraints, domain)
    local = pair._prefix_constraints('confluence:protocol', full)
    assert local == (link, link, domain)  # Keep order and duplicate links.
    planned = dict(pair.order_queries(domain=(domain,), prefix_proofs=True))
    original = planned['confluence:protocol'][-1][1]
    assert all(a.eq(b) for a, b in zip(original, full))


@pytest.mark.parametrize('bad', [False, True])
def test_protocol_source_drop_requires_full_recheck_for_any_local_sat(bad):
    pair = _framed_pair()
    mismatch = z3.Bool('protocol_source_mismatch')
    definition = mismatch == bad
    for history in (pair.left, pair.right):
        history.source.slots = (SimpleNamespace(phases=(
            SimpleNamespace(constraints=(definition,)),)),)
        history.constraints = (*history.constraints, definition)
    pair.differences['protocol.accepted[0].in'] = mismatch
    calls = []

    def solve(label, roots):
        result = z3_check_finite(roots, 10_000)
        calls.append((label, roots, result.answer))
        return result

    result = pair.audit(solve, prefix_proofs=True)
    assert (result.status is AuditCheckStatus.AGREED) is not bad
    local = next(row for row in calls if row[0] == 'confluence:protocol:prefix')
    full = next(row for row in calls if row[0] == 'confluence:protocol')
    assert local[2] is SolverAnswer.SAT
    assert not any(term.eq(definition) for term in local[1])
    assert full[2] is (SolverAnswer.SAT if bad else SolverAnswer.UNSAT)
    assert all(a.eq(b) for a, b in zip(full[1], pair.constraints))


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('invalid_frames', [False, True])
def test_prefix_index_preserves_root_order_duplicates_and_external_constraints(private, invalid_frames):
    pair = _framed_pair(private=private)
    ctx = pair.left.alive.ctx
    early, shared, late, domain = z3.Bools('early shared late external', ctx=ctx)
    for history in (pair.left, pair.right):
        history.source.slots = tuple(SimpleNamespace(phases=(SimpleNamespace(constraints=row),))
                                     for row in ((early, shared), (shared, late)))
        history.constraints = (early, shared, late, *history.monitor_constraints)
    if invalid_frames:
        pair.right.monitor_frame_sizes = (1, 7)
    first_left, last_left = pair.left.monitor_constraints
    first_right, last_right = pair.right.monitor_constraints
    # Select by identity, preserving order and duplicates, even when a monitor
    # definition is repeated outside the original history.
    roots = (last_right, domain, early, first_left, shared, last_left,
             domain, first_right, late, early, last_left)
    plan = _ProofPrefixes(pair, roots)
    expected = {
        'completeness': (domain, early, shared, domain, late, early),
        'memory': (domain, early, shared, domain, late, early),
        'confluence:startup': (domain, domain),
        'confluence:slot[0]': (domain, early, domain, early),
        'confluence:slot[1]': (domain, early, shared, domain, late, early),
        'confluence:monitor[0]': (domain, domain),
        'confluence:monitor[1]': roots if invalid_frames else (domain, first_left, domain, first_right),
        'confluence:monitor[2]': roots if invalid_frames else (last_right, domain, last_left, domain, last_left),
        'confluence:monitor[3]': roots,
        'confluence:protocol': (domain, domain),
        'confluence:product': (domain, early, shared, domain, late, early),
        'confluence:future-label': roots,
    }
    for phase, wanted in expected.items():
        assert len(plan(phase)) == len(wanted)
        assert all(a.eq(b) for a, b in zip(plan(phase), wanted, strict=True))


def test_indexed_prefix_reads_no_historical_ast_ids_again(monkeypatch):
    pair = _framed_pair()
    roots = pair.constraints
    plan = _ProofPrefixes(pair, roots)

    def repeated_scan(_self):
        raise AssertionError('prefix must use its immutable representation index')

    monkeypatch.setattr(z3.AstRef, 'get_id', repeated_scan)
    for _ in range(3):
        assert len(plan('confluence:monitor[2]')) == 2
        assert len(plan('confluence:slot[0]')) == 0
        assert plan('future-label') is roots


@pytest.mark.parametrize('different_phase', [None, 0, 1, 2])
def test_slot_proof_keeps_each_internal_phase_and_original_counterexample_name(different_phase):
    true, false = z3.BoolVal(True), z3.BoolVal(False)
    side = SimpleNamespace(alive=true, unfinished=false, domain_faults={}, constraints=(true,),
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=(
            SimpleNamespace(phases=tuple(SimpleNamespace(constraints=()) for _ in range(3))),)))
    images = {'startup.alive': false, **{
        f'slot[0].phase[{i}].hits.transient': z3.BoolVal(i == different_phase) for i in range(3)},
        'protocol.accepted': false}
    pair = SourceOrderComparison(side, side, images)
    groups = dict(pair._confluence_obligations())
    assert tuple(groups) == ('confluence:startup', 'confluence:slot[0]',
                            'confluence:product')
    oracle = z3.Solver()
    oracle.add(z3.Xor(pair.different, z3.Or(*groups.values())))
    assert oracle.check() == z3.unsat  # Exactly the original disagreement image.
    calls = []

    def solve(phase, roots):
        calls.append(phase)
        return z3_check_finite(roots, 10_000)

    result = pair.audit(solve, prefix_proofs=True)
    assert calls.count('confluence:slot[0]') == 1
    if different_phase is None:
        assert result.status is AuditCheckStatus.AGREED
    else:
        assert result.kind == 'rtl-source-order-sensitive'
        assert f'slot[0].phase[{different_phase}].hits.transient' in result.why
        assert not any(label.startswith('confluence:product') for label in calls)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('bad_field', [None, 'protocol', 'monitor', 'memory', 'unknown'])
def test_product_obligation_keeps_every_cell_and_full_constraints(private, bad_field):
    ctx = z3.Context() if private else None
    x = z3.Bool('product_member', ctx=ctx)
    true, false = z3.BoolVal(True, ctx=ctx), z3.BoolVal(False, ctx=ctx)
    side = SimpleNamespace(alive=true, unfinished=false, domain_faults={}, constraints=(x,),
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=()))
    images = {'startup.alive': false}
    for slot in range(3):
        images[f'protocol.accepted[{slot}].in'] = x if bad_field == 'protocol' and slot == 2 else false
        for cell in range(2):
            images[f'monitor.states[{slot}].channel.pending[{cell}]'] = (
                x if bad_field == 'monitor' and (slot, cell) == (2, 1) else false)
            images[f'memory.arrays.ram[{slot}][{cell}]'] = (
                x if bad_field == 'memory' and (slot, cell) == (2, 1) else false)
    images['future-unrecognized-image'] = x if bad_field == 'unknown' else false
    pair = SourceOrderComparison(side, side, images)
    groups = dict(pair._confluence_obligations())
    assert tuple(groups) == ('confluence:startup', 'confluence:product')
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Xor(pair.different, z3.Or(*groups.values())))
    assert solver.check() == z3.unsat
    consumed = []

    def solve(label, roots):
        if label.startswith('confluence:product'):
            assert not label.endswith(':prefix')
            assert len(roots) >= len(pair.constraints)
            assert all(a.eq(b) for a, b in zip(roots, pair.constraints, strict=False))
            consumed.append(label)
        return z3_check_finite(roots, 10_000, _owned_context=ctx or x.ctx)

    result = pair.audit(solve, prefix_proofs=True)
    assert (result.status is AuditCheckStatus.AGREED) is (bad_field is None)
    if bad_field is None:
        assert tuple(consumed) == tuple(groups)[1:]
    else:
        assert result.kind == 'rtl-source-order-sensitive'


@pytest.mark.parametrize('variable_queue', [False, True])
@pytest.mark.eda('slang')
@pytest.mark.solver
def test_prefix_proofs_agree_with_the_complete_real_source_product(tmp_path, variable_queue):
    from .test_rtl_source_confluence import _comparison
    from .test_rtl_source_history import HISTORY_SOURCE

    pair, *_ = _comparison(tmp_path, HISTORY_SOURCE, 'saved==1', private=True,
                           full_catalogue=True, symbolic_initial=True,
                           variable_queue=variable_queue)
    def solve(_phase, terms):
        return z3_check_finite(terms, 30_000, _owned_context=pair.left.alive.ctx)
    assert pair.audit(solve).status is AuditCheckStatus.AGREED
    assert pair.audit(solve, prefix_proofs=True).status is AuditCheckStatus.AGREED
