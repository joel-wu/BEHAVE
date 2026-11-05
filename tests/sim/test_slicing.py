"""What a cone of influence may drop, and what dropping it would cost.

The property is one-sided, so it is the only one worth testing hard. A cone that
keeps too much makes a slow query. A cone that keeps too little makes a **wrong**
one, in both directions at once: a definition dropped from the path to a goal can
leave the goal reachable in the formula and not in the step, and a guard dropped
can leave it unreachable in the formula and reachable in the step. `unreachable`
is a claim over every admissible trace, so that second one is a soundness fault
rather than a lost optimisation.

So these compare the sliced verdict against the whole step's verdict, on models
built to contain each rule's hard case and then on real ones from the dataset.
`scratch/probe_p4_verdicts.py` is the same comparison over a population sample,
which is where the one real defect was found.
"""
import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import residual
from behave.sim.symbolic import cfg, encoder, ir, ranges, slicing
from behave.sim.behavior.lowering import lower_with_state_ranges


def build(source, **inputs):
    proc = lower_with_state_ranges(source)
    env = ranges.fixpoint(proc, {name: ranges.Range(0, 255) for name in inputs}
                          or {"a": ranges.Range(0, 255)})
    return proc, env


def verdict(proc, env, goal, body=None, timeout_ms=None):
    """Whether ``goal`` is reachable in one step of ``body`` (the whole step by
    default).

    ``timeout_ms`` bounds the query with z3's own timeout, which is the only thing
    that bounds a solve -- a Python alarm is delivered between bytecodes and
    `check()` is one call into C. Without it the `conwaylife` case below ran
    **374 seconds**, the slowest test in the suite by a factor of two.
    """
    if body is not None:
        proc = ir.Procedure(proc.state, proc.inputs, proc.locals, proc.outputs,
                            body, types=proc.types,
                            output_vars=proc.output_vars, count=proc.count)
    enc = encoder.encode_step(proc, env=env)
    solver = z3.Solver()
    if timeout_ms:
        solver.set("timeout", timeout_ms)
    solver.add(enc.constraint)
    solver.add(enc.goal_condition(goal))
    return solver.check()


def sliced(proc, env, goal):
    events = slicing.linearize(proc.body)
    cut = slicing.cone(events, goal)
    assert cut is not None, goal
    return cut, verdict(proc, env, goal, slicing.prune(proc.body, cut.kept))


def agrees(source, **inputs):
    """Every goal of ``source``, sliced and whole, with the shares."""
    proc, env = build(source, **inputs)
    goals = sorted(ir.goals_of(proc.body))
    assert goals, "a model with no goals tests nothing"
    shares = []
    for goal in goals:
        cut, got = sliced(proc, env, goal)
        assert got == verdict(proc, env, goal), (goal, got, cut)
        shares.append(cut.share)
    return shares


# ── the three rules, each with the case that breaks it ──

CONDITIONAL_KILL = ('class M:\n'
                    '    def reset(self):\n'
                    '        self.s = 0\n'
                    '    def process(self, a):\n'
                    '        x = 3\n'                 # must survive
                    '        if a & 1:\n'
                    '            x = 4\n'             # does not kill x
                    '        y = 0\n'
                    '        if x == 3:\n'
                    '            y = 1\n'
                    '        return {"y": y}\n')


def test_a_conditional_assignment_does_not_kill_the_name_it_writes():
    """The mistake that computes a cone too small.

    `x = 3` is dead if `x = 4` kills `x`, and then the branch on `x == 3` is a
    branch on nothing -- the sliced formula would call the goal unreachable when
    an odd `a` reaches it. The encoder if-converts, so after the branch `x` is a
    choice between 4 and 3 and the earlier definition is still live.
    """
    proc, env = build(CONDITIONAL_KILL)
    goals = sorted(ir.goals_of(proc.body))
    inner = [g for g in goals
             if g.startswith("branch:") and ":9:" in g]
    assert inner, goals

    for goal in inner:
        cut, got = sliced(proc, env, goal)
        assert got == verdict(proc, env, goal) == z3.sat, goal
        kept = {id(c) for c in ir.walk(proc.body)} & cut.kept
        assigns = [c for c in ir.walk(proc.body)
                   if isinstance(c, ir.Assign) and c.var == "x"
                   and id(c) in kept]
        assert len(assigns) == 2, \
            "both definitions of x reach the branch that decides the goal"


def _check_branch_cone(body, *, seed=()):
    """Pointwise native IR oracle, including shared sites and failed prefixes."""
    from itertools import product

    from behave.sim.symbolic import interp

    events = slicing.linearize(body)
    cut = slicing.cone(events, "selected" if not seed else None, seed=seed)
    reduced = slicing.prune(body, cut.kept)
    for a, b, x in product(range(3), repeat=3):
        results = []
        for command in (body, reduced):
            machine = interp._Interp({"a": a, "b": b, "x": x, "y": 0, "i": 0})
            hits = set()
            try:
                machine.run(command, hits)
            except interp.Halt:
                pass
            results.append(tuple(machine.rho[name] for name in seed) if seed else "selected" in hits)
        assert results[0] == results[1], (a, b, x)
    return cut


def test_both_branch_writes_kill_only_the_obsolete_value():
    old = ir.Assign("x", ir.Op("mul", ir.Var("a"), ir.Var("b")))
    body = ir._sequence((old,
        ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)), ir.Assign("x", ir.Lit(1)), ir.Assign("x", ir.Lit(2))),
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)), ir.Mark("selected"), ir.Skip())))
    cut = _check_branch_cone(body)
    assert id(old) not in cut.kept
    assert "b" not in cut.needs


@pytest.mark.parametrize("read_before_write", (False, True))
def test_branch_entry_guard_and_early_reads_keep_their_reaching_value(read_before_write):
    old = ir.Assign("x", ir.Var("b"))
    mark = ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)), ir.Mark("selected"), ir.Skip())
    arm = ir._sequence((mark, ir.Assign("x", ir.Lit(0)))) if read_before_write else ir._sequence((
        ir.Assign("x", ir.Lit(0)), ir.Mark("selected")))
    body = ir._sequence((old, ir.If(ir.Op("ne", ir.Var("x"), ir.Lit(0)), arm, ir.Assign("x", ir.Lit(2)))))
    cut = _check_branch_cone(body)
    assert id(old) in cut.kept
    assert "b" in cut.needs


@pytest.mark.parametrize("nest_right", (False, True))
def test_nested_branch_joins_do_not_connect_sibling_definitions(nest_right):
    old = ir.Assign("x", ir.Var("b"))
    inner = ir.If(ir.Op("ne", ir.Var("b"), ir.Lit(0)), ir.Assign("x", ir.Lit(1)), ir.Assign("x", ir.Lit(2)))
    other = ir.Assign("x", ir.Lit(0))
    body = ir._sequence((old, ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)), other if nest_right else inner,
                                  inner if nest_right else other),
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)), ir.Mark("selected"), ir.Skip())))
    assert id(old) not in _check_branch_cone(body).kept


