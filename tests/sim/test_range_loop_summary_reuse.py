"""Planning-local reuse must preserve transfers, observations and all carriers."""

import pytest

from behave.sim.symbolic import ir, plan, ranges
from behave.sim.behavior.lowering import lower_procedure


@pytest.mark.parametrize('kind', ['for', 'while', 'nested'])
@pytest.mark.parametrize('exit_statement', ['pass', 'break', 'continue',
                                         'return {"y": self.x}', 'assert False'])
def test_source_loop_ranges_preserve_concrete_state_hits_and_exits(kind, exit_statement):
    if kind == 'while':
        header = '        left = a\n        while left > 0:\n            left -= 1\n'
        index = 'left'
    else:
        header, index = '        for i in range(a):\n', 'i'
    body = (f'            if {index} == 1:\n                {exit_statement}\n'
            f'            self.x += {index} + 1\n')
    if kind == 'nested':
        body = '            for j in range(2):\n' + ''.join(
            '    ' + line for line in body.splitlines(keepends=True))
    source = ('class Model:\n    def reset(self):\n        self.x = 0\n'
              '    def process(self, a):\n' + header + body + '        return {"y": self.x}\n')
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,))
    from behave.sim.symbolic import interp

    possible_hits, normal_values = set(), []
    for a in range(4):  # Includes zero trips and every exit path.
        for initial in (0, 2):
            env = {'a': ranges.Range(a, a), 'x': ranges.Range(initial, initial)}
            seen = []
            post = ranges.procedure_post(proc, env, source_paths=True,
                witness=lambda cmd, at: seen.append((cmd, dict(at))))
            machine, hits, failed = interp._Interp({'a': a, 'x': initial}), set(), False
            try:
                machine.run(proc.body, hits)
            except interp.Halt:
                failed = True
            possible_hits.update(hits)
            assert (post is None) == failed
            assert {cmd.goal for cmd, _at in seen if isinstance(cmd, (ir.Mark, ir.Fail))
                    and cmd.goal is not None} == hits
            if not failed:
                normal_values.append({name: ranges.range_of_value(machine.rho[name])
                                      for name in ('x', proc.output_vars['y'])})
                for name, value in normal_values[-1].items():
                    assert (post[name].lo, post[name].hi) == (value.lo, value.hi)
    seen = []
    post = ranges.procedure_post(proc, {'a': ranges.Range(0, 3), 'x': ranges.Range(0, 2)},
        source_paths=True, witness=lambda cmd, _at: seen.append(cmd))
    assert possible_hits <= {cmd.goal for cmd in seen if isinstance(cmd, (ir.Mark, ir.Fail))}
    for values in normal_values:
        for name, value in values.items():
            assert post[name].lo <= value.lo <= value.hi <= post[name].hi


@pytest.mark.parametrize('source_paths', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_procedure_observer_visits_nested_writes_marks_and_failed_prefixes(source_paths, failure):
    commands = [ir.Assign('x', ir.Lit(15)), ir.Mark('inside')]
    commands += [ir.Fail('Stop', 'failed')] if failure else [ir.Assign('x', ir.Lit(0))]
    body = ir.For('i', 2, ir.For('j', 3, ir._sequence(commands)))
    proc = ir.Procedure(('x',), (), ('i', 'j'), (), body)
    observed = []
    post = ranges.procedure_post(proc, {'x': ranges.Range(0, 0)},
        witness=lambda command, at: observed.append((command, dict(at))), source_paths=source_paths)
    visits = 1 if failure else 6
    assert sum(isinstance(cmd, ir.Mark) and cmd.goal == 'inside' for cmd, _ in observed) == visits
    assert sum(isinstance(cmd, ir.Assign) and cmd.var == 'x' for cmd, _ in observed) == (
        visits if failure else 2 * visits)
    assert all(at['x'] == ranges.Range(15, 15) for cmd, at in observed if isinstance(cmd, ir.Mark))
    assert (post is None) is failure
    if failure:
        assert any(isinstance(cmd, ir.Fail) and cmd.goal == 'failed' for cmd, _ in observed)
    else:
        assert post['x'] == ranges.Range(0, 0)


def test_graph_observer_keeps_lexical_depth_while_transferring_complete_loop():
    from behave.sim.symbolic import cfg

    loop = ir.For('i', 3, ir.Assign('x', ir.Op('add', ir.Var('x'), ir.Lit(1))))
    graph = cfg.of_command(loop)
    observed = []
    post = ranges.graph_post(graph, {'x': ranges.Range(0, 0)},
                             lambda command, _at: observed.append(command))
    assert observed == [loop]
    assert post['x'] == ranges.Range(3, 3)


def _loop():
    return ir.For("i", 3, ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1))))


