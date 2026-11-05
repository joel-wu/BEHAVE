"""Unit tests for static goal enumeration and instrumentation.

The property these lock is the one the closure loop depends on: the goals a
run reports are the goals the static enumeration listed, under the same
identifiers, with no line-number matching in between.
"""
import ast

from behave.sim.behavior.goals import (
    CONDITION,
    DECISION,
    HOOK,
    LOCATION,
    LOOP,
    LOOP_HOOK,
    catalog,
    enumerate_sites,
    goal_id,
    goal_set,
    instrument_source,
)


def _run(source, hits, **globs):
    """Compile the instrumented ``source`` and run it, recording hits."""
    tree, sites = instrument_source(source)

    def hook(ordinal, value):
        b = bool(value)
        hits.append(goal_id(sites[ordinal], b))
        return b

    def loop_hook(ordinal, iterable):
        site, n = sites[ordinal], 0
        for item in iterable:
            if n == 0:
                hits.append(goal_id(site, True))
            n += 1
            yield item
        if n == 0:
            hits.append(goal_id(site, False))

    ns = {HOOK: hook, LOOP_HOOK: loop_hook, **globs}
    exec(compile(tree, "<test>", "exec"), ns)
    return sites, ns


# ── enumeration ──────────────────────────────────────────────────────

def test_if_is_one_site_with_two_goals():
    sites = enumerate_sites("if x:\n    pass\n")
    assert len(sites) == 1
    assert sites[0].kind == "if"
    assert sites[0].goals == (sites[0].base + ":T", sites[0].base + ":F")


def test_goal_set_lists_both_outcomes_of_every_site():
    src = "if x:\n    pass\nelse:\n    pass\ny = 1 if z else 2\n"
    goals = goal_set(src)
    assert len(goals) == 4
    assert sum(g.endswith(":T") for g in goals) == 2


def test_identifier_carries_file_line_column_and_ordinal():
    src = "if a:\n    pass\n"
    site = enumerate_sites(src, file="m.py")[0]
    assert site.base == "branch:m.py:1:3:0"


def test_two_conditions_on_one_line_stay_distinct():
    sites = enumerate_sites("y = 1 if a else (2 if b else 3)\n")
    assert len(sites) == 2
    assert sites[0].base != sites[1].base
    assert {s.ordinal for s in sites} == {0, 1}


def test_a_compound_condition_is_a_decision_and_its_operands_are_conditions():
    sites = enumerate_sites("if a and b:\n    pass\n")
    assert [s.kind for s in sites] == ["if", "atom", "atom"]
    assert [s.family for s in sites] == [DECISION, CONDITION, CONDITION]


def test_a_loop_is_a_site_with_entered_and_empty_outcomes():
    sites = enumerate_sites("for i in range(4):\n    pass\n")
    assert [s.family for s in sites] == [LOOP]


def test_catalog_records_family_and_class():
    goals = catalog("if a and b:\n    pass\nfor i in range(n):\n    pass\n")
    assert [g.family for g in goals] == [DECISION, DECISION, CONDITION,
                                         CONDITION, CONDITION, CONDITION,
                                         LOOP, LOOP]
    assert {g.cls for g in goals} == {"coverage"}


def test_procedure_statements_are_single_entry_locations():
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.x = 0\n"
           "    def process(self, a):\n"
           "        if a:\n"
           "            self.x = 1\n"
           "        return {'y': self.x}\n")
    sites = enumerate_sites(src, file="m.py")
    locations = [site for site in sites if site.family == LOCATION]
    decisions = [site for site in sites if site.family == DECISION]

    assert [site.line for site in locations] == [5, 6, 7]
    assert [site.ordinal for site in locations] == [0, 1, 2]
    assert [site.goals for site in locations] == [
        ("location:m.py:5:8:0:entry",),
        ("location:m.py:6:12:1:entry",),
        ("location:m.py:7:8:2:entry",),
    ]
    # Adding the location schema does not renumber the existing branch namespace.
    assert decisions[0].goals == (
        "branch:m.py:5:11:0:T", "branch:m.py:5:11:0:F")


def test_location_instrumentation_follows_statement_execution():
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.x = 0\n"
           "    def process(self, a):\n"
           "        if a:\n"
           "            self.x = 1\n"
           "        return {'y': self.x}\n")
    hits = []
    sites, ns = _run(src, hits)
    model = ns["M"]()
    model.reset()
    assert model.process(0) == {"y": 0}

    by_line = {site.line: site.goals[0]
               for site in sites if site.family == LOCATION}
    decision = next(site for site in sites if site.family == DECISION)
    assert hits == [by_line[5], goal_id(decision, False), by_line[7]]

    hits.clear()
    assert model.process(1) == {"y": 1}
    assert hits == [by_line[5], goal_id(decision, True),
                    by_line[6], by_line[7]]


