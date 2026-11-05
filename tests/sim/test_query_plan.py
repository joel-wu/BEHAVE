"""Compact plans preserve requests and bindings without eager expansion."""
import copy
import json

import pytest

from behave.sim.generation import ProbeRequest, ProbeSelection, StimulusField
from behave.sim.query_plan import QueryPlan, QueryTemplate
from behave.sim.search_parallel import QueryWork


DIGEST = 'a' * 64


@pytest.mark.parametrize('length', (0, 1, 2, 5, 20, 100))
def test_trigger_windows_keep_native_suffixes_and_full_epoch_without_quadratic_growth(length):
    from behave.sim.query_plan import ending_windows

    items = tuple(range(length))
    windows = tuple(ending_windows(items))
    if length < 2:
        assert not windows
        return
    assert windows[0] == items[-2:]
    assert windows[-1] == items
    assert all(window == items[-len(window):] for window in windows)
    assert len({len(window) for window in windows}) == len(windows)
    assert sum(map(len, windows)) < 3 * length


def plan(length=4):
    fields = tuple(StimulusField('input', i, 0, i + 1) for i in range(length))
    templates = [QueryTemplate(('g1', 'g2'), f'prefix:{n}', fields[:n], n,
                               catalogue_digest=DIGEST) for n in range(1, length + 1)]
    return QueryPlan(templates, DIGEST, chains=(tuple(range(1, length + 1)),))


def test_plan_round_trip_preserves_exact_expanded_request_and_chain_images():
    original = plan()
    restored = QueryPlan.from_dict(json.loads(json.dumps(original.as_dict())))
    assert [r.as_dict() for r in restored] == [r.as_dict() for r in original]
    assert restored.chains == original.chains
    selection = ProbeSelection(restored, DIGEST)
    assert selection.query_chains == (tuple(r.identity for r in original),)
    assert selection.as_dict()['requests'] == original.as_dict()


def test_context_explorations_survive_transport_and_filtering_without_proof_equivalence():
    original = plan()
    contextual = QueryPlan(original.templates, DIGEST, explorations=(2, 4))
    restored = QueryPlan.from_dict(json.loads(json.dumps(contextual.as_dict())))
    assert restored.explorations == (2, 4)
    assert restored.retaining(restored.templates[1:3]).explorations == (2,)
    assert [r.as_dict() for r in restored] == [r.as_dict() for r in original]
    for ranks in ((True,), (999,), (2, 2), (4, 2)):
        with pytest.raises(ValueError):
            QueryPlan(original.templates, DIGEST, explorations=ranks)
    with pytest.raises(ValueError, match='UNSAT'):
        QueryPlan(original.templates, DIGEST, explorations=(2,), equivalents=((1, 2),))


def test_context_questions_are_not_starved_by_unhit_goal_priority(monkeypatch):
    fields = (StimulusField('input', 0, 0, 1),)
    templates = tuple(QueryTemplate(('covered' if i >= 2 else 'unhit',), f'step:{i}',
        fields, i, catalogue_digest=DIGEST) for i in range(4))
    selected = QueryPlan(templates, DIGEST, explorations=(2, 3))
    request, expanded = QueryTemplate.request, []

    def tracked(self):
        expanded.append(self.rank)
        return request(self)

    monkeypatch.setattr(QueryTemplate, 'request', tracked)
    work = QueryWork(selected, priority=lambda targets: float('inf') if targets == ('covered',) else 0)
    assert not expanded
    assert [work.popleft()[1].rank for _ in range(4)] == [0, 2, 1, 3]


def test_plan_selection_serialization_and_worklist_do_not_expand_questions(monkeypatch):
    original = plan(16)
    request = QueryTemplate.request
    expanded = []

    def tracked(self):
        expanded.append(self.rank)
        return request(self)

    monkeypatch.setattr(QueryTemplate, 'request', tracked)
    selection = ProbeSelection(original, DIGEST)
    selection.as_dict()
    work = QueryWork(original)
    assert not expanded
    index, first = work.popleft()
    assert expanded == [1]
    assert work.continues(index) and work.shares(index) == 1
    narrowed = first.with_targets(('g1',))
    work.appendleft((index, narrowed))
    assert work.popleft() == (index, narrowed)
    assert expanded == [1]
    assert work.popleft()[1].rank == 2
    assert expanded == [1, 2]