def _transfer(loop, env, posts, *, witness=None, product=1, source_paths=False):
    cache = ranges._range_transfer_cache(loop_posts=posts)
    cache[ranges._SOURCE_PATH_RANGES] = source_paths
    return ranges._post(loop, dict(env), witness, cache, product)


def test_reuse_is_exact_and_does_not_retain_unrelated_environment(monkeypatch):
    loop, posts, calls = _loop(), {}, []
    original = ranges._retained_loop_body_post

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(ranges, "_retained_loop_body_post", counted)
    first = _transfer(loop, {"x": ranges.Range(0, 0), "other": ranges.Range(1, 1)}, posts)
    visits = len(calls)
    # A new transfer, not just a repeated lookup within one range walk.
    second = _transfer(loop, {"x": ranges.Range(0, 0), "other": ranges.Range(7, 7)}, posts)
    assert len(calls) == visits == 3
    assert first["x"] == second["x"] == ranges.Range(3, 3)
    assert first["other"] == ranges.Range(1, 1)
    assert second["other"] == ranges.Range(7, 7)
    second["x"] = ranges.Range(-1, -1)
    assert _transfer(loop, {"x": ranges.Range(0, 0)}, posts)["x"] == ranges.Range(3, 3)


def test_changed_and_missing_entry_ranges_are_not_reused():
    loop, posts = _loop(), {}
    for env in ({"x": ranges.Range(0, 0)}, {"x": ranges.Range(3, 9)}, {},
                {"x": ranges.Range(0, 0)}):
        assert _transfer(loop, env, posts) == _transfer(loop, env, {})
    assert len(posts) == 1  # Latest binding, not a retained history of visits.


def test_cell_values_not_just_array_hulls_are_part_of_summary_inputs():
    loop = ir.For("i", 2, ir.Assign("x", ir.Sel(ir.Var("a"), ir.Var("i"))))
    posts = {}
    for values in ([0, 1], [1, 0], [0, 1]):
        env = {"a": ranges._CellRange(tuple(ranges.Range(v, v) for v in values))}
        assert _transfer(loop, env, posts) == _transfer(loop, env, {})
    assert len(posts) == 1


@pytest.mark.parametrize("product", [1, ranges._POINTWISE_LOOP_COMBINATION_LIMIT + 1])
@pytest.mark.parametrize("source_paths", [False, True])
def test_precision_modes_do_not_share_results(product, source_paths):
    loop, posts = _loop(), {}
    env = {"x": ranges.Range(0, 0)}
    _transfer(loop, env, posts)
    assert _transfer(loop, env, posts, product=product, source_paths=source_paths) == (
        _transfer(loop, env, {}, product=product, source_paths=source_paths))
    assert len(posts) == (1 if product == 1 and not source_paths else 2)


def test_completed_failure_can_be_reused_but_observer_visits_cannot():
    loop = ir.For("i", 2, ir.If(ir.Var("stop"), ir.Fail("Stop", "failure"),
                                ir.Assign("x", ir.Var("i"))))
    posts = {}
    for stop in (0, 1, 0, 1):
        env = {"stop": ranges.Range(stop, stop)}
        assert _transfer(loop, env, posts) == _transfer(loop, env, {})
        actual, expected = [], []
        result = _transfer(loop, env, posts,
                           witness=lambda cmd, at, seen=actual: seen.append((cmd, dict(at))))
        control = _transfer(loop, env, {},
                            witness=lambda cmd, at, seen=expected: seen.append((cmd, dict(at))))
        assert result == control
        assert actual == expected
        assert any(command is loop.body for command, _ in actual)