def test_a_literal_loop_declares_both_outcomes_like_any_other():
    """It used to declare only the one its length fixes, and that made this module
    decide which outcomes exist on the strength of what the lowering would emit.

    Two things came of it. The coverage denominator depended on what the frontend
    pre-trimmed, so two models with the same behaviour and different literals were
    measured against different denominators. And an outcome ruled out by the source
    went through a different mechanism from one ruled out by a query, which is the
    shape that let the lowering certify its own unreachability.

    Both are declared now, and the query settles the one the count rules out: for
    `range(2)` the zero-trip goal has a hit condition the encoder folds to false, so
    the answer is unsatisfiable and comes from something that can be checked.
    """
    for source in ("for i in range(2):\n    pass\n",
                   "for i in []:\n    pass\n",
                   "for i in range(n):\n    pass\n"):
        assert sorted(g.outcome for g in catalog(source)) == ["F", "T"], source


def test_the_two_loop_outcomes_are_zero_trip_and_non_zero_trip():
    """Named, because there are three pairs of events they could be taken for.

    Zero against non-zero is what they are. Normal exhaustion against `break` is a
    different family and is not declared. Reaching the unroll bound is a capability
    question and is not a goal at all.

    The host reports them that way: the hook counts the items and reports the empty
    outcome only when there were none, so a loop left by `break` on its first item
    still reports non-zero. `tests/sim/test_lowering.py` checks that the lowered step
    agrees, which is the half that could have been the unrolled guard's true and false
    outcomes instead and is not.
    """
    from behave.sim.behavior.goals import LOOP_OUTCOMES

    assert LOOP_OUTCOMES == ("non-zero trip count", "zero trip count")

    hits = []
    sites, _ns = _run("for i in range(n):\n    break\n", hits, n=3)
    assert hits == [sites[0].base + ":T"], "left by break, and it still ran"

    hits = []
    sites, _ns = _run("for i in range(n):\n    pass\n", hits, n=0)
    assert hits == [sites[0].base + ":F"]


def test_nested_branches_are_all_enumerated():
    src = ("if a:\n"
           "    if b:\n"
           "        pass\n"
           "while c:\n"
           "    break\n")
    assert [s.kind for s in enumerate_sites(src)] == ["if", "if", "while-loop", "while"]


# ── instrumentation ──────────────────────────────────────────────────

def test_instrumented_program_computes_the_same_value():
    src = "out = 1 if a else 2\n"
    hits = []
    _sites, ns = _run(src, hits, a=0)
    assert ns["out"] == 2


def test_hits_use_the_enumerated_identifiers():
    src = "if a:\n    y = 1\nelse:\n    y = 2\n"
    hits = []
    sites, ns = _run(src, hits, a=1)
    assert hits == [sites[0].base + ":T"]
    assert ns["y"] == 1
    assert set(hits) < set(goal_set(src))


def test_short_circuit_leaves_the_second_atom_unreported():
    src = "if a and b:\n    pass\n"
    hits = []
    sites, _ns = _run(src, hits, a=0, b=1)
    # ``a`` is false, so ``b`` is never evaluated and its outcome is a case the
    # run did not exercise. The decision they form reports its own outcome.
    assert hits == [sites[1].base + ":F", sites[0].base + ":F"]


def test_both_atoms_reported_when_both_are_evaluated():
    src = "if a and b:\n    pass\n"
    hits = []
    sites, _ns = _run(src, hits, a=1, b=0)
    assert hits == [sites[1].base + ":T", sites[2].base + ":F",
                    sites[0].base + ":F"]


def test_a_loop_that_runs_reports_entered_only():
    hits = []
    sites, _ns = _run("for i in range(2):\n    pass\n", hits)
    assert hits == [sites[0].base + ":T"]


def test_an_empty_loop_reports_the_empty_outcome():
    hits = []
    sites, _ns = _run("for i in range(0):\n    pass\n", hits)
    assert hits == [sites[0].base + ":F"]


def test_a_loop_left_by_break_still_reports_entered():
    src = "for i in range(4):\n    break\n"
    hits = []
    sites, _ns = _run(src, hits)
    assert hits == [sites[0].base + ":T"]


def test_a_comprehension_has_a_loop_site_and_a_filter_decision():
    sites = enumerate_sites("y = [x for x in buf if x > 1]\n")
    assert [s.family for s in sites] == [LOOP, DECISION]


