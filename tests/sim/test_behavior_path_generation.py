"""Path queries are exact witness subsets, never whole-domain UNSAT proofs."""

import itertools
from types import SimpleNamespace

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType
from behave.sim.engines.behavior.path_generation import project
from behave.sim.engines.local_ir import local_hit_any


def model(body, *, graph_form=False):
    proc = ir.Procedure(('q', 'mem'), ('a', 'n'), ('i', 't'), (), body,
        initial={'q': 0, 'mem': [1, 3, 5, 7]},
        types={'mem': types.Array(4), 'i': types.INTEGER, 't': types.INTEGER},
        signature=ProcedureSignature((('a', FiniteType(3)), ('n', FiniteType(2))), ()))
    if graph_form:
        from behave.sim.symbolic import cfg

        graph = cfg.of_command(proc.body)
        body = cfg.if_convert(graph)
        private = ({c.var for c in ir.walk(body) if isinstance(c, ir.Assign)}
                   - set(proc.types) - set(proc.state) - set(proc.inputs) - set(proc.locals))
        proc = proc.derived(body=body, graph=None, locals=[*proc.locals, *sorted(private)],
                            types={**proc.types, **dict.fromkeys(private, types.BOOLEAN)})
        ir._bind_control_flow(proc, ir._make_control_flow_authority(graph, body))
    env = ranges.fixpoint(proc, {'q': ranges.Range(-16, 31), 'mem': ranges.Range(0, 7),
                                'a': ranges.Range(0, 7), 'n': ranges.Range(0, 3)})
    return proc, env


def branch(condition, name):
    return ir.If(condition, ir.Mark(name), ir.Skip())


BODIES = [
    ir._sequence((
        ir.Assign('q', ir.Op('add', ir.Var('a'), ir.Lit(1))),
        branch(ir.Op('eq', ir.Var('q'), ir.Lit(6)), 'target'))),
    ir._sequence((
        ir.For('i', ir.Var('n'), ir._sequence((
            ir.Assign('n', ir.Lit(0)),  # Entry trip count must stay captured.
            branch(ir.Op('eq', ir.Var('a'), ir.Var('i')), 'target'),
            ir.Assign('q', ir.Op('add', ir.Var('q'), ir.Lit(1)))))),
        branch(ir.Op('eq', ir.Var('q'), ir.Lit(2)), 'after'))),
    ir._sequence((
        ir.Store('mem', ir.Op('and', ir.Var('a'), ir.Lit(3)), ir.Var('n')),
        branch(ir.Op('eq', ir.Sel(ir.Var('mem'), ir.Lit(1)), ir.Lit(2)), 'target'))),
    ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)), ir.Fail('IndexError', 'failure'), ir.Skip()),
        branch(ir.Op('eq', ir.Var('n'), ir.Lit(2)), 'target'))),
    ir.For('i', ir.Var('n'), ir.Mark('entered')),
    ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)), ir.Skip(), ir._sequence((
        ir.Store('mem', ir.Var('n'), ir.Var('a')),
        ir.Assign('q', ir.Sel(ir.Var('mem'), ir.Var('n'))), ir.Mark('target')))),
    ir._sequence((
        ir.If(ir.Op('gt', ir.Var('n'), ir.Lit(0)), ir.Mark('target'), ir.Mark('after')),
        ir.For('i', ir.Var('n'), ir._sequence((
            ir.Assign('q', ir.Var('i')), ir.Store('mem', ir.Var('i'), ir.Var('a')),
            ir.Mark('entered')))))),
    ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)), ir.Skip(), ir._sequence((
        ir.Assign('q', ir.Lit(1)), ir.Fail('IndexError', 'failure'), ir.Mark('target')))),
]


def _specialized_graph(graph, entry):
    from behave.sim.engines.behavior.path_specialization import specialize

    work = specialize(graph, entry)
    while True:
        try:
            next(work)
        except StopIteration as done:
            return done.value


@pytest.mark.parametrize('body', BODIES)
@pytest.mark.parametrize('fixed', [{}, {'a': 2}, {'n': 1}])
def test_search_path_specialization_preserves_state_hits_and_failures(body, fixed):
    from behave.sim.symbolic import cfg
    from behave.sim.symbolic.values import copy_value

    proc, _env = model(body, graph_form=True)
    before = {name: copy_value(value) for name, value in proc.initial.items()}
    graph = _specialized_graph(proc.graph, {**proc.initial, **fixed})
    specialized = cfg.if_convert(graph)
    for a, n in itertools.product(range(8), range(4)):
        inputs = dict(a=a, n=n)
        if any(inputs[key] != value for key, value in fixed.items()):
            continue
        outcomes = []
        for command in (proc.body, specialized):
            machine = interp._Interp({**{name: copy_value(value)
                                         for name, value in proc.initial.items()}, **inputs})
            hits, failure = set(), None
            try:
                machine.run(command, hits)
            except interp.Halt as error:
                failure = error.code
            outcomes.append((failure, hits, {name: machine.rho[name] for name in proc.state}))
        assert outcomes[0] == outcomes[1], (fixed, inputs, outcomes)
    assert proc.initial == before


def test_search_path_specialization_folds_concrete_loop_work_before_conversion():
    from behave.sim.symbolic import cfg

    # A fixed table calculation surrounds one symbolic write. The unknown
    # address must not be mistaken for the address of the original seed.
    operations = [ir.Assign('t', ir.Lit(0))]
    for i in range(32):
        operations.extend((
            ir.Assign('t', ir.Op('add', ir.Var('t'), ir.Sel(ir.Var('mem'), ir.Lit(i % 4)))),
            ir.If(ir.Op('lt', ir.Var('t'), ir.Lit(1000)), ir.Skip(), ir.Fail('bad', 'failure'))))
    operations.extend((
        ir.Store('mem', ir.Op('and', ir.Var('a'), ir.Lit(3)), ir.Var('n')),
        ir.If(ir.Op('eq', ir.Sel(ir.Var('mem'), ir.Lit(2)), ir.Lit(6)),
              ir.Mark('target'), ir.Skip())))
    proc, _env = model(ir._sequence(operations), graph_form=True)
    graph = _specialized_graph(proc.graph, proc.initial)
    assert sum(isinstance(graph[label].term, cfg.Branch) for label in graph.order()) == 1
    assert 'target' in ir.goals_of(cfg.if_convert(graph))


@pytest.mark.parametrize('graph_form', (False, True))
@pytest.mark.parametrize('count', (0, 1, 2))
def test_context_extends_loop_before_replacing_an_already_reached_address(graph_form, count):
    from behave.sim.engines.behavior.path_generation import PathQuery

    body = ir._sequence((
        ir.For('i', ir.Var('n'), ir._sequence((
            ir.Assign('q', ir.Op('add', ir.Var('q'), ir.Lit(1))),
            ir.Mark('target')))),
        ir.Assign('t', ir.Sel(ir.Var('mem'), ir.Var('a')))))
    proc, env = model(body, graph_form=graph_form)
    seed = {'a': 0, 'n': count}
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': seed, 'state': proc.initial}, ('target',),
        validation=encoder._procedure_validation(proc), fixed_inputs={},
        input_constraints=None, context=True)
    answer = query.probe(('target',), 20_000)
    assert answer.outcome.value == 'proposed' and answer.goal is None, answer.detail
    assert answer.candidate['n'] > count
    state, _outputs, _hits, failure = interp.run_step(proc, answer.candidate)
    assert not failure and state['q'] > count


def test_search_path_specialization_preserves_array_copies_and_lazy_terms():
    from behave.sim.symbolic import cfg
    from behave.sim.symbolic.values import copy_value

    body = ir._sequence((
        ir.Assign('copy', ir.Var('mem')),
        ir.Store('mem', ir.Lit(1), ir.Lit(8)),
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)),
              ir.Store('mem', ir.Lit(0), ir.Lit(9)), ir.Skip()),
        ir.Assign('q', ir.Op('ite', ir.Lit(True), ir.Sel(ir.Var('copy'), ir.Lit(1)),
                           ir.Var('unbound'))),
        ir.Assign('q', ir.Op('add', ir.Var('q'), ir.Sel(ir.Var('mem'), ir.Lit(0))))))
    initial = {'mem': [1, 3, 5, 7], 'q': 0}
    graph = cfg.of_command(body)
    specialized = cfg.if_convert(_specialized_graph(graph, initial))
    for a in range(3):
        outcomes = []
        for command in (body, specialized):
            machine = interp._Interp({**{name: copy_value(value)
                                         for name, value in initial.items()}, 'a': a})
            machine.run(command, set())
            outcomes.append((machine.rho['q'], machine.rho['mem'], machine.rho['copy']))
        assert outcomes[0] == outcomes[1]
    assert initial == {'mem': [1, 3, 5, 7], 'q': 0}


@pytest.mark.parametrize('body', BODIES)
@pytest.mark.parametrize('graph_form', [False, True])
def test_path_hits_are_subset_of_original_for_every_input(body, graph_form):
    proc, _env = model(body, graph_form=graph_form)
    validation = encoder._procedure_validation(proc)
    targets = {'target', 'after', 'failure', 'entered'}
    all_inputs = [dict(a=a, n=n) for a, n in itertools.product(range(8), range(4))]
    original = [interp.run_step(proc, values, proc.initial)[2] for values in all_inputs]
    projections = 0
    for seed in all_inputs:
        path = project(proc, seed, proc.initial, targets, validation=validation)
        if path is None:
            continue
        projections += 1
        assert not path.procedure.boundary_complete
        for values, expected in zip(all_inputs, original, strict=True):
            got = interp.run_step(path.procedure, values, proc.initial)[2]
            assert got <= expected, (seed, values, got, expected)
    assert projections


