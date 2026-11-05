"""The safety catalogue declared from the source, against the lowering that produces it.

B13's fault is a common-mode omission: `instance.goals()` builds the catalogue from
`ir.goals_of(proc.body)`, so a goal the lowering never created is a goal the catalogue
never declares. The two agreed for the same reason rather than independently, and a
missing guard left nothing to be missing from.

The test that distinguishes the two arrangements is the first one here, and it is the
only one that does: with the division guard removed from the lowering, the audit must
fail, and it must fail whether or not any sampled run happens to divide by zero.
"""
import ast

import pytest

from behave.sim.behavior import declared
from behave.sim.behavior import instance as behavior_instance
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_procedure
from behave.sim.core.query import SolverAnswer
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema


def model(body, extra=""):
    return ("class M:\n"
            "    def reset(self):\n"
            "        self.n = 0\n"
            f"{extra}"
            "    def process(self, a, b):\n"
            f"{body}"
            "        return {'y': z}\n")


def lower(body, extra="", **kw):
    return lower_procedure(model(body, extra), input_widths=[8, 8], **kw)


# ── the acceptance test ──

def test_removing_the_division_guard_fails_the_audit(monkeypatch):
    """The one test that tells this arrangement from the previous one.

    Under the old one, deleting this guard deleted the goal, deleted the declaration
    with it, and nothing anywhere reported a difference: `well_formed` said nothing,
    `unaccounted_goals` said nothing, and the step simply had one fewer safety goal
    than the source it came from.
    """
    real = _Lowerer._guard

    def without_the_division_guard(self, cond, goal, code, node=None):
        if str(goal).endswith(":divzero"):
            return
        return real(self, cond, goal, code, node)

    monkeypatch.setattr(_Lowerer, "_guard", without_the_division_guard)
    with pytest.raises(LoweringError) as caught:
        lower("        z = a // b\n")
    assert "neither produced nor discharged" in str(caught.value.what)
    assert "divzero" in str(caught.value.what)


def test_the_same_model_lowers_with_the_guard_in_place():
    """So the test above is about the guard and not about the model."""
    proc = lower("        z = a // b\n")
    assert any(g.endswith(":divzero") for g in _safety(proc))


def test_removing_the_negative_shift_guard_fails_the_audit(monkeypatch):
    real = _Lowerer._guard

    def without_the_shift_guard(self, cond, goal, code, node=None):
        if str(goal).endswith(":negativeshift"):
            return
        return real(self, cond, goal, code, node)

    monkeypatch.setattr(_Lowerer, "_guard", without_the_shift_guard)
    with pytest.raises(LoweringError) as caught:
        lower("        z = a >> (b - 1)\n")
    assert "neither produced nor discharged" in str(caught.value.what)
    assert "negativeshift" in str(caught.value.what)


# ── what the declaration says ──

def test_a_division_and_a_subscript_are_declared_and_an_append_is_not():
    """An append owes an analysis limit, not a safety goal.

    A step that appends past the capacity derived from its own appends has reached a
    place this side stopped following the model, so `CapacityExceeded` is in the
    Procedure contract's analysis-error set and the catalogue is not allowed to hold
    it. The declaration said otherwise for one population run and the audit refused
    six models, which is the audit working in the direction nobody asks about: the
    declaration was wrong.
    """
    tree = ast.parse(model("        t = []\n"
                           "        t.append(a // b)\n"
                           "        z = t[a]\n"))
    kinds = sorted({s.kind for s in declared.declare_safety(tree).values()})
    assert kinds == [declared.DIVZERO, declared.INDEXERROR]


def test_mapping_get_with_a_default_has_no_safety_goal():
    source = (
        "T = {0: 5, 1: 6}\n\n"
        "class M:\n"
        "    def reset(self):\n"
        "        self.n = 0\n"
        "    def process(self, a, b):\n"
        "        return {'y': T.get(a, 0)}\n"
    )
    proc = lower_procedure(source, input_widths=[8, 8])

    assert declared.declare_safety(ast.parse(source)) == {}
    assert not any(goal.endswith(":indexerror") for goal in _safety(proc))
    assert not any(goal.endswith(":indexerror") for goal in proc.discharged)

    schema = TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("b", "input", 8),
         Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    instance = behavior_instance.lower(source, schema, bound=1)
    assert not any(goal.cls == "safety" for goal in instance.goals().values())


def test_a_compound_division_is_declared_where_the_lowering_names_it():
    """`x //= y` is the same operation as `x = x // y` and owes the same goal. The
    lowering desugars it into a `BinOp` carrying the statement's own position, and the
    audit found the omission by refusing a model that writes `tmp //= self.TAPS`."""
    tree = ast.parse(model("        z = a\n        z //= b\n"))
    sites = declared.declare_safety(tree)
    assert [s.kind for s in sites.values()] == [declared.DIVZERO]
    proc = lower("        z = a\n        z //= b\n")
    assert set(sites) <= _safety(proc)


def test_a_negative_shift_is_declared_where_the_lowering_names_it():
    tree = ast.parse(model("        z = a >> (b - 1)\n"))
    sites = declared.declare_safety(tree)
    assert [s.kind for s in sites.values()] == [declared.NEGATIVESHIFT]
    proc = lower("        z = a >> (b - 1)\n")
    assert set(sites) <= _safety(proc)