def test_interrupted_transfer_does_not_publish_a_summary(monkeypatch):
    loop, posts = _loop(), {}
    env = {"x": ranges.Range(0, 0)}
    original = ranges._retained_loop_body_post

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("cancel during construction")

    with monkeypatch.context() as patch:
        patch.setattr(ranges, "_retained_loop_body_post", interrupted)
        with pytest.raises(TimeoutError):
            _transfer(loop, env, posts)
    assert not posts
    assert _transfer(loop, env, posts) == _transfer(loop, env, {})


@pytest.mark.parametrize("expression", ["a + row + col", "self.cells[col] + row", "a // (col + 1)"])
@pytest.mark.parametrize("initial", [0, 2, 5])
def test_source_cfg_plans_preserve_every_carrier_and_occurrence(monkeypatch, expression, initial):
    source = f'''class Model:
    def reset(self):
        self.n = 2
        self.cells = [1, 3, 0]

    def process(self, a):
        total = 0
        for row in range(self.n):
            for col in range(3):
                value = {expression}
                if value > 3:
                    total += value
                else:
                    total += 1
        self.n = (self.n + 1) % 6
        return {{"y": total}}
'''
    proc = lower_procedure(source, outputs=["y"], input_widths=(3,),
                           state_ranges={"n": ranges.Range(0, 5),
                                         "cells": ranges.Range(0, 3)})
    env = {"a": ranges.Range(0, 7), "n": ranges.Range(initial, initial),
           "cells": ranges.range_of_value([1, 3, 0])}
    actual = plan.plan_encoding(proc, env)
    original = ranges._range_transfer_cache

    def fresh(validations=None, *, loop_posts=None, syntax=None):
        return original(validations)

    monkeypatch.setattr(ranges, "_range_transfer_cache", fresh)
    expected = plan.plan_encoding(proc, env)
    assert not expected.unresolved
    for name in plan.EncodingPlan.__slots__:
        assert getattr(actual, name) == getattr(expected, name), name


def test_expression_and_join_caches_stay_transfer_local():
    posts, validations = {}, {}
    first = ranges._range_transfer_cache(validations, loop_posts=posts)
    second = ranges._range_transfer_cache(validations, loop_posts=posts)
    assert first[ranges._COMMAND_POST_CACHE] is second[ranges._COMMAND_POST_CACHE] is posts
    for key in (ranges._VALUE_RANGE_CACHE, ranges._RANGE_JOIN_CACHE):
        first[key] = {"temporary": object()}
        assert key not in second


@pytest.mark.parametrize("body", [
    "hole = (hole - 1) // 2",
    "self.x += a\n            hole -= 1",
    "self.cells[hole & 1] = a\n            hole -= 1",
])
def test_unchanged_loop_entry_needs_no_private_observer(monkeypatch, body):
    source = f'''class Model:
    def reset(self):
        self.x = 0
        self.cells = [0, 0]
    def process(self, a):
        hole = 7
        while hole > 0:
            {body}
        return {{"y": self.x}}
'''
    proc = lower_procedure(source, outputs=["y"], input_widths=(3,))
    loop, = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    flow = ranges._checked_loop_flow(loop, {})
    assert set(flow.entry_defined) == {loop.index}
    env = {"a": ranges.Range(0, 7), "x": ranges.Range(0, 0),
           "cells": ranges.range_of_value([0, 0]), "hole": ranges.Range(1, 7),
           loop.index: ranges.Range(0, 0)}
    observed = []
    original = ranges._post

    def post(command, at, witness=None, *args, **kwargs):
        if command is loop.body:
            observed.append(witness)
        return original(command, at, witness, *args, **kwargs)

    monkeypatch.setattr(ranges, "_post", post)
    actual = ranges._retained_loop_body_post(
        loop, dict(env), None, flow, {ranges._SOURCE_PATH_RANGES: True})
    assert observed == [None]
    observed.clear()
    visits = []
    def witness(cmd, at):
        visits.append(cmd)

    expected = ranges._retained_loop_body_post(
        loop, dict(env), witness, flow,
        {ranges._SOURCE_PATH_RANGES: True})
    assert actual == expected
    assert observed == [witness]  # Source-path transfer needs no private replay.
    assert visits  # A real caller's observations are never optimized away.