@pytest.mark.parametrize('body', BODIES)
@pytest.mark.parametrize('graph_form', [False, True])
def test_context_state_preparation_preserves_normal_state_transitions(body, graph_form):
    from behave.sim.engines.behavior.path_generation import _state_preparation
    from behave.sim.symbolic.values import copy_value

    proc, _env = model(body, graph_form=graph_form)
    setup, _validation = _state_preparation(proc, encoder._procedure_validation(proc))
    assert not setup.boundary_complete
    if proc.graph is not None:
        assert setup.graph is not None  # State preparation must preserve source joins.
    for a, n in itertools.product(range(8), range(4)):
        native = interp._Interp({**{k: copy_value(v) for k, v in proc.initial.items()}, 'a': a, 'n': n})
        try:
            native.run(proc.body, set())
        except interp.Halt:
            continue  # A failure-erased proposal is not source acceptance.
        projected = interp._Interp({**{k: copy_value(v) for k, v in proc.initial.items()}, 'a': a, 'n': n})
        hits = set()
        projected.run(setup.body, hits)
        assert not hits
        assert {name: projected.rho[name] for name in proc.state} == {
            name: native.rho[name] for name in proc.state}


@pytest.mark.parametrize('body', BODIES)
def test_preparation_predicates_cannot_invent_real_hits_for_any_fixture_input(body):
    from behave.sim.engines.behavior.path_generation import _ControlFrontiers, _project

    proc, _env = model(body)
    validation = encoder._procedure_validation(proc)
    targets = {'target', 'after', 'failure', 'entered'}
    controls = _ControlFrontiers(proc, validation.graph, targets)
    values = [dict(a=a, n=n) for a, n in itertools.product(range(8), range(4))]
    expected = [interp.run_step(proc, row, proc.initial)[2] for row in values]
    for seed in values:
        work = _project(proc, seed, proc.initial, targets, validation=validation, controls=controls)
        while True:
            try:
                next(work)
            except StopIteration as done:
                path = done.value
                break
        if path is None:
            continue
        private = {marker for marker, _goal in path.preparations}
        assert len(private) <= len(targets) and not private & targets
        assert path.targets <= targets
        for row, hits in zip(values, expected, strict=True):
            observed = interp.run_step(path.procedure, row, proc.initial)[2]
            assert observed - private <= hits, (seed, row, observed, hits)


def test_retained_region_requires_definite_new_locals():
    from behave.sim.engines.behavior.path_generation import _bindings_available

    one_arm = ir.If(ir.Var('a'), ir.Assign('t', ir.Lit(1)), ir.Skip())
    assert not _bindings_available((one_arm,), {'a'})
    assert _bindings_available((one_arm,), {'a', 't'})
    both_arms = ir.If(ir.Var('a'), ir.Assign('t', ir.Lit(1)), ir.Assign('t', ir.Lit(2)))
    assert _bindings_available((both_arms,), {'a'})
    assert _bindings_available((ir.Seq(ir.Assign('t', ir.Lit(0)), one_arm),), {'a'})


def test_complete_setup_keeps_conditional_definitions_visible_to_the_cfg():
    from behave.sim.symbolic import ssa
    from behave.sim.engines.behavior.path_generation import _project

    body = ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)),
              ir.Assign('t', ir.Lit(1)), ir.Assign('t', ir.Lit(2))),
        ir.Assign('q', ir.Var('t')),
        branch(ir.Op('eq', ir.Var('q'), ir.Lit(2)), 'target')))
    proc, env = model(body)
    work = _project(proc, {'a': 0, 'n': 0}, proc.initial, ('target',),
                    validation=encoder._procedure_validation(proc), complete=True)
    while True:
        try:
            next(work)
        except StopIteration as done:
            path = done.value
            break
    graph = path.procedure.graph
    assert all(not isinstance(command, (ir.If, ir.Seq))
               for block in graph.blocks.values() for command in block.instrs)
    assert not ssa.undefined_uses(graph, set(proc.state) | set(proc.inputs))
    for a in range(8):
        values = {'a': a, 'n': 0}
        assert interp.run_step(path.procedure, values, proc.initial)[0] == (
            interp.run_step(proc, values, proc.initial)[0])
    answer = local_hit_any(path.procedure, env, proc.initial, ('target',), timeout_ms=10_000)
    assert answer.outcome.value == 'sat', answer
    assert 'target' in interp.run_step(proc, answer.candidate, proc.initial)[2]


def test_context_setup_does_not_retain_unbound_locals_from_an_unentered_loop():
    from behave.sim.engines.behavior.path_generation import WindowPathQuery
    from .test_behavior_prefix_generation import _selected, _trace
    from behave.sim.engines.behavior import generation_job as jobs

    source = b'''class M:
    def reset(self): self.q = 0; self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a == 0: self.q = b & 7
        elif a == 1:
            values = [0] * 4
            for i in range(4):
                values[i] = self.mem[i] + b
            cursor = b & 3
            while cursor > 0:
                self.q = values[cursor]
                cursor -= 1
            self.q += values[cursor]
        elif a == 2 and self.q == 3:
            y = 1
        return {'y': y}
'''
    built, seed, goal, selected = _selected(2, source=source, commands=[0, 2])
    window = tuple(selected.occurrences.values())
    query = WindowPathQuery(built[4], window, (goal,), context=True,
        validation=selected._query_preparation.validation(),
        mutable_fields=[field for item in window for name, field in item['bindings'] if name == 'b'],
        input_domain=selected.input_domain)
    answer = query.probe((goal,), 20_000)
    from behave.sim.symbolic import slicing
    indexed = slicing.index(query.projection.procedure)
    assert not indexed.binding_faults(frozenset(id(cmd) for cmd, _under in indexed.events))
    assert answer.outcome.value == 'proposed', answer.as_dict()
    candidate = jobs._candidate_many(seed, zip(window, answer.candidate, strict=True))
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in confirmed.replay.hits


def test_projected_query_solves_and_original_execution_confirms():
    proc, env = model(BODIES[0])
    path = project(proc, {'a': 1, 'n': 0}, proc.initial, {'target'},
                   validation=encoder._procedure_validation(proc))
    result = local_hit_any(path.procedure, env, proc.initial, ('target',), timeout_ms=10_000)
    assert result.outcome.value == 'sat', result
    assert 'target' in interp.run_step(proc, result.candidate, proc.initial)[2]


def test_a_path_miss_is_not_a_proof_about_other_paths():
    body = ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)), ir.Skip(),
        ir._sequence((ir.Assign('q', ir.Lit(1)),
                      branch(ir.Op('eq', ir.Var('n'), ir.Lit(0)), 'target'))))
    proc, _env = model(body)
    path = project(proc, {'a': 0, 'n': 0}, proc.initial, {'target'},
                   validation=encoder._procedure_validation(proc))
    assert path is None  # The unobserved next decision needs another step.
    assert 'target' in interp.run_step(proc, {'a': 1, 'n': 0}, proc.initial)[2]


@pytest.mark.parametrize('array_update', [False, True])
def test_joint_branch_updates_do_not_pin_the_seed_choice(array_update):
    from behave.sim.engines.behavior.path_generation import PathQuery

    read = ir.Sel(ir.Var('mem'), ir.Lit(1)) if array_update else ir.Var('q')
    def update(value):
        return (ir.Store('mem', ir.Lit(1), ir.Lit(value)) if array_update else
                ir.Assign('q', ir.Lit(value)))
    proc, env = model(ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(7)), update(3), update(1)),
        branch(ir.Op('eq', read, ir.Lit(3)), 'target'),
        branch(ir.Op('eq', ir.Var('n'), ir.Lit(2)), 'second'))), graph_form=True)
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 0}, 'state': proc.initial}, ('target', 'second'),
        validation=encoder._procedure_validation(proc), fixed_inputs={}, input_constraints=None)
    answer = query.probe(('target',), 10_000)
    assert answer.outcome.value == 'sat', answer
    assert 'target' in interp.run_step(proc, answer.candidate, proc.initial)[2]
    assert query.projection.targets == frozenset(('target', 'second'))
    projection = query.projection
    other = query.probe(('second',), 10_000)
    assert query.projection is projection  # One source construction, two goals.
    assert other.outcome.value == 'sat'
    assert 'second' in interp.run_step(proc, other.candidate, proc.initial)[2]


@pytest.mark.parametrize('depth', [8, 64, 512])
def test_unmergeable_regions_are_not_rescanned_quadratically(monkeypatch, depth):
    from behave.sim.engines.behavior import path_generation as paths

    proc, _env = model(ir._sequence((
        *(ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(i % 8)),
                ir.Assign('q', ir.Lit(i % 8)), ir.Skip()) for i in range(depth)),
        branch(ir.Op('eq', ir.Var('n'), ir.Lit(3)), 'target'))), graph_form=True)
    visits = []
    original = paths._LoopFreeRegions.region
    def counted(owner, graph, label):
        region = original(owner, graph, label)
        visits.append(0 if region is None else len(region.labels))
        return region
    monkeypatch.setattr(paths._LoopFreeRegions, 'region', counted)
    monkeypatch.setattr(paths, '_bindings_available', lambda *args: False)
    path = project(proc, {'a': 0, 'n': 0}, proc.initial, {'target'},
                   validation=encoder._procedure_validation(proc))
    assert path is not None
    assert sum(visits) <= len(proc.graph.blocks)
    assert 'target' in interp.run_step(path.procedure, {'a': 0, 'n': 3}, proc.initial)[2]


def test_branch_regions_stop_before_variable_loops_without_claiming_unreachable():
    proc, _env = model(ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(7)), ir.Assign('q', ir.Lit(1)), ir.Skip()),
        ir.For('i', ir.Var('n'), ir._sequence((
            branch(ir.Op('eq', ir.Var('i'), ir.Lit(2)), 'deep'),
            ir.Assign('q', ir.Op('add', ir.Var('q'), ir.Lit(1)))))),
        branch(ir.Op('eq', ir.Var('q'), ir.Lit(3)), 'after'))))
    path = project(proc, {'a': 0, 'n': 1}, proc.initial, {'deep', 'after'},
                   validation=encoder._procedure_validation(proc))
    assert path is not None
    assert 'deep' not in interp.run_step(path.procedure, {'a': 0, 'n': 3}, proc.initial)[2]
    assert 'deep' in interp.run_step(proc, {'a': 0, 'n': 3}, proc.initial)[2]
    for a, n in itertools.product(range(8), range(4)):
        values = {'a': a, 'n': n}
        assert interp.run_step(path.procedure, values, proc.initial)[2] <= \
            interp.run_step(proc, values, proc.initial)[2]