@pytest.mark.parametrize("in_else", (False, True))
def test_cut_inside_a_branch_preserves_earlier_goal_dependencies(in_else):
    old = ir.Assign("x", ir.Var("b"))
    mark = ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)), ir.Mark("selected"), ir.Skip())
    arm = ir._sequence((mark, ir.Assign("x", ir.Lit(2))))
    other = ir.Assign("x", ir.Lit(0))
    body = ir._sequence((old, ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)), other if in_else else arm,
                                  arm if in_else else other)))
    assert id(old) in _check_branch_cone(body).kept


def test_branch_join_retains_failed_prefix_and_zero_iteration_dependencies():
    old = ir.Assign("x", ir.Var("b"))
    body = ir._sequence((old, ir.For("i", ir.Var("a"),
        ir.If(ir.Op("ne", ir.Var("b"), ir.Lit(0)), ir.Assign("x", ir.Lit(1)), ir.Assign("x", ir.Lit(2)))),
        ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)), ir.Fail("stopped", "bad"), ir.Skip()),
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)), ir.Mark("selected"), ir.Skip())))
    assert id(old) in _check_branch_cone(body).kept


@pytest.mark.parametrize("inside", (False, True))
def test_end_state_observation_retains_value_before_a_branch_failure(inside):
    old = ir.Assign("x", ir.Var("b"))
    fail = ir.Fail("stopped", "bad")
    yes = ir._sequence((fail, ir.Assign("x", ir.Lit(1)))) if inside else ir.Assign("x", ir.Lit(1))
    body = ir._sequence((old,
        ir.Skip() if inside else ir.If(ir.Op("ne", ir.Var("b"), ir.Lit(0)), fail, ir.Skip()),
        ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)), yes, ir.Assign("x", ir.Lit(2)))))
    assert id(old) in _check_branch_cone(body, seed=("x",)).kept


def test_shared_branch_walk_matches_individual_occurrences_after_overwrites():
    mark = ir.Mark("selected")
    body = ir._sequence((ir.Assign("x", ir.Var("b")),
        ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)), ir._sequence((mark, ir.Assign("x", ir.Lit(0)))),
              ir._sequence((ir.Assign("x", ir.Lit(2)), mark))),
        ir.If(ir.Op("ne", ir.Var("x"), ir.Lit(0)), mark, ir.Skip())))
    proc = ir.Procedure(("x",), ("a", "b"), (), (), body)
    indexed = slicing.index(proc)
    occurrences = tuple((site, "selected") for site in indexed.goal_positions["selected"])
    batch = dict(row for row in indexed.occurrence_batch(occurrences) if row is not None)
    for site, goal in occurrences:
        cut = slicing.cone(indexed.events, goal, _site=site)
        assert batch[site, goal].kept == cut.kept
        assert batch[site, goal].needs == cut.needs
    _check_branch_cone(body)


@pytest.mark.parametrize("seed", range(20))
def test_branch_cones_preserve_pointwise_hits_across_mixed_control_trees(seed):
    import random

    randomizer = random.Random(seed)

    def tree(depth):
        if depth == 0:
            return randomizer.choice((ir.Assign("x", ir.Var("b")),
                ir.Assign("b", ir.Var("x")), ir.Assign("x", ir.Lit(1)),
                ir.Mark("selected"), ir.Fail("stopped", "bad"), ir.Skip()))
        return ir.If(ir.Op("ne", ir.Var(randomizer.choice(("a", "b", "x"))), ir.Lit(0)),
            ir._sequence((tree(depth - 1), tree(depth - 1))), tree(depth - 1))

    body = ir._sequence((tree(3), ir.If(ir.Op("ne", ir.Var("x"), ir.Lit(0)), ir.Mark("selected"), ir.Skip())))
    _check_branch_cone(body)


FAILS_BEFORE = ('class M:\n'
                '    def reset(self):\n'
                '        self.s = 0\n'
                '    def process(self, a, b):\n'
                '        q = a // b\n'          # can fail, ending the run
                '        y = 0\n'
                '        if a > 200:\n'
                '            y = 1\n'
                '        return {"y": y}\n')


def test_a_failure_before_the_goal_is_in_the_cone_however_unrelated():
    """`q = a // b` computes nothing the goal reads, and the goal cannot be
    reached on a trace where it failed. Dropping it as dead data flow is the
    other way to compute a cone too small."""
    proc, _env = build(FAILS_BEFORE, a=1, b=1)
    goals = sorted(ir.goals_of(proc.body))
    branch = [g for g in goals if ":7:" in g and g.endswith(":T")]
    assert branch, goals

    events = slicing.linearize(proc.body)
    cut = slicing.cone(events, branch[0])
    fails = [c for c in ir.walk(proc.body) if isinstance(c, ir.Fail)]
    assert fails, "the division has to be guarded for this to test anything"
    assert all(id(f) in cut.kept for f in fails), \
        "every failure that could have ended the run first"


def test_the_cones_of_a_model_agree_with_the_whole_step():
    for source in (CONDITIONAL_KILL, FAILS_BEFORE):
        shares = agrees(source, a=1, b=1)
        assert all(0 < s <= 1.0 for s in shares)


def test_pruned_acyclic_body_preserves_goals_and_prior_failures():
    from behave.sim.symbolic import interp

    before = ''.join(f'        if b == {i}: scratch = {i}\n' for i in range(12))
    after = ''.join(f'        if b == {i}: scratch = {i}\n' for i in range(12, 24))
    source = ('class M:\n    def reset(self): self.s = 0\n'
              '    def process(self, a, b):\n        scratch = 0\n' + before +
              '        q = a // b\n        y = 0\n'
              '        if a == 3: y = 1\n' + after +
              '        return {"y": y}\n')
    proc, env = build(source, a=1, b=1)
    line = next(i for i, text in enumerate(source.splitlines(), 1) if 'if a == 3' in text)
    goal, = [g for g in ir.goals_of(proc.body) if g.startswith(f'branch:behavior.py:{line}:')
             and g.endswith(':T')]
    keep = slicing.for_run(proc, (goal,))
    assert keep is not None and not slicing.audit(proc, (goal,), keep)
    projected = proc.derived(body=slicing.prune(proc.body, keep), graph=None,
                            types=encoder._procedure_validation(proc).types,
                            boundary_complete=False)
    assert sum(1 for _ in ir.walk(projected.body)) < sum(1 for _ in ir.walk(proc.body)) // 2
    whole = encoder.encode_step(proc, env=env, tag='whole')
    cut = encoder.encode_step(projected, env=env, tag='cut')
    solver = z3.Solver()
    solver.add(whole.constraint, cut.constraint)
    for name in (*proc.state, *proc.inputs):
        left, right = whole.pre[name], cut.pre[name]
        left = z3.BV2Int(left) if z3.is_bv(left) else left
        right = z3.BV2Int(right) if z3.is_bv(right) else right
        solver.add(left == right)
    solver.add(whole.goal_condition(goal) != cut.goal_condition(goal))
    assert solver.check() == z3.unsat
    for a in range(8):
        for b in range(24):
            observed = []
            for body in (proc.body, projected.body):
                machine = interp._Interp({**proc.initial, 'a': a, 'b': b})
                hits = set()
                try:
                    machine.run(body, hits)
                except interp.Halt:
                    pass
                observed.append(goal in hits)
            assert observed == [a == 3 and b != 0] * 2


# ── what the cone is allowed to drop ──