def test_context_questions_interleave_before_a_long_widening_chain_finishes():
    original = plan(16)
    contexts = tuple(QueryTemplate(('covered',), f'context:{i}',
        original.templates[0].mutable_fields, i, catalogue_digest=DIGEST) for i in (17, 18))
    selected = QueryPlan((*original.templates, *contexts), DIGEST,
                         chains=original.chains, explorations=(17, 18))
    work = QueryWork(selected)
    index, first = work.popleft()
    assert first.rank == 1
    # An unfinished construction still resumes before any other question.
    work.appendleft((index, first))
    assert work.popleft()[1].rank == 1
    assert [work.popleft()[1].rank for _ in range(len(work))] == [17, 2, 18, *range(3, 17)]


def test_static_goal_priority_does_not_reorder_native_contexts():
    fields = (StimulusField('input', 0, 0, 1),)
    rows = tuple(QueryTemplate((goal,), f'step:{i}', fields, i, catalogue_digest=DIGEST)
                 for i, goal in enumerate(('ordinary', 'covered-deep', 'unhit-shallow')))
    work = QueryWork(QueryPlan(rows, DIGEST, explorations=(1, 2)),
                     priority=lambda targets: 99 if targets == ('covered-deep',) else 0)
    assert [work.popleft()[1].rank for _ in range(len(work))] == [0, 1, 2]


@pytest.mark.parametrize('exploratory_setup', (False, True))
def test_prepared_child_explores_its_context_family_before_more_setup(exploratory_setup):
    fields = (StimulusField('input', 0, 0, 1),)
    rows = tuple(QueryTemplate((goal,), f'step:{i}', fields, i, catalogue_digest=DIGEST)
                 for i, goal in enumerate(('prepared', 'other', 'prepared', 'prepared')))
    plan = QueryPlan(rows, DIGEST, explorations=((0, 1, 2, 3) if exploratory_setup else (1, 2, 3)),
                     preparations=(0,) if exploratory_setup else ())
    baseline = QueryWork(plan)
    assert [baseline.popleft()[1].rank for _ in range(len(baseline))] == [0, 1, 2, 3]
    child = QueryWork(plan, continuation_targets=('prepared',))
    assert [child.popleft()[1].rank for _ in range(len(child))] == (
        [2, 3, 0, 1] if exploratory_setup else [2, 0, 3, 1])


def test_preparation_roles_round_trip_without_changing_questions_or_evidence():
    original = plan()
    selected = QueryPlan(original.templates, DIGEST, explorations=(1, 2, 3), preparations=(1, 3))
    restored = QueryPlan.from_dict(json.loads(json.dumps(selected.as_dict())))
    assert restored.as_dict()['version'] == 'query-plan/4'
    assert restored.preparations == (1, 3)
    assert restored.retaining(restored.templates[1:]).preparations == (3,)
    assert [r.as_dict() for r in restored] == [r.as_dict() for r in original]
    for ranks in ((True,), (999,), (4,), (1, 1), (3, 1)):
        with pytest.raises(ValueError, match='preparations'):
            QueryPlan(original.templates, DIGEST, explorations=(1, 2, 3), preparations=ranks)


def test_native_context_hints_round_trip_and_prioritize_untried_states_only():
    original = plan()
    hints = ((2, 'b' * 64), (4, 'c' * 64))
    selected = QueryPlan(original.templates, DIGEST, explorations=(2, 4), contexts=hints)
    restored = QueryPlan.from_dict(json.loads(json.dumps(selected.as_dict())))
    assert restored.as_dict()['version'] == 'query-plan/3'
    assert tuple(restored.contexts.items()) == hints
    assert dict(restored.retaining(restored.templates[2:]).contexts) == {4: 'c' * 64}
    visits = {'b' * 64: 1}
    work = QueryWork(restored, continuation_targets=('g1',), context_visits=visits)
    assert [work.popleft()[1].rank for _ in range(len(work))] == [4, 1, 2, 3]
    assert visits == {'b' * 64: 1}  # Selection is not a completed attempt.
    assert [r.as_dict() for r in restored] == [r.as_dict() for r in original]
    for bad in (((True, 'b' * 64),), ((1, 'b' * 64),), ((2, 'bad'),),
                ((2, 'b' * 64), (2, 'c' * 64)), ((4, 'c' * 64), (2, 'b' * 64))):
        with pytest.raises(ValueError, match='context identities'):
            QueryPlan(original.templates, DIGEST, explorations=(2, 4), contexts=bad)


def test_plan_rank_lookup_preserves_all_selected_domain_coordinates(monkeypatch):
    original = plan(10)
    candidate = original[7].with_targets(('g1',))
    monkeypatch.setattr(QueryPlan, '__iter__', lambda self: pytest.fail('linear request scan'))
    assert original.selected(candidate).rank == 8
    for changed in (
        dict(rank=3), dict(occurrence='another'), dict(targets=('outside',)),
        dict(catalogue_digest='b' * 64),
        dict(mutable_fields=(StimulusField('input', 0, 0, 99),)),
    ):
        params = dict(targets=candidate.targets, occurrence=candidate.occurrence,
                      mutable_fields=candidate.mutable_fields, rank=candidate.rank,
                      catalogue_digest=candidate.catalogue_digest)
        params.update(changed)
        assert original.selected(ProbeRequest(**params)) is None