def test_projection_keeps_original_source_and_input_state_unchanged():
    proc, _env = model(BODIES[2])
    body, inputs, state = proc.body, {'a': 1, 'n': 0}, {'q': 0, 'mem': [1, 3, 5, 7]}
    project(proc, inputs, state, {'target'}, validation=encoder._procedure_validation(proc))
    assert proc.body is body
    assert inputs == {'a': 1, 'n': 0}
    assert state == proc.initial == {'q': 0, 'mem': [1, 3, 5, 7]}


@pytest.mark.parametrize('body', [
    '        for i in range(b & 3):\n            if a == i + 37:\n                y = 1\n',
    '        for i in range(b & 3):\n            if i == 2:\n                break\n            if a == i + 37:\n                y = 1\n',
    '        for i in range(b & 3):\n            for j in range(2):\n                if a == i + j + 37:\n                    y = 1\n',
    '        i = 0\n        while i < (b & 3):\n            if a == i + 37:\n                y = 1\n            i += 1\n',
])
def test_source_cfg_and_retained_loops_share_the_same_path_rule(body):
    from .test_behavior_prefix_generation import _selected

    source = ('''class M:
    def reset(self): self.q = 0
    def process(self, a, b):
        y = 0
''' + body + '        return {"y": y}\n').encode()
    built, _seed, _goal, selected = _selected(4, source=source)
    proc = built[4].proc
    assert proc.graph is not None
    values = selected.occurrences[next(iter(selected.occurrences))]['input']
    values = {**values, 'a': 0, 'b': 3}
    targets = set(built[6]) - interp.run_step(proc, values, proc.initial)[2]
    path = project(proc, values, proc.initial, targets, validation=encoder._procedure_validation(proc))
    assert path is not None
    result = local_hit_any(path.procedure, built[4].env, proc.initial,
                          tuple(sorted(path.targets)), timeout_ms=20_000)
    assert result.outcome.value == 'sat', result
    assert result.goal in interp.run_step(proc, result.candidate, proc.initial)[2]
    for a, b in itertools.product(range(36, 41), range(4)):
        inputs = {'a': a, 'b': b}
        assert interp.run_step(path.procedure, inputs, proc.initial)[2] <= \
            interp.run_step(proc, inputs, proc.initial)[2]


@pytest.mark.parametrize('depth', [8, 64, 512])
def test_path_representation_grows_linearly_without_nested_decision_depth(depth):
    proc, _env = model(ir.For('i', ir.Lit(depth),
        branch(ir.Op('eq', ir.Var('a'), ir.Lit(7)), 'target')))
    path = project(proc, {'a': 0, 'n': 0}, proc.initial, {'target'},
                   validation=encoder._procedure_validation(proc))
    assert path is not None
    assert path.commands <= 5 * depth
    assert sum(1 for _ in ir.walk(path.procedure.body)) < 100 * depth
    assert path.procedure.graph is not None


def test_path_build_respects_the_callers_deadline():
    proc, _env = model(BODIES[0])
    def stop():
        raise TimeoutError('caller deadline')
    with pytest.raises(TimeoutError, match='caller deadline'):
        project(proc, {'a': 0, 'n': 0}, proc.initial, {'target'},
                validation=encoder._procedure_validation(proc), checkpoint=stop)


@pytest.mark.parametrize('body', [
    '        if b == 3:\n            self.q = a + 1\n            y = 1\n',
    '        for i in range(b & 3):\n            y = 1\n',
])
def test_path_can_enter_an_unexecuted_straight_block_with_original_bindings(body):
    from .test_behavior_prefix_generation import _selected

    source = ('''class M:
    def reset(self): self.q = 0
    def process(self, a, b):
        y = 0
''' + body + '        return {"y": y}\n').encode()
    built, _seed, goal, _selected_trace = _selected(2, source=source)
    proc = built[4].proc
    assert goal not in interp.run_step(proc, {'a': 0, 'b': 0}, proc.initial)[2]
    path = project(proc, {'a': 0, 'b': 0}, proc.initial, {goal},
                   validation=encoder._procedure_validation(proc))
    assert path is not None
    assert any(goal in interp.run_step(path.procedure, {'a': 0, 'b': b}, proc.initial)[2]
               for b in range(1, 4))
    answer = local_hit_any(path.procedure, built[4].env, proc.initial, (goal,), timeout_ms=10_000)
    assert answer.outcome.value == 'sat', answer
    assert goal in interp.run_step(proc, answer.candidate, proc.initial)[2]
    for a, b in itertools.product(range(4), range(4)):
        values = {'a': a, 'b': b}
        assert interp.run_step(path.procedure, values, proc.initial)[2] <= \
            interp.run_step(proc, values, proc.initial)[2]


def test_path_construction_resumes_and_can_narrow_but_not_widen(monkeypatch):
    from behave.sim.engines.behavior import path_generation as paths

    proc, env = model(BODIES[0])
    query = paths.PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 0}, 'state': proc.initial}, {'target', 'absent'},
        validation=encoder._procedure_validation(proc), fixed_inputs={}, input_constraints=None)
    work = query.work
    with monkeypatch.context() as patch:
        ticks = iter((0., 0., 1., 1.))
        patch.setattr(paths, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
        paused = query.probe({'target', 'absent'}, 100)
    assert paused.outcome.value == 'constructing' and paused.scope.value == 'path-prefix'
    assert query.work is work
    answer = query.probe({'target'}, 10_000)
    assert answer.outcome.value == 'sat' and answer.scope.value == 'path-prefix'
    assert 'target' in interp.run_step(proc, answer.candidate, proc.initial)[2]
    with pytest.raises(ValueError, match='only lose targets'):
        query.probe({'other'}, 10_000)


@pytest.mark.parametrize('restriction', ['fixed', 'framing'])
def test_path_query_preserves_fixed_fields_and_input_constraints(restriction):
    from behave.sim.engines.behavior.path_generation import PathQuery

    proc, env = model(BODIES[0])
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 1, 'n': 0}, 'state': proc.initial}, {'target'},
        validation=encoder._procedure_validation(proc),
        fixed_inputs={'a': 1} if restriction == 'fixed' else {},
        input_constraints=(lambda inputs: (inputs['a'] != 5,)) if restriction == 'framing' else None)
    answer = query.probe({'target'}, 10_000)
    assert answer.outcome.value == 'local-unsat' and answer.scope.value == 'path-prefix'


def test_speculative_path_can_only_propose_even_when_its_model_misses_in_replay():
    from behave.sim.engines.behavior.path_generation import PathQuery

    body = ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)),
              ir.Assign('q', ir.Lit(1)), ir.Skip()),
        branch(ir.Op('land', ir.Op('eq', ir.Var('q'), ir.Lit(1)),
                     ir.Op('eq', ir.Var('a'), ir.Lit(1))), 'target')))
    proc, env = model(body)
    instance = SimpleNamespace(proc=proc, env=env)
    occurrence = {'input': {'a': 0, 'n': 0}, 'state': proc.initial}
    options = dict(validation=encoder._procedure_validation(proc),
                   fixed_inputs={'n': 0}, input_constraints=None)
    strict = PathQuery(instance, occurrence, ('target',), **options).probe(('target',), 10_000)
    assert strict.outcome.value == 'local-unsat'
    query = PathQuery(instance, occurrence, ('target',), proposal=True, **options)
    answer = query.probe(('target',), 10_000)
    assert answer.outcome.value == 'proposed' and answer.scope.value == 'state-preparation'
    assert answer.goal is None
    assert answer.candidate['a'] == 1
    assert 'target' not in interp.run_step(proc, answer.candidate, proc.initial)[2]
    assert not query.projection.procedure.boundary_complete
    assert query.projection.state is None and query.projection.normal_exit is None


@pytest.mark.parametrize('restriction', ['fixed', 'framing'])
def test_speculative_path_preserves_domains_without_claiming_unsat(restriction):
    from behave.sim.engines.behavior.path_generation import PathQuery

    proc, env = model(BODIES[0])
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 1, 'n': 0}, 'state': proc.initial}, {'target'},
        validation=encoder._procedure_validation(proc), proposal=True,
        fixed_inputs={'a': 1} if restriction == 'fixed' else {},
        input_constraints=(lambda inputs: (inputs['a'] != 5,)) if restriction == 'framing' else None)
    answer = query.probe({'target'}, 10_000)
    assert answer.outcome.value == 'unknown' and answer.scope.value == 'state-preparation'
    assert answer.candidate is None and answer.goal is None


def test_speculative_path_model_can_supply_a_real_replayable_candidate():
    from behave.sim.engines.behavior.path_generation import PathQuery

    proc, env = model(BODIES[0])
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 0}, 'state': proc.initial}, {'target'},
        validation=encoder._procedure_validation(proc), proposal=True,
        fixed_inputs={}, input_constraints=None)
    answer = query.probe({'target'}, 10_000)
    assert answer.outcome.value == 'proposed' and answer.goal is None
    assert 'target' in interp.run_step(proc, answer.candidate, proc.initial)[2]


SOURCE = b'''class M:
    def reset(self): self.q = 0
    def process(self, a, b):
        y = 0
        if a == 37:
            y = 1
        return {"y": y}
'''


