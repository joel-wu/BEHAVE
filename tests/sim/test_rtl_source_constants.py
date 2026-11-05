"""Source copy folding changes neither execution nor retained event producers."""

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.rtl.source_constants import coalesce_inactive_occurrences, fold_literal_copies
from behave.sim.rtl.source_ir import _sequence


def _run(command, state):
    machine, hits = interp._Interp(dict(state)), set()
    machine.run(command, hits)
    return machine.rho, hits


def _producers(command):
    return {(type(part).__name__, part.goal) for part in ir.walk(command)
            if isinstance(part, (ir.Mark, ir.Fail))}


@pytest.mark.parametrize('constant', range(8))
def test_closed_copy_chain_preserves_results_hits_and_false_producers(constant):
    copied = ir.Assign('copy', ir.Op('uwrap', ir.Var('captured'), ir.Lit(3)))
    condition = ir.Assign('condition', ir.Op('lt', ir.Var('copy'), ir.Lit(3)))
    body = _sequence([ir.Assign('captured', ir.Lit(constant)), copied, condition,
        ir.If(ir.Var('condition'), _sequence([ir.Mark('yes'), ir.Assign('q', ir.Var('a'))]),
              _sequence([ir.Mark('no'), ir.Assign('q', ir.Op('add', ir.Var('a'), ir.Var('copy')))])),
        ir.If(ir.Op('eq', ir.Var('copy'), ir.Lit(20)), ir.Fail('invalid', 'failure'), ir.Skip())])
    result = fold_literal_copies(body, ('captured', 'copy', 'condition'))
    assert _producers(result) == _producers(body)
    for a in range(8):
        assert _run(result, {'a': a}) == _run(body, {'a': a})
    assert all(isinstance(part.guard, ir.Lit) and part.guard.value is False
               for part in ir.walk(result) if isinstance(part, ir.If))


def test_state_and_multiply_written_copies_keep_their_live_values():
    body = _sequence([ir.Assign('argument', ir.Lit(1)),
        ir.If(ir.Var('change'), ir.Assign('argument', ir.Lit(2)), ir.Skip()),
        ir.Assign('q', ir.Op('add', ir.Var('argument'), ir.Var('state'))),
        ir.Assign('state', ir.Lit(99))])
    result = fold_literal_copies(body, ('argument',))
    for change in (False, True):
        for state in range(4):
            initial = {'state': state, 'change': change}
            assert _run(result, initial) == _run(body, initial)


def test_loop_index_and_later_turn_writes_are_not_constant_copies():
    body = _sequence([ir.Assign('sum', ir.Lit(0)),
        ir.Assign('count', ir.Lit(4)), ir.Assign('i', ir.Lit(8)),
        ir.For('i', ir.Var('count'), _sequence([
            ir.Assign('sum', ir.Op('add', ir.Var('sum'), ir.Var('i'))),
            ir.If(ir.Op('eq', ir.Var('i'), ir.Lit(2)), ir.Mark('two'), ir.Skip())]))])
    result = fold_literal_copies(body, ('sum', 'count', 'i'))
    assert _run(result, {}) == _run(body, {})
    assert _run(result, {})[1] == {'two'}


def test_rewrite_is_depth_safe_and_keeps_transient_state_writes():
    body = _sequence([ir.Assign('state', ir.Lit(1)), ir.Mark('changed'),
                      ir.Assign('state', ir.Lit(0)), ir.Mark('restored')])
    for _ in range(1500):
        body = ir.If(ir.Var('yes'), body, ir.Mark('never'))
    body = ir.Seq(ir.Assign('yes', ir.Lit(True)), body)
    result = fold_literal_copies(body, ('yes',))
    assert _producers(result) == _producers(body)
    assert _run(result, {'state': 0}) == _run(body, {'state': 0})
    assert [part.expr.value for part in ir.walk(result)
            if isinstance(part, ir.Assign) and part.var == 'state'] == [1, 0]


def _occurrence_hits(hits, occurrences):
    return {(key, goal) for mark, key, goal in occurrences if mark in hits}


def test_inactive_helper_occurrences_share_false_producers_not_catalogue_entries():
    rows = tuple((f'mark{i}', f'call{i}', f'goal{i % 2}') for i in range(20))
    rows += (('live', 'live-call', 'goal0'),)
    body = _sequence([
        ir.If(ir.Lit(False), _sequence([ir.Mark(row[0]) for row in rows[:-1]]), ir.Skip()),
        ir.If(ir.Var('hit'), ir.Mark('live'), ir.Skip()), ir.Assign('q', ir.Var('a'))])
    result, retained = coalesce_inactive_occurrences(body, rows)
    assert tuple((key, goal) for _, key, goal in retained) == tuple((key, goal) for _, key, goal in rows)
    assert {mark for mark, _, _ in retained} == {'mark0', 'mark1', 'live'}
    assert {mark for _, mark in _producers(result)} == {'mark0', 'mark1', 'live'}
    assert ir.cost(result) < ir.cost(body)
    for a in range(4):
        for hit in (False, True):
            before, original_hits = _run(body, {'a': a, 'hit': hit})
            after, revised_hits = _run(result, {'a': a, 'hit': hit})
            assert before == after
            assert _occurrence_hits(original_hits, rows) == _occurrence_hits(revised_hits, retained)


def test_shared_commands_keep_every_live_path_and_unknown_guard():
    shared = ir.Mark('shared')
    rows = (('dead', 'dead-key', 'goal'), ('shared', 'shared-key', 'goal'),
            ('unknown', 'unknown-key', 'goal'))
    body = _sequence([
        ir.If(ir.Lit(False), _sequence([ir.Mark('dead'), shared]), ir.Skip()),
        ir.If(ir.Var('hit'), shared, ir.Mark('unknown'))])
    result, retained = coalesce_inactive_occurrences(body, rows)
    assert result is body and retained == rows


def test_terminal_and_private_producers_are_not_occurrence_aliases():
    rows = (('first', 'a', 'goal'), ('second', 'b', 'goal'), ('failure', 'c', 'goal'))
    body = ir.If(ir.Lit(False), _sequence([
        ir.Mark('first'), ir.Mark('second'), ir.Fail('terminal', 'failure'),
        ir.Mark('private'), ir.Fail('domain', 'domain')]), ir.Skip())
    result, retained = coalesce_inactive_occurrences(body, rows)
    assert retained == (rows[0], ('first', 'b', 'goal'), rows[2])
    assert _producers(result) == {('Mark', 'first'), ('Mark', 'private'),
                                   ('Fail', 'failure'), ('Fail', 'domain')}
    assert _run(result, {}) == _run(body, {})


def test_coalescing_nested_loops_is_depth_safe_and_keeps_index_effects():
    rows = (('first', 'a', 'goal'), ('second', 'b', 'goal'))
    body = ir.If(ir.Lit(False), ir.Mark('second'), ir.Skip())
    for _ in range(1500):
        body = ir.If(ir.Var('hit'), body, ir.Skip())
    body = ir.Seq(ir.If(ir.Lit(False), ir.Mark('first'), ir.Skip()),
                  ir.For('i', ir.Lit(2), body))
    result, retained = coalesce_inactive_occurrences(body, rows)
    assert retained == (rows[0], ('first', 'b', 'goal'))
    assert any(isinstance(part, ir.For) for part in ir.walk(result))
    for hit in (False, True):
        assert _run(result, {'hit': hit}) == _run(body, {'hit': hit})
