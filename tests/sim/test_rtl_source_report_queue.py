"""Report lifetimes in shared IR, with a separate immediate-removal oracle."""

from itertools import product

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_reports import ReportCapture, SourceReport, SourceReportQueue

from .test_rtl_source_ir import _value_leaves

pytestmark = pytest.mark.solver


def _machine(depth=8, *, terminal=False):
    reports = []
    for index, (owner, site, region) in enumerate(((0, (0, 'x'), 'observed'),
            (1, (1, 'x'), 'observed'), (0, (0, 'z'), 'final'))):
        action = ir._sequence([ir.Mark(f'entry{index}'),
            ir.Fail('RTLAssertion', f'action{index}') if terminal and index == 0 else ir.Skip(),
            ir.Assign('q', ir.Op('add', ir.Op('mul', ir.Var('q'), ir.Lit(16)), ir.Var('held')))])
        reports.append(SourceReport(owner, site, region, f'outcome{index}',
            (ReportCapture('held', types.INTEGER, ranges.Range(0, 15)),), action,
            ir.Assign(f'emit{index}', ir.Lit(0))))
    roots = [ir.Seq(ir.Assign('held', ir.Var('value')), report.emission) for report in reports]
    queue = SourceReportQueue(reports, roots, {'value': ranges.Range(0, 15),
        'q': ranges.Range(0, 16**depth-1)}, transition_bound=depth)
    actions = [*(ir.If(queue.allocation_guard, queue.lower(root), ir.Skip()) for root in roots),
               queue.mature('observed'), queue.mature('final'), queue.cancel(0),
               queue.cancel((0, 'x'), by='site'), queue.dispatch(0, 'observed'),
               queue.dispatch(1, 'observed'), queue.dispatch(0, 'final'),
               ir.If(queue.empty, queue.reset(), ir.Skip())]
    dispatch = ir.Skip()
    for index, action in reversed(tuple(enumerate(actions))):
        dispatch = ir.If(ir.Op('eq', ir.Var('op'), ir.Lit(index)), action, dispatch)
    kinds = {**queue.types, 'op': types.INTEGER, 'value': types.INTEGER,
             'q': types.INTEGER, 'held': types.INTEGER}
    proc = ir.Procedure([*queue.domains, 'q'], ['op', 'value'],
        [name for name in kinds if name not in {*queue.domains, 'q', 'op', 'value'}],
        [], dispatch, types=kinds)
    env = {**queue.domains, 'q': ranges.Range(0, 16**depth-1),
           'op': ranges.Range(0, len(actions)-1), 'value': ranges.Range(0, 15)}
    proc.state_ranges = {name: env[name] for name in proc.state}
    return queue, proc, env, {**queue.boundary(), 'q': 0}


class Oracle:
    def __init__(self, *, terminal=False):
        self.rows = []
        self.q = 0
        self.terminal = terminal

    def step(self, op, value):
        hits, error = set(), ''
        if op < 3:
            self.rows.append({'tag': op, 'owner': int(op == 1), 'site': (int(op == 1), 'z' if op == 2 else 'x'),
                              'region': 'final' if op == 2 else 'observed', 'mature': False, 'value': value})
        elif op in {3, 4}:
            region = 'observed' if op == 3 else 'final'
            for row in self.rows:
                if row['region'] == region and not row['mature']:
                    row['mature'] = True
                    hits.add(f'outcome{row["tag"]}')
        elif op in {5, 6}:
            self.rows = [row for row in self.rows if row['mature'] or
                         (row['owner'] != 0 if op == 5 else row['site'] != (0, 'x'))]
        elif op in {7, 8, 9}:
            owner, region = int(op == 8), 'final' if op == 9 else 'observed'
            for i, row in enumerate(self.rows):
                if row['mature'] and row['owner'] == owner and row['region'] == region:
                    self.rows.pop(i)
                    hits.add(f'entry{row["tag"]}')
                    if self.terminal and row['tag'] == 0:
                        hits.add('action0')
                        error = 'RTLAssertion'
                    else:
                        self.q = self.q * 16 + row['value']
                    break
        return hits, error


def _assert_rows(queue, state, oracle):
    rows = []
    for i in range(state[queue.used]):
        status = state[queue.status][i]
        if not status:
            continue
        tag = state[queue.tag][i]
        rows.append((tag, status == queue.MATURE, state[queue.payloads[tag][0]][i]))
    assert rows == [(row['tag'], row['mature'], row['value']) for row in oracle.rows]
    assert state['q'] == oracle.q