def test_family_priority_scores_shared_targets_once_without_expanding_requests(monkeypatch):
    fields = (StimulusField('input', 0, 0, 1),)
    templates = tuple(QueryTemplate((f'g{rank % 2}',), f'step:{rank}', fields, rank,
                                    catalogue_digest=DIGEST) for rank in range(80))
    original = QueryPlan(templates, DIGEST, chains=((0, 2), (1, 3)))
    request, expanded, scored = QueryTemplate.request, [], []

    def tracked(self):
        expanded.append(self.rank)
        return request(self)

    def priority(targets):
        scored.append(targets)
        return 1 if targets == ('g0',) else 0

    monkeypatch.setattr(QueryTemplate, 'request', tracked)
    work = QueryWork(original, priority=priority)
    assert scored == [('g0',), ('g1',)] and not expanded
    first, second = work.popleft(), work.popleft()
    assert (first[0], second[0]) == (1, 3)
    assert expanded == [1, 3]
    remaining = [work.popleft()[0] for _ in range(len(work))]
    assert sorted([first[0], second[0], *remaining]) == list(range(80))


@pytest.mark.parametrize('mutate', [
    lambda p: p['rows'][0].__setitem__(1, 999),
    lambda p: p['rows'][0].__setitem__(2, -1),
    lambda p: p['rows'][0].__setitem__(0, True),
    lambda p: p['rows'].append(p['rows'][0]),
    lambda p: p['targets'].append(['unused']),
    lambda p: p['targets'][0].reverse(),
    lambda p: p['chains'][0].append(999),
    lambda p: p['chains'].append(p['chains'][0]),
    lambda p: p['sites'][0][1][0].__setitem__(3, True),
    lambda p: p.__setitem__('catalogue_digest', 'invalid'),
    lambda p: p.__setitem__('unknown', []),
])
def test_compact_boundary_rejects_malformed_and_noncanonical_images(mutate):
    image = copy.deepcopy(plan().as_dict())
    mutate(image)
    with pytest.raises((ValueError, TypeError)):
        QueryPlan.from_dict(image)


def test_plan_filters_members_without_renumbering_or_changing_surviving_groups():
    original = plan()
    kept = original.retaining((original.templates[0], original.templates[2]))
    assert [item.rank for item in kept] == [1, 3]
    assert kept.chains == ((1, 3),)
    assert kept[1].as_dict() == original[2].as_dict()
    assert QueryPlan.from_dict(kept.as_dict()).as_dict() == kept.as_dict()


def test_shared_target_table_replaces_repeated_goal_serialization():
    targets = tuple(f'branch:behavior.py:{i}:T' for i in range(200))
    templates = tuple(QueryTemplate(targets, f'step:{i}',
                      (StimulusField('input', i, 0, i + 1),), i,
                      catalogue_digest=DIGEST) for i in range(100))
    original = QueryPlan(templates, DIGEST)
    assert len(original.as_dict()['targets']) == 1
    compact = len(json.dumps(original.as_dict()))
    expanded = len(json.dumps([r.as_dict() for r in original]))
    assert compact < expanded / 10
    with pytest.raises(AttributeError):
        original.templates = ()


def test_shared_field_domain_is_checked_once_per_factory_and_wire_site(monkeypatch):
    fields = tuple(StimulusField('input', i, 0, i + 1) for i in range(100))
    ordinary = tuple(QueryTemplate((f'g{group}',), 'prefix:100', fields, group,
                                  catalogue_digest=DIGEST) for group in range(20))
    image = QueryPlan(ordinary, DIGEST).as_dict()
    original, checked = StimulusField.from_dict, []

    def tracked(cls, value):
        checked.append(value)
        return original(value)

    monkeypatch.setattr(StimulusField, 'from_dict', classmethod(tracked))
    make = QueryTemplate.factory()
    shared = tuple(make((f'g{group}',), 'prefix:100', fields, group,
                        catalogue_digest=DIGEST) for group in range(20))
    assert len(checked) == len(fields)
    assert QueryPlan(shared, DIGEST).as_dict() == image
    assert all(t.mutable_fields is shared[0].mutable_fields for t in shared)
    checked.clear()
    restored = QueryPlan.from_dict(image)
    assert restored.as_dict() == image
    assert len(checked) == 2 * len(fields)  # Wire validation plus one shared domain.
    assert all(t.mutable_fields is restored.templates[0].mutable_fields
               for t in restored.templates)


