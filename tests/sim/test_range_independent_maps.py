"""Independent array iterations must not become cross-cell recurrences."""

import pytest

from behave.sim.symbolic import cfg, ir, ranges, ssa
from behave.sim.behavior.lowering import lower_procedure


def _source(body, rows=24):
    return ('class Model:\n'
            '    def reset(self): pass\n'
            '    def process(self, a):\n'
            f'        cells = [0] * {rows}\n'
            + body + '\n'
            f'        return {{"y": cells[{rows - 1}]}}\n')


@pytest.mark.parametrize('rows, lanes', [(20, 3), (32, 8), (64, 2)])
def test_nested_per_cell_reductions_do_not_multiply_by_row_count(monkeypatch, rows, lanes):
    source = _source(
        f'        for row in range({rows}):\n'
        f'            for lane in range({lanes}):\n'
        '                cells[row] += (a & 3) + lane', rows)
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,))
    # The inner loop still uses its ordinary interval index. Its conservative
    # bound may exceed the exact triangular sum, but not by the row count.
    expected = lanes * (3 + lanes - 1)
    calls = []
    transfer = ranges._retained_loop_body_post

    def count(loop, *args, **kwargs):
        if isinstance(loop.trip_count, ir.Lit) and loop.trip_count.value == rows:
            calls.append(loop.index)
        return transfer(loop, *args, **kwargs)

    monkeypatch.setattr(ranges, '_retained_loop_body_post', count)
    after = ranges.procedure_post(proc, {'a': ranges.Range(0, 3)}, source_paths=True)
    assert after[proc.output_vars['y']].hi == expected
    assert len(calls) == 1
    native = {}
    exec(compile(source, 'independent.py', 'exec'), native)  # noqa: S102
    for value in range(4):
        output = native['Model']().process(value)['y']
        assert after[proc.output_vars['y']].lo <= output <= expected


@pytest.mark.parametrize('body', [
    '        for row in range(1, 24):\n'
    '            cells[row] = cells[row - 1] + a',
    '        for row in range(24):\n'
    '            cells[row % 4] += a',
    '        total = 0\n'
    '        for row in range(24):\n'
    '            total += a\n'
    '            cells[row] = total',
    '        for row in range(24):\n'
    '            cells[row] += a\n'
    '            if row == a:\n'
    '                break',
    '        for row in range(24):\n'
    '            if row == a:\n'
    '                return {"y": cells[0]}\n'
    '            cells[row] += a',
    '        for row in range(23):\n'
    '            cells[row] += a\n'
    '            cells[row + 1] += a',
    '        for row in range(24):\n'
    '            index = row\n'
    '            cells[index] += a\n'
    '            index = 0\n'
    '            cells[index] += a',
])
def test_cross_iteration_data_or_control_dependency_declines_map(monkeypatch, body):
    source = _source(body)
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,))
    accepted = []
    original = ranges._independent_array_loop_post

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        if result is not None:
            accepted.append(args[0].index)
        return result

    monkeypatch.setattr(ranges, '_independent_array_loop_post', observed)
    after = ranges.procedure_post(proc, {'a': ranges.Range(0, 3)}, source_paths=True)
    assert not accepted
    native = {}
    exec(compile(source, 'dependent.py', 'exec'), native)  # noqa: S102
    bound = after[proc.output_vars['y']]
    for value in range(4):
        assert bound.lo <= native['Model']().process(value)['y'] <= bound.hi


@pytest.mark.parametrize('expression', ['row', '2 * row + 1', '47 - row'])
def test_affine_indices_keep_disjoint_iterations_and_conditional_updates(expression):
    source = _source(
        '        for row in range(24):\n'
        f'            index = {expression}\n'
        '            if a & 1:\n'
        '                cells[index] += a\n'
        '            else:\n'
        '                cells[index] -= a', 48)
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,))
    after = ranges.procedure_post(proc, {'a': ranges.Range(0, 3)}, source_paths=True)
    value = after[proc.output_vars['y']]
    assert value.lo >= -3 and value.hi <= 3
    native = {}
    exec(compile(source, 'affine.py', 'exec'), native)  # noqa: S102
    for a in range(4):
        assert value.lo <= native['Model']().process(a)['y'] <= value.hi


def test_zero_trip_map_keeps_the_unmodified_array():
    source = _source('        for row in range(a & 31):\n'
                     '            cells[row] += 7', 32)
    proc = lower_procedure(source, outputs=['y'], input_widths=(5,))
    after = ranges.procedure_post(proc, {'a': ranges.Range(0, 31)}, source_paths=True)
    value = after[proc.output_vars['y']]
    assert value.lo == 0 and value.hi <= 7


def test_multiple_carried_arrays_follow_the_same_iteration(monkeypatch):
    source = _source(
        '        peer = [1] * 24\n'
        '        for row in range(24):\n'
        '            cells[row] += peer[23 - row]\n'
        '            peer[23 - row] += a\n'
        '            cells[row] += peer[23 - row]')
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,))
    accepted = []
    original = ranges._independent_array_loop_post

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        if result is not None:
            accepted.append(args[0].index)
        return result

    monkeypatch.setattr(ranges, '_independent_array_loop_post', observed)
    after = ranges.procedure_post(proc, {'a': ranges.Range(0, 3)}, source_paths=True)
    assert accepted
    bound = after[proc.output_vars['y']]
    assert bound.hi == 5
    native = {}
    exec(compile(source, 'multiple.py', 'exec'), native)  # noqa: S102
    for value in range(4):
        assert bound.lo <= native['Model']().process(value)['y'] <= bound.hi


def test_uniform_independent_map_cost_does_not_follow_trip_count(monkeypatch):
    size = 10**18
    update = ir.Store('cells', ir.Var('row'),
                      ir.Op('add', ir.Sel(ir.Var('cells'), ir.Var('row')), ir.Lit(1)))
    graph = cfg.CFG({'entry': cfg.Block('entry', [update], cfg.Return())}, 'entry')
    term = cfg.if_convert(graph)
    body = ir.Seq(ir.Assign('row', ir.Var('i')), term)
    analysis = ssa.analyze(graph, {'row'})
    trip = ir.Lit(size)
    flow = ir._make_loop_flow(
        size, size, analysis.undefined_names, analysis.out_of('entry') - {'row'},
        {'row'}, graph, cfg.graph_fingerprint(graph), term, body, trip)
    loop = ir.For('i', trip, body, flow=flow)
    calls = []
    original = ranges._retained_loop_body_post

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(ranges, '_retained_loop_body_post', counted)
    after = ranges._post(loop, {'cells': ranges._UniformCellRange(size, ranges.Range(0, 0))},
                         _flows={ranges._SOURCE_PATH_RANGES: True})
    assert calls == [1]
    assert (after['cells'].lo, after['cells'].hi) == (0, 1)
    assert after['i'] == after['row'] == ranges.Range(size - 1, size - 1)


def test_public_observer_is_not_replaced_by_a_map_summary():
    source = _source('        for row in range(24):\n'
                     '            cells[row] += a')
    proc = lower_procedure(source, outputs=['y'], input_widths=(2,))
    loop = next(command for command in ir.walk(proc.body) if isinstance(command, ir.For))
    observed = []
    result = ranges._independent_array_loop_post(
        loop, {'cells': ranges._UniformCellRange(24, ranges.Range(0, 0)),
               'a': ranges.Range(0, 3)}, 24, 24,
        lambda command, env: observed.append(command), loop.flow, {}, 24)
    assert result is None and not observed