def test_source_loop_counter_observation_is_preserved_for_activation_goals(monkeypatch):
    source = '''class Model:
    def reset(self):
        self.x = 0

    def process(self, a):
        hole = 15
        while hole > 0:
            parent = (hole - 1) // 2
            if parent * parent <= a:
                self.x = parent
            else:
                self.x = hole
            hole = parent
        return {"y": self.x}
'''
    proc = lower_procedure(source, outputs=["y"], input_widths=(8,))
    loops = [command for command in ir.walk(proc.body)
             if isinstance(command, ir.For) and command.flow is not None]
    assert loops and all(set(loop.flow.entry_defined) <= {loop.index} for loop in loops)
    # The mechanical counter still occurs in loop-activation observations.
    # It cannot be treated as an unused entry when recovering source ranges.
    assert all(loop.index in ranges._graph_range_footprint(loop.flow.graph, {})
               for loop in loops)
    bodies = {id(loop.body) for loop in loops}
    original_post = ranges._post
    visits = []

    def post(command, env, witness=None, *args, **kwargs):
        if id(command) in bodies:
            visits.append(type(witness))
        return original_post(command, env, witness, *args, **kwargs)

    monkeypatch.setattr(ranges, "_post", post)
    # An absent input range remains unknown. It also makes the term lack a
    # required source fact, so the ordinary finite-facts fast exit cannot apply.
    assert any("a" in loop.flow.required for loop in loops)
    env = {"x": ranges.Range(0, 0)}
    actual = ranges.procedure_post(proc, env)
    # An unavailable external input cannot be recovered by observing the
    # loop-prefix bindings. Do not repeat the body merely to discover that.
    assert ranges._LoopBodyWitness not in visits
    visits.clear()
    observed = []
    expected = ranges.procedure_post(proc, env,
                                    witness=lambda command, at: observed.append(command))
    assert any(isinstance(command, ir.For) for command in observed)
    assert actual == expected
    # A CFG observer sees retained loops as instructions. A term observer
    # explicitly enters their bodies and must still get every inner visit.
    visits.clear()
    ranges._post(loops[0], dict(env), witness=lambda _command, _at: None)
    assert ranges._LoopBodyWitness in visits


