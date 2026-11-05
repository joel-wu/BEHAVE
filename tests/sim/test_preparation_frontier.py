"""Preparation proposals reuse execution material without inventing hits."""

import pytest

from behave.sim import api
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.engines.behavior.preparation_frontier import PreparationFrontier, changed_cells, entry_facts
from behave.sim.engines.local_ir import _LocalQueryPreparation
from behave.sim.generation_protocol import Outcome, ProbeResult, Scope
from .test_behavior_prefix_generation import _selected, _trace


SOURCE = b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a == 0: self.mem[(b >> 3) & 3] = b & 7
        elif a == 3: self.reset()
        elif a == 2 and self.mem[1] == 3 and self.mem[2] == 5 and b == 7:
            y = 1
        return {"y": y}
'''


def _next_ordinary(work):
    """Context exploration may interleave one turn, not replace a query."""
    index, request = work.popleft()
    if request.rank in work.plan.explorations:
        index, request = work.popleft()
    assert request.rank not in work.plan.explorations
    return index, request


@pytest.mark.solver
def test_setup_remains_explorable_after_its_static_goal_is_hit(monkeypatch):
    source = b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a == 0: self.mem[(b >> 3) & 3] = b & 1
        elif a == 2:
            i = 0
            while i < 4:
                if self.mem[i] == 0: break
                i += 1
                y = 1
        return {'y': y}
'''
    built, original, goal, _ = _selected(5, source=source)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    for cell in range(4):
        selection(built, original, goal,
                  [(0, (cell << 3) | 1), *[(1, 0)] * 3, (2, 0)], owner)
    seed, selected = selection(built, original, goal,
                              [(0, 1), *[(1, 0)] * 3, (2, 0)], owner)
    assert goal in selected.replay.hits
    assert max(sum(item['state']['mem']) for item in selected.occurrences.values()) == 1
    request = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
    assert request.rank in selected.requests.explorations
    assert request.rank in selected.requests.preparations
    assert request.rank in selected.requests.contexts
    monkeypatch.setattr(_LocalQueryPreparation, 'probe',
                        lambda *_args, **_kw: pytest.fail('setup must not rebuild a history formula'))
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['goal'] is None
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits
    assert max(sum(item['state']['mem']) for item in confirmed.occurrences.values()) == 4
    restored = jobs.decode_selection_reply(selected.as_dict()).requests
    assert request.rank in restored.explorations
    assert request.rank in restored.preparations
    assert restored.contexts[request.rank] == selected.requests.contexts[request.rank]