UNRELATED = ('class M:\n'
             '    def reset(self):\n'
             '        self.w = 0\n'
             '    def process(self, a):\n'
             '        u = (a * 3) & 255\n'
             '        v = (u + 7) & 255\n'
             '        self.w = (v ^ 12) & 255\n'
             '        y = 0\n'
             '        if a & 1:\n'
             '            y = 1\n'
             '        return {"y": y}\n')


def test_a_chain_nothing_reads_is_dropped():
    """Otherwise there is no point to any of this: `u`, `v` and `w` are three
    commands the branch on `a & 1` does not read, and a cone that keeps them is
    the whole step under another name."""
    proc, env = build(UNRELATED)
    goals = sorted(ir.goals_of(proc.body))
    branch = next(g for g in goals if ":9:" in g and g.endswith(":T"))

    events = slicing.linearize(proc.body)
    cut = slicing.cone(events, branch)
    dropped = {c.var for c in ir.walk(proc.body)
               if isinstance(c, ir.Assign) and id(c) not in cut.kept}
    assert {"u", "v", "w"} <= dropped, dropped
    assert cut.share < 0.75
    assert sliced(proc, env, branch)[1] == verdict(proc, env, branch)


def test_a_pruned_body_is_a_body_and_not_the_one_it_came_from():
    """`prune` rebuilds rather than edits, since `linearize` holds these nodes
    and a body is sliced once per goal."""
    proc, _env = build(UNRELATED)
    events = slicing.linearize(proc.body)
    goal = min(ir.goals_of(proc.body))
    cut = slicing.cone(events, goal)
    before = sum(1 for _ in ir.walk(proc.body))
    small = slicing.prune(proc.body, cut.kept)
    assert sum(1 for _ in ir.walk(proc.body)) == before, \
        "the body it was sliced from is untouched"
    assert sum(1 for _ in ir.walk(small)) < before


TWICE = ('def helper(v):\n'
         '    r = 0\n'
         '    if v > 200:\n'
         '        r = 1\n'
         '    return r\n'
         '\n'
         'class M:\n'
         '    def reset(self):\n'
         '        self.s = 0\n'
         '    def process(self, a):\n'
         '        p = helper(a & 15)\n'      # can never take the branch
         '        q = helper(a)\n'           # can, and is the second site
         '        return {"y": (p + q) & 255}\n')


def test_a_goal_reported_at_two_sites_keeps_both():
    """The bug this module actually had, in the direction that matters.

    An inlined call is duplicated, so one goal is reported by two commands and the
    encoder's hit is the disjunction: `hits[g] = Or(seen, reached)`. A cone cut at
    the *first* site drops the second, and on `DivSqrtRecF64_mulAddZ31` three goals
    then came back **`unsat` sliced against `sat` whole** -- reachable only at the
    site the cone had cut away.

    Here the first call can never take the branch and the second can, so cutting at
    the first site is the difference between reachable and not.
    """
    proc, env = build(TWICE)
    goals = sorted(ir.goals_of(proc.body))
    inner = [g for g in goals if g.endswith(":T")]
    assert inner, goals

    events = slicing.linearize(proc.body)
    for goal in inner:
        sites = [i for i, (c, _) in enumerate(events)
                 if isinstance(c, (ir.Mark, ir.Fail)) and c.goal == goal]
        assert len(sites) == 2, (goal, len(sites))
        cut = slicing.cone(events, goal)
        assert all(id(events[s][0]) in cut.kept for s in sites), \
            "both sites, not the first one"
        assert sliced(proc, env, goal)[1] == verdict(proc, env, goal) == z3.sat


def test_an_occurrence_cone_keeps_one_site_without_claiming_the_public_goal():
    """Generation may search one copied site, but proof still needs them all."""

    proc, _env = build(TWICE)
    events = slicing.linearize(proc.body)
    goal = next(goal for goal in sorted(ir.goals_of(proc.body))
                if goal.endswith(":T"))
    sites = [index for index, (command, _under) in enumerate(events)
             if isinstance(command, (ir.Mark, ir.Fail))
             and command.goal == goal]
    assert len(sites) == 2

    for site in sites:
        cut = slicing.cone(events, goal, _site=site)
        assert cut is not None
        assert id(events[site][0]) in cut.kept
        other = next(index for index in sites if index != site)
        assert id(events[other][0]) not in cut.kept
        assert slicing.audit_occurrence(
            proc, goal, site, cut.kept, events=events) == ()
        assert slicing.audit(proc, (goal,), cut.kept, events=events), \
            "one occurrence is not an all-occurrence unreachability proof"


def test_indexed_occurrences_keep_shared_commands_nested_controls_and_failures(monkeypatch):
    shared = ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1)))
    first, second = ir.Mark("g"), ir.Mark("g")
    body = ir._sequence((
        ir.Assign("x", ir.Lit(0)),
        ir.For("i", 2, ir._sequence((
            ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)), shared, ir.Skip()),
            ir.For("j", 2, ir._sequence((
                ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)), shared, ir.Skip()),
                ir.If(ir.Op("eq", ir.Var("x"), ir.Var("a")), first,
                      ir.Fail("Stopped", "bad")),
            ))),
        ))),
        second,
    ))
    proc = ir.Procedure((), ("a",), ("x", "i", "j"), (), body)
    indexed = slicing.index(proc)
    assert sum(command is shared for command, _ in indexed.events) == 2
    expected = []
    for site, (command, _) in enumerate(indexed.events):
        if not isinstance(command, (ir.Mark, ir.Fail)):
            continue
        plain = encoder._goal_occurrence_keep(proc, command.goal, site, indexed.events)
        cached = encoder._goal_occurrence_keep(
            proc, command.goal, site, indexed.events, _index=indexed)
        assert cached == plain
        expected.append((command.goal, site, plain))
        assert slicing.audit_occurrence(
            proc, command.goal, site, cached, _index=indexed) == ()
        assert slicing.audit_occurrence(
            proc, command.goal, site, cached - {id(command)}, _index=indexed)

    def reread(*_args):
        pytest.fail("an indexed immutable command's direct reads were scanned twice")

    monkeypatch.setattr(slicing, "reads", reread)
    for goal, site, plain in expected:
        assert encoder._goal_occurrence_keep(
            proc, goal, site, indexed.events, _index=indexed) == plain

    foreign = ir.Procedure((), ("a",), (), (), ir.Mark("g"))
    with pytest.raises(ValueError, match="another procedure"):
        encoder._goal_occurrence_keep(foreign, "g", 0, indexed.events, _index=indexed)


