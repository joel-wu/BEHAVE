"""The two lowerings, on one construct each.

One builds a predicated term and one builds a control-flow graph, and they are
meant to compute the same step. That claim is checked on the population by a
harness outside the repository, which is fine for a number in a commit message
and not fine as the only statement of it: a claim nothing in the tree makes is
a claim nothing in the tree keeps.

So each construct that has broken is a case here, and the case is the construct
that broke it rather than a construct that happens to exercise it. Every fault
in the graph lowering so far was found by running the two against each other,
none by reading either, which is the argument for this file rather than for a
file of assertions about shapes.
"""

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import lower_procedure


def _model(body, args="a", reset="pass"):
    return ('class M:\n'
            f'    def reset(self): {reset}\n'
            f'    def process(self, {args}):\n'
            + ''.join(f'        {line}\n' for line in body))


#: What the term does and what the graph does, on the same inputs, for one
#: construct. The comment on each is the fault it is here for.
CASES = {
    "straight_line": _model(['z = a + 1', 'return {"y": z}']),
    "conditional": _model(['if a > 3:', '    z = 1', 'else:', '    z = 2',
                           'return {"y": z}']),
    # Both arms of a conditional landed in the block that branches, holding
    # only their marks, because the arms were lowered while that block was open.
    "conditional_effect": _model(['if a > 3:', '    z = 100 // (a - 3)',
                                  'else:', '    z = 0', 'return {"y": z}']),
    "loop": _model(['z = 0', 'for i in range(3):', '    z = z + a',
                    'return {"y": z}']),
    # The guard for the division was emitted into the block before the loop, so
    # it read the index before any iteration assigned it. This is the case that
    # turned the compound statements over to the graph side.
    "loop_index_in_a_guard": _model(['z = 0', 'for i in range(2):',
                                     '    z = z + (a // (i + 1))',
                                     'return {"y": z}']),
    "loop_with_a_break": _model(['z = 0', 'for i in range(4):',
                                 '    if i > a:', '        break',
                                 '    z = z + 1', 'return {"y": z}']),
    # A break belongs to the loop it is in. A single "something terminated"
    # answer cannot say that the statement beside the inner loop still runs.
    "break_in_a_nested_loop": _model(['z = 0', 'for i in range(2):',
                                      '    for j in range(3):', '        break',
                                      '    z = z + 1', 'return {"y": z}']),
    # A return inside a conditional was two instructions and a fall-through, so
    # both returns of `if a: return 1` then `return 2` ran and the second won.
    "early_return": _model(['if a > 3:', '    return {"y": 1}',
                            'return {"y": 2}']),
    "return_inside_a_loop": _model(['for i in range(3):',
                                    '    return {"y": i}',
                                    'return {"y": 9}']),
    # The same fault one level down: a helper's return leaves the helper, and
    # with a flag instead of an edge both of its returns ran.
    "helper_with_an_early_return": (
        'class M:\n'
        '    def reset(self): pass\n'
        '    def pick(self, v):\n'
        '        if v > 3:\n'
        '            return 1\n'
        '        return 2\n'
        '    def process(self, a):\n'
        '        return {"y": self.pick(a)}\n'),
    # A return reached from a retained loop leaves the inlined helper.  It must
    # neither terminate the whole procedure nor jump out of a loop surrounding
    # the call site.
    "helper_return_inside_a_loop": (
        'class M:\n'
        '    def reset(self): pass\n'
        '    def pick(self, v):\n'
        '        for i in range(4):\n'
        '            if i == v:\n'
        '                return i + 10\n'
        '        return 3\n'
        '    def process(self, a):\n'
        '        z = 0\n'
        '        for j in range(2):\n'
        '            z += self.pick(a)\n'
        '        return {"y": z}\n'),
    # Returning from an inlined helper is a normal continuation for the
    # caller.  Its flow-fact join must retain the list-comprehension carrier
    # that surrounds the call; otherwise the element store loses its shape.
    "helper_inside_a_comprehension": (
        'class M:\n'
        '    def reset(self): pass\n'
        '    def plus_one(self, v):\n'
        '        if v < 2:\n'
        '            return v + 1\n'
        '        return v - 1\n'
        '    def process(self, a):\n'
        '        xs = [self.plus_one(i) for i in range(4)]\n'
        '        return {"y": xs[a & 3]}\n'),
    # What an expression needs to run before it is read belongs where the
    # expression is evaluated, not before the statement.
    "guard_inside_an_arm": _model(['z = 1 if a else 7 // a',
                                   'return {"y": z}']),
    "short_circuit": _model(['z = 1 if (a and (7 // a)) else 0',
                             'return {"y": z}']),
    "while_loop": _model(['z = 0', 'n = a', 'while n > 0:', '    z = z + n',
                          '    n = n - 1', 'return {"y": z}']),
}