def test_graph_summary_reuses_only_complete_identical_unobserved_transfers(monkeypatch):
    from behave.sim.symbolic import cfg

    command = ir.Assign("y", ir.Op("add", ir.Var("x"), ir.Lit(1)))
    graph = cfg.CFG({"entry": cfg.Block("entry", (command,), cfg.Return())}, "entry")
    cache, calls = {}, []
    original = ranges._post

    def post(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(ranges, "_post", post)
    env = {"x": ranges.Range(1, 3)}
    result = ranges.graph_post(graph, env, _flows=cache)
    result["y"] = ranges.Range(99, 99)
    assert ranges.graph_post(graph, env, _flows=cache)["y"] == ranges.Range(2, 4)
    assert len(calls) == 1
    for value in range(50):
        changed = {"x": ranges.Range(value, value + 1)}
        assert ranges.graph_post(graph, changed, _flows=cache) == ranges.graph_post(graph, changed)
    assert len(cache[ranges._GRAPH_POST_CACHE]) == 1
    assert "y" not in ranges.graph_post(graph, {}, _flows=cache)
    visits = []
    for _ in range(2):
        ranges.graph_post(graph, env, lambda cmd, at: visits.append((cmd, dict(at))), _flows=cache)
    assert len(visits) == 2 and visits[0] == visits[1]


def test_graph_summary_does_not_cache_interrupted_work(monkeypatch):
    from behave.sim.symbolic import cfg

    graph = cfg.CFG({"entry": cfg.Block("entry", (ir.Assign("x", ir.Lit(1)),), cfg.Return())}, "entry")
    cache = {}
    original = ranges._post

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("interrupted graph transfer")

    with monkeypatch.context() as patch:
        patch.setattr(ranges, "_post", interrupted)
        with pytest.raises(TimeoutError):
            ranges.graph_post(graph, {}, _flows=cache)
    assert not cache[ranges._GRAPH_POST_CACHE]
    assert ranges.graph_post(graph, {}, _flows=cache)["x"] == ranges.Range(1, 1)


@pytest.mark.parametrize("body", [
    ir.Assign("x", ir.Var("missing")),
    ir.Store("buf", ir.Var("index"), ir.Var("x")),
    ir.Assign("x", ir.Sel(ir.Var("buf"), ir.Var("index"))),
    ir.For("i", 3, ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1)))),
    ir.Fail("Assertion", "failed"),
])
@pytest.mark.parametrize("include_dead", [False, True])
def test_graph_dependency_summary_matches_full_observed_transfer(body, include_dead):
    from behave.sim.symbolic import cfg

    graph = cfg.CFG({
        "entry": cfg.Block("entry", (), cfg.Branch(ir.Var("guard"), "yes", "end")),
        "yes": cfg.Block("yes", (body,), cfg.Goto("end")),
        "end": cfg.Block("end", (), cfg.Return()),
    }, "entry")
    summaries = {}
    for guard in (ranges.Range(0, 1), ranges.Range(1, 1), ranges.Range(0, 0)):
        for cells in ([0, 1], [1, 0], [0, 1]):
            for missing in ({}, {"missing": ranges.Range(7, 7)}):
                env = {"guard": guard, "x": ranges.Range(2, 3),
                       "index": ranges.Range(0, 1), "unused": ranges.Range(8, 9),
                       "buf": ranges._state_product_range(cells), **missing}
                actual = ranges.graph_post(graph, env,
                    _flows=summaries,
                    _include_infeasible_edges=include_dead)
                # An observed traversal cannot consume a postcondition cache
                # or project its environment; it supplies the independent path.
                expected = ranges.graph_post(graph, env, lambda *_: None,
                    _include_infeasible_edges=include_dead)
                assert actual == expected


@pytest.mark.parametrize('operator', ['eq', 'ne', 'lt', 'le', 'gt', 'ge'])
@pytest.mark.parametrize('truth', [False, True])
@pytest.mark.parametrize('swapped', [False, True])
def test_selected_cell_refinement_preserves_every_concrete_branch(operator, truth, swapped):
    import operator as ops

    cells = ranges._UniformCellRange(2, ranges.Range(-2, 2),
                                    [(1, ranges.Range(100, 200))])
    selected = ir.Sel(ir.Var('cells'), ir.Var('index'))
    operands = (ir.Lit(0), selected) if swapped else (selected, ir.Lit(0))
    condition = ir.Op(operator, *operands)
    env = {'cells': cells, 'index': ranges.Range(0, 0)}
    answer = ranges._finite_assumption(condition, truth, env, cache={})
    concrete = [value for value in range(-2, 3)
                if getattr(ops, operator)(*((0, value) if swapped else (value, 0))) is truth]
    if not concrete:
        assert answer is None
    else:
        value = ranges._cell_at(answer['cells'], 0)
        assert value.lo <= min(concrete) <= max(concrete) <= value.hi
        assert ranges._cell_at(answer['cells'], 1) == ranges.Range(100, 200)
    assert ranges._cell_at(cells, 0) == ranges.Range(-2, 2)


def test_unknown_selected_index_cannot_constrain_every_array_cell():
    cells = ranges._UniformCellRange(2, ranges.Range(-2, 2))
    condition = ir.Op('gt', ir.Sel(ir.Var('cells'), ir.Var('index')), ir.Lit(0))
    env = {'cells': cells, 'index': ranges.Range(0, 1)}
    assert ranges._finite_assumption(condition, True, env)['cells'] == cells