@pytest.mark.parametrize("seed", range(16))
def test_indexed_batch_matches_every_acyclic_occurrence(seed):
    import random

    rng = random.Random(seed)
    shared = ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Var("a")))
    observations = ir.Mark("g"), ir.Fail("Stopped", "bad")

    def command(depth=0):
        choice = rng.randrange(7 if depth < 2 else 6)
        name = rng.choice(("x", "y"))
        if choice == 0:
            return ir.Assign(name, ir.Lit(rng.randrange(4)))
        if choice == 1:
            return shared
        if choice == 2:
            return ir.Assign(name, ir.Sel(ir.Var("mem"), ir.Var("b")))
        if choice == 3:
            return ir.Store("mem", ir.Var("a"), ir.Var(name))
        if choice in (4, 5):
            return observations[choice - 4]
        return ir.If(ir.Var(name), ir._sequence((command(depth + 1), command(depth + 1))),
                     command(depth + 1))

    proc = ir.Procedure(("mem",), ("a", "b"), ("x", "y"), (),
                        ir._sequence((ir.Assign("x", ir.Lit(0)), ir.Assign("y", ir.Lit(0)),
                                      *(command() for _ in range(40)), ir.Mark("last"))))
    indexed = slicing.index(proc)
    occurrences = tuple((site, item.goal) for site, (item, _under) in enumerate(indexed.events)
                        if isinstance(item, (ir.Mark, ir.Fail)))
    batch = dict(row for row in indexed.occurrence_batch(occurrences) if row is not None)
    for site, goal in occurrences:
        plain = slicing.cone(indexed.events, goal, _site=site)
        fast = batch[site, goal]
        assert fast.kept == plain.kept
        assert fast.needs == plain.needs
        assert indexed.binding_faults(fast.kept) == slicing._binding_faults(
            proc, plain.kept, indexed.events)

    # The compact path must produce exactly the previous singleton unions,
    # including shared command objects, stores and preceding failures.
    from behave.sim.engines.local_ir import _computation_ids, _shared_dependency_queries

    groups = {}
    for key, cut in batch.items():
        groups.setdefault(frozenset(cut.kept), []).append(key)
    expected, _ = _shared_dependency_queries(groups, indexed.events, prefixes=True)
    actual = [row for row in indexed.occurrence_components(
        occurrences, _computation_ids(indexed.events, None)) if row is not None]
    assert {(keep, tuple(members)) for keep, members, _ in actual} == {
        (keep, tuple(members)) for keep, members, _ in expected}
    for _keep, members, faults in actual:
        assert set(faults) == set().union(*(set(indexed.binding_faults(batch[key].kept))
                                          for key in members))


def test_component_audit_cannot_borrow_another_occurrences_definition():
    # Reading `missing` is invalid in the first cone, even though the second
    # cone writes it and both cones share x. A union-only audit would hide this.
    proc = ir.Procedure((), ("a",), ("x", "missing"), (), ir._sequence((
        ir.Assign("x", ir.Var("a")),
        ir.If(ir.Op("add", ir.Var("x"), ir.Var("missing")), ir.Mark("first"), ir.Skip()),
        ir.Assign("missing", ir.Var("a")),
        ir.If(ir.Op("add", ir.Var("x"), ir.Var("missing")), ir.Mark("second"), ir.Skip()))))
    indexed = slicing.index(proc)
    occurrences = tuple((site, goal) for goal, (site,) in indexed.goal_positions.items())
    computations = {id(cmd) for cmd, _ in indexed.events if isinstance(cmd, ir.Assign)}
    rows = [row for row in indexed.occurrence_components(occurrences, computations)
            if row is not None]
    assert len(rows) == 1
    keep, members, faults = rows[0]
    assert members == occurrences and faults == ("missing",)
    assert indexed.binding_faults(keep) == (), "a union-only audit misses the fault"


def test_component_planning_never_materializes_the_quadratic_singleton_cuts(monkeypatch):
    commands = [ir.Assign("x", ir.Var("a"))]
    for i in range(256):
        commands.extend((ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Lit(1))),
                         ir.If(ir.Var("x"), ir.Mark(f"g{i}"), ir.Skip())))
    proc = ir.Procedure((), ("a",), ("x",), (), ir._sequence(commands))
    indexed = slicing.index(proc)
    occurrences = tuple((site, goal) for goal, (site,) in indexed.goal_positions.items())

    def forbidden(*args, **kwargs):
        pytest.fail("component planning expanded or audited every singleton set")

    monkeypatch.setattr(slicing.SliceIndex, "occurrence_batch", forbidden)
    monkeypatch.setattr(slicing.SliceIndex, "binding_faults", forbidden)
    rows = list(indexed.occurrence_components(occurrences, {id(c) for c in commands
                                                          if isinstance(c, ir.Assign)}))
    components = [row for row in rows if row is not None]
    assert len(components) == 1 and components[0][1] == occurrences
    assert components[0][2] == ()
    assert len(components[0][0]) == 1 + 3 * len(occurrences)
    assert len(rows) < 5 * len(indexed.events)


def test_indexed_batch_shares_the_walk_and_command_reads(monkeypatch):
    irrelevant = tuple(ir.Assign(f"scratch{i}", ir.Var("a")) for i in range(2000))
    proc = ir.Procedure((), ("a",), ("x", *(f"scratch{i}" for i in range(2000))), (),
        ir._sequence((*irrelevant, ir.Assign("x", ir.Var("a")),
                      *(ir.If(ir.Var("x"), ir.Mark(f"g{i}"), ir.Skip()) for i in range(40)))))
    indexed = slicing.index(proc)
    occurrences = tuple((site, goal) for goal, (site,) in indexed.goal_positions.items())
    expected = {key: encoder._goal_occurrence_keep(proc, key[1], key[0], indexed.events)
                for key in occurrences}
    original, visited = slicing._command_reads, []

    def reads(command, out, cache=None):
        visited.append(id(command))
        return original(command, out, cache)

    monkeypatch.setattr(slicing, "_command_reads", reads)
    rows = list(indexed.occurrence_batch(occurrences))
    assert {key: cut.kept for key, cut in (row for row in rows if row is not None)} == expected
    assert not set(visited).intersection(map(id, irrelevant))
    assert len(set(visited)) <= 41
    assert len(rows) < 3 * len(indexed.events), "one batch walk, not one prefix walk per goal"


@pytest.mark.parametrize('steps', [1, 2])
@pytest.mark.parametrize('loop', [False, True])
def test_run_goal_union_matches_independent_cones(steps, loop):
    from itertools import combinations

    inner = ir._sequence((
        ir.If(ir.Var('a'), ir._sequence((ir.Mark('early'),
            ir.Assign('x', ir.Var('s')))), ir.Assign('x', ir.Lit(7))),
        ir.If(ir.Var('x'), ir.Mark('middle'), ir.Fail('Stopped', 'bad')),
        ir.Assign('x', ir.Var('a')),
        ir.If(ir.Var('x'), ir.Mark('early'), ir.Skip()),
        ir.Assign('s', ir.Op('add', ir.Var('s'), ir.Lit(1))),
    ))
    body = ir._sequence((ir.Assign('x', ir.Lit(0)),
        ir.For('i', 2, inner) if loop else inner,
        ir.If(ir.Var('s'), ir.Mark('late'), ir.Skip()), ir.Fail('Tail', 'tail')))
    proc = ir.Procedure(('s',), ('a',), ('x', 'i'), (), body)
    indexed = slicing.index(proc)
    goals = ('early', 'middle', 'late', 'bad', 'tail', 'absent')
    for size in (1, 2, len(goals)):
        for selected in combinations(goals, size):
            # Former algorithm: independent occurrence-position cones followed
            # by the same fixed-point predecessor closure. This is independent
            # of the new union walk, including repeated goal sites and failures.
            cuts = [cut for goal in selected
                    if (cut := slicing.cone(indexed.events, goal)) is not None]
            expected = None
            if cuts:
                expected = set().union(*(cut.kept for cut in cuts))
                needs = set().union(*(cut.needs for cut in cuts)) & set(proc.state)
                if steps > 1:
                    while True:
                        earlier = slicing.cone(indexed.events, seed=needs)
                        expected.update(earlier.kept)
                        expanded = (needs | earlier.needs) & set(proc.state)
                        if expanded == needs:
                            break
                        needs = expanded
            assert slicing.for_run(proc, iter(selected), steps=steps, _index=indexed) == expected