def test_the_production_lowering_is_the_graph_one():
    """Which lowering a certificate rests on, asserted rather than left to a default.

    The graph one, since M1. What that buys is that the questions about control flow are
    asked of a graph while it is still a graph: whether a use has a definition reaching
    it, whether a loop runs, whether two conditions are the same one. On the term path
    those were inference over the flattened program, and inference that each construct
    defeats in its own way: 34 models refused to encode for want of a width the term
    lowering had, because a `#reached` flag the range analysis could not see through
    ended a range behind it.

    Measured before the flip and unchanged after: the two paths agree on the step and the
    goals reached for all 609 models both can run, and no model lowers under one and not
    the other.
    """
    from behave.sim.behavior.lower_graph import GraphLowerer
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = CASES["conditional"]
    proc = lower_procedure(source)
    assert proc.graph is not None, "the default built a graph rather than a term"
    assert len(proc.graph.blocks) > 1

    # And through the entry point every caller uses -- where the rough range pass
    # runs on the **same** lowerer as the kept pass since review 16's follow-up:
    # ranges that decide the graph program's loop bounds are computed over the
    # graph's own lowering of the step, never the term oracle's.
    got = lower_with_state_ranges(source, input_widths=[8])
    assert got.graph is not None
    assert isinstance(lower_procedure(source, lowerer=GraphLowerer).graph,
                      type(proc.graph)), "asking for it explicitly is the same thing"


def test_while_strategy_dispatch_is_shared_not_graph_specific():
    from behave.sim.behavior.lowering import _Lowerer

    assert GraphLowerer._while is _Lowerer._while
    assert GraphLowerer._exact_while is not _Lowerer._exact_while
    assert GraphLowerer._bounded_while is not _Lowerer._bounded_while
    assert GraphLowerer._exit_bounded_while is not _Lowerer._exit_bounded_while


@pytest.mark.parametrize('graph', (False, True), ids=['term', 'graph'])
def test_non_exhausting_loop_keeps_else_goals_without_executing_else(graph):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.lowering import _Lowerer

    source = _model([
        'for i in range(3):',
        '    if a:',
        '        break',
        '    return {"y": 13}',
        'else:',
        '    unreachable = 1 // 0',
        'return {"y": a}',
    ])
    native, sites, native_hits = instrumented.load_source(source)
    proc = lower_procedure(
        source, input_widths=[2], lowerer=GraphLowerer if graph else _Lowerer)
    declared = {goal for site in sites for goal in site.goals}
    assert declared <= ir.goals_of(proc.body)
    for value in range(4):
        native_hits.clear()
        expected = native['M']().process(value)
        _state, output, hits, error = interp.run_step(proc, {'a': value})
        assert not error
        assert output.transactions == [expected]
        assert hits == set(native_hits)