def test_state_planning_retains_fixed_cells_until_public_hull():
    source = '''class Model:
    def reset(self):
        self.counts = [0, 3]

    def process(self, a):
        for i in range(4):
            if self.counts[0] > 0:
                self.counts[0] -= 1
        return {"y": self.counts[0]}
'''
    proc = lower_procedure(source, outputs=['y'], input_widths=(1,))
    answer = ranges.bounded(proc, {'a': ranges.Range(0, 1)}, 100, _state_planning=True)
    assert type(answer['counts']) is ranges.Range
    assert answer['counts'] == ranges.Range(0, 3)
    assert answer[proc.output_vars['y']] == ranges.Range(0, 0)


def test_initial_state_products_compress_uniform_values_and_reject_cycles():
    value = ranges._state_product_range([3] * 10000)
    assert value.length == 10000 and value.runs == ()
    assert value.default == ranges.Range(3, 3)
    cycle = []
    cycle.append(cycle)
    assert ranges._state_product_range(cycle) is None
    assert ranges.range_of_value([1, 3]) == ranges.Range(1, 3)


def test_recovered_selection_guard_respects_array_writes():
    selected = ir.Sel(ir.Var('cells'), ir.Lit(0))
    load = ir.Assign('value', selected)
    guard = ir.Op('gt', ir.Var('value'), ir.Lit(0))
    before, _ = ranges._branch_predicate(guard, (load,))
    assert isinstance(before.args[0], ir.Sel)
    store = ir.Store('cells', ir.Lit(0), ir.Lit(99))
    after, _ = ranges._branch_predicate(guard, (load, store))
    assert after.args[0].name == 'value'


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('bounds', [None, (-3, -1), (-1, 1), (0, 0), (0, 2), (1, 2), (2, 9), (8, 9)])
def test_functional_and_statement_array_writes_preserve_possible_cells(compact, bounds):
    cells = (ranges.Range(-2, 1), ranges.Range(4, 7), ranges.Range(20, 20))
    array = (ranges._UniformCellRange(3, cells[0], enumerate(cells)) if compact
             else ranges._CellRange(cells))
    env = {'a': array, 'v': ranges.Range(0, 5)}
    if bounds is not None:
        env['i'] = ranges.Range(*bounds)
    expression = ir.Op('store', ir.Var('a'), ir.Var('i'), ir.Var('v'))
    written = ranges._value_range(expression, env, {})
    statement = ranges._post(ir.Store('a', ir.Var('i'), ir.Var('v')), dict(env))
    assert written == statement['a']
    assert ranges._is_cell_range(written)
    selected = set(range(3) if bounds is None else range(max(0, bounds[0]), min(3, bounds[1] + 1)))
    for i, cell in enumerate(cells):
        actual = ranges._cell_at(written, i)
        expected = cell
        if i in selected:
            expected = env['v'] if bounds is not None and bounds[0] == bounds[1] else cell.join(env['v'])
        assert actual == expected
        assert ranges._cell_at(array, i) == cell


def test_dynamic_store_in_huge_uniform_product_is_run_sized():
    length = 10**18
    array = ranges._UniformCellRange(length, ranges.Range(0, 0))
    value = ranges.Range(3, 3)
    written = ranges._stored_range(array, ranges.Range(100, length - 101), value)
    assert written.runs == ((100, length - 100, ranges.Range(0, 3)),)
    assert written.cell(99) == written.cell(length - 100) == ranges.Range(0, 0)
    everywhere = ranges._stored_range(array, None, value)
    assert everywhere.runs == () and everywhere.default == ranges.Range(0, 3)


def test_nested_functional_array_stores_keep_untouched_products():
    array = ir.Op('arraylit', ir.Op('arraylit', ir.Lit(1), ir.Lit(2)),
                  ir.Op('arraylit', ir.Lit(3), ir.Lit(4)))
    updated = ir.Op('store', array, ir.Lit(0),
                    ir.Op('arraylit', ir.Lit(8), ir.Lit(9)))
    result = ranges._value_range(updated, {})
    assert result.cells[0].cells == (ranges.Range(8, 8), ranges.Range(9, 9))
    assert result.cells[1].cells == (ranges.Range(3, 3), ranges.Range(4, 4))
    assert ranges._value_range(ir.Op('store', ir.Var('missing'), ir.Lit(0), ir.Lit(1)), {}) is None