@pytest.mark.solver
def test_baseline_material_prepares_an_unselected_seed_without_constructing_queries(monkeypatch):
    from behave.sim.behavior.instance import BehaviorInstance
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.engines.behavior import generation as parent, generation_session as child
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract

    built, original, goal, _ = _selected(4, source=SOURCE)
    reference = load({'behavior.py': SOURCE}, config={'class': 'M'})
    context = dict(source=thaw_record_value(parent._authenticated_source_image(reference)),
                   contract=_contract().as_dict(), target_artifact='target-a', targets=[goal],
                   generation_spec=api.GenerationSpec(5, 4, 1, 0, 0).as_dict())
    session = child.PreparedGeneration(context)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    seeds = [selection(built, original, goal, values, owner)[0] for values in (
        [(0, 11), (3, 0), (3, 0), (2, 0)],
        [(0, 21), (3, 0), (3, 0), (2, 0)],
        [(3, 0), (3, 0), (3, 0), (2, 0)],
    )]
    identity_reads = []
    original_identity = BehaviorInstance.identity.fget

    def identity(instance):
        identity_reads.append(instance)
        return original_identity(instance)

    with monkeypatch.context() as patch:
        patch.setattr(BehaviorInstance, 'identity', property(identity))
        patch.setattr(jobs.SelectedTrace, '_build_requests',
                      lambda *_: pytest.fail('baseline material must not construct queries'))
        for seed in seeds[:2]:
            assert session.prepare(dict(seed=seed.as_dict(), trace=_trace(seed).as_dict())) is None
    # One identity binds each audit's occurrences; material exchange has not
    # happened yet and must not fingerprint the same procedure again.
    assert identity_reads == [session.built[4]] * 2
    material = session.prepare({'material': None})
    assert len(material['writers']) == 2
    assert session.prepare({'updates': True})['writers'] == []
    assert session.selected is None and not session.confirmed
    assert not session.proved_questions
    # Input donors are independent of the physical slot horizon. The receiver
    # still constructs/audits its own selected trace and exact queries.
    other = child.PreparedGeneration({**context,
        'generation_spec': api.GenerationSpec(9, 4, 1, 0, 0).as_dict()})
    assert other.prepare({'material': material}) is None
    assert other.prepare({'updates': True})['writers'] == []
    assert other.selected is None and not other.confirmed
    seed = seeds[-1]
    selected = jobs.decode_selection_reply(other.select(dict(
        seed=seed.as_dict(), trace=_trace(seed).as_dict(), targets=[goal])))
    assert goal not in selected.replay.hits
    request = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
    issued = other.selected.as_dict()['preparation_plan']
    # A later search result introduces a writer absent from the baseline pool.
    donor = selection(built, original, goal,
                      [(0, 29), (3, 0), (3, 0), (2, 0)], owner)[0]
    session.prepare(dict(seed=donor.as_dict(), trace=_trace(donor).as_dict()))
    delta = session.prepare({'updates': True})
    assert [row[3] for row in delta['writers']] == [[3]]
    assert other.prepare({'material': delta}) is None
    assert other.prepare({'updates': True})['writers'] == []
    assert len(other.queries._preparation_frontier.writers) == 3
    assert other.select(dict(seed=seed.as_dict(), trace=_trace(seed).as_dict(),
                             targets=[goal]))['preparation_plan'] == issued
    answer = other.selected.probe(request.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['goal'] is None
    candidate = api.Stimulus.from_dict(answer['candidate'])
    checked = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert any([o['state']['mem'][i] for i in (1, 2)] == [3, 5]
               for o in checked.occurrences.values())
    with pytest.raises(ValueError, match='another source'):
        other.prepare({'material': {**material, 'binding': 'wrong-source'}})


def test_shared_writer_material_is_detached_and_validated_atomically():
    effects = {'op': ((0, {'mem'}, (), 1, {'mem'}),)}
    frontier = PreparationFrontier(effects)
    rows = [['op', 0, 'mem', [1], {'op': 0, 'value': 3}]]
    frontier.extend(rows)
    rows[0][-1]['value'] = 9
    assert frontier.rows()[0][-1]['value'] == 3
    exported = frontier.rows()
    exported[0][-1]['value'] = 8
    with pytest.raises(ValueError):
        frontier.extend([rows[0], ['op', 0, 'other', [1], {'op': 0}]])
    assert frontier.rows()[0][-1]['value'] == 3


def test_preparation_updates_are_deltas_and_imports_do_not_echo():
    effects = {'op': ((0, {'mem'}, (), 1, {'mem'}), (2, set(), (), 1, set()))}
    frontier = PreparationFrontier(effects)
    occurrences = [dict(position=0, input={'op': 0, 'value': 3}, state={'mem': [0, 0]}, hits=('a',)),
                   dict(position=1, input={'op': 2, 'value': 0}, state={'mem': [3, 0]}, hits=('b',))]
    frontier.observe(occurrences)
    writers, commands = frontier.updates()
    assert len(writers) == 1 and len(commands) == 2
    assert frontier.updates() == ([], [])
    frontier.observe(occurrences)
    assert frontier.updates() == ([], [])
    receiver = PreparationFrontier(effects)
    receiver.extend(writers, commands)
    receiver.observe(occurrences)
    assert receiver.updates() == ([], [])
    writers[0][-1]['value'] = 99
    assert frontier.rows()[0][-1]['value'] == receiver.rows()[0][-1]['value'] == 3


def test_writer_material_preserves_alternatives_at_the_same_cell():
    effects = {'op': ((0, {'mem'}, (), 1, {'mem'}),)}
    rows = [['op', 0, 'mem', [1], {'op': 0, 'value': value}] for value in (3, 5, 7)]
    frontier = PreparationFrontier(effects)
    frontier.extend(rows)
    assert frontier.rows() == rows
    restored = PreparationFrontier(effects)
    restored.extend(frontier.rows())
    restored.extend(rows)  # Importing the same material does not duplicate inputs.
    assert restored.rows() == rows
    rows[0][-1]['value'] = 9
    assert restored.rows()[0][-1]['value'] == 3


def test_writer_recipes_rotate_without_enumerating_combinations():
    from types import SimpleNamespace

    frontier = PreparationFrontier({'op': ((0, {'mem'}, (), 1, {'mem'}),
                                          (2, set(), {'goal'}, 1, set()))})
    frontier.extend([['op', 0, 'mem', [cell], {'op': 0, 'data': 10 * cell + value}]
                     for cell in range(2) for value in (1, 2, 3)])
    proc = SimpleNamespace(state=('mem',), input_framing=())
    window = tuple(dict(id=str(i), position=i, input={'op': 2, 'data': 0},
                        bindings=(('op', ('op', i)), ('data', ('data', i)))) for i in range(3))
    recipes = []
    for _ in range(3):
        row, = frontier.proposals({'prefix': window}, [(0, ('goal',))], proc,
            SimpleNamespace(digest=lambda: 'catalogue'), {'goal': 1},
            factory=lambda targets, **kw: SimpleNamespace(targets=targets, **kw))
        recipes.append([value['data'] for _, value in row[1]][:2])
    assert recipes == [[3, 13], [2, 12], [1, 11]]
    assert len(frontier._planned_writers['op', 2]) == 6
    assert set(frontier._planned_writers['op', 2].values()) == {1}


def test_multi_cell_writer_deduplication_does_not_starve_other_values():
    from types import SimpleNamespace

    frontier = PreparationFrontier({'op': ((0, {'mem'}, (), 1, {'mem'}),
                                          (2, set(), {'goal'}, 1, set()))})
    shared = {'op': 0, 'data': 3}
    alternative = {'op': 0, 'data': 7}
    frontier.extend([['op', 0, 'mem', [0], shared],
                     ['op', 0, 'mem', [1], alternative],
                     ['op', 0, 'mem', [1], shared]])
    proc = SimpleNamespace(state=('mem',), input_framing=())
    window = tuple(dict(id=str(i), position=i, input={'op': 2, 'data': 0},
                        bindings=(('op', ('op', i)), ('data', ('data', i)))) for i in range(3))
    recipes = []
    for _ in range(2):
        row, = frontier.proposals({'prefix': window}, [(0, ('goal',))], proc,
            SimpleNamespace(digest=lambda: 'catalogue'), {'goal': 1},
            factory=lambda targets, **kw: SimpleNamespace(targets=targets, **kw))
        recipes.append([value for _, value in row[1] if value['op'] == 0])
    assert recipes == [[shared], [shared, alternative]]


@pytest.mark.solver
def test_preparation_can_reuse_a_writer_that_a_later_sample_overwrote(monkeypatch):
    built, original, goal, _ = _selected(4, source=SOURCE)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    for value in (11, 12, 21, 22):
        selection(built, original, goal, [(0, value), (3, 0), (3, 0), (2, 7)], owner)
    _, selected = selection(built, original, goal, [(3, 0), (3, 0), (3, 0), (2, 0)], owner)
    monkeypatch.setattr(_LocalQueryPreparation, 'probe',
                        lambda *_args, **_kw: pytest.fail('writer recipes must not invoke SMT'))
    for _ in range(4):
        request = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
        answer = selected.probe(request.as_dict(), 20_000)
        assert answer['outcome'] == 'proposed' and answer['goal'] is None
        candidate = api.Stimulus.from_dict(answer['candidate'])
        assert candidate.slots == original.slots
        selected = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(),
                                      [goal], preparation=owner)
        if goal in selected.replay.hits:
            break
    assert goal in selected.replay.hits