def _full_steps(proc, rows):
    """Full step semantics, chained: [(state, transactions, hits, exact error)].

    What review 17 P0-1 named: comparing outputs and hits alone passed a lowerer
    whose *state update* flipped `+` to `-`, and comparing `bool(error)` passed one
    that halts with the wrong code. So the tuple carries the next state and the
    exact code, and the rows chain state so the second step runs on what the first
    left.
    """
    state, trace = None, []
    for row in rows:
        state, out, hits, err = interp.run_step(proc, row, state=state)
        trace.append((dict(state), list(out.transactions), set(hits), err))
        if err:
            break
    return trace


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("value", [0, 1, 4, 9])
def test_the_two_lowerings_compute_the_same_step(name, value):
    """**The secondary regression between the two lowerings** -- secondary because
    they share `_Lowerer`'s expression lowering, so a fault there makes both wrong
    together and only the native oracle (`test_native_oracle`) sees it. What this
    still holds: the two *control-flow* constructions, over full step semantics
    (next state, outputs, hits, exact error code) across two chained steps. When
    the graph became the default this silently compared the graph with itself;
    the term side asks for the term lowerer by name now, and
    `test_a_broken_lowerer_fails_this_oracle` holds the harness effective.
    """
    from behave.sim.behavior.lowering import _Lowerer

    source = CASES[name]
    # The public task owns the input carrier.  A source ``while`` cannot have
    # a finite semantic plan if this direct unit-level call withholds that
    # carrier, so give both lowerers the same small task domain explicitly.
    term = lower_procedure(source, input_widths=[8], lowerer=_Lowerer)
    graph = lower_procedure(source, input_widths=[8], lowerer=GraphLowerer)
    assert term.graph is None and graph.graph is not None, \
        "the two sides really are the two lowerers"
    rows = [{"a": value}, {"a": (value + 3) % 10}]
    assert _full_steps(term, rows) == _full_steps(graph, rows)


def test_a_loop_over_a_value_runs_the_iterations_that_are_due():
    """A count that is not a number unrolls to its largest value, and an
    iteration past the count is an edge to the loop's exit rather than a guard
    on the body. The term needed a flag for this and the graph does not."""
    from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

    source = _model(['z = 0', 'for i in range(a):', '    z = z + 1',
                     'return {"y": z}'])
    term = lower_with_state_ranges(source, input_widths=[3], lowerer=_Lowerer)
    graph = lower_with_state_ranges(source, input_widths=[3],
                                    lowerer=GraphLowerer)
    assert term.graph is None and graph.graph is not None
    for value in range(8):
        _, out_term, hits_term, _ = interp.run_step(term, {"a": value})
        _, out_graph, hits_graph, _ = interp.run_step(graph, {"a": value})
        assert out_graph.transactions == [{"y": value}]
        assert out_term.transactions == out_graph.transactions
        assert hits_term == hits_graph


def test_cfg_entry_facts_bound_nested_retained_loops_for_a_task_signature():
    """An exact CFG predecessor fact must reach the retained loop planner.

    With a finite task signature the output-contract query builds an encoding
    plan immediately.  If-conversion loses the temporary holding ``1 << a`` at
    the outer loop entry, while the graph has its exact ``[1, 8]`` range.  The
    graph fact must participate soon enough for the range walk to enter the
    outer body and discover the inner loop bound as well.
    """
    from behave.sim.behavior.lowering import lower_with_state_ranges
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.schema import Endpoint, TransactionSchema

    source = _model([
        "n = 1 << a",
        "z = 0",
        "for i in range(n):",
        "    m = 8 // n",
        "    for j in range(m):",
        "        z += i + j",
        'return {"y": z & 255}',
    ])
    signature = ProcedureSignature.from_schema(
        TransactionSchema([
            Endpoint("a", "input", 2),
            Endpoint("y", "output", 8),
        ]),
        input_names=("a",),
    )

    proc = lower_with_state_ranges(
        source, outputs=["y"], signature=signature, bound=1)

    assert len([cmd for cmd in ir.walk(proc.body)
                if isinstance(cmd, ir.For)]) == 2


def test_the_body_of_a_loop_is_retained_once():
    """The frontend's structure is independent of the encoding maximum."""
    source = _model(['z = 0', 'for i in range(3):',
                     '    z = z + (a // (i + 1))', 'return {"y": z}'])
    low = GraphLowerer.__mro__[0]
    proc = lower_procedure(source, lowerer=low)
    loops = [cmd for cmd in ir.walk(proc.body) if isinstance(cmd, ir.For)]
    assert len(loops) == 1
    assert loops[0].bound == 3

    much_larger = lower_procedure(
        source.replace("range(3)", "range(3000)"), lowerer=low)
    assert ir.cost(much_larger.body) == ir.cost(proc.body), \
        "the domain maximum belongs to the encoding plan, not semantic IR"
    # The outer graph remains acyclic; the loop is a structured instruction.
    assert len(proc.graph.order()) <= len(proc.graph.blocks)


