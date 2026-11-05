"""Unit tests for lowering an admitted model to the representation.

Two obligations are under test. Every declared goal must have a ``mark`` in the
lowered program, or a hit predicted by the encoding and a hit reported by a run
would be different events. Every partial operation must be guarded, or a
failure would be an exception the semantics does not model instead of a marked
safety goal.
"""
import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.behavior.goals import goal_set
from behave.sim.behavior.lowering import LoweringError, lower_expression, lower_procedure, lower_with_state_ranges

MODEL = '''
class M:
    def reset(self):
        self.acc = 0

    def process(self, a, b):
        if a > b:
            self.acc = a - b
        else:
            self.acc = b - a
        return {"y": self.acc}
'''


def _marks(cmd):
    return [c.goal for c in ir.walk(cmd) if isinstance(c, ir.Mark)]


def test_every_declared_goal_has_a_mark():
    proc = lower_procedure(MODEL)
    assert set(_marks(proc.body)) == set(goal_set(MODEL))


def test_the_lowered_procedure_is_normalized():
    assert ir.normalized(lower_procedure(MODEL)) == []


def test_inputs_outputs_and_state_are_separated():
    proc = lower_procedure(MODEL)
    assert proc.inputs == ["a", "b"]
    assert proc.outputs == ["y"]
    assert proc.state == ["acc"]


def test_a_division_is_guarded_and_marked():
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a, b):\n'
           '        q = a // b\n'
           '        return {"y": q}\n')
    proc = lower_procedure(src)
    guards = [c for c in ir.walk(proc.body)
              if isinstance(c, ir.If) and any(isinstance(x, ir.Fail)
                                              for x in ir.walk(c.then))]
    assert len(guards) == 1
    # The failure carries the goal it reports, rather than a mark being written
    # beside it and something else checking that the two stayed together.
    assert any(isinstance(c, ir.Fail) and "divzero" in (c.goal or "")
               for c in ir.walk(guards[0].then))
    assert ir.normalized(proc) == []


def test_an_index_is_guarded():
    src = ('class M:\n'
           '    def reset(self): self.tab = [0] * 8\n'
           '    def process(self, a):\n'
           '        v = self.tab[a]\n'
           '        return {"y": v}\n')
    proc = lower_procedure(src)
    assert any(isinstance(c, ir.Fail) and c.code == "IndexError"
               for c in ir.walk(proc.body))


def test_scalar_min_and_max_lower_to_exact_comparisons():
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a, b):\n'
           '        return {"y": max(min(a, b), 0)}\n')
    proc = lower_procedure(src)

    for inputs, expected in (
            ({"a": -2, "b": 3}, 0),
            ({"a": 7, "b": 3}, 3),
            ({"a": 4, "b": 4}, 4)):
        _state, output, _hits, error = interp.run_step(proc, inputs)
        assert not error
        assert output.transactions == [{"y": expected}]


def test_the_lowering_produces_every_goal_the_catalogue_declares():
    """Including both outcomes of a loop whose count is a literal.

    This asserted the opposite: that a literal loop declares one outcome, because the
    unrolling has no guard that could report the other. That made the catalogue's
    content depend on what the lowering would emit, which is the wrong direction and
    had a consequence beyond tidiness: the case ruled out by the source was recorded
    as impossible by construction, on the frontend's word, while the same case in a
    data-dependent loop was decided by a query.

    Both are declared now. The lowering emits a test of the trip count either way, so
    for `range(4)` the zero-trip mark sits under a condition the encoder folds to
    false and the query reports it unreachable at the bound.
    """
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        y = 0\n'
           '        for i in range(4):\n'
           '            y = y + a\n'
           '        return {"y": y}\n')
    proc = lower_procedure(src)
    declared = set(goal_set(src))
    marked = {c.goal for c in ir.walk(proc.body) if isinstance(c, ir.Mark)}

    loop_goals = {g for g in declared if g.startswith("branch:") and ":5:" in g}
    assert len(loop_goals) == 2, "zero trip and non-zero trip"
    assert loop_goals <= marked, "and the lowering produces both"
    assert declared <= marked, "every declared goal has a producer"
    assert proc.impossible == {}, "nothing is ruled out on the frontend's word"