def test_command_material_is_detached_ranked_and_validated_before_import():
    effects = {'op': ((0, {'mem'}, (), 1, {'mem'}), (2, set(), (), 1, set()))}
    frontier = PreparationFrontier(effects)
    commands = [['op', 2, 4, {'op': 2, 'value': 7}]]
    frontier.extend([], commands)
    commands[0][-1]['value'] = 9
    assert frontier.command_rows() == [['op', 2, 4, {'op': 2, 'value': 7}]]
    frontier.extend([], [['op', 2, 1, {'op': 2, 'value': 7}]])
    assert frontier.command_rows()[0][2] == 4
    exported = frontier.command_rows()
    exported[0][-1]['value'] = 0
    with pytest.raises(ValueError, match='source command'):
        frontier.extend([['op', 0, 'mem', [1], {'op': 0, 'value': 3}]],
                        [['op', 9, 4, {'op': 9, 'value': 7}]])
    assert not frontier.writers  # Neither half of a malformed import is retained.
    assert frontier.command_rows()[0][-1]['value'] == 7


def test_command_composition_preserves_framing_and_queue_length():
    from types import SimpleNamespace

    frontier = PreparationFrontier({'op': ((0, {'mem'}, (), 1, {'mem'}),
                                          (2, set(), {'goal'}, 1, set()))})
    frontier.extend([['op', 0, 'mem', [0], {'op': 0, 'data': 7, 'frame': 0}]], [
        ['op', 2, 100, {'op': 2, 'data': 255, 'frame': 1}],
        ['op', 2, 50, {'op': 2, 'data': 9, 'frame': 0}],
    ])
    proc = SimpleNamespace(state=('mem',), input_framing=((
        SimpleNamespace(fields=('frame',)), (('frame', 'frame'),)),))
    window = tuple(dict(id=str(i), position=i, input={'op': 0, 'data': 0, 'frame': 0},
                        bindings=(('op', ('op', i)), ('data', ('data', i)), ('frame', ('frame', i))))
                   for i in range(4))
    rows = list(frontier.proposals({'prefix': window}, [(0, ('goal',))], proc,
        SimpleNamespace(digest=lambda: 'catalogue'), {'goal': 1},
        factory=lambda targets, **kwargs: SimpleNamespace(targets=targets, **kwargs)))
    assert len(rows) == 1
    request, updates = rows[0]
    assert [values for _, values in updates] == [
        {'op': 0, 'data': 7, 'frame': 0}, {'op': 2, 'data': 0, 'frame': 0},
        {'op': 2, 'data': 9, 'frame': 0}]
    assert all(field[0] != 'frame' for field in request.mutable_fields)
    assert len(updates) <= len(window)