def test_selected_path_has_native_witness_and_keeps_complete_fallback():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    built, seed, goal, selected = _selected(2, source=SOURCE)
    path = next(r for r in selected.requests if r.occurrence.startswith('native-path:')
                and len(r.mutable_fields) == 2)
    full = next(r for r in selected.requests if r.occurrence == path.occurrence.removeprefix('native-path:')
                and r.mutable_fields == path.mutable_fields)
    assert any(path.identity in group and full.identity in group for group in selected.query_chains)
    answer = selected.probe(path.as_dict(), 10_000)
    assert answer['outcome'] == 'sat' and answer['scope'] == 'path-prefix'
    decoded = jobs.decode_probe_reply(answer, expected_catalogue_digest=selected.manifest.digest())
    assert decoded.scope.value == 'path-prefix'
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    replay = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in replay.replay.hits
    assert not selected._unsat_questions


def test_selected_speculative_request_keeps_both_strict_queries_and_replay_gate():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    built, seed, goal, selected = _selected(2, source=SOURCE)
    proposal = next(r for r in selected.requests if r.occurrence.startswith('path-proposal:')
                    and len(r.mutable_fields) == 2)
    identity = proposal.occurrence.removeprefix('path-proposal:')
    strict = [next(r for r in selected.requests if r.occurrence == prefix + identity
                   and r.mutable_fields == proposal.mutable_fields)
              for prefix in ('native-path:', '')]
    assert proposal.rank < strict[0].rank < strict[1].rank
    assert any(all(r.identity in group for r in (proposal, *strict))
               for group in selected.query_chains)
    assert all(proposal.identity not in group for group in selected.equivalent_requests)
    answer = selected.probe(proposal.as_dict(), 10_000)
    decoded = jobs.decode_probe_reply(answer, expected_catalogue_digest=selected.manifest.digest())
    assert decoded.outcome.value == 'proposed' and decoded.goal is None
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    replay = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in replay.replay.hits
    assert replay.manifest.digest() == selected.manifest.digest()
    assert not selected._unsat_questions
    # The proposal cannot replace or certify the complete local question.
    full = selected.probe(strict[1].as_dict(), 10_000)
    assert full['outcome'] == 'sat' and full['scope'] == 'exact-local-hit'


def test_speculative_construction_pause_has_no_goal_or_negative_verdict(monkeypatch):
    from behave.sim.engines.behavior import path_generation as paths

    proc, env = model(BODIES[0])
    query = paths.PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 0}, 'state': proc.initial}, {'target'},
        validation=encoder._procedure_validation(proc), proposal=True,
        fixed_inputs={}, input_constraints=None)
    work = query.work
    with monkeypatch.context() as patch:
        ticks = iter((0., 0., 1., 1.))
        patch.setattr(paths, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
        paused = query.probe({'target'}, 100)
    assert paused.outcome.value == 'constructing' and paused.scope.value == 'state-preparation'
    assert paused.goal is None and paused.candidate is None and query.work is work
    assert query.probe({'target'}, 10_000).outcome.value == 'proposed'


def test_path_preparation_does_not_skip_a_satisfiable_complete_question():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    source = SOURCE.replace(b'        if a == 37:\n            y = 1',
        b'        if a == 2:\n            if b == 37:\n                y = 1')
    built, seed, goal, selected = _selected(2, source=source)
    path = next(r for r in selected.requests if r.occurrence.startswith('native-path:')
                and len(r.mutable_fields) == 2)
    answer = selected.probe(path.as_dict(), 10_000)
    assert answer['outcome'] == 'sat' and answer['scope'] == 'path-prefix'
    assert answer['goal'] == goal
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    replay = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert replay.manifest.digest() == selected.manifest.digest()
    assert not any(g.startswith('_search_preparation:') for g in replay.replay.hits)
    # Both correlated decisions are solved in one source region. Native replay
    # still has to confirm the goal, and the full-source fallback remains.
    assert goal in replay.replay.hits
    assert any(row == (2,) for row in dict(candidate.queues)['left'])
    assert not selected._unsat_questions
    full = next(r for r in selected.requests if r.occurrence == path.occurrence.removeprefix('native-path:')
                and r.mutable_fields == path.mutable_fields)
    answer = selected.probe(full.as_dict(), 10_000)
    assert answer['outcome'] == 'sat' and answer['scope'] == 'exact-local-hit'


@pytest.mark.parametrize('seed_count', [0, 1, 7])
@pytest.mark.parametrize('loop', ['for', 'while'])
def test_native_window_preparation_grows_loop_depth_without_complete_unrolling(seed_count, loop):
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    source = b'''class M:
    def reset(self): self.n = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.n = b & 15
        elif a == 1:
            for i in range(self.n):
                if i == 7:
                    y = 1
        return {"y": y}
'''
    if loop == 'while':
        source = source.replace(b'            for i in range(self.n):',
            b'            i = 0\n            while i < self.n:')
        source = source.replace(b'                    y = 1',
            b'                    y = 1\n                i += 1')
    built, original, goal, _selected_trace = _selected(2, source=source, commands=[0, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,)], 'right': [(seed_count,), (0,)]},
                        slots=original.slots)
    counts = [seed_count]
    for _ in range(16):  # Exhaustive finite input domain of this test, not a runtime limit.
        selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
        if goal in selected.replay.hits:
            break
        request = next(r for r in selected.requests
                       if r.occurrence.startswith('native-path:prefix:')
                       and len(r.mutable_fields) == 2)
        answer = selected.probe(request.as_dict(), 20_000)
        assert answer['outcome'] == 'proposed', answer
        assert answer['scope'] == 'state-preparation' and answer['goal'] is None
        decoded = jobs.decode_probe_reply(answer, expected_catalogue_digest=selected.manifest.digest())
        assert decoded.outcome.value == 'proposed'
        seed = api.Stimulus.from_dict(answer['candidate'])
        assert seed.slots == original.slots
        assert dict(seed.queues)['left'] == ((0,), (1,))
        count = dict(seed.queues)['right'][0][0] & 15
        assert count > counts[-1]
        counts.append(count)
    else:
        pytest.fail(f'no native target after increasing loop depth: {counts}')
    assert counts[-1] >= 8
    assert not any(g.startswith('_search_preparation:') for g in selected.replay.hits)


@pytest.mark.parametrize('restriction', ['fixed', 'domain', 'targets'])
def test_control_preparation_respects_input_restrictions_and_narrowed_targets(restriction):
    from behave.sim.engines.behavior.path_generation import PathQuery

    proc, env = model(ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(7)),
        ir._sequence((ir.Assign('q', ir.Lit(1)),
                      branch(ir.Op('eq', ir.Var('n'), ir.Lit(2)), 'target'))), ir.Skip()))
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 0}, 'state': proc.initial}, {'target', 'absent'},
        validation=encoder._procedure_validation(proc),
        fixed_inputs={'a': 0} if restriction == 'fixed' else {},
        input_constraints=(lambda inputs: (inputs['a'] != 7,)) if restriction == 'domain' else None)
    answer = query.probe({'absent'} if restriction == 'targets' else {'target'}, 10_000)
    assert answer.outcome.value == 'local-unsat' and answer.goal is None


def test_path_equivalence_binds_mutable_seed_values_and_never_merges_full_queries():
    from .test_behavior_prefix_generation import _selected

    _built, _seed, _goal, selected = _selected(2, source=SOURCE, commands=[3, 38])
    requests = {r.identity: r for r in selected.requests}
    paths = {r.identity for r in requests.values()
             if r.occurrence.startswith('native-path:') and len(r.mutable_fields) == 2}
    assert len(paths) == 2
    for group in selected.equivalent_requests:
        assert len(paths.intersection(group)) <= 1
        assert len({requests[key].occurrence.startswith('native-path:') for key in group}) == 1


@pytest.mark.parametrize('storage', ['registers', 'indexed', 'dependent_index'])
def test_window_path_changes_preparation_and_confirms_original_execution(storage):
    from behave.sim.engines.behavior.path_generation import WindowPathQuery
    from .test_behavior_prefix_generation import _selected, _trace
    from behave.sim.engines.behavior import generation_job as jobs

    setup, first, second, value = {
        'registers': ('self.left = 0; self.right = 0', 'self.left = b & 7',
                      'self.right = b & 7', 'self.left + self.right'),
        'indexed': ('self.mem = [0] * 4', 'self.mem[0] = b & 7',
                    'self.mem[1] = b & 7', 'self.mem[0] + self.mem[1]'),
        'dependent_index': ('self.mem = [0] * 4; self.index = 0', 'self.index = b & 3',
                            'self.mem[self.index] = b & 15', 'self.mem[self.index]'),
    }[storage]
    source = f'''class M:
    def reset(self): {setup}
    def process(self, a, b):
        y = 0
        if a == 0: {first}
        elif a == 1: {second}
        elif a == 2:
            if {value} == 8:
                y = 1
        return {{"y": y}}
'''.encode()
    built, seed, goal, selected = _selected(3, source=source, commands=[0, 1, 2])
    window = tuple(selected.occurrences.values())
    query = WindowPathQuery(built[4], window, (goal,),
        validation=selected._query_preparation.validation(),
        mutable_fields=[field for item in window for name, field in item['bindings'] if name == 'b'],
        input_domain=selected.input_domain)
    answer = query.probe((goal,), 20_000)
    assert answer.outcome.value == 'sat', answer.as_dict()
    assert answer.scope.value == 'path-prefix'
    assert [row['a'] for row in answer.candidate] == [0, 1, 2]
    changed = jobs._candidate_many(seed, zip(window, answer.candidate, strict=True))
    confirmed = jobs.SelectedTrace(built, changed.as_dict(), _trace(changed).as_dict(), [goal])
    assert goal not in selected.replay.hits and goal in confirmed.replay.hits
    assert changed.slots == seed.slots
    # The new path is a real public work item, not a test-only alternate API.
    request = next(r for r in selected.requests
        if r.occurrence.startswith('native-path:prefix:')
        and len(selected.prefixes[r.occurrence.removeprefix('native-path:')]) == 3
        and len(r.mutable_fields) == 3)
    public = selected.probe(request.as_dict(), 20_000)
    assert public['outcome'] == 'sat', public
    assert public['scope'] == 'path-prefix'
    assert any(r.occurrence == request.occurrence.removeprefix('native-path:')
               and r.mutable_fields == request.mutable_fields for r in selected.requests)