def _encoded_step(proc, env, state, inputs, post, hits, error, encoded=None):
    import z3

    encoded = encoded or encoder.encode_step(proc, env=env)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for name, value in {**state, **inputs}.items():
        solver.add(*(term == cell for term, cell in _value_leaves(encoded.pre[name], value)))
    assert solver.check() == z3.sat
    model = solver.model()
    assert encoder.error_name(model.eval(encoded.error), encoded.error_codes) == error
    assert hits == {goal for goal, term in encoded.hits.items() if z3.is_true(model.eval(term))}
    for name, value in post.items():
        for term, cell in _value_leaves(encoded.post[name], value):
            actual = model.eval(term, model_completion=True)
            assert (z3.is_true(actual) == cell if z3.is_bool(actual)
                    else actual.as_long() == cell % (1 << actual.size()))
    return encoded


def _run(operations, *, terminal=False, symbolic=True):
    queue, proc, env, state = _machine(len(operations), terminal=terminal)
    oracle, all_hits, encoded = Oracle(terminal=terminal), set(), None
    for op, value in operations:
        inputs = {'op': op, 'value': value}
        post, _, hits, error = interp.run_step(proc, inputs, state)
        expected, failure = oracle.step(op, value)
        assert hits == expected and error == failure
        _assert_rows(queue, post, oracle)
        if symbolic:
            encoded = _encoded_step(proc, env, state, inputs, post, hits, error, encoded)
        all_hits.update(hits)
        state = post
        if error:
            break
    return state['q'], all_hits, error


@pytest.mark.parametrize('operations,q', [
    ([(0, 3), (0, 9), (3, 0), (7, 0), (7, 0)], 57),
    ([(0, 3), (2, 9), (6, 0), (3, 0), (4, 0), (7, 0), (9, 0)], 9),
    ([(0, 3), (1, 9), (5, 0), (3, 0), (7, 0), (8, 0)], 9),
    ([(0, 3), (1, 9), (3, 0), (5, 0), (6, 0), (8, 0), (7, 0)], 147),
    ([(0, 3), (6, 0), (0, 9), (3, 0), (7, 0)], 9),
    ([(0, 3), (3, 0), (0, 9), (6, 0), (7, 0), (3, 0), (7, 0)], 3),
    ([(0, 3), (3, 0), (7, 0), (10, 0), (0, 9), (3, 0), (7, 0)], 57),
])
def test_frame_order_region_and_cancellation_match_solver(operations, q):
    assert _run(operations)[0] == q


def test_maturity_survives_terminal_action_without_inventing_later_entry():
    q, hits, error = _run([(0, 3), (1, 9), (3, 0), (7, 0), (8, 0)], terminal=True)
    assert error == 'RTLAssertion' and q == 0
    assert hits == {'outcome0', 'outcome1', 'entry0', 'action0'}


def test_all_four_command_words_preserve_independent_queue_oracle():
    queue, proc, _, initial = _machine(4)
    for word in product(range(10), repeat=4):
        state, oracle = initial, Oracle()
        for position, op in enumerate(word):
            post, _, hits, error = interp.run_step(proc, {'op': op, 'value': position+1}, state)
            expected, failure = oracle.step(op, position+1)
            assert hits == expected and error == failure
            _assert_rows(queue, post, oracle)
            state = post


@pytest.mark.parametrize('steps', [1, 7, 100])
def test_storage_bound_is_derived_from_finite_source_emissions(steps):
    marker = ir.Assign('emit', ir.Lit(0))
    report = SourceReport(0, 0, 'observed', 'hit', (), ir.Skip(), marker)
    body = ir.Seq(marker, ir.For('i', ir.Var('n'),
        ir.If(ir.Op('eq', ir.Var('i'), ir.Lit(1)), marker, ir.For('j', 2, marker))))
    queue = SourceReportQueue((report,), (body,), {'n': ranges.Range(0, 3)}, transition_bound=steps)
    assert queue.per_transition == 7
    assert queue.capacity == 7 * steps
    assert not any(id(cmd) == id(marker) for cmd in ir.walk(queue.lower(body)))


def test_unproved_iteration_domain_cannot_become_a_queue_cutoff():
    marker = ir.Assign('emit', ir.Lit(0))
    report = SourceReport(0, 0, 'observed', 'hit', (), ir.Skip(), marker)
    with pytest.raises(Refused, match='finite source emission bound'):
        SourceReportQueue((report,), (ir.For('i', ir.Var('n'), marker),), {}, transition_bound=3)


def test_unused_payload_carriers_do_not_constrain_real_capture_values():
    marker = ir.Assign('emit', ir.Lit(0))
    report = SourceReport(0, 0, 'observed', 'hit',
        (ReportCapture('v', types.INTEGER, ranges.Range(5, 9)),), ir.Skip(), marker)
    queue = SourceReportQueue((report,), (marker,), {}, transition_bound=2)
    field, = queue.payloads[0]
    assert queue.boundary()[field][0] == 0
    assert queue.domains[field].lo == 0 and queue.domains[field].hi == 9