def test_functional_counter_reset_does_not_inherit_another_cells_range():
    source = '''class Model:
    def reset(self):
        self.counts = [0, 100]

    def process(self, a):
        self.counts[0] = a & 3
        for i in range(4):
            if self.counts[0] > 0:
                self.counts[0] -= 1
        return {"y": self.counts[0]}
'''
    proc = lower_procedure(source, outputs=['y'], input_widths=(4,))
    answer = ranges.bounded(proc, {'a': ranges.Range(0, 15)}, 100, _state_planning=True)
    assert answer['counts'] == ranges.Range(0, 100)
    assert answer[proc.output_vars['y']] == ranges.Range(0, 0)


@pytest.mark.parametrize('operation,truth', [('lor', True), ('land', False)])
def test_alternative_cell_guards_join_products_without_conflating_cells(operation, truth):
    cells = ranges._UniformCellRange(2, ranges.Range(-2, 2), [(1, ranges.Range(100, 200))])
    selected = ir.Sel(ir.Var('cells'), ir.Lit(0))
    predicate = ir.Op(operation, ir.Op('lt', selected, ir.Lit(0)),
                      ir.Op('gt', selected, ir.Lit(0)))
    env = {'cells': cells}
    answer = ranges._finite_assumption(predicate, truth, env, cache={})
    assert ranges._cell_at(answer['cells'], 0) == ranges.Range(-2, 2)
    assert ranges._cell_at(answer['cells'], 1) == ranges.Range(100, 200)
    assert env['cells'] is cells


@pytest.mark.parametrize('functional', [False, True])
def test_typed_array_hull_allows_strong_updates_without_narrowing_other_cells(functional):
    from behave.sim.symbolic import typecheck as types

    write = (ir.Assign('cells', ir.Op('store', ir.Var('cells'), ir.Lit(0), ir.Lit(3)))
             if functional else ir.Store('cells', ir.Lit(0), ir.Lit(3)))
    body = ir.Seq(write, ir.Assign('y', ir.Sel(ir.Var('cells'), ir.Lit(0))))
    proc = ir.Procedure(['cells'], [], ['y'], ['y'], body,
                        types={'cells': types.Array(2), 'y': types.INTEGER})
    env = {'cells': ranges.Range(-100, 100)}
    after = ranges.procedure_post(proc, env)
    assert after['y'] == ranges.Range(3, 3)
    assert ranges._cell_at(after['cells'], 1) == env['cells']
    assert env == {'cells': ranges.Range(-100, 100)}


def test_typed_entry_shape_is_compact_recursive_and_preserves_existing_information():
    from behave.sim.symbolic import typecheck as types

    rich = ranges._CellRange((ranges.Range(0, 0), ranges.Range(3, 3)))
    proc = ir.Procedure(['a', 'b', 'empty', 'missing'], [], [], [], ir.Skip(), types={
        'a': types.Array(10**18, types.Array(10**12)),
        'b': types.Array(2), 'empty': types.Array(0), 'missing': types.Array(2),
        'unknown': types.Array(2)})
    env = {'a': ranges.Range(-3, 7), 'b': rich, 'empty': ranges.Range(0, 0),
           'unknown': ranges.UNKNOWN, 'untyped': ranges.Range(-1, 1)}
    entry = ranges._typed_entry_ranges(proc, env)
    assert entry['a'].length == 10**18 and not entry['a'].runs
    assert entry['a'].default.length == 10**12 and not entry['a'].default.runs
    assert entry['a'].default.default == env['a']
    assert entry['b'] is rich and entry['empty'].length == 0
    assert entry['unknown'] is ranges.UNKNOWN and 'missing' not in entry
    assert entry['untyped'] is env['untyped']