@pytest.mark.parametrize('failure', [False, True])
def test_window_projection_never_crosses_a_departure_or_source_failure(failure):
    from behave.sim.engines.behavior.path_generation import WindowPathQuery
    from .test_behavior_prefix_generation import _selected

    source = b'''class M:
    def reset(self): self.q = 0
    def process(self, a, b):
        y = 0
        if a == 0:
            self.q = b
        elif a == 1:
            if self.q == 2:
                y = 1
        return {"y": y}
'''
    if failure:
        source = source.replace(b'        y = 0',
            b'        if a == 0 and b == 2:\n            self.q = 1 // (b - 2)\n        y = 0')
    built, _seed, goal, selected = _selected(2, source=source, commands=[0, 1])
    window = tuple(selected.occurrences.values())
    query = WindowPathQuery(built[4], window, (goal,),
        validation=selected._query_preparation.validation(),
        mutable_fields=[field for item in window for _name, field in item['bindings']],
        input_domain=selected.input_domain)
    answer = query.probe((goal,), 20_000)
    assert answer.outcome.value == ('local-unsat' if failure else 'sat'), answer.as_dict()
    for a, b, c, d in itertools.product(range(3), repeat=4):
        values = (dict(a=a, b=b), dict(a=c, b=d))
        joined = {query.names[i][name]: value for i, row in enumerate(values) for name, value in row.items()}
        # This is a goal-only projection, not a complete output transition.
        # A departed path may have no output buffer at all.
        from behave.sim.symbolic.values import copy_value
        machine = interp._Interp({**{name: copy_value(value)
                                     for name, value in built[4].proc.initial.items()}, **joined})
        got = set()
        try:
            machine.run(query.projection.procedure.body, got)
        except interp.Halt:
            pass
        state, expected = built[4].proc.initial, set()
        for row in values:
            state, _outputs, hits, error = interp.run_step(built[4].proc, row, state)
            expected.update(hits)
            if error:
                break
        assert got <= expected, (values, got, expected)


def test_window_path_finds_a_goal_before_a_later_command_clears_the_state():
    from behave.sim.engines.behavior.path_generation import WindowPathQuery
    from .test_behavior_prefix_generation import _selected, _trace
    from behave.sim.engines.behavior import generation_job as jobs

    source = b'''class M:
    def reset(self): self.q = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.q = b
        elif a == 1:
            if self.q == 2:
                y = 1
        elif a == 2: self.q = 0
        return {"y": y}
'''
    built, seed, goal, selected = _selected(3, source=source, commands=[0, 1, 2])
    window = tuple(selected.occurrences.values())
    query = WindowPathQuery(built[4], window, (goal,),
        validation=selected._query_preparation.validation(),
        mutable_fields=[field for item in window for name, field in item['bindings'] if name == 'b'],
        input_domain=selected.input_domain)
    answer = query.probe((goal,), 20_000)
    assert answer.outcome.value == 'sat', answer.as_dict()
    state, positions = built[4].proc.initial, []
    for i, values in enumerate(answer.candidate):
        state, _outputs, hits, error = interp.run_step(built[4].proc, values, state)
        assert not error
        if goal in hits:
            positions.append(i)
    assert positions == [1] and state['q'] == 0
    changed = jobs._candidate_many(seed, zip(window, answer.candidate, strict=True))
    confirmed = jobs.SelectedTrace(built, changed.as_dict(), _trace(changed).as_dict(), [goal])
    assert goal not in selected.replay.hits and goal in confirmed.replay.hits


@pytest.mark.parametrize('storage', ['register', 'indexed'])
@pytest.mark.parametrize('other_command_loops', [False, True])
def test_window_can_change_a_preparation_branch_not_entered_by_the_seed(storage, other_command_loops):
    from behave.sim.engines.behavior.path_generation import WindowPathQuery
    from .test_behavior_prefix_generation import _selected, _trace
    from behave.sim.engines.behavior import generation_job as jobs

    reset, write, condition = {
        'register': ('self.q = 0', 'self.q = b & 7', 'self.q == 7'),
        'indexed': ('self.mem = [0] * 4', 'self.mem[(b >> 3) & 3] = b & 7',
                    'self.mem[2] == 7'),
    }[storage]
    extra = ('        elif a == 2:\n            for i in range(b & 31):\n                y += i\n'
             if other_command_loops else '')
    source = (f'''class M:
    def reset(self): {reset}
    def process(self, a, b):
        y = 0
        if a == 0:
            if b & 128:
                {write}
        elif a == 1:
            if {condition}:
                y = 1
''' + extra + '        return {"y": y}\n').encode()
    built, seed, goal, selected = _selected(2, source=source, commands=[0, 1])
    window = tuple(selected.occurrences.values())
    query = WindowPathQuery(built[4], window, (goal,),
        validation=selected._query_preparation.validation(),
        mutable_fields=[field for item in window for name, field in item['bindings'] if name == 'b'],
        input_domain=selected.input_domain)
    answer = query.probe((goal,), 20_000)
    assert answer.outcome.value == 'sat', answer.as_dict()
    assert answer.scope.value == 'path-prefix'
    assert answer.candidate[0]['b'] & 128
    assert [row['a'] for row in answer.candidate] == [0, 1]
    assert not any(isinstance(cmd, ir.For) for cmd in ir.walk(query.projection.procedure.body))
    changed = jobs._candidate_many(seed, zip(window, answer.candidate, strict=True))
    confirmed = jobs.SelectedTrace(built, changed.as_dict(), _trace(changed).as_dict(), [goal])
    assert goal not in selected.replay.hits and goal in confirmed.replay.hits


def test_window_path_refuses_reset_crossing_and_keeps_fixed_fields():
    from behave.sim.engines.behavior.path_generation import WindowPathQuery
    from .test_behavior_prefix_generation import _selected

    built, _seed, goal, selected = _selected(3, commands=[0, 1, 2], reset_at=1)
    window = tuple(selected.occurrences.values())
    with pytest.raises(ValueError, match='reset'):
        WindowPathQuery(built[4], window, (goal,),
            validation=selected._query_preparation.validation(), mutable_fields=(),
            input_domain=selected.input_domain)


@pytest.mark.parametrize('depth', [8, 64, 256])
def test_loop_free_preparation_keeps_shared_joins_without_path_enumeration(depth):
    from behave.sim.symbolic import cfg
    from behave.sim.engines.behavior.path_generation import _project

    body = ir._sequence([branch(ir.Op('eq', ir.Var('a'), ir.Lit(i)), 'target')
                         for i in range(depth)])
    proc, _env = model(body)
    graph = cfg.of_command(body)
    body = cfg.if_convert(graph)
    private = {cmd.var for cmd in ir.walk(body) if isinstance(cmd, ir.Assign)}
    proc = proc.derived(body=body, graph=None, locals=[*proc.locals, *sorted(private)],
                        types={**dict(proc.types), **dict.fromkeys(private, types.BOOLEAN)})
    ir._bind_control_flow(proc, ir._make_control_flow_authority(graph, body))
    work = _project(proc, dict(a=0, n=0), proc.initial, {'target'},
                    validation=encoder._procedure_validation(proc), complete=True)
    try:
        while True:
            next(work)
    except StopIteration as done:
        path = done.value
    assert len(path.procedure.graph.blocks) <= len(graph.blocks) + 2
    assert path.decisions == depth and path.commands >= depth
    for a in range(8):
        values = dict(a=a, n=0)
        assert interp.run_step(path.procedure, values, proc.initial) == \
            interp.run_step(proc, values, proc.initial)


@pytest.mark.parametrize('restriction', ['fixed', 'input-domain'])
def test_retained_preparation_branches_obey_window_input_restrictions(restriction):
    from behave.sim.engines.behavior.path_generation import WindowPathQuery
    from .test_behavior_prefix_generation import _selected

    source = b'''class M:
    def reset(self): self.q = 0
    def process(self, a, b):
        y = 0
        if a == 0:
            if b == 2: self.q = b
        elif a == 1:
            if self.q == 2:
                y = 1
        return {"y": y}
'''
    built, _seed, goal, selected = _selected(2, source=source, commands=[0, 1])
    window = tuple(selected.occurrences.values())
    fields = [field for item in window for name, field in item['bindings'] if name == 'b']
    domain = selected.input_domain
    if restriction == 'input-domain':
        domain = SimpleNamespace(constraints=lambda rank, values:
            (*selected.input_domain.constraints(rank, values), values['b'] != 2))
    query = WindowPathQuery(built[4], window, (goal,),
        validation=selected._query_preparation.validation(),
        mutable_fields=fields if restriction == 'input-domain' else (), input_domain=domain)
    answer = query.probe((goal,), 20_000)
    assert answer.outcome.value == 'local-unsat' and answer.scope.value == 'path-prefix'


def test_correlated_unbound_frontier_leaves_other_paths_queryable():
    goal = 'target'
    condition = ir.Op('eq', ir.Var('n'), ir.Lit(3))
    proc, env = model(ir._sequence((
        ir.If(condition, ir.Assign('t', ir.Op('add', ir.Var('a'), ir.Lit(5))), ir.Skip()),
        ir.If(condition, ir._sequence((
            ir.Assign('q', ir.Op('add', ir.Var('t'), ir.Lit(7))),
            ir.Mark(goal))), ir.Skip()))))
    path = project(proc, {'a': 0, 'n': 0}, proc.initial, {goal},
                   validation=encoder._procedure_validation(proc))
    # The one-edge alternative needs a definition on another unexecuted
    # branch. Do not fabricate that value, or refuse unrelated path targets.
    assert path is None
    answer = local_hit_any(proc, env, proc.initial, (goal,), timeout_ms=20_000)
    assert answer.outcome.value == 'sat', answer.as_dict()
    assert goal in interp.run_step(proc, answer.candidate, proc.initial)[2]