def test_command_slots_are_distributed_without_a_state_by_input_table():
    from types import SimpleNamespace

    frontier = PreparationFrontier({'op': ((0, {'mem'}, (), 1, {'mem'}),
                                          (2, set(), {'goal'}, 1, set()))})
    frontier.extend([['op', 0, 'mem', [0], {'op': 0, 'data': 7}]], [
        ['op', 2, 10 - i, {'op': 2, 'data': i}] for i in range(1, 9)])
    proc = SimpleNamespace(state=('mem',), input_framing=())
    manifest = SimpleNamespace(digest=lambda: 'catalogue')
    window = tuple(dict(id=str(i), position=i, input={'op': 0, 'data': 0},
                        bindings=(('op', ('op', i)), ('data', ('data', i)))) for i in range(3))
    issued, values = [], []
    for _ in range(16):
        rows = list(frontier.proposals({'prefix': window}, [(0, ('goal',))], proc,
            manifest, {'goal': 1},
            factory=lambda targets, **kwargs: SimpleNamespace(targets=targets, **kwargs)))
        assert len(rows) == 1
        request, updates = rows[0]
        assert request.targets == ('goal',)
        assert len(updates) == len(window)
        values.append(updates[-1][1]['data'])
        issued.append(updates)
    assert values == list(range(1, 9)) * 2
    assert [updates[-1][1]['data'] for updates in issued] == values
    assert len(frontier._planned_samples[('op', 2)]) == 8
    assert set(frontier._planned_samples[('op', 2)].values()) == {2}


@pytest.mark.solver
@pytest.mark.parametrize('cells', [4, 12, 24])
@pytest.mark.parametrize('extra_queries', [0, 7])
def test_observed_query_commands_follow_shared_preparation_without_symbolic_work(monkeypatch, cells, extra_queries):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.engines.behavior import generation as parent, generation_session as child
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract

    query_value = 9 if extra_queries else 255
    source = f'''class M:
    def reset(self): self.mem = [0] * {cells}
    def process(self, a, b):
        y = 0
        if a == 0: self.mem[(b >> 3) % {cells}] = b & 7
        elif a == 3: self.reset()
        elif a == 2:
            ready = True
            for i in range({cells}):
                if self.mem[i] != i % 7 + 1: ready = False
            if ready and b == {query_value}:
                y = 1
        return {{'y': y}}
'''.encode()
    length = cells + 3
    built, original, goal, _ = _selected(length, source=source)
    reference = load({'behavior.py': source}, config={'class': 'M'})
    context = dict(source=thaw_record_value(parent._authenticated_source_image(reference)),
                   contract=_contract().as_dict(), target_artifact='target-a', targets=[goal],
                   generation_spec=api.GenerationSpec(length + 1, length, 1, 0, 0).as_dict())
    collector = child.PreparedGeneration(context)

    def seed_for(values):
        return api.Stimulus(queues={'left': [(a,) for a, _ in values],
                                    'right': [(b,) for _, b in values]}, slots=original.slots)

    for cell in range(cells):
        donor = seed_for([(0, cell * 8 + cell % 7 + 1), *[(3, 0)] * (length - 2), (2, query_value)])
        collector.prepare(dict(seed=donor.as_dict(), trace=_trace(donor).as_dict()))
    for value in range(1, extra_queries + 1):
        donor = seed_for([*[(3, 0)] * (length - 1), (2, value)])
        collector.prepare(dict(seed=donor.as_dict(), trace=_trace(donor).as_dict()))
    material = collector.prepare({'material': None})
    receiver = child.PreparedGeneration(context)
    receiver.prepare({'material': material})
    seed = seed_for([*[(3, 0)] * (length - 1), (2, 0)])
    selected = jobs.decode_selection_reply(receiver.select(dict(
        seed=seed.as_dict(), trace=_trace(seed).as_dict(), targets=[goal])))
    assert goal not in selected.replay.hits
    def forbidden(*args, **kwargs):
        pytest.fail('recombining observed inputs does not construct a symbolic query')

    monkeypatch.setattr(_LocalQueryPreparation, 'probe', forbidden)
    hit = False
    for _ in range(extra_queries + 1):
        request = next((r for r in selected.requests if r.occurrence.startswith('state-setup:')), None)
        if request is None:
            break
        proposal = receiver.selected.probe(request.as_dict(), 20_000)
        assert proposal['outcome'] == 'proposed' and proposal['goal'] is None
        candidate = api.Stimulus.from_dict(proposal['candidate'])
        assert candidate.slots == seed.slots
        assert {name: len(rows) for name, rows in candidate.queues} == {
            name: len(rows) for name, rows in seed.queues}
        replay = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
        if goal in replay.replay.hits:
            hit = True
            break  # Only the ordinary native replay grants this hit.
        selected = jobs.decode_selection_reply(receiver.select(dict(
            seed=candidate.as_dict(), trace=_trace(candidate).as_dict(), targets=[goal])))
    assert hit, 'remaining observed inputs were never tried in the prepared state'