@pytest.mark.parametrize('positional', [False, True])
def test_run_goal_union_scans_body_once_not_once_per_goal(monkeypatch, positional):
    proc = ir.Procedure((), ('a',), ('x',), (), ir._sequence((
        ir.Assign('x', ir.Var('a')),
        *(ir.If(ir.Var('x'), ir.Mark(f'g{i}'), ir.Skip()) for i in range(100)))))
    indexed = slicing.index(proc)
    expected = set().union(*(slicing.cone(indexed.events, goal).kept
                             for goal in indexed.goal_positions))
    original, walks = slicing._reverse_cones, []

    def reverse(*args, **kwargs):
        walks.append(args[1])
        return (yield from original(*args, **kwargs))

    monkeypatch.setattr(slicing, '_reverse_cones', reverse)
    if positional:
        assert slicing.run_positions(proc, indexed.goal_positions, 1, _index=indexed) == (expected,)
    else:
        assert slicing.for_run(proc, indexed.goal_positions, _index=indexed) == expected
    assert len(walks) == 1


def test_indexed_binding_audit_does_not_hide_missing_names():
    from behave.sim.engines.local_ir import _LocalQueryPreparation

    proc = ir.Procedure((), (), (), (), ir.If(ir.Var("missing"), ir.Mark("g"), ir.Skip()))
    indexed = slicing.index(proc)
    site, = indexed.goal_positions["g"]
    with pytest.raises(encoder.EncodingError, match="missing"):
        encoder._goal_occurrence_keep(proc, "g", site, indexed.events, _index=indexed)
    owner = _LocalQueryPreparation(proc, {})
    owner.prepare_cones(((site, "g"),), lambda: None)
    with pytest.raises(encoder.EncodingError, match="missing"):
        owner.cone("g", site)


def test_known_occurrence_does_not_scan_unrelated_goal_sites():
    class IndexedEvents(list):
        def __iter__(self):
            pytest.fail("a known occurrence scanned the entire event list")

    mark = ir.Mark("g")
    events = IndexedEvents([(mark, ()), (ir.Mark("g"), ())])
    cut = slicing.cone(events, "g", _site=0, _bodies={}, _parents={})
    assert cut.kept == {id(mark)}


def test_an_occurrence_run_has_distinct_predecessor_target_and_tail_cones():
    goal = "copied-run-goal"
    update = ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Var("a")))
    first = ir.Mark(goal)
    dead = ir.Assign("dead", ir.Op("add", ir.Var("a"), ir.Lit(9)))
    second = ir.Mark(goal)
    body = ir._sequence((
        update,
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(1)), first, ir.Skip()),
        dead,
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(2)), second, ir.Skip()),
    ))
    proc = ir.Procedure(
        ("x",), ("a",), ("dead",), (), body, initial={"x": 0})
    events = slicing.linearize(body)
    sites = [
        index for index, (command, _under) in enumerate(events)
        if command is second
    ]
    assert len(sites) == 1

    keeps = slicing.for_occurrence_run(
        proc, goal, sites, target_step=1, steps=3, events=events)

    assert id(update) in keeps[0], "the predecessor computes target state"
    assert id(second) in keeps[1]
    assert id(first) not in keeps[1], "only the selected copied site is queried"
    assert id(dead) not in keeps[1]
    assert keeps[2] == frozenset(), "later code cannot change an earlier hit"
    assert slicing.audit_occurrence_run(
        proc, goal, sites, 1, 3, keeps, events=events) == ()

    broken = list(keeps)
    broken[0] = frozenset(set(broken[0]) - {id(update)})
    assert slicing.audit_occurrence_run(
        proc, goal, sites, 1, 3, broken, events=events)


def test_one_pass_occurrence_batch_is_the_union_of_its_singletons():
    goal = "copied-goal"
    first = ir.Mark(goal)
    second = ir.Mark(goal)
    body = ir._sequence((
        ir.Assign("x", ir.Var("a")),
        ir.If(ir.Var("x"), first, ir.Skip()),
        ir.Assign("x", ir.Var("b")),
        ir.If(ir.Var("x"), second, ir.Skip()),
    ))
    proc = ir.Procedure(("x",), ("a", "b"), (), (), body,
                        initial={"x": 0})
    indexed = slicing.index(proc)
    sites = tuple(
        position for position, (command, _under) in enumerate(indexed.events)
        if command is first or command is second)

    combined = slicing.occurrence_run(
        proc, goal, sites, target_step=1, steps=2, _index=indexed)
    separate = tuple(
        slicing.occurrence_run(
            proc, goal, (site,), target_step=1, steps=2, _index=indexed)
        for site in sites)

    assert combined.keeps == tuple(
        frozenset().union(*(part.keeps[step] for part in separate))
        for step in range(2))
    assert slicing.audit_occurrence_run(
        proc, goal, sites, 1, 2, combined, _index=indexed) == ()
    with pytest.raises(AttributeError):
        combined.sites = ()


def test_an_occurrence_unrolling_preserves_the_target_step_semantics():
    """The structural split remains an exact bounded-run question.

    The goal is reached only on the second step.  Its query therefore needs the
    first step's state update, while the third step is semantic padding.  All
    three input rows must nevertheless remain in the decoded trace domain.
    """

    goal = "second-step-goal"
    update = ir.Assign("x", ir.Op("add", ir.Var("x"), ir.Var("a")))
    mark = ir.Mark(goal)
    body = ir._sequence((
        update,
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(2)), mark, ir.Skip()),
    ))
    proc = ir.Procedure(
        ("x",), ("a",), (), (), body, initial={"x": 0})
    env = {"x": ranges.Range(0, 3), "a": ranges.Range(0, 1)}
    events = slicing.linearize(body)
    sites = tuple(
        index for index, (command, _under) in enumerate(events)
        if command is mark)

    run = residual.unroll(
        proc, env, 3, goals=(goal,), _occurrence=(1, sites))

    assert run.supports(goal)
    assert len(run.encodings) == 3
    for values, expected in (
            ((1, 1, 0), z3.sat),
            ((0, 1, 0), z3.unsat),
            ((1, 0, 1), z3.unsat)):
        solver = z3.Solver()
        solver.add(*run.formula.full_constraints, run.goal_condition(goal))
        solver.add(*(run.inputs_at(step, "a") == value
                     for step, value in enumerate(values)))
        assert solver.check() == expected

    solver = z3.Solver()
    solver.add(*run.formula.full_constraints, run.goal_condition(goal))
    assert solver.check() == z3.sat
    model = solver.model()
    decoded = [
        model.eval(run.inputs_at(step, "a"), model_completion=True).as_long()
        for step in range(3)
    ]
    assert len(decoded) == 3