def test_a_loop_outcome_is_the_trip_count_and_not_the_unrolled_guard():
    """The distinction the two outcomes would lose if they were the guard's.

    A loop whose count is a value unrolls to its largest count with each iteration
    guarded by `j < n`. One run with `n = 1` under a bound of 4 makes that guard true
    once and false three times, so if the outcomes were the guard's then a single run
    would cover both and `n = 0` would never be tested. The mark is a test of the
    clamped trip count emitted before the loop instead, so `n = 0` reaches only the
    zero-trip goal and any positive count reaches only the other.
    """
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        z = 0\n'
           '        for i in range(a):\n'
           '            z = z + 1\n'
           '        return {"y": z}\n')
    proc = lower_with_state_ranges(src, input_widths=[3])
    loop_goals = sorted(g for g in goal_set(src)
                        if g.startswith("branch:") and ":5:" in g)
    zero = {g for g in loop_goals if g.endswith(":F")}
    nonzero = {g for g in loop_goals if g.endswith(":T")}
    assert len(zero) == 1 and len(nonzero) == 1

    for value in (0, 1, 4, 7):
        hits = interp.run_step(proc, {"a": value})[2] & set(loop_goals)
        assert hits == (zero if value == 0 else nonzero), (value, hits)


def test_an_effect_stays_in_the_branch_that_evaluates_it():
    """What an expression needs to run before it is read belongs where the
    expression is evaluated. Hoisting it to the statement makes the step fail
    on an operand the source never evaluates, and the safety goal it reaches
    is one no run of the model reaches."""
    cases = (
        ('1 if a else 7 // b', {"a": 1, "b": 0}, 1),
        ('1 if (a or (7 // b)) else 0', {"a": 1, "b": 0}, 1),
        ('1 if (a and (7 // b)) else 0', {"a": 0, "b": 0}, 0),
    )
    for expression, inputs, want in cases:
        src = ('class M:\n'
               '    def reset(self): pass\n'
               '    def process(self, a, b):\n'
               f'        return {{"y": {expression}}}\n')
        proc = lower_procedure(src)
        _, out, _, error = interp.run_step(proc, inputs)
        assert not error, f"{expression} failed on {inputs}"
        assert out.transactions == [{"y": want}]


def test_the_condition_sites_are_the_leaves_of_the_boolean_tree():
    """An inner node of a boolean tree carries the position of its own first
    operand, so a site looked up by position finds that operand and is marked
    with the value of everything the operator combines: `a and (b or c)`
    reported b as true whenever `b or c` was."""
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a, b, c):\n'
           '        y = 1 if (a and (b or c)) else 0\n'
           '        return {"y": y}\n')
    proc = lower_procedure(src)
    marked = {c.goal for c in ir.walk(proc.body) if isinstance(c, ir.Mark)}

    # b is false and c is true, so the atom b is false and the decision holds.
    _, out, hits, _ = interp.run_step(proc, {"a": 1, "b": 0, "c": 1})
    assert out.transactions == [{"y": 1}]
    at_b = {g for g in marked if ":4:25:" in g}
    assert len(at_b) == 2, "b has both outcomes as a condition goal"
    assert {g for g in at_b if g.endswith(":F")} <= hits
    assert not {g for g in at_b if g.endswith(":T")} & hits


def test_lower_procedure_preserves_a_loop_else_clause():
    """The direct lowering entry point keeps normal-exhaustion semantics."""
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        y = 0\n'
           '        for i in range(2):\n'
           '            y = y + 1\n'
           '        else:\n'
           '            y = 99\n'
           '        return {"y": y}\n')
    proc = lower_procedure(src)
    _state, output, _hits, error = interp.run_step(proc, {"a": 0})
    assert not error
    assert output.transactions == [{"y": 99}]


def test_an_index_into_an_array_of_unknown_length_is_refused():
    """What a subscript means depends on how many cells there are: an index of
    minus one names the last of them. An array whose length is not fixed
    before the run therefore has no reading, and is refused rather than
    given one."""
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        v = self.tab[a]\n'
           '        return {"y": v}\n')
    with pytest.raises(LoweringError):
        lower_procedure(src)


def test_a_guard_precedes_the_command_that_uses_the_operation():
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a, b):\n'
           '        return {"y": a // b}\n')
    proc = lower_procedure(src)
    # The guard is emitted before the assignment of the output, so a failing
    # division never reaches the assignment.
    cmds = list(ir.walk(proc.body))
    guard = next(i for i, c in enumerate(cmds)
                 if isinstance(c, ir.If)
                 and any(isinstance(x, ir.Fail) for x in ir.walk(c.then)))
    out = next(i for i, c in enumerate(cmds)
               if isinstance(c, ir.Store)
               and c.var == proc.output_vars["y"])
    assert guard < out