def test_reset_declares_nothing():
    """`reset` is executed, by constructing the model, not lowered. An operation there
    is performed by Python and owes no goal to a step."""
    tree = ast.parse("class M:\n"
                     "    def reset(self):\n"
                     "        self.n = 7 // 2\n"
                     "    def process(self, a):\n"
                     "        return {'y': a}\n")
    assert declared.declare_safety(tree) == {}


# ── what the lowering discharges, and why ──

def test_a_literal_index_is_discharged_with_a_reason():
    """Not absent. The declaration is syntactic and cannot see that this subscript is
    total; the lowering can, and says so where it decides it."""
    proc = lower("        t = [1, 2, 3]\n        z = t[1]\n")
    reasons = [why for why in proc.discharged.values()]
    assert any("folded" in why for why in reasons), proc.discharged


def test_a_folded_index_is_discharged_too():
    """The case a syntactic reading cannot reach: the index is a name in the source and
    a literal after the class constant is folded. 2072 of the 2117 index sites in the
    population are literal at this point and only some of them look literal."""
    proc = lower("        t = [1, 2, 3]\n        z = t[self.K]\n",
                 extra="    K = 2\n")
    assert any("folded" in why for why in proc.discharged.values())


def test_a_discharged_safety_goal_has_an_exact_unsat_query():
    source = model("        t = [1, 2, 3]\n        z = t[1]\n")
    schema = TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("b", "input", 8),
         Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    instance = behavior_instance.lower(source, schema, bound=1)
    safety = tuple(
        goal for goal, declaration in instance.goals().items()
        if declaration.cls == "safety")

    assert safety == tuple(instance.proc.discharged)
    verdict = instance.query(safety, steps=1, timeout_ms=5_000, workers=1)

    assert verdict.unreachable == set(safety)
    assert verdict.undecided == {}
    assert len(verdict.queries) == 1
    assert verdict.queries[0].answer is SolverAnswer.UNSAT


def test_an_operation_no_execution_reaches_is_discharged_as_unreached():
    """A loop that unrolls zero times never lowers its body. The site is real in the
    source and there is no bounded execution that performs it, which is not a rule that
    went missing and must not be reported as one."""
    proc = lower("        z = 0\n"
                 "        for i in range(0):\n"
                 "            z = a // b\n")
    assert any("no bounded execution" in why
               for why in proc.discharged.values()), proc.discharged


def test_a_helper_nothing_calls_declares_nothing():
    """The declaration is made over the code the lowering lowers, which is the step and
    the helpers actually inlined -- not every function the model defines."""
    proc = lower("        z = a\n",
                 extra="    def unused(self, v):\n        return v // 2\n\n")
    assert not any(g.endswith(":divzero") for g in _safety(proc))
    assert not any(g.endswith(":divzero") for g in proc.discharged)


def test_a_helper_that_is_called_declares_its_sites():
    proc = lower("        z = self.half(a, b)\n",
                 extra="    def half(self, v, w):\n        return v // w\n\n")
    assert any(g.endswith(":divzero") for g in _safety(proc))


# ── the table identifier, which the policy required changing ──

def test_two_reads_of_one_table_are_two_goals():
    """`goals.POLICY` is source-site: a goal is a position in the source. The table
    rule named its goal after the table, so every read of one table shared a goal --
    the one identifier in the program a declaration made from the source could not
    match, and the 45 models that read a table were the only ones whose declared and
    produced sets could not be compared at all.
    """
    proc = lower_procedure(
        "T = {0: 5, 1: 6}\n\n"
        "class M:\n"
        "    def reset(self):\n"
        "        self.n = 0\n"
        "    def process(self, a, b):\n"
        "        return {'y': T[a] + T[b]}\n", input_widths=[8, 8])
    index = sorted(g for g in _safety(proc) if g.endswith(":indexerror"))
    assert len(index) == 2, index
    assert all(g.startswith("safety:behavior.py:") for g in index), index


def test_nested_subscripts_are_distinct_and_native_failure_hits_the_inner_one():
    source = ("class M:\n"
              "    def process(self, a, xs):\n"
              "        return {'y': xs[a][0]}\n")
    tree = ast.parse(source)
    sites = declared.declare_safety(tree)

    assert len(sites) == 2
    assert len({site.id for site in sites.values()}) == 2
    inner = min(sites.values(), key=lambda site: site.end_col)
    namespace = {}
    exec(compile(tree, "behavior.py", "exec"), namespace)  # noqa: S102 - fixed native oracle
    try:
        namespace["M"]().process(1, [[7]])
    except IndexError as error:
        from behave.sim.behavior.instrumented import attribute_safety_failure

        assert attribute_safety_failure(error, sites) == inner.id
    else:  # pragma: no cover - the fixture is deliberately out of range
        raise AssertionError("the inner subscript did not fail")


def test_a_reachable_function_does_not_borrow_an_uncalled_nested_scope():
    tree = ast.parse(
        "class M:\n"
        "    def process(self, a):\n"
        "        def unused(x):\n"
        "            return 1 // x\n"
        "        return {'y': a}\n")
    process = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "process")

    assert declared.declare_safety(tree, methods=(process,)) == {}


def _safety(proc):
    from behave.sim.symbolic import ir

    return {g for g in ir.goals_of(proc.body) if str(g).startswith("safety:")}
