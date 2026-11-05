"""Running the representation, and what a run must not touch.

The interpreter is one of the two readings the step audit compares, so a fault
here is invisible to that audit and shows up only against the model. These are
the properties that make a run mean what it says.
"""
import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.behavior.lowering import lower_with_state_ranges
from behave.sim.symbolic.uniform_array import UniformArray

MODEL = ('class M:\n'
         '    def reset(self):\n'
         '        self.seen = [0] * 4\n'
         '    def process(self, a):\n'
         '        self.seen[a & 3] = 1\n'
         '        return {"y": self.seen[a & 3]}\n')


@pytest.mark.parametrize("size", [1, 32, 1024])
@pytest.mark.parametrize("nested", [False, True])
def test_indexed_reads_copy_only_the_selected_value(monkeypatch, size, nested):
    values = [[i, i + 1] for i in range(size)] if nested else list(range(size))
    machine = interp._Interp({'a': values})
    copied = []
    original = interp.copy_value

    def record(value):
        copied.append(value)
        return original(value)

    monkeypatch.setattr(interp, 'copy_value', record)
    for index in range(size):
        expression = ir.Sel(ir.Var('a'), ir.Lit(index))
        if nested:
            expression = ir.Sel(expression, ir.Lit(0))
        assert machine.value(expression) == index
    # No dense ancestor traversals, regardless of its length or nesting.
    assert copied == list(range(size))


@pytest.mark.parametrize("compact", [False, True])
def test_selected_rows_and_watch_records_do_not_alias_state(compact):
    values = [[1, 2], [3, 4]]
    if compact:
        values = UniformArray.from_values(values)
    state, watch = {'a': values}, []
    machine = interp._Interp(state, watch)
    row = ir.Sel(ir.Var('a'), ir.Lit(0))
    returned = machine.value(row)
    returned[0] = 99
    assert state['a'][0] == [1, 2]
    machine.cmd(ir.Assign('b', row), set())
    machine.cmd(ir.Store('b', ir.Lit(0), ir.Lit(88)), set())
    assert state['a'][0] == [1, 2]
    assert state['b'] == [88, 2]
    assert watch[0][2] == [1, 2]
    assert watch[1][2] == [88, 2]


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("index", [-1, 2])
def test_out_of_range_nested_selection_retains_shape_and_boolean_type(compact, index):
    values = [[True, False], [False, True]]
    if compact:
        values = UniformArray.from_values(values)
    machine = interp._Interp({'a': values})
    row = ir.Sel(ir.Var('a'), ir.Lit(index))
    assert machine.value(row) == [False, False]
    assert machine.value(ir.Sel(row, ir.Lit(0))) is False
    assert machine.value(ir.Sel(row, ir.Lit(9))) is False
    assert interp._Interp({'a': []}).value(
        ir.Sel(ir.Var('a'), ir.Lit(index))) == 0


def test_selection_preserves_lazy_array_primitives_and_error_order():
    machine = interp._Interp({'a': [[5, 6]]})
    chosen = ir.Op('ite', ir.Lit(True), ir.Var('a'), ir.Var('absent'))
    row = machine.value(ir.Sel(chosen, ir.Lit(0)))
    row[0] = 99
    assert machine.rho['a'] == [[5, 6]]
    with pytest.raises(interp.InterpError, match='array whose cells'):
        machine.value(ir.Sel(ir.Lit(1), ir.Var('absent')))
    with pytest.raises(interp.InterpError, match='absent is read'):
        machine.value(ir.Sel(ir.Var('absent'), ir.Lit(True)))
    with pytest.raises(interp.InterpError, match='Boolean value'):
        machine.value(ir.Sel(ir.Var('a'), ir.Lit(True)))


def test_a_run_does_not_write_into_the_state_it_started_from():
    """An array is a value, and the state handed in belongs to the caller. A
    run that writes through it leaves the declared initial state holding what
    the run computed, so every run after the first starts somewhere else and
    the first trace of a population audit is the only honest one."""
    proc = lower_with_state_ranges(MODEL)
    assert proc.initial["seen"] == [0, 0, 0, 0]

    post, _, _, _ = interp.run_step(proc, {"a": 1})
    assert proc.initial["seen"] == [0, 0, 0, 0], "the run wrote into reset"
    assert post["seen"] == [0, 1, 0, 0]

    after = interp.run_step(proc, {"a": 2}, post)[0]
    assert post["seen"] == [0, 1, 0, 0], "the run wrote into its caller"
    assert after["seen"] == [0, 1, 1, 0]


def test_a_trace_starts_where_reset_left_it():
    proc = lower_with_state_ranges(MODEL)
    _, _, _, _ = interp.run_trace(proc, [{"a": 0}, {"a": 1}, {"a": 2}])
    assert proc.initial["seen"] == [0, 0, 0, 0]
    state, _, _, _ = interp.run_trace(proc, [{"a": 3}])
    assert state["seen"] == [0, 0, 0, 1], "the second trace started from reset"