@pytest.mark.solver
@pytest.mark.parametrize('cells', [4, 12, 24])
def test_split_table_initialization_then_local_query(cells):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.engines.behavior import generation as parent, generation_session as child
    from behave.sim.search_parallel import QueryWork
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract

    source = f'''class M:
    def reset(self): self.mem = [0] * {cells}
    def process(self, a, b):
        y = 0
        if a == 0: self.mem[(b >> 3) % {cells}] = b & 7
        elif a == 3: self.reset()
        elif a == 2:
            ready = True
            for i in range({cells}):
                if self.mem[i] != i % 7 + 1: ready = False
            if ready and b == 255:
                y = 1
        return {{'y': y}}
'''.encode()
    built, original, goal, _ = _selected(cells + 2, source=source)
    reference = load({'behavior.py': source}, config={'class': 'M'})
    session = child.PreparedGeneration(dict(
        source=thaw_record_value(parent._authenticated_source_image(reference)),
        contract=_contract().as_dict(), target_artifact='target-a', targets=[goal],
        generation_spec=api.GenerationSpec(cells + 3, cells + 2, 1, 0, 0).as_dict()))
    def seed_for(values):
        return api.Stimulus(queues={'left': [(a,) for a, _ in values],
                                    'right': [(b,) for _, b in values]}, slots=original.slots)
    for cell in range(cells):
        donor = seed_for([(0, cell * 8 + cell % 7 + 1),
                          *[(3, 0)] * cells, (2, 0)])
        session.prepare(dict(seed=donor.as_dict(), trace=_trace(donor).as_dict()))
    assert len(session.prepare({'material': None})['writers']) == cells
    seed = seed_for([*[(3, 0)] * (cells + 1), (2, 0)])
    selected = jobs.decode_selection_reply(session.select(dict(
        seed=seed.as_dict(), trace=_trace(seed).as_dict(), targets=[goal])))
    assert goal not in selected.replay.hits
    prepare = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
    proposed = session.selected.probe(prepare.as_dict(), 20_000)
    assert proposed['outcome'] == 'proposed' and proposed['goal'] is None
    candidate = api.Stimulus.from_dict(proposed['candidate'])
    checked = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal not in checked.replay.hits
    work = QueryWork(checked.requests)
    _, request = work.popleft()
    assert request.occurrence.startswith('path-proposal:')
    proposal = checked.probe(request.as_dict(), 20_000)
    assert proposal['outcome'] == 'proposed' and proposal['goal'] is None
    trial = api.Stimulus.from_dict(proposal['candidate'])
    confirmed = jobs.SelectedTrace(built, trial.as_dict(), _trace(trial).as_dict(), [goal])
    assert goal in confirmed.replay.hits
    # Context work interleaves; the next ordinary query is still independent.
    _, request = _next_ordinary(work)
    assert request.occurrence.startswith('native-path:')
    answer = checked.probe(request.as_dict(), 20_000)
    if answer['outcome'] == 'local-unsat':
        _, request = _next_ordinary(work)
        answer = checked.probe(request.as_dict(), 20_000)
    assert answer['outcome'] == 'sat', answer
    final = api.Stimulus.from_dict(answer['candidate'])
    replay = jobs.SelectedTrace(built, final.as_dict(), _trace(final).as_dict(), [goal])
    assert goal in replay.replay.hits


def selection(built, original, goal, values, owner):
    seed = api.Stimulus(queues={"left": [(a,) for a, _ in values],
                               "right": [(b,) for _, b in values]}, slots=original.slots)
    return seed, jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(),
                                   [goal], preparation=owner)


