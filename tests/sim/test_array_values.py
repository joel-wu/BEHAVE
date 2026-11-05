"""Arrays are fixed-size values, and where that could be observed.

Y4. `ir.Assign` had no defined meaning when the value is an array, and the three things
that run the IR each guessed a different one. Python holds a list by reference. The
interpreter bound the same Python list and so reproduced that -- by accident of being
written in Python, not by a rule. The encoder holds an array as a list of terms and
refused the assignment outright, from inside a formula, with "array t used where a value
is expected". One IR, three readings.

The decision is that an array is a value and assigning one copies. Both backends
implement it. Value semantics and Python's differ on exactly one thing: a **write**
through a name after the array has been bound to a second name. Not the binding --
`self.stack = new_stack` binds an array to a second name and is the ordinary way a step
writes state.

So each test below states a shape, what the host answers, and what the frontend does
with it. The host is run, not assumed.
"""
import pytest

from behave.sim.behavior import lower_graph
from behave.sim.symbolic.interp import run_step
from behave.sim.behavior.lowering import LoweringError, lower_procedure

SHELL = """class M:
    def reset(self):
        self.t = [1, 2, 3]

    def process(self, a):
{body}
"""

LOWERERS = [("term", None), ("graph", lower_graph.GraphLowerer)]


def source(body):
    return SHELL.format(
        body="\n".join(f"        {line}" for line in body.splitlines()))


def host(body):
    """What Python answers, run rather than assumed."""
    ns = {}
    exec(source(body), ns)                                      # noqa: S102
    model = ns["M"]()
    model.reset()
    return model.process(0)["y"]


def lowered(body, lowerer):
    proc = lower_procedure(source(body), input_widths=[8], lowerer=lowerer)
    return run_step(proc, {"a": 0})[1].transactions[0]["y"]


#: Shapes where value semantics and Python agree, so they must lower and match.
FAITHFUL = {
    "a read-only alias":
        'u = self.t\nreturn {"y": u[0] + self.t[1]}',
    "writing back an array the step built":
        'u = [7, 8, 9]\nu[0] = 5\nself.t = u\nreturn {"y": self.t[0]}',
    "a name rebound to a fresh array, then written":
        'u = self.t\nu = [4, 5, 6]\nu[0] = 9\nreturn {"y": u[0] + self.t[0]}',
    "writing an array the step never shared":
        'self.t[0] = 5\nreturn {"y": self.t[0]}',
}

#: Shapes where a write is visible through another name on the host and not under value
#: semantics. Refused, with the site, rather than answered differently.
HAZARDS = {
    "writing through an alias":
        'u = self.t\nu[0] = 5\nreturn {"y": self.t[0]}',
    "writing through the original after aliasing it":
        'u = self.t\nself.t[0] = 5\nreturn {"y": u[0]}',
    "writing through an alias of an alias":
        'u = self.t\nv = u\nv[0] = 5\nreturn {"y": self.t[0]}',
    "appending through an alias":
        'u = self.t\nu.append(9)\nreturn {"y": self.t[0]}',
}


@pytest.mark.parametrize("name", sorted(FAITHFUL))
@pytest.mark.parametrize("kind,lowerer", LOWERERS)
def test_a_faithful_shape_lowers_and_agrees_with_the_host(name, kind, lowerer):
    body = FAITHFUL[name]
    assert lowered(body, lowerer) == host(body)


@pytest.mark.parametrize("name", sorted(HAZARDS))
@pytest.mark.parametrize("kind,lowerer", LOWERERS)
def test_a_write_through_a_shared_array_is_refused(name, kind, lowerer):
    with pytest.raises(LoweringError) as caught:
        lower_procedure(source(HAZARDS[name]), input_widths=[8], lowerer=lowerer)
    assert "same array as" in str(caught.value.what)