def test_each_goal_occurrence_keeps_its_own_reaching_definition():
    """A later occurrence may not borrow the definition of an earlier one.

    Both sites report the same public goal.  The assignment between them kills
    ``x`` only for the later site; the first site still depends on ``x = a``.
    Comparing existential verdicts would miss this defect because both whole
    and reduced formulas are satisfiable for *some* input.  Pinning the same
    boundary input exposes the spurious reduced witness.
    """
    goal = "same-public-goal"

    def is_one():
        return ir.Op("eq", ir.Var("x"), ir.Lit(1))

    first_definition = ir.Assign("x", ir.Var("a"))
    body = ir._sequence((
        first_definition,
        ir.If(is_one(), ir.Mark(goal), ir.Skip()),
        ir.Assign("x", ir.Lit(0)),
        ir.If(is_one(), ir.Mark(goal), ir.Skip()),
    ))
    proc = ir.Procedure(
        ("x",), ("a",), (), (), body, initial={"x": 1})
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 1)})
    events = slicing.linearize(body)
    keep = slicing.for_run(proc, (goal,), events=events)

    assert id(first_definition) in keep
    assert slicing.audit(proc, (goal,), keep, events=events) == ()
    refused = slicing.audit(
        proc, (goal,), set(keep) - {id(first_definition)}, events=events)
    assert refused and "goal occurrence" in refused[0]

    whole = residual.unroll(proc, env, 1)
    reduced = residual.unroll(proc, env, 1, goals=(goal,))
    for value, expected in ((0, z3.unsat), (1, z3.sat)):
        answers = []
        for run in (whole, reduced):
            solver = z3.Solver()
            solver.add(
                *run.formula.full_constraints,
                run.goal_condition(goal),
                run.inputs_at(0, "a") == value,
            )
            answers.append(solver.check())
        assert answers == [expected, expected]


def test_a_loop_cone_includes_updates_for_the_next_iteration():
    """A body suffix can reach the same occurrence on the next iteration."""
    goal = "loop-carried-goal"
    update = ir.Assign("x", ir.Lit(1))
    loop = ir.For(
        "i", ir.Lit(2),
        ir._sequence((
            ir.If(
                ir.Op("eq", ir.Var("x"), ir.Lit(1)),
                ir.Mark(goal), ir.Skip()),
            update,
        )))
    proc = ir.Procedure(
        ("x",), (), ("i",), (), loop, initial={"x": 0})
    env = ranges.fixpoint(proc, {})
    events = slicing.linearize(loop)
    keep = slicing.for_run(proc, (goal,), events=events)

    assert id(update) in keep
    assert slicing.audit(proc, (goal,), keep, events=events) == ()

    whole = residual.unroll(proc, env, 1)
    reduced = residual.unroll(proc, env, 1, goals=(goal,))
    answers = []
    for run in (whole, reduced):
        solver = z3.Solver()
        solver.add(*run.formula.full_constraints, run.goal_condition(goal))
        answers.append(solver.check())
    assert answers == [z3.sat, z3.sat]


def test_a_loop_cone_keeps_a_failure_before_the_next_iteration():
    """A suffix failure can prevent a later visit to the same goal site."""
    goal = "second-iteration-goal"
    failure = ir.Fail("stopped", "loop-failure")
    loop = ir.For(
        "i", ir.Lit(2),
        ir._sequence((
            ir.If(
                ir.Op("eq", ir.Var("i"), ir.Lit(1)),
                ir.Mark(goal), ir.Skip()),
            failure,
        )))
    proc = ir.Procedure((), (), ("i",), (), loop)
    env = ranges.fixpoint(proc, {})
    events = slicing.linearize(loop)
    keep = slicing.for_run(proc, (goal,), events=events)

    assert id(failure) in keep

    whole = residual.unroll(proc, env, 1)
    reduced = residual.unroll(proc, env, 1, goals=(goal,))
    answers = []
    for run in (whole, reduced):
        solver = z3.Solver()
        solver.add(*run.formula.full_constraints, run.goal_condition(goal))
        answers.append(solver.check())
    assert answers == [z3.unsat, z3.unsat]


def test_a_goal_that_is_not_there_has_no_cone():
    proc, _env = build(UNRELATED)
    events = slicing.linearize(proc.body)
    assert slicing.cone(events, "no such goal") is None


def test_an_earlier_step_is_sliced_against_what_the_later_one_needs():
    """How a cone crosses a step boundary. With no goal of its own, the step owes
    the query the state names the next step reads and every failure it could have
    halted on -- and nothing else."""
    proc, _env = build(UNRELATED)
    events = slicing.linearize(proc.body)
    empty = slicing.cone(events, seed=())
    assert empty is not None
    assert not any(isinstance(c, ir.Assign) and id(c) in empty.kept
                   for c in ir.walk(proc.body)), \
        "a step nothing reads from contributes only its failures"

    wanted = slicing.cone(events, seed={"w"})
    kept = {c.var for c in ir.walk(proc.body)
            if isinstance(c, ir.Assign) and id(c) in wanted.kept}
    assert {"u", "v", "w"} <= kept, "the chain w is the end of"
    assert "a" in wanted.needs or not wanted.needs, wanted.needs


def test_a_predecessor_keeps_failures_after_the_goals_last_site():
    """A late failure in step 0 still prevents a goal in step 1.

    The one-step cone may drop a failure after the selected goal's last site:
    reaching that goal has already happened.  A multi-step cone may not; on an
    earlier step the same failure absorbs the run before a later occurrence.
    """
    goal = ir.Mark("selected")
    failure = ir.Fail("stopped", "late-failure")
    body = ir._sequence((
        ir.If(ir.Var("s"), goal, ir.Skip()),
        ir.Assign("s", ir.Lit(1)),
        failure,
    ))
    proc = ir.Procedure(("s",), (), (), (), body, initial={"s": 0})
    events = slicing.linearize(body)

    one = slicing.for_run(proc, ("selected",), steps=1, events=events)
    two = slicing.for_run(proc, ("selected",), steps=2, events=events)

    assert id(failure) not in one
    assert id(failure) in two


def test_residual_unrolling_consumes_the_audited_command_cone():
    proc, env = build(UNRELATED)
    goal = next(g for g in sorted(ir.goals_of(proc.body)) if g.endswith(":T"))

    whole = residual.unroll(proc, env, 1)
    selected = residual.unroll(proc, env, 1, goals=(goal,))

    assert selected.supports(goal)
    assert len(selected.formula.definitions) < len(whole.formula.definitions)
    whole_solver = z3.Solver()
    whole_solver.add(*whole.formula.full_constraints, whole.goal_condition(goal))
    selected_solver = z3.Solver()
    selected_solver.add(
        *selected.formula.full_constraints, selected.goal_condition(goal))
    assert selected_solver.check() == whole_solver.check() == z3.sat


def test_positional_cones_keep_preparation_but_drop_the_final_unused_update(monkeypatch):
    mark = ir.Mark("selected")
    update = ir.Assign("s", ir.Var("a"))
    failure = ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)),
                    ir.Fail("stopped", "late-failure"), ir.Skip())
    body = ir._sequence((ir.If(ir.Var("s"), mark, ir.Skip()), update, failure))
    proc = ir.Procedure(("s",), ("a",), (), (), body, initial={"s": 0})
    derive, calls = slicing._reverse_cones, []

    def counted(*args, **kwargs):
        calls.append(args[1])
        return (yield from derive(*args, **kwargs))

    monkeypatch.setattr(slicing, "_reverse_cones", counted)
    keeps = slicing.run_positions(proc, ("selected",), 100)
    assert all(id(update) in keep and id(failure) in keep for keep in keeps[:-1])
    assert id(update) not in keeps[-1] and id(failure) not in keeps[-1]
    assert all(id(mark) in keep for keep in keeps)
    assert len(calls) == 2  # One target walk, one distinct predecessor support.
    assert all(keep is keeps[0] for keep in keeps[:-1])