@pytest.mark.solver
def test_native_writes_accumulate_then_existing_local_search_finishes():
    built, original, goal, _ = _selected(4, source=SOURCE)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    seed, selected = selection(built, original, goal,
                              [(0, 11), (3, 0), (0, 21), (2, 0)], owner)
    assert goal not in selected.replay.hits  # The reset separates useful writes.
    request = next(r for r in selected.requests.values() if r.occurrence.startswith('state-setup:'))
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['goal'] is None
    decoded = jobs.decode_probe_reply(answer, expected_catalogue_digest=selected.manifest.digest())
    assert decoded.scope is Scope.STATE_PREPARATION
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    checked = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(),
                                 [goal], preparation=owner)
    assert goal not in checked.replay.hits  # Preparation is not a target hit.
    entry = next(o for o in checked.occurrences.values()
                 if o['input']['a'] == 2 and [o['state']['mem'][i] for i in (1, 2)] == [3, 5])
    from behave.sim.search_parallel import QueryWork

    work = QueryWork(checked.requests)
    index, proposal = work.popleft()
    assert proposal.occurrence == 'path-proposal:' + entry['id']
    proposed = checked.probe(proposal.as_dict(), 20_000)
    assert proposed['outcome'] == 'proposed' and proposed['goal'] is None
    trial = api.Stimulus.from_dict(proposed['candidate'])
    confirmed = jobs.SelectedTrace(built, trial.as_dict(), _trace(trial).as_dict(), [goal])
    assert goal in confirmed.replay.hits
    proposal_family = work.family[index]
    index, local = _next_ordinary(work)
    assert local.occurrence == 'native-path:' + entry['id']  # No manual choice of a deep query.
    assert [name for name, field in entry['bindings'] if field in local.mutable_fields] == ['b']
    assert work.family[index] == proposal_family
    solved = checked.probe(local.as_dict(), 20_000)
    assert solved['scope'] == 'path-prefix'
    # This test selects only the final location, not intermediate branches.
    # If its next edge contains computation, the restricted path must defer
    # to the immediately following complete question, without rotating seeds.
    if solved['outcome'] == 'local-unsat':
        index, full = _next_ordinary(work)
        assert work.family[index] == proposal_family
        assert full.occurrence == entry['id'] and full.mutable_fields == local.mutable_fields
        solved = checked.probe(full.as_dict(), 20_000)
        assert solved['scope'] == 'exact-local-hit'
    assert solved['outcome'] == 'sat'
    final = api.Stimulus.from_dict(solved['candidate'])
    replay = jobs.SelectedTrace(built, final.as_dict(), _trace(final).as_dict(), [goal])
    assert goal in replay.replay.hits


@pytest.mark.solver
def test_native_entry_priority_is_deterministic_after_context_restoration():
    built, original, goal, _ = _selected(4, source=SOURCE)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    seed, selected = selection(built, original, goal,
                              [(0, 11), (0, 21), (2, 0), (3, 0)], owner)
    restored = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal],
                                 verify_feedback=False,
                                 preparation_recipes=selected.preparation_recipes)
    assert selected.requests.as_dict() == restored.requests.as_dict()
    assert selected.query_chains == restored.query_chains


def test_entry_ranking_keeps_large_shared_values_compact(monkeypatch):
    from types import SimpleNamespace
    from behave.sim.symbolic.uniform_array import UniformArray
    from behave.sim.engines.behavior import preparation_frontier as module

    initial = UniformArray(10**9, 0)
    changed = initial.store(999_999_999, 4)
    proc = SimpleNamespace(state=('mem',), initial={'mem': initial})
    items = [dict(id=str(i), input={'op': 2}, state={'mem': changed}) for i in range(100)]
    calls, original = [], module.changed_cells
    def visit(before, after, path=()):
        calls.append(path)
        yield from original(before, after, path)
    monkeypatch.setattr(module, 'changed_cells', visit)
    facts = entry_facts(items, proc, {'op': ((2, (), frozenset({'goal'}), 1, ()),)})
    assert all(value == (1, frozenset({'goal'})) for value in facts.values())
    assert calls == [(), (999_999_999,)]


@pytest.mark.solver
def test_pool_reuses_writers_across_seeds_but_not_exact_queries():
    built, original, goal, _ = _selected(4, source=SOURCE)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    _, first = selection(built, original, goal, [(0, 11), (3, 0), (3, 0), (2, 0)], owner)
    old_requests = first.requests.as_dict()
    old_answers = {r.identity: first.probe(r.as_dict(), 20_000)
                   for r in first.requests.values() if r.occurrence.startswith('state-setup:')}
    _, second = selection(built, original, goal, [(0, 21), (3, 0), (3, 0), (2, 0)], owner)
    request = next(r for r in second.requests.values() if r.occurrence.startswith('state-setup:'))
    candidate = api.Stimulus.from_dict(second.probe(request.as_dict(), 20_000)['candidate'])
    checked = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert any([o['state']['mem'][i] for i in (1, 2)] == [3, 5] for o in checked.occurrences.values())
    assert first.requests.as_dict() == old_requests
    assert {r.identity: first.probe(r.as_dict(), 20_000) for r in first.requests.values()
            if r.occurrence.startswith('state-setup:')} == old_answers


def test_compact_changed_cells_do_not_enumerate_address_space(monkeypatch):
    from behave.sim.symbolic.uniform_array import UniformArray

    before = UniformArray(10**9, 0)
    after = before.store(999_999_999, 3)
    calls, original = [], UniformArray.__getitem__
    def read(self, index):
        calls.append(index)
        return original(self, index)
    monkeypatch.setattr(UniformArray, '__getitem__', read)
    assert list(changed_cells(before, after)) == [(999_999_999,)]
    assert len(calls) == 2


def test_reset_boundaries_and_ineffective_writes_supply_no_donor():
    frontier = PreparationFrontier({'op': ((0, {'mem'}, (), 1, {'mem'}),)})
    first = dict(position=0, input={'op': 0}, state={'mem': [0, 0]})
    reset = dict(position=0, input={'op': 0}, state={'mem': [1, 0]})
    frontier.observe((first, reset))
    frontier.observe((first, {**first, 'position': 1}))
    assert not frontier.writers