@pytest.mark.parametrize('changes', [
    dict(targets=()), dict(targets=('same', 'same')), dict(targets=('',)),
    dict(occurrence=None), dict(occurrence=' '), dict(rank=True), dict(rank=-1),
    dict(catalogue_digest='bad'),
    dict(mutable_fields=(StimulusField('input', True, 0, 1),)),
])
def test_reusing_fields_does_not_bypass_other_validation(changes):
    make = QueryTemplate.factory()
    args = dict(targets=('g',), occurrence='here', rank=0,
                mutable_fields=(StimulusField('input', 0, 0, 1),), catalogue_digest=DIGEST)
    make(**args)
    with pytest.raises((ValueError, TypeError)):
        make(**{**args, **changes})


def test_retargeted_site_preserves_canonical_request_without_rechecking_fields(monkeypatch):
    template = plan().templates[-1]
    expected = QueryTemplate(template.targets, 'native-path:another', template.mutable_fields,
                             17, catalogue_digest=DIGEST).request().as_dict()
    with monkeypatch.context() as patch:
        patch.setattr(StimulusField, 'from_dict', lambda *_: pytest.fail('repeated field check'))
        other = template.at('native-path:another', 17)
    assert other.request().as_dict() == expected
    assert other.mutable_fields is template.mutable_fields
    for occurrence in (None, '', ' '):
        with pytest.raises(ValueError):
            template.at(occurrence, 17)


def test_selection_checks_shared_map_once_but_rechecks_for_a_different_seed(monkeypatch):
    from collections import defaultdict
    from behave.sim import api
    from behave.sim.generation import _selection

    fields = tuple(StimulusField('input', i, 0, i) for i in range(10))
    make = QueryTemplate.factory()
    templates = tuple(make((f'g{i}',), f'step:{i}', fields, i,
                           catalogue_digest=DIGEST) for i in range(20))
    selected = ProbeSelection(QueryPlan(templates, DIGEST), DIGEST)
    seed = api.Stimulus(queues={'input': [(0,)] * 10},
                        slots=[api.EventSlot(offers=('input',)) for _ in range(10)])
    coordinate, calls = StimulusField.coordinate.fget, []

    def counted(field):
        calls.append(field)
        return coordinate(field)

    monkeypatch.setattr(StimulusField, 'coordinate', property(counted))
    targets = {f'g{i}' for i in range(20)}
    actual = _selection(seed, selected, targets, defaultdict(int), DIGEST)
    assert actual.as_dict() == selected.requests.as_dict()
    assert len(calls) == 2 * len(fields)
    # Same immutable fields, different offered schedule: no cross-seed authority.
    other = api.Stimulus(queues={'input': [(0,)] * 10}, slots=[api.EventSlot()] * 10)
    telemetry = defaultdict(int, refusals={}, refusal_why={}, refusal_detail={})
    assert len(_selection(other, selected, targets, telemetry, DIGEST)) == 0
    assert telemetry['request_refusals'] == 20


def test_wire_target_groups_are_validated_by_table_not_per_row(monkeypatch):
    from behave.sim import generation_protocol

    make = QueryTemplate.factory()
    fields = (StimulusField('input', 0, 0, 1),)
    templates = tuple(make((f'g{i % 20}',), f'site:{i // 20}', fields, i,
                           catalogue_digest=DIGEST) for i in range(100))
    image = QueryPlan(templates, DIGEST).as_dict()
    original, calls = generation_protocol._targets, []
    def checked(values):
        calls.append(values)
        return original(values)
    monkeypatch.setattr(generation_protocol, '_targets', checked)
    restored = QueryPlan.from_dict(image)
    assert restored.as_dict() == image
    assert len(calls) == len(image['targets']) + len(image['sites'])
    assert all(t.mutable_fields is restored.templates[0].mutable_fields
               for t in restored.templates)


def test_wire_field_sharing_checks_types_and_keeps_schedule_coordinates():
    make = QueryTemplate.factory()
    templates = tuple(make(('g',), f'site:{i}', (StimulusField('input', 1, 0, i + 1),),
                           i, catalogue_digest=DIGEST) for i in range(2))
    image = QueryPlan(templates, DIGEST).as_dict()
    restored = QueryPlan.from_dict(image)
    assert restored.templates[0].mutable_fields[0] is not restored.templates[1].mutable_fields[0]
    image['sites'][1][1][0] = ['input', True, 0, 1]
    with pytest.raises(TypeError):
        QueryPlan.from_dict(image)