def test_the_refusal_names_the_other_holder():
    """A refusal an author can act on says which two names it is about."""
    with pytest.raises(LoweringError) as caught:
        lower_procedure(source(HAZARDS["writing through an alias"]),
                        input_widths=[8])
    what = str(caught.value.what)
    assert "u" in what and "t" in what


def test_the_call_in_an_index_does_not_change_which_array_is_read():
    """Python evaluates the primary first and obtains the object, so a call in the
    index that rebinds the attribute does not change which array the subscript reads.

    Y4 recorded the host saying 1 and both lowerings saying 9. `_capture` had declined
    to bind an array, on the ground that Python holds a list by reference, which is the
    wrong reading of that rule.
    """
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.t = [1, 2, 3]\n"
           "    def bump(self):\n"
           "        self.t = [9, 9, 9]\n"
           "        return 0\n"
           "    def process(self, a):\n"
           "        return {'y': self.t[self.bump()]}\n")
    ns = {}
    exec(src, ns)                                               # noqa: S102
    model = ns["M"]()
    model.reset()
    assert model.process(0)["y"] == 1
    for _kind, lowerer in LOWERERS:
        proc = lower_procedure(src, input_widths=[8], lowerer=lowerer)
        assert run_step(proc, {"a": 0})[1].transactions[0]["y"] == 1


#: Passing an array to a helper binds it to a second name, which is the one path Y4's
#: rule did not reach: `_rebind` is called from `_assign`, and `_inline` builds its
#: parameter bindings directly. B16's third acceptance case.
THROUGH_A_HELPER = """class M:
    def reset(self):
        self.t = [1, 2, 3]

    def grow(self, arr):
        arr.append(9)
        return 0

    def process(self, a):
        self.grow(self.t)
        return {"y": self.t[0]}
"""


def test_a_write_through_a_helpers_array_parameter_is_refused():
    """The alias rule reaches the parameter binding, and the message names both names.

    It costs nothing today, and that is why it was worth doing now rather than with the
    coverage work. Measured over the population: **no model that lowers writes through
    a helper's array parameter**, and the one model that writes that way does not lower
    -- because an inlined parameter carries no length, so a subscript of it is refused
    as an array whose length is not static.
    """
    with pytest.raises(LoweringError) as caught:
        lower_procedure(THROUGH_A_HELPER, input_widths=[8])
    what = str(caught.value.what)
    assert "same array as" in what, what
    assert "t" in what


def test_a_store_through_a_helpers_parameter_reaches_the_alias_rule():
    """Parameter shapes survive inlining; the semantic alias guard owns refusal."""
    src = THROUGH_A_HELPER.replace("arr.append(9)", "arr[0] = 5")
    with pytest.raises(LoweringError) as caught:
        lower_procedure(src, input_widths=[8])
    what = str(caught.value.what)
    assert "same array as" in what


@pytest.mark.parametrize("kind,lowerer", LOWERERS)
def test_state_holding_an_array_of_arrays_is_one_recursive_value(
        kind, lowerer):
    """Recursive state has the same shape and meaning in both lowerings."""
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.t = [[1, 2], [3, 4]]\n"
           "    def process(self, a):\n"
           "        self.t = [[5, 6], [7, 8]]\n"
           "        return {'y': self.t[1][0]}\n")
    proc = lower_procedure(src, input_widths=[8], lowerer=lowerer)
    state, out, _hits, error = run_step(proc, {"a": 0})
    assert not error
    assert state["t"] == [[5, 6], [7, 8]]
    assert out.transactions == [{"y": 7}]


def test_the_interpreter_copies_on_assignment():
    """The IR's rule, not this module's. Binding the list instead made two names hold
    one object here and nothing at all in the encoder, and a program with one meaning
    per backend has no meaning."""
    proc = lower_procedure(source('u = self.t\nreturn {"y": u[0]}'),
                           input_widths=[8])
    state, _out, *_ = run_step(proc, {"a": 0})
    assert state["t"] == [1, 2, 3]