def test_the_rough_range_pass_runs_on_the_lowerer_whose_output_is_kept():
    """Review 16's ordering, the T3-11 subitem itself: the term planner is out of
    the production graph path. `plan_state`'s rough pass ran on `_Lowerer` for
    cost, so the ranges deciding the graph program's loop bounds were computed
    over a different lowering of the step -- two authorities over one question,
    held together by a population-level agreement number with no per-run check.
    The rough pass now takes the same `lowerer` as the kept pass, threaded from
    `lower_with_state_ranges`.
    """
    from unittest import mock

    from behave.sim.behavior import lowering as lowering_module
    from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges

    source = _model(['z = 0', 'for i in range(a):', '    z = z + 1',
                     'return {"y": z}'])
    used = []
    real = lowering_module.lower_procedure

    def spying(*args, **kwargs):
        got = real(*args, **kwargs)
        used.append("term" if got.graph is None else "graph")
        return got

    with mock.patch.object(lowering_module, "lower_procedure", spying):
        lower_with_state_ranges(source, input_widths=[3])
    assert used == ["graph", "graph"], \
        f"both passes on the production lowerer, got {used}"

    used.clear()
    with mock.patch.object(lowering_module, "lower_procedure", spying):
        lower_with_state_ranges(source, input_widths=[3], lowerer=_Lowerer)
    assert used == ["term", "term"], \
        "an explicit term caller (the oracle) is coherent too"


def test_a_broken_lowerer_fails_this_oracle():
    """The differential harness is only evidence while a mutation of either side
    fails it -- a vacuous oracle passed for a release (review 16 / todo3 T3-11:
    "mutation of either path must fail the differential test"). One semantic
    mutation per side, run through exactly the comparison the parity test makes,
    and each must be caught.
    """
    from behave.sim.symbolic import ir
    from behave.sim.behavior.lowering import _Lowerer

    source = CASES["conditional"]

    def disagree(mutant, against):
        for value in (0, 1, 4, 9):
            rows = [{"a": value}, {"a": (value + 3) % 10}]
            a = _full_steps(lower_procedure(source, lowerer=mutant), rows)
            b = _full_steps(lower_procedure(source, lowerer=against), rows)
            if a != b:
                return True
        return False

    # A graph side that flips every branch condition must be caught by the term
    # oracle, and a term side that does the same must be caught by the graph.
    class BrokenGraph(GraphLowerer):
        def expr(self, node):
            import ast as _ast

            got = super().expr(node)
            if isinstance(node, _ast.Compare):
                return ir.Op("not", got)
            return got

    class BrokenTerm(_Lowerer):
        def expr(self, node):
            import ast as _ast

            got = super().expr(node)
            if isinstance(node, _ast.Compare):
                return ir.Op("not", got)
            return got

    assert disagree(BrokenGraph, _Lowerer), \
        "a mutated graph lowering must fail the differential"
    assert disagree(BrokenTerm, GraphLowerer), \
        "a mutated term lowering must fail the differential"
    assert not disagree(GraphLowerer, _Lowerer), \
        "and the unmutated pair still agrees"

    # Review 17 P0-1's exact miss: a mutation visible only in the **next state**
    # (the step's output is untouched). The output-and-hits comparison passed it;
    # the full-tuple comparison must not.
    stateful = _model(['self.x = (self.x + a) & 0xFF', 'return {"y": a}'],
                      reset="self.x = 0")

    class BrokenGraphState(GraphLowerer):
        def expr(self, node):
            import ast as _ast

            got = super().expr(node)
            if (isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Add)
                    and isinstance(got, ir.Op) and got.op == "add"):
                return ir.Op("sub", *got.args)
            return got

    def disagree_on(src, mutant, against):
        rows = [{"a": 1}, {"a": 1}]
        return (_full_steps(lower_procedure(src, lowerer=mutant), rows)
                != _full_steps(lower_procedure(src, lowerer=against), rows))

    assert disagree_on(stateful, BrokenGraphState, _Lowerer), \
        "a state-only fault must fail the full-semantics differential"