def test_complete_path_departure_is_not_a_normal_exit():
    from behave.sim.symbolic import cfg
    from behave.sim.engines.behavior.path_generation import _project

    proc, _env = model(ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(0)),
                           ir.Assign('q', ir.Var('n')), ir.Fail('IndexError', 'failure')))
    work = _project(proc, {'a': 0, 'n': 2}, proc.initial, (),
                    validation=encoder._procedure_validation(proc), complete=True)
    try:
        while True:
            next(work)
    except StopIteration as done:
        path = done.value
    assert path.normal_exit is not None and path.state['q'] == 2
    graph = path.procedure.graph
    graph[path.normal_exit].instrs.append(ir.Mark('completed'))
    checked = path.procedure.derived(body=cfg.if_convert(graph), graph=None)
    assert 'completed' in interp.run_step(checked, {'a': 0, 'n': 3}, proc.initial)[2]
    assert 'completed' not in interp.run_step(checked, {'a': 1, 'n': 3}, proc.initial)[2]


@pytest.mark.parametrize('depth', [4, 8, 16])
def test_context_frontier_reaches_later_loop_state_after_static_goal_was_hit(depth):
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    source = f'''class M:
    def reset(self): self.deepest = 0
    def process(self, a, b):
        y = 0
        word = a | (b << 8)
        for i in range({depth}):
            if (word >> i) & 1:
                y = 1
                self.deepest = i + 1
        return {{"y": y}}
'''.encode()
    source += b'''    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"], slot["inputs"]["right"]["b"])}
'''
    built, seed, goal, selected = _selected(1, source=source, commands=[1])
    assert goal in selected.replay.hits
    original_catalogue = selected.manifest.as_dict()
    request, = (r for r in selected.requests if r.occurrence.startswith('context-path:'))
    assert request.rank in selected.requests.explorations
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['scope'] == 'state-preparation', answer
    assert answer['goal'] is None
    candidate = api.Stimulus.from_dict(answer['candidate'])
    word = dict(candidate.queues)['left'][0][0] | (dict(candidate.queues)['right'][0][0] << 8)
    assert word & ((1 << depth) - 1) == (1 << (depth - 1)) | 1
    replayed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in replayed.replay.hits and replayed.manifest.as_dict() == original_catalogue
    assert not any(':context:' in g for g in replayed.replay.hits)
    namespace = {}
    exec(source, namespace)
    native = namespace['M']()
    native.reset()
    native.process(1, 0)
    assert native.deepest == 1
    native.reset()
    native.process(dict(candidate.queues)['left'][0][0], dict(candidate.queues)['right'][0][0])
    assert native.deepest == depth
    if depth == 8:
        from behave.sim.generation import generate
        from tests.support.reference import load
        from .test_behavior_generation_provider import _contract, _realization

        reference = load({'behavior.py': source}, config={'class': 'M'})
        task = api.admit(_contract(), _realization(), reference=reference)
        spec = api.GenerationSpec(len(seed.slots), 1, 1, 0, 0)
        public_seed = api.Stimulus(queues=dict(seed.queues), slots=(
            api.EventSlot(reset=True, controls={'rst': 1, 'result_ready': 0}),
            api.EventSlot(offers=('left', 'right'), controls={'rst': 0, 'result_ready': 1})))
        provider = api.prepare_behavior_generation(task, spec, reference, _trace,
            targets=(goal,), timeout_s=60, timeout_ms=20_000)
        result = generate((public_seed,), provider, contract=_contract(), generation_spec=spec,
                          probe_budget=2)
        baseline_coverage = result.as_dict()['telemetry']['baseline_coverage']
        assert goal in baseline_coverage['hits'] and baseline_coverage['remaining'] == []
        assert result.generated and not result.telemetry['refusals'], result.as_dict()
        depths = []
        for stimulus in result.generated:
            native.reset()
            native.process(dict(stimulus.queues)['left'][0][0], dict(stimulus.queues)['right'][0][0])
            depths.append(native.deepest)
        assert max(depths) == depth


@pytest.mark.parametrize('graph_form', (False, True))
@pytest.mark.parametrize('index', (ir.Var('n'), ir.Op('and', ir.Var('n'), ir.Lit(3))))
def test_context_can_relocate_a_state_write_without_changing_its_payload(index, graph_form):
    from behave.sim.engines.behavior.path_generation import PathQuery

    proc, env = model(ir._sequence((ir.Store('mem', index, ir.Var('a')),
                                    ir.Mark('target'))), graph_form=graph_form)
    inputs = {'a': 6, 'n': 0}
    before, _, hits, error = interp.run_step(proc, inputs)
    assert hits == {'target'} and not error
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': inputs, 'state': proc.initial}, ('target',),
        validation=encoder._procedure_validation(proc), fixed_inputs={},
        input_constraints=None, context=True)
    result = query.probe(('target',), 20_000)
    assert result.outcome.value == 'proposed' and result.goal is None, result.detail
    assert result.candidate['a'] == inputs['a']
    assert result.candidate['n'] != inputs['n']
    after, _, hits, error = interp.run_step(proc, result.candidate)
    assert hits == {'target'} and not error
    assert after['mem'] != before['mem']
    assert after['mem'][result.candidate['n']] == inputs['a']
    assert after['mem'][0] == proc.initial['mem'][0]


@pytest.mark.parametrize('visited', ((0, 1, 2), (0, 1, 2, 3)))
def test_context_write_address_novelty_is_not_a_reachability_verdict(visited):
    from behave.sim.engines.behavior.path_generation import PathQuery

    proc, env = model(ir._sequence((ir.Store('mem', ir.Var('n'), ir.Var('a')),
                                    ir.Mark('target'))))
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 6, 'n': 0}, 'state': proc.initial}, ('target',),
        validation=encoder._procedure_validation(proc), fixed_inputs={},
        input_constraints=None, context=True, write_locations={'mem': visited})
    result = query.probe(('target',), 20_000)
    assert result.goal is None
    if len(visited) == 4:
        assert result.outcome.value == 'unknown' and result.candidate is None
    else:
        assert result.outcome.value == 'proposed', result.detail
        assert result.candidate['n'] == 3 and result.candidate['a'] == 6


def test_context_relocated_writer_is_replayed_with_the_same_catalogue():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    source = b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        if a == 0:
            self.mem[b & 3] = (b >> 2) & 7
        y = 1
        return {'y': y}
'''
    built, original, goal, _ = _selected(4, source=source)
    seed = api.Stimulus(queues={'left': [(0,), (0,), (0,), (1,)],
                               'right': [(28,), (29,), (30,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    assert goal in selected.replay.hits
    catalogue = selected.manifest.as_dict()
    assert selected._write_locations['mem'] == (0, 1, 2)
    request = next(r for r in selected.requests if r.occurrence.startswith('context-path:')
                   and {f.transaction for f in r.mutable_fields} == {0})
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['goal'] is None, answer
    candidate = api.Stimulus.from_dict(answer['candidate'])
    b = dict(candidate.queues)['right'][0][0]
    assert b & 3 == 3 and (b >> 2) & 7 == 7
    assert candidate.slots == seed.slots
    replayed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert replayed.manifest.as_dict() == catalogue
    assert replayed.replay.hits == selected.replay.hits
    assert not any(':context:' in goal for goal in replayed.replay.hits)

    # Exercise the ordinary scheduler too, with no hand-selected next query.
    from behave.sim.generation import generate
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract, _realization

    source += b'''    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"], slot["inputs"]["right"]["b"])}