def test_proposals_cannot_claim_satisfiability_or_goal_hits():
    for outcome, scope, goal in ((Outcome.SAT, Scope.STATE_PREPARATION, None),
                                 (Outcome.PROPOSED, Scope.EXACT_LOCAL_HIT, None),
                                 (Outcome.PROPOSED, Scope.STATE_PREPARATION, 'fake')):
        with pytest.raises(ValueError):
            ProbeResult(outcome, scope, goal=goal, candidate={})
    with pytest.raises(ValueError):
        ProbeResult(Outcome.LOCAL_UNSAT, Scope.STATE_PREPARATION)
    for outcome in (Outcome.UNKNOWN, Outcome.CONSTRUCTING):
        answer = ProbeResult(outcome, Scope.STATE_PREPARATION)
        assert answer.goal is None and answer.candidate is None


@pytest.mark.solver
def test_evicted_selection_restores_issued_recipes_not_the_changed_donor_pool():
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.engines.behavior import generation as parent, generation_session as child
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract

    built, original, goal, _ = _selected(6, source=SOURCE)
    reference = load({'behavior.py': SOURCE}, config={'class': 'M'})
    session = child.PreparedGeneration(dict(
        source=thaw_record_value(parent._authenticated_source_image(reference)),
        contract=_contract().as_dict(), target_artifact='target-a', targets=[goal],
        generation_spec=api.GenerationSpec(7, 6, 1, 0, 0).as_dict()))
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    first, _ = selection(built, original, goal,
                        [(0, 11), (3, 0), (0, 21), (2, 1), (2, 2), (2, 0)], owner)
    second, _ = selection(built, original, goal,
                         [(0, 14), (0, 17), (3, 0), (2, 3), (2, 4), (2, 0)], owner)
    def payload(seed):
        return dict(seed=seed.as_dict(), trace=_trace(seed).as_dict(), targets=[goal])
    selected = jobs.decode_selection_reply(session.select(payload(first)))
    request = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
    expected = session.selected.probe(request.as_dict(), 20_000)
    assert any(session.queries._preparation_frontier._planned_samples.values())
    session.select(payload(second))
    assert session.probing is None  # The old native trace really was evicted.
    restored = session.probe({**payload(first), 'request': request.as_dict(), 'timeout_ms': 20_000})
    assert restored == expected
    # Recipes are input records and occurrence identifiers, never old native states.
    assert all('state' not in values for recipes in session.preparation_recipes.values()
               for _template, recipe in recipes for _identity, values in recipe)


@pytest.mark.solver
def test_domain_reopen_preserves_issued_preparation_without_retaining_a_worker(monkeypatch):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.engines.behavior import generation as parent, generation_session as child
    from behave.sim.engines.behavior import generation_transport as transport
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract

    built, original, goal, _ = _selected(6, source=SOURCE)
    reference = load({'behavior.py': SOURCE}, config={'class': 'M'})
    context = dict(source=thaw_record_value(parent._authenticated_source_image(reference)),
        contract=_contract().as_dict(), target_artifact='target-a', targets=[goal],
        generation_spec=api.GenerationSpec(7, 6, 1, 0, 0).as_dict())
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    seeds = [selection(built, original, goal, values, owner)[0] for values in (
        [(0, 11), (3, 0), (0, 21), (2, 1), (2, 2), (2, 0)],
        [(0, 14), (0, 17), (3, 0), (2, 3), (2, 4), (2, 0)],
    )]
    collector = child.PreparedGeneration(context)
    for seed in seeds:
        collector.prepare(dict(seed=seed.as_dict(), trace=_trace(seed).as_dict()))
    material = collector.prepare({'material': None})
    layout = jobs.decode_layout_reply(collector.layout())
    plans, workers = {}, []

    class Session:
        def __init__(self, _name, payload, **_kwargs):
            self.prepared = child.PreparedGeneration(payload)
            self.opened = self.prepared.layout()
            workers.append(self)

        def _exchange(self, op, payload, _timeout):
            return getattr(self.prepared, op)(payload)

        def close(self):
            self.prepared = None

    monkeypatch.setattr(transport, 'JobSession', Session)
    first, second = seeds
    def start(saved):
        search = transport.BehaviorSearch(context, layout, timeout_s=60,
            probe_budget=None, preparation_plans=saved)
        search.prepare({'material': material})
        return search

    search = start(plans)
    search.select(second, _trace(second), [goal])
    selected = search.select(first, _trace(first), [goal])
    request = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
    expected = search.probe(first, _trace(first), [goal], request, 20_000)
    assert expected.outcome is Outcome.PROPOSED and expected.goal is None
    search.close()
    assert workers[0].prepared is None  # No native state or solver survived.

    cold = start({})
    changed = cold.select(first, _trace(first), [goal])
    assert changed.requests.selected(request) is None  # Original lost-context defect.
    cold.close()

    resumed = start(plans)
    restored = resumed.select(first, _trace(first), [goal])
    assert restored.requests.selected(request) is not None
    got = resumed.probe(first, _trace(first), [goal], request, 20_000)
    assert got.as_dict() == expected.as_dict()
    assert len(plans) == 2
    resumed.close()
    assert all(worker.prepared is None for worker in workers)