@pytest.mark.parametrize("late_failure", (False, True))
def test_positional_queries_agree_with_full_runs_for_every_small_input_sequence(late_failure):
    from itertools import product

    body = [ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(1)), ir.Mark("selected"), ir.Skip()),
            ir.Assign("s", ir.Var("a"))]
    if late_failure:
        body.append(ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)),
                          ir.Fail("stopped", "late-failure"), ir.Skip()))
    proc = ir.Procedure(("s",), ("a",), (), (), ir._sequence(body), initial={"s": 0})
    env = {"s": ranges.Range(0, 2), "a": ranges.Range(0, 2)}
    runs = (residual.unroll(proc, env, 3), residual.unroll(proc, env, 3, goals=("selected",)))
    for values in product(range(3), repeat=3):
        answers = []
        for run in runs:
            solver = z3.Solver()
            solver.add(*run.formula.full_constraints, run.goal_condition("selected"))
            solver.add(*(run.inputs_at(step, "a") == value for step, value in enumerate(values)))
            answers.append(solver.check())
        assert answers[0] == answers[1], values


def test_window_extension_rebuilds_the_changed_tail_without_reencoding_equal_steps():
    update = ir.Assign("s", ir.Var("a"))
    proc = ir.Procedure(("s",), ("a",), (), (), ir._sequence((
        ir.If(ir.Op("eq", ir.Var("s"), ir.Lit(1)), ir.Mark("selected"), ir.Skip()),
        update)), initial={"s": 0})
    env = {"s": ranges.Range(0, 1), "a": ranges.Range(0, 1)}
    short_owner = residual._RunConstruction()
    short = residual.unroll(proc, env, 2, goals=("selected",), _construction=short_owner)
    longer_owner = residual._RunConstruction(previous=short_owner)
    longer = residual.unroll(proc, env, 4, goals=("selected",), _construction=longer_owner)
    assert longer_owner.reused_steps == 1
    assert longer.encodings[0] is short.encodings[0]
    assert longer.encodings[1] is not short.encodings[1]
    solver = z3.Solver()
    solver.add(*longer.formula.full_constraints, longer.goal_condition("selected"))
    solver.add(*(longer.inputs_at(step, "a") == value for step, value in enumerate((0, 0, 1, 0))))
    assert solver.check() == z3.sat  # The rebuilt former tail must propagate state.


# ── against real models ──

#: Milliseconds each side of the comparison gets. Not the frontend's `QUERY_MS`,
#: which is fifteen minutes: these run in every normal test run, and the point is
#: to compare two verdicts rather than to obtain them at any cost.
QUERY_MS = 4_000

#: Models chosen for what they contain, not for being handy, and each verified to
#: lower and encode -- a fixture that cannot be lowered tests the frontend's refusal
#: and calls it a slicing result.
#:
#: The first has 92 goals of which 14 are reported at **two sites**, which is the
#: case that made this module wrong the first time. The second is the widest cone
#: reduction measured, 2.0% of 2448 commands. The third is a long unrolled body, which
#: is what made `prune` recurse past the interpreter's stack. The fourth is small and
#: dense, where a cone is a third of the step and the saving is nearly nothing.
REAL = ("numeric_pool_softfp_DivSqrtRecF64_mulAddZ31",
        "numeric_pool_dense_linalg_blas_dsyr2",
        "conwaylife",
        "ai_ml_arch_buffer_bank_bank_conflict_arbiter")


@pytest.mark.dataset
@pytest.mark.parametrize("name", REAL)
def test_a_sliced_query_answers_what_the_whole_step_answers(name):
    """The property the whole module rests on, on models rather than on sketches.

    A disagreement here is a defect and not a slow query: `unsat` where the whole
    step says `sat` means a goal called unreachable that a stimulus reaches, and
    `unreachable` is a claim over every admissible trace.

    Over a 150-model population sample this ran to **0 disagreements**, having found
    3 real ones first -- all of them goals reported at two sites. That measurement is
    `scratch/probe_p4_verdicts.py`; these four are what a normal run re-checks.

    Both sides are bounded by the same solver timeout, and a goal either side leaves
    `unknown` carries no information about the other -- that is the third answer, not
    a weaker `unsat`. How many were skipped for that reason is asserted rather than
    ignored, because a run where every goal timed out passes every comparison it
    makes and checks nothing.
    """
    from behave.core.paths import problems_root
    from behave.sim import task

    found = sorted(problems_root().rglob(f"{name}/behavior.py"))
    if not found:
        pytest.skip(f"{name} is not in this checkout's dataset")
    path = found[0]
    schema = task.schema_of_spec((path.parent / "spec.md").read_text(), name)
    from behave.sim.behavior import instance as bi

    inst = bi.lower(path.read_text(), schema, file=str(path), bound=1)
    goals = sorted(inst.goals())
    assert goals, f"{name} has no goals to slice"

    events = slicing.linearize(inst.proc.body)
    compared = 0
    for goal in goals:
        cut = slicing.cone(events, goal, total=len(events))
        assert cut is not None, goal
        small = slicing.sliced(inst.proc, [goal])
        whole = verdict(inst.proc, inst.env, goal, timeout_ms=QUERY_MS)
        part = verdict(small, inst.env, goal, timeout_ms=QUERY_MS)
        if z3.unknown in (whole, part):
            continue
        compared += 1
        assert part == whole, (name, goal, cut)
    assert compared >= len(goals) // 2, \
        f"{compared} of {len(goals)} goals decided in {QUERY_MS} ms: too few to " \
        f"be evidence of anything"


@pytest.mark.dataset
def test_a_share_of_goals_is_sliced_once_for_all_of_them():
    """What makes this pay. Two goals almost never have the same cone -- measured
    over the population, 2500 distinct cones for 2500 goals -- so the unit that
    matters is the **share** a worker rebuilds the formula for, not the goal.

    The union of a share's cones is still a fraction of the step, which is the whole
    claim: `for_run` over every goal of this model against the body it came from.
    """
    from behave.core.paths import problems_root
    from behave.sim import task
    from behave.sim.behavior import instance as bi

    found = sorted(problems_root().rglob(f"{REAL[0]}/behavior.py"))
    if not found:
        pytest.skip("dataset not in this checkout")
    path = found[0]
    schema = task.schema_of_spec((path.parent / "spec.md").read_text(), REAL[0])
    inst = bi.lower(path.read_text(), schema, file=str(path), bound=1)
    goals = sorted(inst.goals())

    events = slicing.linearize(inst.proc.body)
    total = len(events)
    every = slicing.for_run(inst.proc, goals)
    assert len(every) < total, "a union of every cone that is the whole step buys nothing"
    for goal in goals:
        one = slicing.cone(events, goal, total=total)
        assert one.kept <= every, "a share's slice covers each of its goals"