def test_an_augmented_assignment_expands_to_its_operator():
    src = ('class M:\n'
           '    def reset(self): self.acc = 0\n'
           '    def process(self, a):\n'
           '        self.acc += a\n'
           '        return {"y": self.acc}\n')
    proc = lower_procedure(src)
    assert any(isinstance(c, ir.Assign) and isinstance(c.expr, ir.Op)
               and c.expr.op == "add" for c in ir.walk(proc.body))


def test_expressions_map_to_core_primitives():
    e, pending = lower_expression("a + b * 2")
    assert isinstance(e, ir.Op) and e.op == "add"
    assert pending == []


def test_a_form_with_no_rule_is_rejected_with_its_position():
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        x = [i for i in range(a)]\n'
           '        return {"y": x}\n')
    with pytest.raises(LoweringError) as excinfo:
        lower_procedure(src)
    assert "line 4" in str(excinfo.value)

def test_a_statement_says_how_it_can_finish():
    """Whether control can leave at a statement is the lowering's own answer.

    It used to be a second walk over the source, in a predicate that had to
    agree with the flags `_live` tests, with nothing holding the two together.
    """
    import ast as _ast

    from behave.sim.behavior.lowering import NORMAL, _Lowerer

    low = _Lowerer([])
    low.activation.destination = None

    def ways(source):
        return low.stmt(_ast.parse(source).body[0])[1]

    assert ways("x = 1") == {NORMAL}
    assert ways("pass") == {NORMAL}
    # A conditional finishes however either branch can, and an absent else is
    # a branch that falls through.
    assert ways("if a:\n    x = 1\n") == {NORMAL}


def test_a_break_in_a_nested_loop_is_consumed_by_that_loop():
    """The statement after the inner loop still runs. A single "did something
    terminate" answer cannot express this, which is why the loop reports how it
    finishes rather than the block inferring it from the source."""
    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.x = 0\n'
           '    def process(self, a):\n'
           '        for i in range(2):\n'
           '            for j in range(3):\n'
           '                break\n'
           '            self.x = self.x + 1\n'
           '        return {"y": self.x}\n')
    proc = lower_procedure(src)
    _, out, _, error = interp.run_step(proc, {"a": 1})
    assert not error
    assert out.transactions == [{"y": 2}], "both outer iterations ran"


def test_a_return_inside_a_loop_escapes_the_loop():
    """The other way round: a return leaves the procedure, so what is written
    after the loop does not run either."""
    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.x = 0\n'
           '    def process(self, a):\n'
           '        for i in range(3):\n'
           '            self.x = 5\n'
           '            return {"y": self.x}\n'
           '        self.x = 99\n'
           '        return {"y": self.x}\n')
    proc = lower_procedure(src)
    _, out, _, error = interp.run_step(proc, {"a": 1})
    assert not error
    assert out.transactions == [{"y": 5}]


def test_a_loop_outcome_is_a_test_of_its_trip_count():
    """The lowering used to decide which outcome a loop can report and record
    the other in `impossible` for the closure to take on its word. That is the
    frontend certifying its own unreachability, and asking the query about it
    could not help: a goal with no site has a hit condition that is false by
    construction, so the query would agree for the same reason rather than an
    independent one.

    Now the outcome is a conditional on the count. Where the count is a number
    the encoder folds the test and the query proves the other outcome
    unsatisfiable, which is the same answer reached by something checkable.
    """
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        z = 0\n'
           '        for i in range(4):\n'
           '            z = z + a\n'
           '        return {"y": z}\n')
    proc = lower_procedure(src)
    assert proc.impossible == {}, "nothing is ruled out by fiat"

    # Both outcomes of the loop have a producer in the program, so the query is
    # what decides them rather than the lowering's bookkeeping.
    loop_marks = {c.goal for c in ir.walk(proc.body)
                  if isinstance(c, ir.Mark) and c.goal.startswith("branch:")
                  and ":5:" in c.goal}
    declared = {g for g in goal_set(src)
                if g.startswith("branch:") and ":5:" in g}
    assert loop_marks == declared


def test_a_loop_whose_count_is_a_value_reports_both_outcomes():
    """The same rule, asked of a count that is not a number. Both paths through
    the loop rule say what they mean in the representation now, where one of
    them used to say it in a side table."""
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        z = 0\n'
           '        for i in range(a):\n'
           '            z = z + 1\n'
           '        return {"y": z}\n')
    proc = lower_with_state_ranges(src, input_widths=[4])
    assert proc.impossible == {}
    marks = {c.goal for c in ir.walk(proc.body) if isinstance(c, ir.Mark)}
    assert len({g for g in marks
                if g.startswith("branch:") and ":5:" in g}) == 2, \
        "entered and not entered both have producers"