'''
    reference = load({'behavior.py': source}, config={'class': 'M'})
    task = api.admit(_contract(), _realization(), reference=reference)
    seed = api.Stimulus(queues=dict(seed.queues), slots=[
        api.EventSlot(reset=True, controls={'rst': 1, 'result_ready': 0}), *[
            api.EventSlot(offers=('left', 'right'), controls={'rst': 0, 'result_ready': 1})
            for _ in range(4)]])
    spec = api.GenerationSpec(5, 4, 1, 0, 0)
    provider = api.prepare_behavior_generation(task, spec, reference, _trace,
        targets=(goal,), timeout_s=60, timeout_ms=20_000)
    result = generate((seed,), provider, contract=_contract(), generation_spec=spec, probe_budget=4)
    assert not result.telemetry['refusals'], result.as_dict()
    assert goal in result.telemetry['baseline_coverage']['hits']
    assert any(a == 0 and b & 3 == 3 and (b >> 2) & 7 == 7
               for candidate in result.generated
               for (a,), (b,) in zip(dict(candidate.queues)['left'], dict(candidate.queues)['right']))


@pytest.mark.parametrize('size', [4, 8])
@pytest.mark.parametrize('already_hit', [False, True])
def test_joint_read_frontier_synthesizes_new_writer_content(size, already_hit):
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    source = f'''class M:
    def reset(self): self.mem = [0] * {size}
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[b % {size}] = (b // {size}) % {size}
        elif a == 1:
            at = self.mem[0]
            y = self.mem[at]
            if y == {0 if already_hit else size - 1}:
                y = 1
        return {{'y': y}}
'''.encode()
    built, original, goal, _ = _selected(2, source=source, commands=[0, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,)], 'right': [(size,), (0,)]},
                        slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    request = next(r for r in selected.requests
                   if r.occurrence.startswith('context-path:prefix:')
                   and {f.transaction for f in r.mutable_fields} == ({0} if already_hit else {0, 1})
                   and {f.endpoint for f in r.mutable_fields} == {'right'})
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['goal'] is None, answer
    candidate = api.Stimulus.from_dict(answer['candidate'])
    writer = dict(candidate.queues)['right'][0][0]
    assert writer % size == 0
    assert (writer // size) % size not in {0, 1}
    assert dict(candidate.queues)['left'] == dict(seed.queues)['left']
    if already_hit:
        assert dict(candidate.queues)['right'][1] == dict(seed.queues)['right'][1]
    assert candidate.slots == seed.slots
    replayed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert replayed.manifest.as_dict() == selected.manifest.as_dict()
    assert (goal in replayed.replay.hits) == already_hit
    assert not any(':context:' in hit for hit in replayed.replay.hits)


@pytest.mark.parametrize('graph_form', [False, True])
@pytest.mark.parametrize('saturated,fixed', [(False, False), (True, False), (False, True)])
def test_read_frontiers_preserve_fixed_inputs_and_are_not_public_goals(graph_form, saturated, fixed):
    from behave.sim.engines.behavior.path_generation import PathQuery

    reads = ([ir.Assign('t', ir.Sel(ir.Var('mem'), ir.Lit(i))) for i in range(4)]
             if saturated else [])
    proc, env = model(ir._sequence((*reads,
        ir.Assign('t', ir.Sel(ir.Var('mem'), ir.Var('n'))), ir.Mark('target'))),
        graph_form=graph_form)
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 0}, 'state': proc.initial}, ('target',),
        validation=encoder._procedure_validation(proc), fixed_inputs={'n': 0} if fixed else {},
        input_constraints=None, context=True)
    result = query.probe(('target',), 20_000)
    assert result.goal is None
    if saturated or fixed:
        assert result.outcome.value == 'unknown' and result.candidate is None, result
    else:
        assert result.outcome.value == 'proposed' and result.candidate['n'] != 0, result
        after, _, hits, error = interp.run_step(proc, result.candidate)
        assert not error and hits == {'target'} and after['mem'] == proc.initial['mem']


@pytest.mark.parametrize('graph_form', [False, True])
def test_read_frontier_prefers_preserved_memory_prefix_without_removing_wider_paths(graph_form):
    from behave.sim.engines.behavior.path_generation import PathQuery

    proc, env = model(ir._sequence((
        ir.Assign('t', ir.Sel(ir.Var('mem'), ir.Op('and', ir.Var('a'), ir.Lit(3)))),
        ir.Assign('t', ir.Sel(ir.Var('mem'), ir.Var('n'))), ir.Mark('target'))),
        graph_form=graph_form)
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 1}, 'state': proc.initial}, ('target',),
        validation=encoder._procedure_validation(proc), fixed_inputs={},
        input_constraints=None, context=True)
    result = query.probe(('target',), 20_000)
    assert result.outcome.value == 'proposed' and result.goal is None, result
    assert result.candidate['a'] & 3 == 0
    assert result.candidate['n'] not in (0, 1)
    preferred, = query.projection.frontiers[0]
    assert preferred.endswith(':prefix')
    projected = query.projection.procedure
    assert preferred in interp.run_step(projected, {'a': 0, 'n': 2})[2]
    changed_prefix = interp.run_step(projected, {'a': 3, 'n': 2})[2]
    assert preferred not in changed_prefix
    # Context projections intentionally omit already-observed public marks.
    # Public evidence still comes only from the unchanged source replay.
    assert interp.run_step(proc, {'a': 3, 'n': 2})[2] == {'target'}
    assert preferred.removesuffix(':prefix') in changed_prefix  # Wider query retained.


def test_read_preparation_shares_one_predicate_across_target_batch():
    from behave.sim.engines.behavior.path_generation import PathQuery

    goals = tuple(f'target_{i}' for i in range(64))
    proc, env = model(ir._sequence((ir.Assign('t', ir.Sel(ir.Var('mem'), ir.Var('n'))),
                                    *(ir.Mark(goal) for goal in goals))))
    query = PathQuery(SimpleNamespace(proc=proc, env=env),
        {'input': {'a': 0, 'n': 0}, 'state': proc.initial}, goals,
        validation=encoder._procedure_validation(proc), fixed_inputs={},
        input_constraints=None, context=True)
    # Restrict a batch to its last target, retaining the shared condition.
    result = query.probe((goals[-1],), 20_000)
    assert result.outcome.value == 'proposed' and result.goal is None, result
    markers = {marker for marker, _ in query.projection.preparations}
    assert len(markers) == 1
    assert {goal for _, goal in query.projection.preparations} == set(goals)
    assert sum(isinstance(c, ir.Mark) and c.goal in markers
               for c in ir.walk(query.projection.procedure.body)) == 1


def test_array_preparation_keeps_distinct_observed_address_branches():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    source = b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a == 0:
            if (b & 3) < 2:
                self.mem[b & 3] = 7
            else:
                self.mem[b & 3] = 7
        elif a == 1:
            for i in range(4):
                if self.mem[i] == 7:
                    y = 1
        return {'y': y}
'''
    built, original, goal, _ = _selected(3, source=source, commands=[0, 0, 1])
    seed = api.Stimulus(queues={'left': [(0,), (0,), (1,)],
                               'right': [(0,), (2,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    contexts = [r for r in selected.requests if r.occurrence.startswith('context-path:')]
    assert [{f.transaction for f in r.mutable_fields} for r in contexts[:2]] == [{1}, {0}]
    assert len({r.identity for r in contexts}) == len(contexts)
    assert any(r.occurrence.startswith('context-path:prefix:') for r in contexts)
    for request, position, address in zip(contexts[:2], (1, 0), (3, 1)):
        answer = selected.probe(request.as_dict(), 20_000)
        assert answer['outcome'] == 'proposed' and answer['goal'] is None, answer
        candidate = api.Stimulus.from_dict(answer['candidate'])
        assert dict(candidate.queues)['left'] == dict(seed.queues)['left']
        assert dict(candidate.queues)['right'][position][0] & 3 == address
        replayed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
        assert replayed.manifest.as_dict() == selected.manifest.as_dict()
        assert replayed.replay.hits == selected.replay.hits


def test_array_preparation_is_tried_before_rebuilding_the_trigger_history():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from behave.sim.generation import generate
    from tests.support.reference import load
    from .test_behavior_prefix_generation import _selected, _trace
    from .test_behavior_generation_provider import _contract, _realization

    source = b'''class M:
    def reset(self): self.mem = [0] * 4
    def process(self, a, b):
        y = 0
        if a == 0:
            self.mem[b & 3] = (b >> 2) & 7
        elif a == 1:
            for i in range(4):
                if self.mem[i] == 7:
                    y = 1
        return {'y': y}
    def process_slot(self, slot):
        if set(slot['inputs']) != {'left', 'right'}: return {}
        return {'result': self.process(slot['inputs']['left']['a'], slot['inputs']['right']['b'])}
'''
    built, original, goal, _ = _selected(3, source=source, commands=[0, 1, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,), (1,)],
                               'right': [(28,), (0,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    assert goal in selected.replay.hits
    contexts = [r for r in selected.requests if r.occurrence.startswith('context-path:')]
    first = contexts[0]
    assert {f.transaction for f in first.mutable_fields} == {0}
    assert {f.endpoint for f in first.mutable_fields} == {'right'}
    assert any(r.occurrence.startswith('context-path:prefix:') for r in contexts)

    # Exercise the ordinary scheduler, not a manually selected Probe. Neither
    # a public goal nor an authored candidate setup is added by this policy.
    reference = load({'behavior.py': source}, config={'class': 'M'})
    task = api.admit(_contract(), _realization(), reference=reference)
    seed = api.Stimulus(queues=dict(seed.queues), slots=[
        api.EventSlot(reset=True, controls={'rst': 1, 'result_ready': 0}), *[
            api.EventSlot(offers=('left', 'right'), controls={'rst': 0, 'result_ready': 1})
            for _ in range(3)]])
    spec = api.GenerationSpec(4, 3, 1, 0, 0)
    provider = api.prepare_behavior_generation(task, spec, reference, _trace,
        targets=(goal,), timeout_s=60, timeout_ms=20_000)
    result = generate((seed,), provider, contract=_contract(), generation_spec=spec, probe_budget=1)
    assert not result.telemetry['refusals'], result.as_dict()
    assert result.generated
    candidate = result.generated[0]
    assert dict(candidate.queues)['left'] == dict(seed.queues)['left']
    written = dict(candidate.queues)['right'][0][0]
    assert written & 3 != 0 and (written >> 2) & 7 == 7
    replayed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert replayed.manifest.as_dict() == selected.manifest.as_dict()
    assert replayed.replay.hits == selected.replay.hits


def test_context_frontier_jointly_changes_setup_before_already_covered_query():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

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
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"], slot["inputs"]["right"]["b"])}
'''
    built, original, goal, _ = _selected(2, source=source, commands=[0, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,)], 'right': [(1,), (0,)]}, slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    assert goal in selected.replay.hits
    last = tuple(selected.occurrences)[-1]
    local = next(r for r in selected.requests if r.occurrence == 'context-path:' + last)
    assert {field.transaction for field in local.mutable_fields} == {1}
    answer = selected.probe(local.as_dict(), 20_000)
    assert answer['outcome'] == 'unknown', answer  # Fixed old state cannot reach the later branch.
    joint = next(r for r in selected.requests if r.occurrence.startswith('context-path:prefix:')
                 and len(r.mutable_fields) == 2)
    contexts = [r for r in selected.requests if r.occurrence.startswith('context-path:')]
    assert contexts[0].identity == joint.identity
    assert local.identity in {r.identity for r in contexts}
    answer = selected.probe(joint.as_dict(), 20_000)
    assert answer['outcome'] == 'proposed' and answer['goal'] is None, answer
    candidate = api.Stimulus.from_dict(answer['candidate'])
    assert candidate.slots == seed.slots
    assert dict(candidate.queues)['left'] == ((0,), (1,))
    assert dict(candidate.queues)['right'][0][0] == 129
    checked = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(), [goal])
    assert goal in checked.replay.hits

    # The public scheduler must ask this state-changing question too, not
    # repeatedly pin the old setup behind already covered static goals.
    from behave.sim.generation import generate
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract, _realization

    reference = load({'behavior.py': source}, config={'class': 'M'})
    task = api.admit(_contract(), _realization(), reference=reference)
    runtime_seed = api.Stimulus(queues=dict(seed.queues), slots=[
        api.EventSlot(reset=True, controls={'rst': 1, 'result_ready': 0}),
        *[api.EventSlot(offers=('left', 'right'), controls={'rst': 0, 'result_ready': 1})] * 2])
    spec = api.GenerationSpec(3, 2, 1, 0, 0)
    provider = api.prepare_behavior_generation(task, spec, reference, _trace,
        targets=(goal,), timeout_s=120, timeout_ms=20000)
    result = generate((runtime_seed,), provider, contract=_contract(),
                      generation_spec=spec, probe_budget=1)
    assert result.generated and not result.telemetry['refusals']
    assert any(dict(row.queues)['right'][0][0] & 128 for row in result.generated)
    assert result.telemetry['answers'][0]['occurrence'].startswith('context-path:prefix:')


@pytest.mark.parametrize('operator,control', (('and', 1), ('or', 0)))
def test_context_observation_does_not_pin_a_short_circuit_transition(operator, control):
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from behave.sim.engines.behavior.path_generation import PathQuery
    from .test_behavior_prefix_generation import _selected, _trace

    source = f'''class M:
    def reset(self): self.state = 0
    def process(self, a, b):
        y = 0
        if a == 1 {operator} b == self.state + 37:
            self.state += 1
            y = 1
        else:
            self.state = 0
        return {{"y": y}}
'''.encode()
    built, original, goal, _ = _selected(2, source=source, commands=[control, control])
    seed = api.Stimulus(queues={'left': [(control,), (control,)], 'right': [(37,), (0,)]},
                        slots=original.slots)
    selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
    assert goal in selected.replay.hits
    last = list(selected.occurrences)[-1]
    assert selected.occurrences[last]['state']['state'] == 1
    query = PathQuery(built[4], selected.occurrences[last], [goal],
        validation=encoder._procedure_validation(built[4].proc),
        fixed_inputs={'a': control}, input_constraints=None, context=True)
    answer = query.probe([goal], 20_000)
    assert answer.outcome.value == 'proposed' and answer.goal is None, answer
    assert answer.candidate == {'a': control, 'b': 38}
    namespace = {}
    exec(source, namespace)
    native = namespace['M']()
    native.reset()
    native.process(control, 37)
    native.process(**answer.candidate)
    assert native.state == 2


@pytest.mark.parametrize('graph_form', (False, True))
def test_context_query_preserves_deep_prefix_instead_of_easy_early_departure(graph_form):
    from behave.sim.engines.behavior.path_generation import PathQuery
    from behave.sim.engines.probing import Outcome

    body = ir._sequence((branch(ir.Op('eq', ir.Var('a'), ir.Lit(7)), 'early'),
        ir.For('i', ir.Var('n'), ir.If(
            ir.Op('eq', ir.Op('and', ir.Op('shr', ir.Var('a'), ir.Var('i')), ir.Lit(1)),
                  ir.Lit(1)), ir.Seq(ir.Mark('repeated'),
                                    ir.Assign('q', ir.Op('add', ir.Var('q'), ir.Lit(1)))),
            ir.Skip()))))
    proc, env = model(body, graph_form=graph_form)
    seed = {'a': 1, 'n': 3}
    goals = {'early', 'repeated'}
    assert 'repeated' in interp.run_step(proc, seed, proc.initial)[2]
    query = PathQuery(SimpleNamespace(proc=proc, env=env), {'input': seed, 'state': proc.initial},
                      goals, validation=encoder._procedure_validation(proc), fixed_inputs={'n': 3},
                      input_constraints=None, context=True)
    answer = query.probe(goals, 20_000)
    assert answer.outcome is Outcome.PROPOSED and answer.goal is None, answer
    # Keep the earlier a != 7 decision and first two loop iterations intact;
    # extend the last iteration instead of returning the easy early a == 7.
    assert answer.candidate == {'a': 5, 'n': 3}
    assert interp.run_step(proc, answer.candidate, proc.initial)[2] == {'repeated'}


def test_context_search_extends_a_gated_state_prefix_after_static_hit():
    from behave.sim import api
    from behave.sim.engines.behavior import generation_job as jobs
    from .test_behavior_prefix_generation import _selected, _trace

    source = b'''class M:
    def reset(self):
        self.mask = 0
        self.depth = 0
    def process(self, a, b):
        y = 0
        if a == 0: self.mask = b
        elif a == 1:
            self.depth = 0
            for i in range(8):
                if ((self.mask >> i) & 1) == 0:
                    break
                self.depth += 1
                y = 1
        return {"y": y}
'''
    built, original, goal, _ = _selected(2, source=source, commands=[0, 1])
    seed = api.Stimulus(queues={'left': [(0,), (1,)], 'right': [(1,), (0,)]}, slots=original.slots)
    namespace = {}
    exec(source, namespace)
    depths = [1]
    for _ in range(7):
        selected = jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), [goal])
        assert goal in selected.replay.hits
        joint = next(r for r in selected.requests if r.occurrence.startswith('context-path:prefix:')
                     and len(r.mutable_fields) == 2)
        answer = selected.probe(joint.as_dict(), 20_000)
        assert answer['outcome'] == 'proposed' and answer['goal'] is None, (depths, answer)
        seed = api.Stimulus.from_dict(answer['candidate'])
        assert dict(seed.queues)['left'] == ((0,), (1,))
        native = namespace['M']()
        native.reset()
        for (a,), (b,) in zip(dict(seed.queues)['left'], dict(seed.queues)['right'], strict=True):
            native.process(a, b)
        assert native.depth > depths[-1], (depths, native.depth)
        depths.append(native.depth)
        if native.depth == 8:
            break
    assert depths[-1] == 8

    from behave.sim.generation import generate
    from tests.support.reference import load
    from .test_behavior_generation_provider import _contract, _realization

    source += b'''    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"], slot["inputs"]["right"]["b"])}
'''
    reference = load({'behavior.py': source}, config={'class': 'M'})
    task = api.admit(_contract(), _realization(), reference=reference)
    public_seed = api.Stimulus(queues={'left': [(0,), (1,)], 'right': [(1,), (0,)]}, slots=(
        api.EventSlot(reset=True, controls={'rst': 1, 'result_ready': 0}),
        api.EventSlot(offers=('left', 'right'), controls={'rst': 0, 'result_ready': 1}),
        api.EventSlot(offers=('left', 'right'), controls={'rst': 0, 'result_ready': 1})))
    spec = api.GenerationSpec(3, 2, 1, 0, 0)
    provider = api.prepare_behavior_generation(task, spec, reference, _trace,
        targets=(goal,), timeout_s=60, timeout_ms=20_000)
    result = generate((public_seed,), provider, contract=_contract(), generation_spec=spec,
                      probe_budget=32)
    assert not result.telemetry['refusals'], result.as_dict()
    public_depths = []
    for candidate in result.generated:
        native = namespace['M']()
        native.reset()
        for (a,), (b,) in zip(dict(candidate.queues)['left'], dict(candidate.queues)['right'], strict=True):
            native.process(a, b)
        public_depths.append(native.depth)
    # A bounded public run must actually advance beyond the already-hit
    # context. It need not exhaust every depth within this test's probe budget;
    # the direct-query loop above separately checks the whole eight-level path.
    assert max(public_depths, default=0) > 1, (public_depths, result.as_dict())


@pytest.mark.parametrize('exit_kind', ('break', 'continue', 'return', 'nested'))
def test_source_loop_path_keeps_binding_and_exit_semantics_after_transport(exit_kind):
    import pickle

    from behave.sim.symbolic import cfg
    from behave.sim.engines.behavior.path_generation import _project
    from .test_behavior_prefix_generation import _selected

    control = 'return {"y": y}' if exit_kind == 'return' else exit_kind
    loop = ('''        for i in range(4):
            if i == (b & 3):
                CONTROL
            self.depth += i + 1
            y = 1
'''.replace('CONTROL', control) if exit_kind != 'nested' else '''        for i in range(2):
            for j in range(2):
                if i + j == (b & 3):
                    break
                self.depth += i + j + 1
                y = 1
''')
    source = ('''class M:
    def reset(self): self.depth = 0
    def process(self, a, b):
        y = 0
''' + loop + '        return {"y": y}\n').encode()
    built, _, _, _ = _selected(1, source=source)
    # Pickle flattens Seq grouping. Matching must not rely on in-process IDs.
    proc = pickle.loads(pickle.dumps(built[4].proc))
    targets = ir.goals_of(proc.body)
    validation = encoder._procedure_validation(proc)
    for seed in range(4):
        work = _project(proc, {'a': 0, 'b': seed}, proc.initial, targets,
                        validation=validation, complete=True)
        while True:
            try:
                next(work)
            except StopIteration as done:
                path = done.value
                break
        assert path.normal_exit is not None
        graph = path.procedure.graph
        blocks = dict(graph.blocks)
        exit_block = graph[path.normal_exit]
        blocks[path.normal_exit] = cfg.Block(path.normal_exit,
            [*exit_block.instrs, ir.Mark('completed')], exit_block.term)
        checked = path.procedure.derived(body=cfg.if_convert(cfg.CFG(blocks, graph.entry)), graph=None)
        for b in range(4):
            values = {'a': 0, 'b': b}
            expected = interp.run_step(proc, values, proc.initial)
            actual = interp.run_step(checked, values, proc.initial)
            assert actual[2] - {'completed'} <= expected[2]
            if 'completed' in actual[2]:
                assert actual[:2] == expected[:2]
                assert actual[3] == expected[3]