# ── the audit: checked, not argued ──

def test_the_audit_passes_on_every_slice_this_module_computes():
    """The fourth condition, on each of the sketches. Passing here is the
    ordinary case; the next test is what happens when it does not."""
    for source, kwargs in ((CONDITIONAL_KILL, {}), (FAILS_BEFORE, {"a": 1, "b": 1}),
                           (UNRELATED, {}), (TWICE, {})):
        proc, _env = build(source, **kwargs)
        goals = sorted(ir.goals_of(proc.body))
        events = slicing.linearize(proc.body)
        for goal in goals:
            keep = slicing.for_run(proc, [goal], events=events)
            assert slicing.audit(proc, [goal], keep, events=events) == (), \
                (source.splitlines()[0], goal)
        keep = slicing.for_run(proc, goals, events=events)
        assert slicing.audit(proc, goals, keep, events=events) == ()


def test_a_slice_that_reads_a_name_nothing_writes_is_refused():
    """The check that replaces the argument.

    Reasoning about this module has been wrong twice, and both times the
    comparison found it rather than the reasoning. So the condition is checked per
    artifact: a kept command reading a name no kept command binds means a formula
    with a name standing for nothing, which is how a definition that reaches the
    goal comes to be missing.

    Simulated by removing one assignment from a slice that needs it -- what the
    conditional-kill bug did on its own.
    """
    proc, _env = build(UNRELATED)
    goal = next(g for g in sorted(ir.goals_of(proc.body)) if g.endswith(":T"))
    events = slicing.linearize(proc.body)
    keep = set(slicing.for_run(proc, [goal], events=events))
    assert slicing.audit(proc, [goal], keep, events=events) == ()

    # `y = 0` is in the cone and binds `y`; drop it and `y` is read by nothing
    # that writes it.
    victim = next(c for c, _ in events
                  if isinstance(c, ir.Assign) and id(c) in keep
                  and c.var not in proc.state and c.var not in proc.inputs)
    broken = keep - {id(victim)}
    why = slicing.audit(proc, [goal], broken, events=events)
    assert why, f"dropping the definition of {victim.var} has to be caught"
    assert victim.var in why


def test_a_dropped_goal_site_is_refused():
    """The first condition, checked as well as constructed. A slice keeping only
    some of a goal's sites answers a different question, and `TWICE` is the shape
    where that changes the verdict."""
    proc, _env = build(TWICE)
    goal = next(g for g in sorted(ir.goals_of(proc.body)) if g.endswith(":T"))
    events = slicing.linearize(proc.body)
    keep = set(slicing.for_run(proc, [goal], events=events))
    sites = [c for c, _ in events
             if isinstance(c, (ir.Mark, ir.Fail)) and c.goal == goal]
    assert len(sites) == 2

    why = slicing.audit(proc, [goal], keep - {id(sites[0])}, events=events)
    assert why and "sites" in why[0], why


def test_a_refused_slice_gives_back_the_whole_step():
    """What `sliced` does with a failed audit: hands back the step it was asked to
    slice, and says so. Faster and wrong is worse than slow, and the unsliced step
    answers the same question."""
    proc, _env = build(UNRELATED)
    goals = sorted(ir.goals_of(proc.body))

    told = []
    small = slicing.sliced(proc, goals, refused=told.append)
    assert not told, "these slices pass, so nothing is refused"
    assert small is not proc

    # A goal that is not in the body leaves nothing to slice, which is not a
    # refusal: there is no cone to audit.
    same = slicing.sliced(proc, ["no such goal"], refused=told.append)
    assert same is proc and not told


def _procedure_with_graph(graph, *, state, inputs=(), locals=(), outputs=(),
                          initial=None):
    body = cfg.if_convert(graph)
    proc = ir.Procedure(
        state, inputs, locals, outputs, body, initial=initial)
    ir._bind_control_flow(
        proc, ir._make_control_flow_authority(graph, body))
    return proc


def test_state_range_graph_drops_output_only_computation():
    output_work = ir.For(
        "i", ir.Lit(8),
        ir.Assign("temporary", ir.Op(
            "add", ir.Var("temporary"), ir.Var("i"))),
    )
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (
            ir.Assign("state", ir.Op(
                "add", ir.Var("state"), ir.Var("a"))),
            output_work,
            ir.Assign("y", ir.Var("temporary")),
        ), cfg.Return()),
    }, "entry")
    proc = _procedure_with_graph(
        graph, state=["state"], inputs=["a"],
        locals=["i", "temporary"], outputs=["y"],
        initial={"state": 0})
    projected = slicing.state_range_graph(proc)
    at = {"state": ranges.Range(0, 3), "a": ranges.Range(0, 1)}

    assert sum(len(block.instrs) for block in projected.blocks.values()) == 1
    assert ranges.graph_post(projected, at)["state"] \
        == ranges.graph_post(graph, at)["state"]


def test_state_range_graph_retains_a_guard_that_bounds_state():
    graph = cfg.CFG({
        "entry": cfg.Block(
            "entry", (ir.Assign(
                "condition", ir.Op("lt", ir.Var("state"), ir.Lit(5))),),
            cfg.Branch(ir.Var("condition"), "increment", "exit")),
        "increment": cfg.Block(
            "increment", (ir.Assign(
                "state", ir.Op("add", ir.Var("state"), ir.Lit(1))),),
            cfg.Goto("exit")),
        "exit": cfg.Block("exit", (), cfg.Return()),
    }, "entry")
    proc = _procedure_with_graph(
        graph, state=["state"], locals=["condition"],
        initial={"state": 0})

    projected = slicing.state_range_graph(proc)

    assert ranges.fixpoint(proc, {}, _graph=projected)["state"] \
        == ranges.Range(0, 5)


def test_state_range_graph_may_only_add_a_successor_when_dropping_work():
    output_work = ir.For(
        "i", ir.Lit(1), ir.Fail("output-failed", "safety"))
    graph = cfg.CFG({
        "entry": cfg.Block("entry", (
            output_work,
            ir.Assign("state", ir.Lit(7)),
        ), cfg.Return()),
    }, "entry")
    proc = _procedure_with_graph(
        graph, state=["state"], locals=["i"], initial={"state": 0})

    complete = ranges.graph_post(graph, {"state": ranges.Range(0, 0)})
    projected = ranges.graph_post(
        slicing.state_range_graph(proc), {"state": ranges.Range(0, 0)})

    assert complete is ranges._NO_NORMAL
    assert projected["state"] == ranges.Range(7, 7)


def test_boundary_cone_keeps_data_dependencies_and_erases_failures():
    source = ir.Assign("temporary", ir.Var("a"))
    failure = ir.Fail("IndexError", "safety")
    state = ir.Assign("state", ir.Var("temporary"))
    unrelated = ir.Assign("y", ir.Lit(9))
    body = ir._sequence((source, failure, state, unrelated))
    proc = ir.Procedure(
        ("state",), ("a",), ("temporary",), ("y",), body,
        initial={"state": 0}, output_vars={"y": "y"})

    kept = slicing.for_boundary(proc, ("state",))

    assert kept is not None
    assert id(source) in kept
    assert id(state) in kept
    assert id(failure) not in kept
    assert id(unrelated) not in kept