def test_a_helper_called_twice_is_one_goal_because_that_is_the_policy():
    """A branch inside a helper is one goal however many callsites reach it.

    That is `goals.POLICY`, which is `source-site`, and it is a policy rather than a
    defect. This test asserted the opposite: that the certificate is withheld when a
    goal is produced in two places. So it was asserting that the code is wrong for
    implementing the policy the code implements, on the strength of an appendix that
    claimed a callsite-sensitive policy nothing has ever built. 267 of 836 models had
    their certificates withheld for conforming.

    What is still worth checking is that the count is reported, since it says how much
    of the step is shared helper code and it is what a callsite-sensitive policy would
    multiply the denominator by. And that the policy is named in one place, since it
    was previously written in three that disagreed: the identifier's shape, the policy
    string, and the appendix.
    """
    from behave.sim.behavior import goals as goals_mod
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.symbolic import ranges as ranges_mod

    def built(body):
        src = ('class M:\n'
               '    def reset(self): pass\n'
               '    def pick(self, v):\n'
               '        if v > 3:\n'
               '            return 1\n'
               '        return 2\n'
               '    def process(self, a):\n'
               + body)
        proc = lower_with_state_ranges(src, input_widths=[8])
        env = ranges_mod.fixpoint(proc, {"a": ranges_mod.Range(0, 255)})
        return behavior_instance.BehaviorInstance(proc, env, source=src)

    once = built('        return {"y": self.pick(a)}\n')
    assert once.shared_goals() == {}, "one call, one place for each goal"

    twice = built('        return {"y": self.pick(a) + self.pick(a + 1)}\n')
    shared = twice.shared_goals()
    assert shared, "the branch inside the helper is produced at both calls"
    assert set(shared.values()) == {2}, shared

    assert once.goals().keys() == twice.goals().keys(), \
        "the same source declares the same goals however often it is inlined"

    assert goals_mod.POLICY == "source-site"
    assert goals_mod.POLICY_VERSION == "python-structural/12"
    assert goals_mod.POLICY in behavior_instance.POLICY, \
        "the certificate names the policy its denominator is relative to"


def test_a_starting_value_under_a_condition_is_not_read_as_one():
    """`reset` is read for what it fixes outright, and the statements used to be
    walked without regard to what they were inside. So a field two branches set
    differently was read as whichever assignment the walk reached last, and a
    field set only inside a branch was read as though it always were: the initial
    state, which every reachability verdict is relative to, was a value no run
    need ever hold.

    Only the top level of the body is read now. A field left out that way is named
    in `guessed_initial`, which withholds the certificate, so what is not known is
    said rather than invented.
    """
    # Read directly rather than through the lowering. A conditional or a loop in
    # `reset` declares branch goals, and `reset` is not lowered, so nothing
    # produces them and the lowering refuses the model: the enumeration walks the
    # whole source while only `process` is lowered, which is a separate fault and
    # not this one.
    from behave.sim.behavior.extract import _initial_state, source_model

    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.plain = 5\n'
           '        if self.plain > 1:\n'
           '            self.forked = 7\n'
           '        else:\n'
           '            self.forked = 9\n'
           '    def process(self, a):\n'
           '        return {"y": a}\n')
    got = _initial_state(source_model(src))
    assert got["plain"] == 5, "fixed outright"
    assert "forked" not in got, \
        "set on both branches of a conditional, so not fixed outright"


def test_a_step_that_answers_nothing_is_not_a_step_that_answers_zero():
    """What a step produces is a sequence of transactions, not a value, and a
    buffer alone cannot tell an empty sequence from a sequence holding a zero.
    A model that consumes on some beats and answers on others is the ordinary
    case here, so the two have to be different observations or a comparison
    against the other side is comparing the wrong thing.

    This is the test the blocker naming it was waiting for: the buffer carries
    the count, and the count is what says how much of it this step wrote.
    """
    src = ('class M:\n'
           '    def reset(self):\n'
           '        self.n = 0\n'
           '    def process(self, a):\n'
           '        self.n = self.n + 1\n'
           '        if a > 2:\n'
           '            return [{"y": 0}]\n'
           '        return []\n')
    proc = lower_with_state_ranges(src, input_widths=[4])

    _, quiet, _, error = interp.run_step(proc, {"a": 1})
    assert not error
    assert quiet.count == 0, "nothing was answered"
    assert quiet.transactions == [], "so there is no transaction"

    _, spoke, _, error = interp.run_step(proc, {"a": 7})
    assert not error
    assert spoke.count == 1, "one thing was answered"
    assert spoke.transactions == [{"y": 0}], "and it was a zero"

    assert quiet.transactions != spoke.transactions, \
        "an empty answer and an answer of zero are different observations"