def test_source_output_proof_keeps_array_resets_with_wide_entry_hulls():
    source = '''class Model:
    def reset(self):
        self.cells = [0, 0]

    def process(self, a):
        self.cells[0] = a & 3
        self.cells[1] = 7
        return {"y": self.cells[0]}
'''
    proc = lower_procedure(source, outputs=['y'], input_widths=(4,))
    env = {'a': ranges.Range(0, 15), 'cells': ranges.Range(-100, 100)}
    after = ranges.procedure_post(proc, env, source_paths=True)
    output = after[proc.output_vars['y']]
    assert (output.lo, output.hi) == (0, 3)
    assert ranges._cell_at(after['cells'], 1) == ranges.Range(7, 7)


def test_state_planning_retains_helper_bank_through_nested_control_flow():
    source = '''class Model:
    def reset(self):
        self.cells = [5, 6, 7, 8, -5, -6, -7, -8]
        self.counts = [0, 0]

    def take(self, bank):
        base = bank * 4
        n = self.counts[bank] - 1
        self.counts[bank] = n
        if n > 0:
            value = self.cells[base + n]
            hole = 0
            while 2 * hole + 1 < n:
                child = 2 * hole + 1
                self.cells[base + hole] = self.cells[base + child]
                hole = child
            self.cells[base + hole] = value

    def process(self, a):
        self.counts[1] = a + 1
        while self.counts[1] > 0:
            self.take(1)
        return {"y": self.cells[0]}
'''
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,), bound=2)
    result = ranges.bounded(proc, {'a': ranges.Range(0, 3)}, 2, _state_planning=True)
    assert result[proc.output_vars['y']] == ranges.Range(5, 5)


def test_exact_parent_loop_excludes_zero_trip_state_from_its_body():
    source = '''class Model:
    def reset(self):
        self.cells = [5, 6, 7, 8, -5, -6, -7, -8]

    def process(self, a):
        hole = a
        while hole > 0:
            parent = (hole - 1) // 2
            self.cells[hole] = self.cells[parent]
            hole = parent
        return {"y": self.cells[1]}
'''
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,), bound=2)
    result = ranges.bounded(proc, {'a': ranges.Range(0, 3)}, 2, _state_planning=True)
    output = result[proc.output_vars['y']]
    assert 5 <= output.lo <= output.hi <= 8


@pytest.mark.parametrize('compressed', [False, True])
def test_interval_array_reads_join_only_possible_cells(compressed):
    values = (5, 6, 7, 8, -5, -6, -7, -8)
    cells = tuple(ranges.Range(v, v) for v in values)
    array = (ranges._UniformCellRange(8, cells[0], enumerate(cells))
             if compressed else ranges._CellRange(cells))
    expr = ir.Sel(ir.Var('cells'), ir.Var('i'))
    for lo in range(8):
        for hi in range(lo, 8):
            env = {'cells': array, 'i': ranges.Range(lo, hi)}
            expected = ranges.Range(min(values[lo:hi + 1]), max(values[lo:hi + 1]))
            assert ranges.of_expression(expr, env) == expected
            assert ranges.bounds_of_expression(expr, env).finite() == expected


def test_interval_array_reads_preserve_nested_shape_and_skip_huge_uniform_spans():
    row = ranges._CellRange((ranges.Range(1, 1), ranges.Range(100, 100)))
    other = ranges._CellRange((ranges.Range(3, 3), ranges.Range(200, 200)))
    array = ranges._UniformCellRange._from_runs(10**18, row, ((10, 20, other),))
    selected = ranges._selected_range(array, ranges.Range(0, 30))
    assert selected.cells == (ranges.Range(1, 3), ranges.Range(100, 200))
    assert ranges._selected_range(array, ranges.Range(10, 19)) is other
    assert ranges._selected_range(array, ranges.Bounds(20, None)) is row
    assert ranges._selected_range(array, None) == selected
    # Invalid selections do not claim a successful access or manufacture a
    # normal-exit proof; source safety checking still owns those events.
    assert ranges._selected_range(array, ranges.Range(-2, -1)) == ranges.Range(1, 200)