def test_while_condition_reports_every_evaluation():
    src = "n = 0\nwhile n < 2:\n    n = n + 1\n"
    hits = []
    sites, ns = _run(src, hits)
    assert ns["n"] == 2
    loop, condition = sites
    assert hits == [condition.base + ":T", loop.base + ":T",
                    condition.base + ":T", condition.base + ":F"]


def test_while_activation_distinguishes_empty_exhausted_and_broken_loops():
    for body, n, expected in (("n -= 1", 0, False), ("n -= 1", 2, True),
                              ("break", 2, True)):
        hits = []
        sites, _ = _run(f"while n > 0:\n    {body}\n", hits, n=n)
        loop = next(site for site in sites if site.family == LOOP)
        assert [hit for hit in hits if hit in loop.goals] == [goal_id(loop, expected)]


def test_while_activation_is_per_dynamic_entry_and_preserves_locals():
    source = ("_behave_while_entered_1 = 17\n"
              "for n in [0, 2, 0]:\n"
              "    while n > 0:\n"
              "        n -= 1\n")
    hits = []
    sites, namespace = _run(source, hits)
    loop = next(site for site in sites if site.kind == "while-loop")
    assert [hit for hit in hits if hit in loop.goals] == [
        goal_id(loop, False), goal_id(loop, True), goal_id(loop, False)]
    assert namespace["_behave_while_entered_1"] == 17


def test_while_activation_precedes_return_or_failure_but_not_failed_test():
    import pytest

    source = ("def run(n):\n"
              "    while 1 // n:\n"
              "        return 1 // (n - 1)\n"
              "    return 7\n")
    hits = []
    sites, namespace = _run(source, hits)
    loop = next(site for site in sites if site.kind == "while-loop")
    assert namespace["run"](2) == 7
    assert goal_id(loop, False) in hits
    hits.clear()
    with pytest.raises(ZeroDivisionError):
        namespace["run"](0)
    assert not set(hits) & set(loop.goals)
    hits.clear()
    with pytest.raises(ZeroDivisionError):
        namespace["run"](1)
    assert set(hits) & set(loop.goals) == {goal_id(loop, True)}


def test_instrumented_tree_is_valid_python():
    src = "if a:\n    pass\n"
    tree, _sites = instrument_source(src)
    assert isinstance(tree, ast.Module)
    compile(tree, "<test>", "exec")


def test_a_decision_in_reset_declares_no_goal():
    """`reset` and `__init__` are executed, by constructing the model, and nothing
    lowers them, so a decision written in one is not a decision the step takes.

    This is the test that was owed. The enumeration entered every function once, so a
    conditional in `reset` declared a branch goal the lowering could not produce and
    `_check_producers` refused the whole model for it -- 77 of the 836 models declare
    a site in `reset` and 31 in `__init__`. The rule was added and this was not, which
    left a frontend blocker withholding **497** certificates for a defect that had been
    fixed; a certificate withheld for a reason that no longer holds is as wrong as one
    granted for a reason that never did.
    """
    src = ("class M:\n"
           "    def __init__(self):\n"
           "        for i in range(2):\n"
           "            self.k = i\n"
           "    def reset(self):\n"
           "        if self.k > 0:\n"
           "            self.n = 1\n"
           "        else:\n"
           "            self.n = 0\n"
           "    def process(self, a):\n"
           "        if a > 3:\n"
           "            self.n = self.n + 1\n"
           "        return {'y': self.n}\n")
    sites = enumerate_sites(src)
    decision_lines = {site.line for site in sites if site.family != LOCATION}
    location_lines = {site.line for site in sites if site.family == LOCATION}
    assert decision_lines == {11}, sorted(decision_lines)
    assert location_lines == {11, 12, 13}, sorted(location_lines)


def test_a_decision_in_a_helper_still_declares_one():
    """The other half, so the rule above is about what is lowered and not about which
    method a decision happens to sit in. `process` calls the helper and the lowering
    inlines it, so a decision inside it is a decision the step takes."""
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.n = 0\n"
           "    def clamp(self, v):\n"
           "        if v > 7:\n"
           "            return 7\n"
           "        return v\n"
           "    def process(self, a):\n"
           "        return {'y': self.clamp(a)}\n")
    assert {site.line for site in enumerate_sites(src)
            if site.family != LOCATION} == {5}


def test_ordinals_are_stable_under_edits_below_the_site():
    src_a = "if a:\n    pass\n"
    src_b = "if a:\n    pass\nif b:\n    pass\n"
    assert enumerate_sites(src_a)[0].base == enumerate_sites(src_b)[0].base
