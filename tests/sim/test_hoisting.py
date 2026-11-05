"""What a value needs run first, in the two lowerings that mean different things by it.

An expression rule returns a value and sometimes needs something to have run before that
value can be read: a division guard, the conditional a short circuit becomes, the copy
that pins an array. `before` is how a rule says so, and `mark` / `hoisted_since` are how
a caller asks whether a particular expression said it.

The two lowerings answer all three differently and neither answer is more correct. In the
term lowering "before" is a queue prepended to the statement being built. In the graph
lowering it is the block being filled, which is not a queue and has nothing to prepend
to. That is the point of the three methods: the rules say what they mean, and where it
goes is the lowering's business.

Each test here constructs one lowerer and asks it one question, which is what M2 asks
for. The term lowerer's answers do not depend on the graph existing, and the graph's do
not depend on the term.
"""
import ast

from behave.sim.symbolic import cfg, ir
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer


def term():
    return _Lowerer([])


def graph():
    return GraphLowerer([])


def _deep_sequence(length=1500):
    """A straight-line term deeper than Python's default recursion limit."""
    cmd = ir.Skip()
    for index in range(length):
        cmd = ir.Seq(cmd, ir.Assign(f"v{index}", ir.Lit(index)))
    return cmd


# ── the term lowering: a queue prepended to the statement ──

def test_a_term_lowering_prepends_what_was_hoisted():
    low = term()
    low.before(ir.Assign("t", ir.Lit(1)))
    out = low._flush(ir.Assign("y", ir.Var("t")))
    assert isinstance(out, ir.Seq)
    assert isinstance(out.first, ir.Assign) and out.first.var == "t"


def test_a_term_flush_empties_the_queue():
    """Two statements in a row must not each carry the other's hoists."""
    low = term()
    low.before(ir.Assign("t", ir.Lit(1)))
    low._flush(ir.Skip())
    assert low._flush(ir.Assign("y", ir.Lit(0))).__class__ is ir.Assign


def test_a_mark_is_about_this_expression_and_not_about_the_queue():
    low = term()
    low.before(ir.Assign("t", ir.Lit(1)))
    mark = low.mark()
    assert not low.hoisted_since(mark)      # the queue is not empty; nothing new is in it
    low.before(ir.Assign("u", ir.Lit(2)))
    assert low.hoisted_since(mark)


# ── the graph lowering: the block being filled ──

def test_a_graph_lowering_puts_what_was_hoisted_in_the_block():
    low = graph()
    low.before(ir.Assign("t", ir.Lit(1)))
    assert [c.var for c in low._instrs] == ["t"]


def test_a_graph_flush_prepends_nothing():
    """It is already in the block, in order, so there is nothing to prepend it to."""
    low = graph()
    low.before(ir.Assign("t", ir.Lit(1)))
    cmd = ir.Assign("y", ir.Var("t"))
    assert low._flush(cmd) is cmd


def test_a_full_block_is_not_a_hoist():
    """The regression the mark exists for.

    The block being filled is non-empty for almost the whole of a step, so "is there
    anything queued" is true almost always in the graph and every condition took a name
    it did not need. The question is whether *this* expression hoisted anything.
    """
    low = graph()
    low.before(ir.Assign("t", ir.Lit(1)))
    mark = low.mark()
    assert not low.hoisted_since(mark)


def test_crossing_a_block_boundary_counts_as_a_hoist():
    """A count is only comparable with a count of the same block.

    An inlined call or a scoped arm closes the block and opens another, so a value
    lowered across the boundary needed something by definition, and a fresh block that
    happens to hold as many instructions as the old one must not read as unchanged.
    """
    low = graph()
    low.before(ir.Assign("t", ir.Lit(1)))
    mark = low.mark()
    low.close(cfg.Goto("next"))
    low.open("next")
    low.before(ir.Assign("u", ir.Lit(2)))
    assert low.hoisted_since(mark)


def test_a_graph_flattens_a_deep_sequence_without_python_recursion():
    low = graph()
    low.emit(_deep_sequence())
    assert len(low._instrs) == 1500
    assert low._instrs[0].var == "v0"
    assert low._instrs[-1].var == "v1499"


def test_deep_straight_line_helper_initialization_is_inserted_in_order():
    low = graph()
    mark = low.mark()
    low._insert_straight_line(mark, _deep_sequence())
    assert len(low._instrs) == 1500
    assert low._instrs[0].var == "v0"
    assert low._instrs[-1].var == "v1499"


# ── the two together, on the one thing they must agree about ──

def test_a_pure_condition_needs_no_name_in_either_lowering():
    """`_atom_of` returns the expression itself when the rule hoisted nothing."""
    node = ast.parse("a > 1", mode="eval").body
    for low in (term(), graph()):
        before, value = low._atom_of(node)
        assert before is None, type(low).__name__
        assert isinstance(value, ir.Op), type(low).__name__


def test_a_guarded_condition_takes_a_name_in_either_lowering():
    """A division guard is hoisted, so the value cannot stay in expression position."""
    node = ast.parse("(7 // a) > 1", mode="eval").body
    for low in (term(), graph()):
        before, value = low._atom_of(node)
        assert before is not None, type(low).__name__
        assert isinstance(value, ir.Var), type(low).__name__
