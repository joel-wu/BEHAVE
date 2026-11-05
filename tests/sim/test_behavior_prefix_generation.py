"""State setup must be searched jointly, without changing the public schedule."""

import pytest

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.engines.behavior import generation as parent
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.generation import ProbeRequest
from tests.support.reference import load

from .test_behavior_generation_provider import _contract

pytestmark = pytest.mark.solver

SOURCE = b'''class M:
    def reset(self):
        self.left = 0
        self.right = 0
    def process(self, a, b):
        y = 0
        if a == 0:
            self.left = b & 7
        elif a == 1:
            self.right = b & 7
        elif a == 2:
            if self.left == 3 and self.right == 5:
                y = 1
        return {"y": y}
'''


def _trace(seed):
    queues, indices, records = dict(seed.queues), dict(left=0, right=0), []
    for slot in seed.slots:
        accepted = {}
        for name in slot.offers:
            accepted[name] = queues[name][indices[name]]
            indices[name] += 1
        records.append(api.SlotRecord(accepted=accepted))
    return api.AuditedTrace(seed, records=records, artifact="target-a", domain="test-domain")


def _selected(length=3, reset_at=None, source=SOURCE, commands=None):
    reference = load({"behavior.py": source}, config={"class": "M"})
    slots = [api.EventSlot(reset=True)]
    for i in range(length):
        if i == reset_at:
            slots.append(api.EventSlot(reset=True))
        slots.append(api.EventSlot(offers=("left", "right")))
    seed = api.Stimulus(queues={"left": [(value,) for value in commands] if commands is not None
                               else [(3,)] * length, "right": [(0,)] * length},
                        slots=slots)
    built = jobs._build(dict(
        source=thaw_record_value(parent._authenticated_source_image(reference)),
        contract=_contract().as_dict(), target_artifact="target-a",
        generation_spec=api.GenerationSpec(len(slots), length, 1, 0, 0).as_dict()))
    line, = [i for i, text in enumerate(source.splitlines(), 1) if text.strip() == b"y = 1"]
    goal, = [g for g in built[6] if g.startswith(f"location:behavior.py:{line}:")]
    return built, seed, goal, jobs.SelectedTrace(
        built, seed.as_dict(), _trace(seed).as_dict(), [goal])


def _joint_requests(selected, *, controls=False):
    for request in selected.requests.values():
        if request.occurrence.startswith("prefix:"):
            window = selected.prefixes[request.occurrence]
            restricted = len(request.mutable_fields) < sum(len(item["bindings"]) for item in window)
            if restricted == controls:
                yield request


def test_writer_window_uses_first_consumer_without_dropping_intervening_inputs():
    from behave.sim.engines.behavior.prefix_generation import first_consumer_windows

    commands = [0, 3, 1, 1, 1]
    epoch = tuple({'id': str(i), 'input': {'opcode': command}, 'position': i}
                  for i, command in enumerate(commands))
    windows = tuple(first_consumer_windows(epoch, (epoch[0],), {'opcode': ()}))
    assert windows == (epoch[:3],)
    assert not tuple(first_consumer_windows(epoch, (epoch[-1],), {'opcode': ()}))
    assert not tuple(first_consumer_windows(epoch, (epoch[0],), {}))


def test_selection_keeps_shared_templates_lazy_while_sorting(monkeypatch):
    from behave.sim.query_plan import QueryTemplate

    monkeypatch.setattr(QueryTemplate, 'request',
                        lambda _self: pytest.fail('selection expanded a query template'))
    _, _, _, selected = _selected(4)
    selected.select(list(selected.available))
    assert selected.requests.templates
    selected.as_dict()