@pytest.mark.solver
@pytest.mark.parametrize('mutation', ['binding', 'occurrence', 'input', 'catalogue', 'state', 'duplicate'])
def test_restored_preparation_cannot_change_binding_inputs_or_add_evidence(mutation):
    import copy

    built, original, goal, _ = _selected(6, source=SOURCE)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    seed, selected = selection(built, original, goal,
        [(0, 11), (3, 0), (0, 21), (2, 1), (2, 2), (2, 0)], owner)
    image = copy.deepcopy(selected.as_dict()['preparation_plan'])
    assert image['recipes']
    recipe = image['recipes'][0]
    if mutation == 'binding': image['binding'] = '0' * 64
    elif mutation == 'occurrence': recipe['updates'][0][0] = 'another-epoch'
    elif mutation == 'input': recipe['updates'][0][1]['b'] ^= 1
    elif mutation == 'catalogue':
        from behave.sim.generation import ProbeRequest

        request = ProbeRequest.from_dict(recipe['request'])
        recipe['request'] = ProbeRequest(request.targets, request.occurrence,
            request.mutable_fields, request.rank, catalogue_digest='0' * 64).as_dict()
    elif mutation == 'state': recipe['state'] = {'mem': [0] * 4}
    elif mutation == 'duplicate': image['recipes'].append(copy.deepcopy(recipe))
    with pytest.raises((ValueError, TypeError)):
        jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal],
            preparation_recipes=image)


@pytest.mark.isolation
@pytest.mark.solver
def test_public_provider_resumes_preparation_after_real_worker_cleanup():
    from tests.support.reference import load
    from behave.sim.search_policy import SearchBudget, search_scope
    from .test_behavior_generation_provider import SOURCE as SLOT_SOURCE, _contract, _realization

    source = SOURCE + SLOT_SOURCE[SLOT_SOURCE.index(b'    def process_slot'):]
    built, original, goal, _ = _selected(6, source=source)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    seeds = [selection(built, original, goal, values, owner)[0] for values in (
        [(0, 11), (3, 0), (0, 21), (2, 1), (2, 2), (2, 0)],
        [(0, 14), (0, 17), (3, 0), (2, 3), (2, 4), (2, 0)],
    )]
    reference = load({'behavior.py': source}, config={'class': 'M'})
    task = api.admit(_contract(), _realization(), reference=reference)
    provider = api.prepare_behavior_generation(task, api.GenerationSpec(7, 6, 1, 0, 0),
        reference, _trace, targets=[goal], timeout_s=60, timeout_ms=20_000)
    first, second = seeds
    with search_scope(SearchBudget(api.SearchPolicy(time_budget_s=120)).direction(1)):
        with provider.search_scope(probe_budget=None):
            material = provider.collect_preparation(seeds)
            provider.select(second)
            selected = provider.select(first)
            request = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
            expected = provider.probe(first, request)
            assert expected.outcome is Outcome.PROPOSED
        assert not provider._searches and not provider._selected
        with provider.search_scope(probe_budget=None):
            provider.use_preparation(material)
            selected = provider.select(first)
            assert selected.requests.selected(request) is not None
            assert provider.probe(first, request).as_dict() == expected.as_dict()
    assert not provider._searches and not provider._selected


@pytest.mark.solver
def test_standalone_preparation_probe_reaudits_its_source_material():
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.engines.behavior import generation as parent
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract

    built, original, goal, _ = _selected(4, source=SOURCE)
    owner = _LocalQueryPreparation(built[4].proc, built[4].env)
    seed, _ = selection(built, original, goal, [(0, 11), (3, 0), (0, 21), (2, 0)], owner)
    reference = load({'behavior.py': SOURCE}, config={'class': 'M'})
    payload = dict(source=thaw_record_value(parent._authenticated_source_image(reference)),
                   contract=_contract().as_dict(), target_artifact='target-a', targets=[goal],
                   generation_spec=api.GenerationSpec(5, 4, 1, 0, 0).as_dict(),
                   seed=seed.as_dict(), trace=_trace(seed).as_dict())
    selected = jobs.decode_selection_reply(jobs.decode_context_reply(jobs.select_job(payload))[0])
    request = next(r for r in selected.requests if r.occurrence.startswith('state-setup:'))
    answer = jobs.decode_context_reply(jobs.probe_job({**payload, 'request': request.as_dict(),
                                                     'timeout_ms': 20_000}))[0]
    assert answer['outcome'] == 'proposed', answer
    assert answer['goal'] is None