def test_context_selection_starts_from_exercised_command_and_preserves_opcode():
    source = b'''class M:
    def reset(self): self.mask = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.mask = b
        elif a == 1:
            for i in range(8):
                if ((self.mask | b) >> i) & 1:
                    y = 1
        return {"y": y}
'''
    built, original, goal, _ = _selected(3, source=source, commands=[0, 0, 1])
    seed = api.Stimulus(queues={'left': [(0,), (0,), (1,)], 'right': [(0,), (1,), (0,)]},
                        slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    first = next(r for r in selected.requests if r.occurrence.startswith('context-path:')
                 and not r.occurrence.startswith('context-path:prefix:'))
    assert {field.transaction for field in first.mutable_fields} == {2}
    assert {field.endpoint for field in first.mutable_fields} == {'right'}
    answer = selected.probe(first.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed', answer
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert dict(candidate.queues)['left'] == dict(seed.queues)['left']
    assert dict(candidate.queues)['right'][2] == (128,)


def test_context_identity_follows_entry_state_not_stimulus_or_mutable_payload():
    built, original, goal, _ = _selected(3, commands=[0, 1, 2])

    def identity(values):
        seed = api.Stimulus(queues={'left': [(0,), (1,), (2,)],
                                   'right': [(value,) for value in values]}, slots=original.slots)
        selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
        request = next(r for r in selected.requests if r.occurrence.startswith('context-path:')
                       and {f.transaction for f in r.mutable_fields} == {2}
                       and {f.endpoint for f in r.mutable_fields} == {'right'})
        return selected.requests.contexts[request.rank]

    assert identity([0, 0, 0]) == identity([0, 0, 1])
    assert identity([0, 0, 0]) != identity([3, 0, 0])


@pytest.mark.parametrize('length', (10, 20))
def test_public_context_search_continues_past_repeated_static_hits(length):
    from behave.sim.generation import generate
    from .test_behavior_generation_provider import _realization

    source = b'''class M:
    def reset(self): self.state = 0
    def process(self, a, b):
        y = 0
        if a == 1 and b == self.state + 37:
            self.state += 1
            y = 1
        else:
            self.state = 0
        return {"y": y}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"], slot["inputs"]["right"]["b"])}
'''
    _, _, goal, _ = _selected(length, source=source)
    reference = load({'behavior.py': source}, config={'class': 'M'})
    task = api.admit(_contract(), _realization(), reference=reference)
    seed = api.Stimulus(queues={'left': [(1,)] * length,
                               'right': [(37,)] + [(0,)] * (length - 1)},
        slots=[api.EventSlot(reset=True, controls={'rst': 1, 'result_ready': 0})] +
              [api.EventSlot(offers=('left', 'right'), controls={'rst': 0, 'result_ready': 1})] * length)
    spec = api.GenerationSpec(length + 1, length, 1, 0, 0)
    provider = api.prepare_behavior_generation(task, spec, reference, _trace,
        targets=(goal,), timeout_s=120, timeout_ms=20000)
    result = generate((seed,), provider, contract=_contract(), generation_spec=spec, probe_budget=64)
    namespace = {}
    exec(source, namespace)
    depths = []
    for candidate in result.generated:
        model = namespace['M']()
        model.reset()
        for (a,), (b,) in zip(dict(candidate.queues)['left'], dict(candidate.queues)['right'], strict=True):
            model.process(a, b)
            depths.append(model.state)
    # The public scheduler, not a hand-picked next query. The old restart
    # policy reached only depth 4; do not promise exhaustive bounded search.
    assert max(depths) > length // 2
    assert not result.telemetry['refusals']
    assert result.telemetry['termination'] == 'probe-budget-exhausted'
    assert goal in result.telemetry['coverage']['hits']


def test_context_setup_window_ends_at_trigger_not_at_next_power_of_two():
    source = b'''class M:
    def reset(self): self.mask = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.mask = b
        elif a == 1:
            for i in range(8):
                if (self.mask >> i) & 1:
                    y = 1
        return {"y": y}
'''
    commands = [0, 0, 0, 0, 1, 2, 2, 2]
    built, original, goal, _ = _selected(8, source=source, commands=commands)
    seed = api.Stimulus(queues={'left': [(x,) for x in commands],
                              'right': [(1,)] * 4 + [(0,)] * 4}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    contexts = [r for r in selected.requests if r.occurrence.startswith('context-path:')]
    joint = contexts[0]
    local = next(r for r in contexts if not r.occurrence.startswith('context-path:prefix:'))
    assert {f.transaction for f in local.mutable_fields} == {4}
    window = selected.prefixes[joint.occurrence.removeprefix('context-path:')]
    assert [item['position'] for item in window] == [3, 4]
    assert {f.transaction for f in joint.mutable_fields} == {3, 4}
    anchored = [selected.prefixes[r.occurrence.removeprefix('context-path:')]
                for r in contexts if r.occurrence.startswith('context-path:prefix:')]
    assert {len(w) for w in anchored if w[-1]['position'] == 4} >= {2, 4, 5}
    assert selected.probe(local.as_dict(), 20_000)['outcome'] == 'unknown'
    answer = selected.probe(joint.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['goal'] is None, answer
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    assert dict(candidate.queues)['left'] == dict(seed.queues)['left']
    assert dict(candidate.queues)['right'][3] == (129,)
    assert dict(candidate.queues)['right'][5:] == dict(seed.queues)['right'][5:]


def test_context_setup_starts_at_a_state_writer_before_neutral_commands():
    source = b'''class M:
    def reset(self): self.mask = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.mask = b
        elif a == 1:
            for i in range(8):
                if (self.mask >> i) & 1:
                    y = 1
        return {"y": y}
'''
    commands = [0, 2, 2, 1]
    built, original, goal, _ = _selected(4, source=source, commands=commands)
    seed = api.Stimulus(queues={'left': [(x,) for x in commands],
                              'right': [(1,), (0,), (0,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    joint = next(r for r in selected.requests if r.occurrence.startswith('context-path:'))
    window = selected.prefixes[joint.occurrence.removeprefix('context-path:')]
    assert [item['position'] for item in window] == [0, 1, 2, 3]
    answer = selected.probe(joint.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed', answer
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert dict(candidate.queues)['right'][0][0] & 128
    assert dict(candidate.queues)['left'] == dict(seed.queues)['left']


def test_joint_context_does_not_pin_read_only_queries_before_its_trigger():
    source = b'''class M:
    def reset(self): self.mask = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.mask = b
        elif a == 1:
            for i in range(8):
                if not ((self.mask >> i) & 1): break
                y = 1
        return {"y": y}
'''
    built, original, goal, _ = _selected(3, source=source, commands=[0, 1, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,), (1,)],
                              'right': [(1,), (0,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    joint = next(r for r in selected.requests if r.occurrence.startswith('context-path:prefix:')
                 and len(selected.prefixes[r.occurrence.removeprefix('context-path:')]) == 3
                 and {f.endpoint for f in r.mutable_fields} == {'right'})
    answer = selected.probe(joint.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed', answer
    candidate = api.Stimulus.from_dict(answer['candidate'])
    # The first query must be allowed to take its new path too. Pinning its
    # old break excludes exactly the setup needed by the final query.
    assert dict(candidate.queues)['right'][0][0] & 3 == 3
    assert dict(candidate.queues)['left'] == dict(seed.queues)['left']


def test_first_array_consumer_does_not_replace_later_anchored_history():
    source = b'''class M:
    def reset(self):
        self.mem = [0] * 4
        self.count = 0
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[b & 3] = (b >> 2) & 7
        elif a == 1:
            self.count += 1
            if self.count > 1 and self.mem[0] == 7:
                y = 1
        return {"y": y}
'''
    built, original, goal, _ = _selected(3, source=source, commands=[0, 1, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,), (1,)],
                              'right': [(28,), (0,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    windows = [selected.prefixes[r.occurrence.removeprefix('context-path:')]
               for r in selected.requests if r.occurrence.startswith('context-path:prefix:')]
    assert {(w[0]['position'], w[-1]['position']) for w in windows} >= {(0, 1), (0, 2)}
    for length in (2, 3):
        requests = [r for r in selected.requests if r.occurrence.startswith('context-path:prefix:')
                    and len(selected.prefixes[r.occurrence.removeprefix('context-path:')]) == length]
        assert any({field.transaction for field in r.mutable_fields} == {0} for r in requests)
        assert any({field.transaction for field in r.mutable_fields} == set(range(length))
                   for r in requests)


def test_context_writer_priority_does_not_mistake_query_scratch_for_setup():
    source = b'''class M:
    def reset(self):
        self.mask = 0
        self.scratch = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.mask = b
        elif a == 1:
            self.scratch = b
            for i in range(8):
                self.scratch += (self.mask >> i) & 1
                if self.scratch > 0:
                    y = 1
        return {"y": y}
'''
    built, original, goal, _ = _selected(3, source=source, commands=[0, 1, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,), (1,)],
                              'right': [(1,), (0,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    # Rank against the later trigger explicitly; earlier query scratch writes
    # must not be mistaken for its configuration. This is an ordering hint,
    # not a proof that a query can never supply state to another query.
    from behave.sim.engines.behavior.prefix_generation import setup_window

    window = setup_window(tuple(selected.occurrences.values()), built[4].proc,
                          selected._query_preparation.command_preparations(),
                          selected._query_preparation.state_dependencies()[goal])
    assert [item['position'] for item in window] == [0, 1, 2]


@pytest.mark.parametrize('length', [4, 32])
def test_equivalence_preparation_shares_entry_images_not_goal_verdicts(monkeypatch, length):
    from collections import Counter
    from behave.sim.behavior import value_records

    _, _, _, selected = _selected(length)
    selected.select(list(selected.available))
    entries = {id(item['state']) for item in selected.occurrences.values()}
    original, visits = value_records.domain_tree, Counter()

    def counted(value):
        if id(value) in entries:
            visits[id(value)] += 1
        return original(value)

    monkeypatch.setattr(value_records, 'domain_tree', counted)
    shared = selected._equivalent_requests()
    assert visits and max(visits.values()) == 1
    window_question = selected._window_question
    state_key = selected._state_key
    monkeypatch.setattr(selected, '_window_question',
        lambda request, **_: window_question(request))
    monkeypatch.setattr(selected, '_state_key',
        lambda item, _states: state_key(item, None))
    visits.clear()
    assert selected._equivalent_requests() == shared
    assert max(visits.values()) > 1


def test_preparation_path_changes_commands_before_solving_state_values():
    built, seed, goal, selected = _selected(4)
    paths = [r for r in selected.requests.values() if r.occurrence.startswith("prepare:")]
    assert paths
    request = next(r for r in paths if len(selected.prefixes[r.occurrence]) == 4)
    window = selected.prefixes[request.occurrence]
    assert [item["prepared_inputs"]["a"] for item in window] == [0, 1, 0, 2]
    assert all(item["input"]["a"] == 3 for item in window)
    assert len(request.mutable_fields) == 8
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert candidate.slots == seed.slots
    checked = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in checked.replay.hits
    # A negative answer for a fixed command path must never answer the original
    # all-command domain, even though its public mutable coordinates agree.
    original = next(r for r in _joint_requests(selected)
                    if len(selected.prefixes[r.occurrence]) == 4)
    assert selected._window_question(request) != selected._window_question(original)


def test_preparation_effects_do_not_unroll_or_overwrite_source_state():
    _built, _seed, _goal, selected = _selected(4)
    owner = selected._query_preparation
    effects = owner.command_preparations()
    assert effects is owner.command_preparations()
    by_command = {row[0]: row[1] for row in effects["a"]}
    assert by_command[0] == {"left"}
    assert by_command[1] == {"right"}
    assert by_command[2] == set()
    assert selected.instance.proc.initial == {"left": 0, "right": 0}


def test_preparation_repeats_payload_writes_instead_of_clearing_indexed_memory():
    source = b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[(b >> 3) & 3] = b & 7
        elif a == 1:
            self.mem = [0] * 4
        elif a == 2 and self.mem[1] == 3 and self.mem[2] == 5:
            y = 1
        return {"y": y}
'''
    built, seed, goal, selected = _selected(4, source=source)
    request = next(r for r in selected.requests.values() if r.occurrence.startswith("prepare:")
                   and len(selected.prefixes[r.occurrence]) == 4)
    assert [item["prepared_inputs"]["a"] for item in selected.prefixes[request.occurrence]] == [0, 0, 0, 2]
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert candidate.slots == seed.slots
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def test_preparation_paths_add_no_hidden_input_horizon_or_cartesian_command_product():
    _built, _seed, _goal, selected = _selected(32)
    paths = {r.occurrence for r in selected.requests.values() if r.occurrence.startswith("prepare:")}
    assert sorted(len(selected.prefixes[key]) for key in paths) == [2, 4, 8, 16, 32]
    assert all(selected.prefixes[key][0]["position"] == 0 for key in paths)
    assert len(list(_joint_requests(selected))) > len(paths)


def test_preparation_initialization_does_not_erase_completed_payload_writes():
    source = b'''class M:
    def reset(self):
        self.mem = [0] * 4
        self.enabled = 0
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[(b >> 3) & 3] = b & 7
        elif a == 3:
            self.mem = [0] * 4
            self.enabled = 1
        elif a == 2 and self.enabled and self.mem[1] == 3 and self.mem[2] == 5:
            y = 1
        return {"y": y}
'''
    built, seed, goal, selected = _selected(8, source=source, commands=(1,) * 8)
    request = next(r for r in selected.requests.values() if r.occurrence.startswith("prepare:")
                   and len(selected.prefixes[r.occurrence]) == 8)
    commands = [item["prepared_inputs"]["a"] for item in selected.prefixes[request.occurrence]]
    assert commands == [3, 0, 0, 0, 0, 0, 0, 2]
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert candidate.slots == seed.slots
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def test_native_array_entries_keep_cell_facts_and_do_not_alias_equal_hulls():
    from behave.sim.symbolic import ir, ranges
    from behave.sim.engines.local_ir import _LocalQueryPreparation

    source = b'''class M:
    def reset(self): self.mem = [0, 0, 64]
    def process(self, a, b):
        y = 0
        if a == 0: self.mem[0] = b & 7
        elif a == 1: self.mem[1] = b & 7
        k = self.mem[0]
        while k > 0:
            k -= 1
            if k == 3:
                y = 1
        return {"y": y}
'''
    built, _seed, goal, _selected_goal = _selected(3, source=source)
    instance = built[4]
    assert instance.proc.graph is not None
    owner = _LocalQueryPreparation(instance.proc, instance.env)
    owners = []
    for command, cells, expected in ((1, [0, 4, 64], "local-unsat"),
                                     (0, [4, 0, 64], "sat"),
                                     (1, [0, 4, 64], "local-unsat")):
        prepared = owner.for_state({"mem": cells})
        owners.append(prepared)
        value = ranges.of_expression(ir.Sel(ir.Var("mem"), ir.Lit(0)), prepared._env)
        assert (value.lo, value.hi) == (cells[0], cells[0])
        answer = owner.probe({"mem": cells}, (goal,), timeout_ms=20_000,
                             fixed_inputs={"a": 3, "b": 0})
        assert answer.outcome.value == expected, answer.detail
        hits = []
        witness = ({"a": command, "b": 4}, answer.candidate or {"a": 3, "b": 0})
        checked = instance.source_audit((witness,),
            observe_step=lambda trace, step, state, goals, outputs: hits.append(goal in goals))
        assert checked.status.value == "agreed"
        assert hits[-1] == (expected == "sat")
    assert owners[0] is not owners[1] and owners[1] is not owners[2]


def test_joint_prefix_reaches_a_goal_unreachable_by_every_single_input_query():
    built, seed, goal, selected = _selected()
    assert goal not in selected.replay.hits
    first = next(iter(selected.requests.values()))
    assert first.occurrence.startswith('path-proposal:')
    assert first.occurrence.removeprefix('path-proposal:') in selected.occurrences
    local = [r for r in selected.requests.values() if r.occurrence in selected.occurrences]
    assert local
    for request in local:
        answer = selected.probe(request.as_dict(), 20_000)
        assert answer["outcome"] == "local-unsat", answer
    short, full = _joint_requests(selected)
    assert selected.probe(short.as_dict(), 20_000)["outcome"] == "local-unsat"
    answer = selected.probe(full.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert candidate.slots == seed.slots
    assert candidate.depth("left") == candidate.depth("right") == 3
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits
    # A wire round trip retains the common scheduler's authority over proposals.
    jobs.decode_selection_reply(selected.as_dict())
    jobs.decode_probe_reply(answer, expected_catalogue_digest=selected.manifest.digest())


def test_public_selection_partitions_goals_without_dropping_a_dependency_component():
    built, seed, _goal, _selected_goal = _selected(4)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), list(built[6]))
    dependencies = selected._query_preparation.state_dependencies()
    by_occurrence = {}
    for request in selected.requests.values():
        masks = {dependencies.get(goal, 0) for goal in request.targets}
        assert len(masks) == 1
        by_occurrence.setdefault(request.occurrence, set()).update(request.targets)
    for identity, occurrence in selected.occurrences.items():
        expected = set(built[6]) - set(occurrence["hits"]) - set(selected.ruled_out)
        assert by_occurrence[identity] == expected
    assert len(set(dependencies.values())) > 1
    classes = [selected.manifest[r.targets[0]].cls for r in selected.requests.values()]
    assert classes == sorted(classes, key=lambda c: c == "safety")
    jobs.decode_selection_reply(selected.as_dict())


def test_selection_keeps_templates_and_ranks_when_native_hits_narrow_targets(monkeypatch):
    built, seed, goal, _one = _selected(8)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), list(built[6]))
    original = tuple(selected.requests.values())
    templates = selected._request_templates
    monkeypatch.setattr(selected, "_build_requests", lambda: pytest.fail("rebuilt trace queries"))
    selected.select([goal])
    expected = [request.with_targets((goal,)) for request in original if goal in request.targets]
    assert [r.as_dict() for r in selected.requests.values()] == [r.as_dict() for r in expected]
    assert selected._request_templates is templates
    assert selected.query_chains
    for chain in selected.query_chains:
        rows = [selected.requests[key] for key in chain]
        windows = [selected.prefixes[row.occurrence] for row in rows
                   if row.occurrence in selected.prefixes]
        assert len({window[0]["id"] for window in windows}) <= 1
        assert [len(window) for window in windows] == sorted(len(window) for window in windows)
        assert all(row.targets == (goal,) for row in rows)
        if rows[0].occurrence in selected.occurrences:
            if windows:
                assert rows[0].occurrence == windows[0][0]["id"]
            local = [row for row in rows if row.occurrence in selected.occurrences]
            assert all(row.occurrence == rows[0].occurrence for row in local)
            assert len(rows) == len(windows) + len(local)
    decoded = jobs.decode_selection_reply(selected.as_dict())
    assert decoded.query_chains == selected.query_chains


def test_first_local_probe_continues_into_joint_setup_before_rotating_seed():
    from behave.sim.search_parallel import QueryWork

    _built, _seed, goal, selected = _selected(8)
    # Ordinary-only plans keep construction affinity. Mixed plans now give
    # contexts a turn between completed questions (covered separately).
    ordinary = selected.requests.retaining(tuple(row for row in selected.requests.templates
        if row.rank not in selected.requests.explorations))
    work = QueryWork(ordinary)
    index, first = work.popleft()
    assert first.occurrence.startswith('path-proposal:')
    entry = first.occurrence.removeprefix('path-proposal:')
    assert entry in selected.occurrences and first.targets == (goal,)
    assert work.continues(index)
    while True:
        index, second = work.popleft()
        identity = second.occurrence.removeprefix('path-proposal:').removeprefix('native-path:')
        if identity in selected.prefixes:
            break
        assert identity == entry and work.continues(index)
    window = selected.prefixes[second.occurrence.removeprefix('native-path:')]
    assert window[0]["id"] == entry and len(window) == 2
    assert second.targets == first.targets


def test_window_questions_share_exact_planning_and_completed_prefixes(monkeypatch):
    from collections import Counter
    from behave.sim.symbolic import plan
    from behave.sim.engines.behavior.prefix_generation import PrefixPreparation, PrefixQuery

    built, seed, goal, selected = _selected(length=4)
    requests = list(_joint_requests(selected))[:2]
    assert [len(selected.prefixes[r.occurrence]) for r in requests] == [2, 4]
    preparation = PrefixPreparation(selected.instance.proc)
    derive, calls = plan.plan_encoding, []

    def counted(proc, env, *args, **kwargs):
        calls.append((id(proc), plan._environment_image(env)))
        return derive(proc, env, *args, **kwargs)

    monkeypatch.setattr(plan, "plan_encoding", counted)
    queries, answers = [], []
    for request in requests:
        query = PrefixQuery(selected.instance, selected.prefixes[request.occurrence],
                            request.targets, preparation=preparation)
        queries.append(query)
        answers.append(query.probe(request, 20_000))
    assert [a.outcome.value for a in answers] == ["local-unsat", "sat"]
    assert queries[0].proc is queries[1].proc
    assert queries[0]._prepared.validation is queries[1]._prepared.validation
    assert queries[0]._construction is not queries[1]._construction
    assert queries[1]._construction.reused_steps == 2
    assert calls and max(Counter(calls).values()) == 1
    # SAT still decodes through the ordinary candidate/native execution path.
    candidate = jobs._candidate_many(seed, zip(
        selected.prefixes[requests[-1].occurrence], answers[-1].candidate, strict=True))
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits
    entry = dict(queries[0].proc.initial)
    changed = {**entry, "left": 6}
    later, later_validation = preparation.at(changed, requests[0].targets, 2)
    assert later is not queries[0].proc and later.initial["left"] == 6
    assert queries[0].proc.initial == entry
    assert later_validation is not queries[0]._prepared.validation


def test_joint_window_extension_reuses_indexed_memory_transitions(monkeypatch):
    from collections import Counter
    from behave.sim.symbolic import encoder

    source = b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a < 4:
            self.mem[a] = b & 7
        elif a == 4 and self.mem[1] == 3 and self.mem[2] == 5:
            y = 1
        return {"y": y}
'''
    built, seed, goal, selected = _selected(4, source=source)
    short, longer = list(_joint_requests(selected))[:2]
    encode, starts = encoder._encode_step_iter, Counter()

    def counted(*args, **kwargs):
        starts[kwargs["tag"]] += 1
        return (yield from encode(*args, **kwargs))

    monkeypatch.setattr(encoder, "_encode_step_iter", counted)
    assert selected.probe(short.as_dict(), 20_000)["outcome"] == "local-unsat"
    answer = selected.probe(longer.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    assert selected._prefix_query[1]._construction.reused_steps == 2
    assert starts["@0"] == starts["@1"] == 1
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits
    assert candidate.slots == seed.slots


def test_window_planning_owner_cannot_be_spliced_across_sources():
    from behave.sim.engines.behavior.prefix_generation import PrefixPreparation, PrefixQuery

    _built, _seed, _goal, selected = _selected()
    other, *_rest = _selected(source=SOURCE.replace(b"left == 3", b"left == 4"))
    request = next(_joint_requests(selected))
    with pytest.raises(ValueError, match="another source"):
        PrefixQuery(selected.instance, selected.prefixes[request.occurrence], request.targets,
                    preparation=PrefixPreparation(other[4].proc))


def test_joint_seed_control_search_changes_payloads_but_retains_command_sequence():
    built, seed, goal, selected = _selected(4, commands=(0, 1, 2, 3))
    assert goal not in selected.replay.hits
    request = next(r for r in _joint_requests(selected, controls=True)
                   if len(selected.prefixes[r.occurrence]) == 4)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    assert {field.endpoint for field in request.mutable_fields} == {"right"}
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert dict(candidate.queues)["left"] == dict(seed.queues)["left"]
    assert dict(candidate.queues)["right"] != dict(seed.queues)["right"]
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


@pytest.mark.parametrize("seed_controls", [False, True])
def test_prefix_resumes_encoded_steps_without_restarting(monkeypatch, seed_controls):
    from collections import Counter
    from behave.sim.symbolic import encoder
    from behave.sim.engines.behavior import prefix_generation
    from behave.sim.engines.behavior.prefix_generation import PrefixQuery

    built, _seed, goal, selected = _selected(3, commands=(0, 1, 2))
    request = next(r for r in _joint_requests(selected, controls=seed_controls)
                   if len(selected.prefixes[r.occurrence]) == 3)
    original, checkpoint = encoder._encode_step_iter, encoder._Commands.checkpoint
    starts, paused = Counter(), set()
    clock = [0.0]
    monkeypatch.setattr(prefix_generation.time, "monotonic", lambda: clock[0])

    def encode(*args, **kwargs):
        starts[kwargs["tag"]] += 1
        return (yield from original(*args, **kwargs))

    def pause_once(enc):
        checkpoint(enc)
        if enc.tag not in paused:
            paused.add(enc.tag)
            clock[0] += 21
            raise encoder._EncodingPaused

    monkeypatch.setattr(encoder, "_encode_step_iter", encode)
    monkeypatch.setattr(encoder._Commands, "checkpoint", pause_once)
    query = PrefixQuery(selected.instance, selected.prefixes[request.occurrence],
                        request.targets)
    outcomes = []
    for _ in range(10):
        answer = query.probe(request, 20_000)
        outcomes.append(answer.outcome.value)
        if answer.outcome.value != "constructing":
            break
    assert outcomes.count("constructing") == 3, outcomes
    assert answer.outcome.value == "sat", answer
    assert starts == {"@0": 1, "@1": 1, "@2": 1}
    candidate = jobs._candidate_many(selected.seed, zip(
        selected.prefixes[request.occurrence], answer.candidate, strict=True))
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits
    assert query._construction.checkpoint.callback is None
    assert query._formula_checkpoint.callback is None


def test_external_prefix_cancellation_is_not_resumable(monkeypatch):
    from behave.sim.symbolic import encoder
    from behave.sim.engines.behavior.prefix_generation import PrefixQuery

    _built, _seed, _goal, selected = _selected()
    request = next(r for r in selected.requests.values() if r.occurrence in selected.prefixes)
    query = PrefixQuery(selected.instance, selected.prefixes[request.occurrence],
                        request.targets)

    def cancelled(_enc):
        raise TimeoutError("worker cancelled")

    monkeypatch.setattr(encoder._Commands, "checkpoint", cancelled)
    answer = query.probe(request, 20_000)
    assert answer.outcome.value == "unknown"
    assert query._construction.iterator is None
    assert query._construction.checkpoint.callback is None


@pytest.mark.parametrize("pause_at", ["encoding", "registration", "slice"])
def test_narrowed_prefix_reuses_construction_not_the_old_goal_disjunction(monkeypatch, pause_at):
    from collections import Counter
    from behave.sim.symbolic import exact_formula
    from behave.sim.symbolic import encoder
    from behave.sim.engines.behavior import prefix_generation

    built, _seed, goal, selected = _selected(3, commands=(0, 1, 2))
    line = SOURCE.splitlines().index(b"            if self.left == 3 and self.right == 5:") + 1
    dependencies = selected._query_preparation.state_dependencies()
    # The opposite branch shares the target's state dependencies; a setup
    # assignment belongs to another query group after dependency partitioning.
    other = next(g for g in built[6] if g.startswith(f"branch:behavior.py:{line}:")
                 and g.endswith(":F") and dependencies[g] == dependencies[goal])
    selected.select(sorted([goal, other]))
    request = next(r for r in selected.requests.values()
                   if r.occurrence in selected.prefixes and len(selected.prefixes[r.occurrence]) == 3
                   and set(r.targets) == {goal, other})
    clock, starts = [0.0], Counter()
    monkeypatch.setattr(prefix_generation.time, "monotonic", lambda: clock[0])
    encode = encoder._encode_step_iter

    def count(*args, **kwargs):
        starts[kwargs["tag"]] += 1
        return (yield from encode(*args, **kwargs))

    def pause_once():
        if clock[0] == 0:
            clock[0] = 21

    monkeypatch.setattr(encoder, "_encode_step_iter", count)
    if pause_at == "encoding":
        checkpoint = encoder._Commands.checkpoint
        def pause(enc):
            checkpoint(enc)
            if clock[0] == 0:
                pause_once()
                raise encoder._EncodingPaused
        monkeypatch.setattr(encoder._Commands, "checkpoint", pause)
    elif pause_at == "registration":
        advance = exact_formula._FormulaConstruction.advance
        def pause(owner, *args, **kwargs):
            result = advance(owner, *args, **kwargs)
            pause_once()
            return result
        monkeypatch.setattr(exact_formula._FormulaConstruction, "advance", pause)
    else:
        slice_formula = exact_formula.ExactFormula.slice
        def pause(owner, *args, **kwargs):
            result = slice_formula(owner, *args, **kwargs)
            pause_once()
            return result
        monkeypatch.setattr(exact_formula.ExactFormula, "slice", pause)

    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "constructing", answer
    query = selected._prefix_query[1]
    narrow = request.with_targets((goal,))
    answer = selected.probe(narrow.as_dict(), 20_000)
    assert selected._prefix_query[1] is query
    assert answer["outcome"] == "sat" and answer["goal"] == goal, answer
    assert starts == {"@0": 1, "@1": 1, "@2": 1}
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def test_first_partitioned_probe_constructs_only_its_requested_targets(monkeypatch):
    from behave.sim.engines.behavior import prefix_generation

    built, seed, goal, selected = _selected(3, commands=(0, 1, 2))
    line = SOURCE.splitlines().index(b"            if self.left == 3 and self.right == 5:") + 1
    dependencies = selected._query_preparation.state_dependencies()
    other = next(g for g in built[6] if g.startswith(f"branch:behavior.py:{line}:")
                 and g.endswith(":F") and dependencies[g] == dependencies[goal])
    selected.select(sorted([goal, other]))
    request = next(r for r in _joint_requests(selected)
                   if len(selected.prefixes[r.occurrence]) == 3
                   and set(r.targets) == {goal, other})
    create, constructed = prefix_generation.PrefixQuery, []

    def observed(instance, window, goals, **kwargs):
        constructed.append(tuple(goals))
        return create(instance, window, goals, **kwargs)

    monkeypatch.setattr(prefix_generation, "PrefixQuery", observed)
    for target in (goal, other):
        answer = selected.probe(request.with_targets((target,)).as_dict(), 20_000)
        assert answer["outcome"] == "sat", answer
        candidate = api.Stimulus.from_dict(answer["candidate"])
        assert candidate.slots == seed.slots
        checked = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [target])
        assert target in checked.replay.hits
    assert constructed == [(goal,), (other,)]


def test_prefix_keeps_restricted_fields_fixed():
    from behave.sim.core.refusal import Refused
    from behave.sim.engines.behavior.prefix_generation import PrefixQuery

    _built, _seed, _goal, selected = _selected()
    request = next(r for r in _joint_requests(selected)
                   if len(selected.prefixes[r.occurrence]) == 3)
    restricted = ProbeRequest(request.targets, occurrence=request.occurrence,
        mutable_fields=tuple(f for f in request.mutable_fields if f.endpoint == "right"),
        rank=request.rank, catalogue_digest=request.catalogue_digest)
    # No opcode can change, hence no register write or query can occur.
    # The public selector does not authorize arbitrary new field scopes.
    with pytest.raises(Refused, match="request-not-selected"):
        selected.probe(restricted.as_dict(), 20_000)
    query = PrefixQuery(selected.instance, selected.prefixes[request.occurrence], request.targets)
    answer = query.probe(restricted, 20_000)
    assert answer.outcome.value == "local-unsat", answer
    assert selected.probe(request.as_dict(), 20_000)["outcome"] == "sat"


def test_prefix_does_not_join_state_across_reset():
    _built, _seed, _goal, selected = _selected(4, reset_at=2)
    assert len([key for key in selected.prefixes if key.startswith("prefix:")]) == 2
    for request in selected.requests.values():
        if request.occurrence in selected.prefixes:
            assert len(selected.prefixes[request.occurrence]) == 2
            answer = selected.probe(request.as_dict(), 20_000)
            assert answer["outcome"] == "local-unsat", answer


def test_prefix_ladder_has_no_smaller_hidden_horizon():
    _built, _seed, _goal, selected = _selected(10)
    assert [len(window) for key, window in selected.prefixes.items()
            if key.startswith("prefix:") and window[0]["position"] == 0] == [2, 4, 8, 10]
    assert any(window[0]["position"] > 0 for window in selected.prefixes.values())


def test_command_paths_are_explicit_and_deep_windows_precede_unrestricted_fallback():
    _built, _seed, _goal, selected = _selected(8)
    restricted = list(_joint_requests(selected, controls=True))
    unrestricted = list(_joint_requests(selected))
    assert restricted and unrestricted
    assert max(r.rank for r in restricted) < min(r.rank for r in unrestricted)
    assert any(len(selected.prefixes[r.occurrence]) == 8 for r in restricted)
    assert {r.occurrence for r in restricted} == {r.occurrence for r in unrestricted}
    jobs.decode_selection_reply(selected.as_dict())


@pytest.mark.parametrize("memory", [False, True])
def test_joint_window_uses_the_real_entry_state_and_preserves_prior_setup(memory):
    source = (b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[0] = 7
        elif a == 1:
            self.mem[b & 3] = b
        elif a == 2 and self.mem[0] == 7 and self.mem[1] == 5:
            y = 1
        return {"y": y}
''' if memory else SOURCE.replace(b'self.left = b & 7', b'self.left = 3'))
    built, seed, goal, selected = _selected(4, source=source, commands=(0, 3, 3, 3))
    request = next(r for r in _joint_requests(selected)
                   if selected.prefixes[r.occurrence][0]["position"] == 2)
    assert goal not in selected.replay.hits
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    for endpoint, values in candidate.queues:
        assert values[:2] == dict(seed.queues)[endpoint][:2]
    assert candidate.slots == seed.slots
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


@pytest.mark.parametrize('size', (4, 8))
def test_continuation_prepares_one_write_without_rebuilding_the_reached_state(size):
    source = f'''class M:
    def reset(self): self.mem = [0] * {size}
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[0] = 7
        elif a == 1:
            self.mem[b & {size - 1}] = b
        elif a == 2 and self.mem[0] == 7 and self.mem[{size - 1}] == {2 * size - 1}:
            y = 1
        return {{"y": y}}
'''.encode()
    built, seed, goal, selected = _selected(4, source=source, commands=(0, 3, 2, 2))
    requests = [r for r in selected.requests
                if r.occurrence.startswith('native-path:prepare:continuation:')
                and selected.prefixes[r.occurrence.removeprefix('native-path:')][0]['position'] == 2]
    assert requests, 'source-ranked preparation must also extend a reached state'
    request = next(r for r in requests if {f.transaction for f in r.mutable_fields} == {2})
    window = selected.prefixes[request.occurrence.removeprefix('native-path:')]
    assert window[0]['prepared_inputs'] == {'a': 1}
    assert window[-1]['prepared_inputs'] == window[-1]['input']
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer['outcome'] in ('sat', 'proposed'), str(answer)
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    for endpoint, values in candidate.queues:
        old = dict(seed.queues)[endpoint]
        assert values[:2] == old[:2] and values[3:] == old[3:]
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def test_continuation_paths_do_not_cross_reset_or_grow_quadratically():
    _built, _seed, _goal, selected = _selected(20, reset_at=10, commands=[2] * 20)
    windows = {r.occurrence: selected.prefixes[r.occurrence.removeprefix('context-path:')]
               for r in selected.requests if r.occurrence.startswith('context-path:prepare:continuation:')}
    assert windows
    assert len(windows) <= 2 * 18  # Two source writer classes, eighteen adjacent pairs.
    assert all(len(w) == 2 and w[1]['position'] == w[0]['position'] + 1
               for w in windows.values())
    contexts = {selected.requests.contexts[r.rank] for r in selected.requests
                if r.occurrence in windows and windows[r.occurrence][0]['position'] == 1
                and {f.transaction for f in r.mutable_fields} == {1}}
    assert len(contexts) == 2  # Prepared command choices must not deduplicate each other.


def test_window_equivalence_requires_entry_state_and_released_fields():
    _built, _seed, _goal, selected = _selected(4)
    full = [r for r in _joint_requests(selected) if len(selected.prefixes[r.occurrence]) == 2]
    fixed = next(r for r in _joint_requests(selected, controls=True)
                 if r.occurrence == full[0].occurrence)
    assert len(full) == 2
    assert selected._window_question(full[0]) == selected._window_question(full[1])
    assert selected._window_question(full[0]) != selected._window_question(fixed)
    assert any({r.identity for r in full} <= set(group) for group in selected.equivalent_requests)

    source = SOURCE.replace(b'self.left = b & 7', b'self.left = 3')
    _built, _seed, _goal, changed = _selected(4, source=source, commands=(0, 3, 3, 3))
    full = [r for r in _joint_requests(changed) if len(changed.prefixes[r.occurrence]) == 2]
    assert changed._window_question(full[0]) != changed._window_question(full[1])


def test_window_family_does_not_enumerate_quadratic_start_end_pairs():
    from behave.sim.engines.behavior.prefix_generation import prefixes

    count = 1024
    rows = tuple({"id": str(i), "position": i} for i in range(count))
    windows = prefixes(rows)
    assert len(windows) < count
    assert sum(map(len, windows.values())) == count * 10
    assert any(len(window) == count for window in windows.values())


def test_prefix_drops_output_only_loops_before_planning(monkeypatch):
    from behave.sim.behavior import residual
    from behave.sim.symbolic import ir
    from behave.sim.engines.behavior import prefix_generation as prefix

    source = SOURCE.replace(b'        return {"y": y}', b'''        scratch = 0
        for i in range(50):
            scratch = (scratch + a) & 255
        return {"y": y ^ scratch}''')
    built, _seed, goal, selected = _selected(source=source)
    assert any(isinstance(cmd, ir.For) for cmd in ir.walk(built[4].proc.body))
    request = next(r for r in _joint_requests(selected)
                   if len(selected.prefixes[r.occurrence]) == 3)
    unroll = residual.unroll
    planned = []

    def encode(proc, *args, **kwargs):
        assert not any(isinstance(cmd, ir.For) for cmd in ir.walk(proc.body))
        planned.append(proc)
        return unroll(proc, *args, **kwargs)

    monkeypatch.setattr(prefix.residual, "unroll", encode)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    assert len(planned) == 1  # One explicit request has one construction owner.
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def test_expired_prefix_query_never_reports_unsat(monkeypatch):
    from behave.sim.engines.behavior import prefix_generation as prefix

    _built, _seed, _goal, selected = _selected()
    request = next(r for r in selected.requests.values() if r.occurrence in selected.prefixes)
    clock = iter([0, 1, 1])
    monkeypatch.setattr(prefix.time, "monotonic", lambda: next(clock))
    answer = selected.probe(request.as_dict(), 1)
    assert answer["outcome"] == "constructing", answer
    assert answer["candidate"] is None


@pytest.mark.parametrize("can_stop", [False, True])
def test_prefix_unsat_requires_no_reachable_representation_stop(can_stop):
    from types import SimpleNamespace
    from behave.sim.symbolic import ir, ranges, typecheck as types
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType
    from behave.sim.engines.behavior.prefix_generation import PrefixQuery
    from behave.sim.generation import StimulusField

    body = ir.Seq(
        ir.If(ir.Lit(can_stop), ir.Fail("Unwound", "limit"), ir.Skip()),
        ir.If(ir.Lit(False), ir.Mark("target"), ir.Skip()))
    proc = ir.Procedure((), ("a",), (), (), body, types={"a": types.INTEGER},
                        initial={}, signature=ProcedureSignature((("a", FiniteType(1)),), ()))
    instance = SimpleNamespace(proc=proc, env={"a": ranges.Range(0, 1)})
    fields = tuple(StimulusField("in", i, 0, i) for i in range(2))
    window = tuple(dict(state={}, input={"a": 0}, bindings=(("a", field),))
                   for field in fields)
    request = ProbeRequest(("target",), occurrence="prefix:0:2", mutable_fields=fields,
                           catalogue_digest="0" * 64)
    answer = PrefixQuery(instance, window, request.targets).probe(request, 20_000)
    assert answer.outcome.value == ("unknown" if can_stop else "local-unsat"), answer
    assert answer.candidate is None


@pytest.mark.parametrize("depth", [10, 20])
def test_joint_prefix_can_drive_a_deep_state_sequence(depth):
    source = f'''class M:
    def reset(self): self.state = 0
    def process(self, a, b):
        y = 0
        if a == self.state + 37:
            self.state += 1
        else:
            self.state = 0
        if self.state == {depth}:
            y = 1
        return {{"y": y}}
'''.encode()
    built, _seed, goal, selected = _selected(depth, source=source)
    assert goal not in selected.replay.hits
    request = next(r for r in selected.requests.values()
                   if r.occurrence in selected.prefixes and len(selected.prefixes[r.occurrence]) == depth)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


@pytest.mark.parametrize("offset", [0, 2])
def test_prefix_retains_framing_markers_and_the_fixed_suffix(offset):
    from .test_input_framing import contract, nested

    source = b'''class M:
    def reset(self): self.old = 0
    def process(self, pixel, sof, eof, sol, eol):
        y = 0
        if self.old == 37 and pixel == 91:
            y = 1
        self.old = pixel
        return {"out_pixel": y}
'''
    task = contract(nested())
    rows = [(0, 1, 0, 1, 0), (0, 0, 0, 0, 0),
            (0, 0, 0, 0, 0), (0, 0, 1, 0, 1)]
    seed = api.Stimulus(queues={"in": rows}, slots=[api.EventSlot(reset=True)] +
                       [api.EventSlot(offers=("in",))] * 4)

    def trace(stimulus):
        return api.AuditedTrace(stimulus, records=[api.SlotRecord()] + [
            api.SlotRecord(accepted={"in": row}) for row in dict(stimulus.queues)["in"]],
            artifact="target-a", domain="test-domain")

    reference = load({"behavior.py": source}, config={"class": "M"})
    built = jobs._build(dict(
        source=thaw_record_value(parent._authenticated_source_image(reference)),
        contract=task.as_dict(), target_artifact="target-a",
        generation_spec=api.GenerationSpec(5, 4, 1, 0, 0).as_dict()))
    goal, = [g for g in built[6] if g.startswith("location:") and ":6:" in g]
    selected = jobs.SelectedTrace(built, seed.as_dict(), trace(seed).as_dict(), [goal])
    request = next(r for r in selected.requests.values() if r.occurrence in selected.prefixes
                   and selected.prefixes[r.occurrence][0]["position"] == offset
                   and len(selected.prefixes[r.occurrence]) == 2)
    assert selected.prefixes[request.occurrence][0]["framing"] == ((0, offset),)
    assert all(field.field == 0 for field in request.mutable_fields)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    actual = dict(candidate.queues)["in"]
    assert [row[1:] for row in actual] == [row[1:] for row in rows]
    assert all(tuple(actual[i]) == tuple(rows[i]) for i in range(4)
               if i < offset or i >= offset + 2)
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def test_prefix_construction_honors_the_same_query_deadline(monkeypatch):
    from behave.sim.behavior import residual
    from behave.sim.engines.behavior.prefix_generation import PrefixQuery

    _built, _seed, _goal, selected = _selected()
    request = next(r for r in selected.requests.values() if r.occurrence in selected.prefixes)
    original = residual.unroll
    calls = []

    def unroll(*args, **kwargs):
        assert callable(kwargs["_checkpoint"])
        def stop():
            calls.append(True)
            if len(calls) == 3:
                raise TimeoutError("prefix construction cancelled")
        return original(*args, **{**kwargs, "_checkpoint": stop})

    monkeypatch.setattr(residual, "unroll", unroll)
    query = PrefixQuery(selected.instance, selected.prefixes[request.occurrence],
                        request.targets)
    answer = query.probe(request, 20_000).as_dict()
    assert len(calls) == 3
    assert answer["outcome"] == "unknown", answer
    assert "prefix construction cancelled" in answer["detail"]


def test_prefix_starts_at_reset_but_does_not_bound_successors_by_it(monkeypatch):
    from behave.sim.behavior import residual
    from behave.sim.symbolic import ranges

    source = b'''class M:
    def reset(self):
        self.mem = [-2] * 4
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[b & 3] = b * 3
        elif a == 1 and self.mem[3] == 765:
            y = 1
        return {"y": y}
'''
    built, _seed, goal, selected = _selected(source=source)
    request = next(_joint_requests(selected))
    original = residual.unroll
    entries = []

    def unroll(proc, env, *args, **kwargs):
        for name in proc.state:
            assert (env[name].lo, env[name].hi) == (-2, -2)
        entries.append(True)
        return original(proc, env, *args, **kwargs)

    def no_horizon_range_pass(*args, **kwargs):
        pytest.fail("prefix construction must not replan the entire state horizon")

    monkeypatch.setattr(residual, "unroll", unroll)
    monkeypatch.setattr(ranges, "bounded", no_horizon_range_pass)
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    assert entries == [True]
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def _ir_prefix(proc, rows, *, mutable=True):
    from types import SimpleNamespace
    from behave.sim.symbolic import ranges
    from behave.sim.engines.behavior.prefix_generation import PrefixQuery
    from behave.sim.generation import StimulusField

    fields = tuple(StimulusField("in", i, 0, i) for i in range(len(rows)))
    window = tuple(dict(state=proc.initial, input={"a": value}, bindings=(("a", field),))
                   for value, field in zip(rows, fields, strict=True))
    request = ProbeRequest(("target",), occurrence="prefix:test", mutable_fields=fields if mutable else (),
                           catalogue_digest="0" * 64)
    instance = SimpleNamespace(proc=proc, env={"a": ranges.Range(0, 3)})
    return PrefixQuery(instance, window, request.targets), request


def test_prefix_queries_a_reachable_occurrence_before_its_expensive_tail(monkeypatch):
    from behave.sim.symbolic import encoder, interp, ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    tail = ir.For("i", 4, ir._sequence((
        ir.Assign("tail", ir.Op("mul", ir.Var("a"), ir.Var("i"))),
        ir.If(ir.Op("eq", ir.Var("tail"), ir.Lit(9)),
              ir.Fail("Stopped", "failed"), ir.Skip()))))
    body = ir._sequence((
        ir.Assign("q", ir.Op("add", ir.Var("q"), ir.Lit(1))),
        ir.If(ir.Op("eq", ir.Var("q"), ir.Lit(3)), ir.Mark("target"), ir.Skip()),
        tail))
    proc = ir.Procedure(("q",), ("a",), ("i", "tail"), (), body,
        initial={"q": 0}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    query, request = _ir_prefix(proc, (0, 0, 0, 2))
    original, visits = encoder._Commands._assign, []

    def assign(self, command, *args, **kwargs):
        if self.tag == "@2" and command.var == "tail":
            pytest.fail("a replayable prefix witness should not wait for the target step's tail")
        visits.append((self.tag, command.var))
        return original(self, command, *args, **kwargs)

    monkeypatch.setattr(encoder._Commands, "_assign", assign)
    answer = query.probe(request, 20_000)
    assert answer.outcome.value == "sat", answer.as_dict()
    assert "occurrence" in answer.detail and query.run is None
    assert ("@0", "tail") in visits and ("@1", "tail") in visits
    assert answer.candidate[-1] == {"a": 2}  # The unencoded suffix stays untouched.
    state, hits = proc.initial, set()
    for row in answer.candidate:
        state, _outputs, found, failure = interp.run_step(proc, row, state)
        hits.update(found)
        if failure:
            break
    assert "target" in hits


def test_unsat_first_occurrence_does_not_hide_a_later_hit():
    from behave.sim.symbolic import ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    body = ir._sequence((
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)), ir.Mark("target"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)), ir.Mark("target"), ir.Skip())))
    proc = ir.Procedure((), ("a",), (), (), body,
        initial={}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    query, request = _ir_prefix(proc, (2, 2), mutable=False)
    answer = query.probe(request, 20_000)
    assert answer.outcome.value == "sat", answer.as_dict()
    # Fixed inputs are propagated during construction; the later occurrence
    # may now be found before constructing the complete run.
    assert answer.candidate == [{"a": 2}, {"a": 2}]


def test_joint_query_reuses_completed_formula_registration_across_inputs(monkeypatch):
    import z3
    from behave.sim.symbolic.exact_formula import ExactFormula, _FormulaConstruction
    from behave.sim.symbolic import ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    # Every occurrence is symbolically nontrivial but impossible over integers.
    # A local UNSAT must continue to later inputs without rebuilding earlier DAGs.
    body = ir.If(ir.Op('eq', ir.Op('mul', ir.Var('a'), ir.Var('a')), ir.Lit(3)),
                 ir.Mark('target'), ir.Skip())
    proc = ir.Procedure((), ('a',), (), (), body, initial={},
        signature=ProcedureSignature((('a', FiniteType(2)),), ()))
    query, request = _ir_prefix(proc, (0, 0, 0, 0))
    initialize, advance = _FormulaConstruction.__init__, _FormulaConstruction.advance
    owners, products = [], []

    def begin(owner, definitions, restrictions, boundary_atoms, *, prefix=None):
        owners.append((owner, prefix, tuple(definitions), tuple(restrictions), tuple(boundary_atoms)))
        initialize(owner, definitions, restrictions, boundary_atoms, prefix=prefix)

    def finish(owner, checkpoint):
        result = advance(owner, checkpoint)
        if not any(old is result for old in products):
            products.append(result)
        return result

    monkeypatch.setattr(_FormulaConstruction, '__init__', begin)
    monkeypatch.setattr(_FormulaConstruction, 'advance', finish)
    result = query.probe(request, 20_000)
    assert result.outcome.value == 'local-unsat', result.as_dict()
    # The completed-run fallback registers its final definitions too, but now
    # extends the same owner instead of restarting after all four occurrences.
    assert len(products) == 5
    assert [len(product.boundary_atoms) for product in products] == [1, 2, 3, 4, 4]
    assert query._construction.formula is products[-1]
    for index, (owner, prefix, definitions, restrictions, boundary) in enumerate(owners):
        assert prefix is (products[index - 1] if index else None)
        if prefix is not None:
            assert all(a is b for a, b in zip(prefix.definitions, definitions))
            assert prefix._boundary_ids <= owner.result._boundary_ids
        cold = ExactFormula._from_generated_qfbv(
            definitions=definitions, restrictions=restrictions, boundary_atoms=boundary)
        assert owner.result.sliced_sat_extends == cold.sliced_sat_extends
        assert all(z3.eq(a, b) for a, b in zip(owner.result.full_constraints,
                                             cold.full_constraints, strict=True))


def test_prefix_occurrence_preserves_prior_transaction_failure():
    from behave.sim.symbolic import ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure((), ("a",), (), (),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
              ir.Fail("Stopped", "failed"), ir.Mark("target")),
        initial={}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    query, request = _ir_prefix(proc, (0, 1), mutable=False)
    answer = query.probe(request, 20_000)
    assert answer.outcome.value == "local-unsat", answer.as_dict()
    assert answer.candidate is None


def test_fixed_input_construction_is_not_reused_for_a_wider_query():
    import z3
    from behave.sim.symbolic import ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure((), ("a",), (), (),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)), ir.Mark("target"), ir.Skip()),
        initial={}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    query, narrow = _ir_prefix(proc, (0, 0), mutable=False)
    answer = query.probe(narrow, 20_000)
    assert answer.outcome.value == "local-unsat"
    assert all(z3.is_bv_value(enc.pre["a"]) for enc in query.run.encodings)
    _other, broad = _ir_prefix(proc, (0, 0))
    answer = query.probe(broad, 20_000)
    assert answer.outcome.value == "sat", answer.as_dict()
    assert any(row["a"] == 1 for row in answer.candidate)


def test_prefix_solver_timeout_is_not_reported_as_construction(monkeypatch):
    import z3
    from behave.sim.symbolic import ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType
    from behave.sim.engines.behavior import prefix_generation

    proc = ir.Procedure((), ("a",), (), (),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)), ir.Mark("target"), ir.Skip()),
        initial={}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    query, request = _ir_prefix(proc, (0, 0))
    clock = [0.0]
    monkeypatch.setattr(prefix_generation.time, "monotonic", lambda: clock[0])

    def expire(_solver, *assumptions):
        clock[0] = 2.0
        return z3.unknown

    monkeypatch.setattr(z3.Solver, "check", expire)
    answer = query.probe(request, 1000)
    assert answer.outcome.value == "unknown", answer.as_dict()
    assert answer.detail == "input-prefix solver budget exhausted"
    assert answer.candidate is None


def test_early_unknown_leaves_budget_for_later_occurrences(monkeypatch):
    import z3
    from behave.sim.symbolic import ir
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType
    from behave.sim.engines.behavior import prefix_generation

    proc = ir.Procedure((), ("a",), (), (),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)), ir.Mark("target"), ir.Skip()),
        initial={}, signature=ProcedureSignature((("a", FiniteType(2)),), ()))
    query, request = _ir_prefix(proc, (0, 0, 0, 0))
    monkeypatch.setattr(prefix_generation.time, "monotonic", lambda: 0.0)
    original_set, original_check = z3.Solver.set, z3.Solver.check
    budgets, calls = [], []

    def record(solver, *args, **kwargs):
        if "timeout" in kwargs:
            budgets.append(kwargs["timeout"])
        return original_set(solver, *args, **kwargs)

    def first_unknown(solver, *assumptions):
        calls.append(True)
        return z3.unknown if len(calls) == 1 else original_check(solver, *assumptions)

    monkeypatch.setattr(z3.Solver, "set", record)
    monkeypatch.setattr(z3.Solver, "check", first_unknown)
    answer = query.probe(request, 1000)
    assert budgets[:2] == [200, 250]  # Four steps plus fallback, then three plus fallback.
    assert answer.outcome.value == "sat", answer.as_dict()
    assert answer.candidate[1] == {"a": 1}


@pytest.mark.parametrize("upper", [3, (1 << 30) - 1])
def test_nonlinear_dispatch_partition_keeps_the_entire_input_domain(upper):
    import z3
    from behave.sim.engines.behavior.prefix_generation import _dispatch_partitions

    command, data = z3.BitVecs("command data", 32)
    domain = z3.And(command >= 0, command <= upper)
    literal = lambda value: z3.BitVecVal(value, 32)
    clauses = (domain, data * data == z3.If(command == 0, literal(0),
               z3.If(command == 1, literal(1), z3.If(command == 3, literal(4), literal(9)))))
    cases = _dispatch_partitions(clauses, ((command, (0, upper)),), lambda: None)
    assert len(cases) == 4
    solver = z3.Solver()
    solver.add(domain, z3.Not(z3.Or(*cases)))
    assert solver.check() == z3.unsat
    for index, left in enumerate(cases):
        for right in cases[index + 1:]:
            solver = z3.Solver()
            solver.add(domain, left, right)
            assert solver.check() == z3.unsat


def test_linear_prefix_does_not_need_dispatch_partitioning():
    import z3
    from behave.sim.engines.behavior.prefix_generation import _dispatch_partitions

    value = z3.BitVec("value", 9)
    clauses = (value >= 0, value <= 255, z3.If(value == 1, value + 2, value - 1) == 7)
    assert not _dispatch_partitions(clauses, ((value, (0, 255)),), lambda: None)


def test_partitioned_solver_restores_original_input_and_ssa_model():
    import time
    import z3
    from behave.sim.engines.behavior.prefix_generation import _solve_prefix

    command, data, alias = z3.BitVecs("command data alias", 9)
    clauses = (command >= 0, command <= 255, data >= 0, data <= 10,
               alias == data, alias * alias == 49,
               z3.If(command == 0, 0, z3.If(command == 1, 1, command)) == 42)
    answer, model = _solve_prefix(clauses, ((command, (0, 255)), (data, (0, 10))),
                                  time.monotonic() + 20, lambda: None)
    assert answer == z3.sat
    assert all(z3.is_true(model.eval(term, model_completion=True)) for term in clauses)
    assert model.eval(command).as_long() == 42
    assert model.eval(data).as_long() == model.eval(alias).as_long() == 7


@pytest.mark.parametrize("answers,expected", [
    (("unsat", "unsat", "unsat"), "unsat"),
    (("unknown", "unsat", "unsat"), "unknown"),
    (("unknown", "sat"), "sat"),
])
def test_partitioned_solver_does_not_turn_one_unknown_into_unsat(monkeypatch, answers, expected):
    import time
    import z3
    from behave.sim.engines.behavior import prefix_generation as prefix

    command, data = z3.BitVecs("command data", 9)
    clauses = (data * data == z3.If(command == 0, z3.BitVecVal(0, 9),
                                  z3.If(command == 1, z3.BitVecVal(1, 9), z3.BitVecVal(4, 9))),)
    pending, calls, witness = iter(answers), [], object()

    class Solver:
        def add(self, *assertions):
            calls.append(assertions[-1])
        def set(self, **kwargs):
            assert kwargs["timeout"] > 0
        def check(self):
            return getattr(z3, next(pending))
        def model(self):
            return witness

    class Pipeline:
        def solver(self):
            return Solver()

    monkeypatch.setattr(z3, "Then", lambda *stages: Pipeline())
    answer, model = prefix._solve_prefix(clauses, ((command, (0, 255)),),
                                          time.monotonic() + 20, lambda: None)
    assert str(answer) == expected and len(calls) == len(answers)
    assert model is (witness if expected == "sat" else None)
