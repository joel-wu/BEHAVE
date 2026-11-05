"""Control flow as a graph, and what that makes exact.

The step the encoder consumes is a term in which leaving early is a flag that
later conditions test. That is the output of if-conversion, and writing it
directly threw away the shape it came from, so every question about that shape
had to be recovered by analysis over the term. Those analyses were expensive,
imprecise, and never finished: each construct loses the information its own way.

Here the questions are lookups. A join is a join and a definition either reaches
a use or does not.
"""

from behave.sim.symbolic import cfg, ir, ssa
from behave.sim.behavior.lowering import lower_procedure


def build(body, extra=""):
    """A term, and the graph `cfg.of_command` reads back out of it.

    Explicitly the term lowerer, which is what these tests are about. The default is the
    graph lowerer now, and then `proc.body` is already if-converted: reading a graph out
    of that gives the graph of the flattened program, where an arm is guarded
    straight-line code rather than a branch, so the questions below about dominance and
    about which blocks carry a condition are asked of the wrong thing. Two of them
    failed exactly that way when the default moved.

    `of_command` exists to read a graph out of a term and is the fallback for a procedure
    nobody built one for, so a test of it hands it a term.
    """
    from behave.sim.behavior.lowering import _Lowerer

    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.n = 0\n"
           + extra
           + "    def process(self, a):\n"
           + "".join(f"        {line}\n" for line in body))
    proc = lower_procedure(src, lowerer=_Lowerer)
    graph = cfg.of_command(proc.body)
    return proc, graph


def undefined(proc, graph):
    return ssa.undefined_uses(graph, set(proc.state) | set(proc.inputs))


def test_a_terminator_is_a_terminator():
    """A failure ends the block. As a command in the term it was one more thing
    in a sequence, and that nothing after it runs was a fact about the encoding
    rather than about the program."""
    _proc, graph = build(['return {"y": a // 0}'])
    fails = [b for b in graph.blocks.values()
             if isinstance(b.term, cfg.Fail)]
    assert fails, "the guarded division ends a block"
    assert fails[0].term.successors() == ()
    assert all(not b.instrs or not any(isinstance(i, ir.Fail)
                                       for i in b.instrs)
               for b in graph.blocks.values()), \
        "no failure is left sitting among the instructions"


def test_the_graph_is_acyclic_so_every_block_has_an_order():
    _proc, graph = build(["z = 0",
                         "for i in range(3):",
                         "    z = z + i",
                         'return {"y": z}'])
    order = graph.order()
    assert len(order) == len(graph.blocks)
    place = {label: n for n, label in enumerate(order)}
    for label, block in graph.blocks.items():
        for succ in block.term.successors():
            assert place[label] < place[succ], f"{label} -> {succ}"


def test_a_read_after_an_early_return_needs_no_interval_analysis():
    """The case that forced one. `if a: return` followed by `t = 5; return t`
    puts the write and the read under two guards that a term cannot relate, so
    the analysis over the term reported it and an interval analysis was added
    to explain it away. On the graph the two are one path and there is nothing
    to explain."""
    proc, graph = build(["if a:", '    return {"y": 1}', "t = 5",
                         'return {"y": t}'])
    assert undefined(proc, graph) == []


def test_a_use_with_no_definition_on_some_path_is_found():
    """The check has to be able to fail, or it says nothing. A store into a
    name on one arm only leaves a path to the read that never wrote it."""
    graph = cfg.CFG({
        "entry": cfg.Block("entry", [], cfg.Branch(ir.Var("a"), "t", "e")),
        "t": cfg.Block("t", [ir.Assign("v", ir.Lit(1))], cfg.Goto("join")),
        "e": cfg.Block("e", [], cfg.Goto("join")),
        "join": cfg.Block("join", [ir.Assign("y", ir.Var("v"))],
                          cfg.Return()),
    }, "entry")
    reasons = ssa.undefined_uses(graph, {"a"})
    assert len(reasons) == 1 and "v is read in join" in reasons[0]


def test_repeated_immutable_guard_removes_contradictory_definition_paths():
    graph = cfg.CFG({
        "entry": cfg.Block(
            "entry", [], cfg.Branch(
                ir.Var("g0"), "left_def", "right_def", atom="choose")),
        "left_def": cfg.Block(
            "left_def", [ir.Assign("left", ir.Lit(1))], cfg.Goto("join")),
        "right_def": cfg.Block(
            "right_def", [ir.Assign("right", ir.Lit(2))], cfg.Goto("join")),
        "join": cfg.Block(
            "join", [], cfg.Branch(
                ir.Var("g1"), "left_use", "right_use", atom="choose")),
        "left_use": cfg.Block(
            "left_use", [ir.Assign("y", ir.Var("left"))], cfg.Return()),
        "right_use": cfg.Block(
            "right_use", [ir.Assign("y", ir.Var("right"))], cfg.Return()),
    }, "entry")

    assert ssa.undefined_uses(graph, {"g0", "g1"}) == []


def test_unrelated_guards_do_not_lend_definitions_to_each_other():
    graph = cfg.CFG({
        "entry": cfg.Block(
            "entry", [], cfg.Branch(
                ir.Var("g0"), "defined", "empty", atom="first")),
        "defined": cfg.Block(
            "defined", [ir.Assign("value", ir.Lit(1))], cfg.Goto("join")),
        "empty": cfg.Block("empty", [], cfg.Goto("join")),
        "join": cfg.Block(
            "join", [], cfg.Branch(
                ir.Var("g1"), "use", "done", atom="second")),
        "use": cfg.Block(
            "use", [ir.Assign("y", ir.Var("value"))], cfg.Return()),
        "done": cfg.Block("done", [], cfg.Return()),
    }, "entry")

    reasons = ssa.undefined_uses(graph, {"g0", "g1"})
    assert len(reasons) == 1 and "value is read in use" in reasons[0]


DIAMOND = cfg.CFG({
    "entry": cfg.Block("entry", [], cfg.Branch(ir.Var("a"), "t", "e")),
    "t": cfg.Block("t", [], cfg.Goto("join")),
    "e": cfg.Block("e", [], cfg.Goto("join")),
    "join": cfg.Block("join", [], cfg.Goto("after")),
    "after": cfg.Block("after", [], cfg.Return()),
}, "entry")


def test_the_join_of_a_conditional_is_reached_whenever_the_branch_was():
    """Which is what makes its flag unnecessary. Both arms lead to the join, so
    the join post-dominates the block that branched, and control reaching that
    block reaches the join."""
    idom = DIAMOND.dominators()
    ipdom = DIAMOND.post_dominators()
    assert idom["entry"] is None
    assert idom["t"] == "entry" and idom["e"] == "entry"
    assert idom["join"] == "entry", "neither arm dominates the join"
    assert ipdom["entry"] == "join", "the join is where the arms come back"
    assert ipdom["t"] == "join" and ipdom["e"] == "join"


def test_an_arm_is_not_reached_whenever_the_branch_was():
    """The other half of the rule, and the half that keeps it honest: an arm
    needs a condition of its own, because the other arm avoids it."""
    ipdom = DIAMOND.post_dominators()
    assert ipdom["entry"] != "t" and ipdom["entry"] != "e"


def test_indexed_post_dominance_is_the_parent_pointer_relation():
    """Indexing is a performance change, not a second control-flow rule."""
    separate_exits = cfg.CFG({
        "entry": cfg.Block(
            "entry", [], cfg.Branch(ir.Var("a"), "returned", "failed")),
        "returned": cfg.Block("returned", [], cfg.Return()),
        "failed": cfg.Block("failed", [], cfg.Fail("Stopped")),
    }, "entry")
    for graph in (DIAMOND, separate_exits):
        ipdom = graph.post_dominators()
        intervals = cfg._forest_intervals(ipdom)
        assert intervals is not None
        for dominator in ipdom:
            for label in ipdom:
                assert cfg._follows(label, dominator, ipdom, intervals) == \
                    cfg._follows(label, dominator, ipdom)


def test_a_block_reached_whenever_its_dominator_was_carries_no_condition():
    """The join of a conditional and everything after it. Their instructions run
    under the condition the branch ran under, which for a plain if/else at the
    top of a step is the truth, so they are not wrapped at all.

    The true arm shares the decision flag; the false arm needs one assignment.
    There is nothing to clear or disjoin for either successor."""
    _proc, graph = build(["if a:", "    z = 1", "else:", "    z = 2",
                         "w = z + 1", 'return {"y": w}'])
    term = cfg.if_convert(graph)
    writes = [c for c in ir.walk(term)
              if isinstance(c, ir.Assign) and c.var.startswith("#reached:")]
    assert len(writes) == 1, "only the false arm needs a separate flag"
    assert all(not isinstance(c.expr, ir.Op) or c.expr.op != "or"
               for c in writes), "no join, so no disjunction"
    assert not any(isinstance(c.expr, ir.Lit) for c in writes), \
        "one edge decides each, so there is nothing to clear"
    decided = {c.var for c in ir.walk(term)
               if isinstance(c, ir.Assign) and c.var.startswith("#taken:")}
    assert len(decided) == 1, "one branch, one decision"
    decisions = [c for c in ir.walk(term)
                 if isinstance(c, ir.Assign) and c.var in decided]
    assert len(decisions) == 1, "an unconditional decision needs no clear"


def test_shared_decision_does_not_evaluate_an_inactive_guard():
    # Inner guards can read temporaries only defined in their active branch.
    agrees(["y = 0", "if a:", "    t = a + 1", "    if t > 2:",
            "        y = t", 'return {"y": y}'],
           [{"a": a} for a in range(4)])


def test_conditional_decision_is_cleared_on_each_loop_entry():
    from behave.sim.symbolic import interp

    graph = cfg.CFG({
        "entry": cfg.Block("entry", [], cfg.Branch(ir.Var("i"), "exit", "inner")),
        "inner": cfg.Block("inner", [], cfg.Branch(ir.Var("a"), "hit", "exit")),
        "hit": cfg.Block("hit", [ir.Assign("y", ir.Op("add", ir.Var("y"), ir.Lit(1))),
                                  ir.Mark("inner-hit")], cfg.Goto("exit")),
        "exit": cfg.Block("exit", [], cfg.Return()),
    }, "entry")
    body = cfg.if_convert(graph)
    flags = sorted({c.var for c in ir.walk(body)
                    if isinstance(c, ir.Assign) and c.var.startswith("#")})
    proc = ir.Procedure([], ["a"], ["y", "i", *flags], ["y"],
                        ir.Seq(ir.Assign("y", ir.Lit(0)),
                               ir.For("i", ir.Lit(3), body)))
    for a in (0, 1):
        _, output, hits, error = interp.run_step(proc, {"a": a})
        assert not error
        assert output.transactions == [{"y": a}]
        assert ("inner-hit" in hits) == bool(a)


def test_shared_true_edge_does_not_replace_a_real_join():
    from behave.sim.symbolic import interp

    graph = cfg.CFG({
        "entry": cfg.Block("entry", [], cfg.Branch(ir.Var("a"), "hit", "other")),
        "other": cfg.Block("other", [], cfg.Branch(ir.Var("b"), "hit", "exit")),
        "hit": cfg.Block("hit", [ir.Assign("y", ir.Lit(1)), ir.Mark("hit")],
                         cfg.Goto("exit")),
        "exit": cfg.Block("exit", [], cfg.Return()),
    }, "entry")
    body = cfg.if_convert(graph)
    flags = sorted({c.var for c in ir.walk(body)
                    if isinstance(c, ir.Assign) and c.var.startswith("#")})
    proc = ir.Procedure([], ["a", "b"], ["y", *flags], ["y"],
                        ir.Seq(ir.Assign("y", ir.Lit(0)), body))
    for a in (0, 1):
        for b in (0, 1):
            _, output, hits, error = interp.run_step(proc, {"a": a, "b": b})
            assert not error
            assert output.transactions == [{"y": int(bool(a or b))}]
            assert ("hit" in hits) == bool(a or b)

def test_a_condition_that_always_holds_does_not_cost_a_range():
    """Why the rule is not an optimisation. The instructions of a block sit under
    its condition, the range analysis joins the arms of a conditional, and a
    variable first written inside one has no range afterwards. So a flag the
    analysis cannot see through loses a width, and thirty-four models refused to
    encode for want of one the term lowering had.

    Stated as the range rather than as the shape: what is owed is the width, and
    which conditionals are present is how it is paid.
    """
    from behave.sim.symbolic import ranges
    from behave.sim.behavior.lower_graph import GraphLowerer
    from behave.sim.behavior.lowering import lower_procedure

    src = ("class M:\n"
           "    def reset(self): pass\n"
           "    def process(self, a):\n"
           "        w = 1\n"
           "        if a > 2:\n"
           "            w = 4\n"
           "        z = a << w\n"
           '        return {"y": z}\n')
    wanted = ranges.fixpoint(lower_procedure(src), {"a": ranges.Range(0, 7)})
    graph = lower_procedure(src, lowerer=GraphLowerer)
    got = ranges.fixpoint(graph, {"a": ranges.Range(0, 7)})
    assert got["z"] == wanted["z"], (got.get("z"), wanted["z"])


def test_a_flag_is_still_written_where_a_block_needs_one():
    """The rule has to be able to say yes. A guard's failure block is entered
    only on one arm, so it is not reached whenever its dominator was and it
    carries its own condition."""
    proc, graph = build(["z = 100 // a", 'return {"y": z}'])
    term = cfg.if_convert(graph)
    assert any(isinstance(c, ir.Fail) for c in ir.walk(term))
    # And the step still fails on the input the guard is there for.
    from behave.sim.symbolic import interp
    proc.body = term
    _, _, _, error = interp.run_step(proc, {"a": 0})
    assert error == "DivZero"


def test_a_branch_on_something_undefined_is_found_too():
    graph = cfg.CFG({
        "entry": cfg.Block("entry", [], cfg.Branch(ir.Var("v"), "t", "e")),
        "t": cfg.Block("t", [], cfg.Goto("exit")),
        "e": cfg.Block("e", [], cfg.Goto("exit")),
        "exit": cfg.Block("exit", [], cfg.Return()),
    }, "entry")
    reasons = ssa.undefined_uses(graph, set())
    assert len(reasons) == 1 and "branched on" in reasons[0]


def test_a_join_takes_what_both_ways_in_leave():
    graph = cfg.CFG({
        "entry": cfg.Block("entry", [ir.Assign("p", ir.Lit(0))],
                           cfg.Branch(ir.Var("p"), "t", "e")),
        "t": cfg.Block("t", [ir.Assign("v", ir.Lit(1)),
                             ir.Assign("w", ir.Lit(1))], cfg.Goto("join")),
        "e": cfg.Block("e", [ir.Assign("v", ir.Lit(2))], cfg.Goto("join")),
        "join": cfg.Block("join", [], cfg.Return()),
    }, "entry")
    into, _ = ssa.reaching(graph, set())
    assert "v" in into["join"], "assigned both ways in"
    assert "w" not in into["join"], "assigned one way in"


def test_one_compact_analysis_owns_reaching_and_undefined_facts():
    analysis = ssa.analyze(DIAMOND, {"a"})

    assert all(type(mask) is int for mask in analysis.into_masks.values())
    assert all(type(mask) is int for mask in analysis.out_masks.values())
    assert analysis.into("join") == {"a"}
    assert analysis.undefined_names == set()
    assert analysis.undefined_uses == ()


def test_the_construction_does_not_use_the_interpreter_stack():
    """The step this reads has its loops unrolled and its calls inlined, so a
    nesting of a few thousand is ordinary. Seventy-four models of the
    population were deep enough to exhaust the recursion the first version
    used, which is a fact about the recursion and not about them."""
    deep = ir.Skip()
    for n in range(4000):
        deep = ir.Seq(deep, ir.Assign(f"t{n}", ir.Lit(n)))
    graph = cfg.of_command(deep)
    assert len(graph.order()) == len(graph.blocks)


# ── if-conversion, whose obligation is that it changes nothing ──

def convert(proc):
    """The procedure the graph flattens back to, ready to run."""
    graph = cfg.of_command(proc.body)
    back = cfg.if_convert(graph)
    flags = sorted({c.var for c in ir.walk(back)
                    if isinstance(c, ir.Assign) and c.var.startswith("#")})
    return ir.Procedure(proc.state, proc.inputs, list(proc.locals) + flags,
                        proc.outputs, back, count=proc.count,
                        impossible=proc.impossible, initial=proc.initial,
                        output_vars=proc.output_vars)


def agrees(body, stimuli, extra=""):
    """The term and the graph flattened back, on the same stimuli."""
    from behave.sim.symbolic import interp

    proc, _graph = build(body, extra)
    other = convert(proc)
    for row in stimuli:
        state_a, out_a, hits_a, err_a = interp.run_step(proc, row)
        state_b, out_b, hits_b, err_b = interp.run_step(other, row)
        assert bool(err_a) == bool(err_b), (row, err_a, err_b)
        assert out_a.transactions == out_b.transactions, row
        assert hits_a == hits_b, row
        assert state_a == state_b, row
    return proc


def test_if_conversion_changes_nothing_about_a_conditional():
    agrees(["if a:", "    y = 1", "else:", "    y = 2", 'return {"y": y}'],
           [{"a": 0}, {"a": 1}])


def test_if_conversion_changes_nothing_about_a_loop():
    agrees(["z = 0", "for i in range(4):", "    z = z + i",
            'return {"y": z}'], [{"a": 0}, {"a": 3}])


def test_if_conversion_changes_nothing_about_an_early_return():
    agrees(["if a:", '    return {"y": 1}', "t = 5", 'return {"y": t}'],
           [{"a": 0}, {"a": 1}])


def test_if_conversion_changes_nothing_about_a_failure():
    """A failure is a terminator in the graph and a command in the term, so
    this is the case where the two representations differ most."""
    agrees(['return {"y": a // (a - 1)}'], [{"a": 0}, {"a": 1}, {"a": 5}])


def test_the_flags_are_written_before_they_are_read():
    """The first version wrote none of them, and the interpreter refused to run
    the result: a name with no definition is what this layer exists to make
    impossible, so producing one would have been the layer failing its own test.

    Written, not cleared. Clearing is how a name several edges can set gets a
    starting value; a name one edge decides is assigned outright, and asking for
    a clear would be asking for the shape the conversion had rather than for the
    property it owes.
    """
    for body in (["if a:", "    y = 1", "else:", "    y = 2",
                  'return {"y": y}'],
                 ["z = 100 // a", "if z > 2:", "    z = z + 1",
                  'return {"y": z}'],
                 ["z = 0", "for i in range(3):", "    if i > a:", "        z = 1",
                  'return {"y": z}']):
        proc, _ = build(body)
        back = cfg.if_convert(cfg.of_command(proc.body))
        written = set()
        for c in ir.walk(back):
            if isinstance(c, ir.If):
                read = {n for n in ssa._reads(c.guard) if n.startswith("#")}
                assert read <= written, sorted(read - written)
            if isinstance(c, ir.Assign):
                if c.var.startswith("#"):
                    # Its own reads come first: a disjunction onto a name reads
                    # the name, and that is fine only once it has a value.
                    read = {n for n in ssa._reads(c.expr) if n.startswith("#")}
                    assert read <= written | {c.var}, sorted(read - written)
                written.add(c.var)


def test_the_certificate_asks_the_graph_about_definitions():
    """Whether a use has a definition reaching it is a question about control
    flow, so it is asked of the control flow rather than of the term it was
    flattened into. The check has to be able to fail, or adding it bought
    nothing."""
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.symbolic import ranges

    proc, _ = build(["if a:", "    y = 1", "else:", "    y = 2",
                     'return {"y": y}'])
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 255)})
    inst = behavior_instance.BehaviorInstance(proc, env)
    assert inst.well_formed() == []

    # One arm stops assigning, so a path reaches the read having written
    # nothing. The term is edited rather than the source, since the lowering
    # will not produce this and the point is that the check would catch it.
    def blind(cmd):
        if isinstance(cmd, ir.If) and isinstance(cmd.orelse, ir.Seq):
            return ir.If(cmd.guard, cmd.then, ir.Skip())
        if isinstance(cmd, ir.Seq):
            return ir.Seq(blind(cmd.first), blind(cmd.second))
        if isinstance(cmd, ir.If):
            return ir.If(cmd.guard, blind(cmd.then), blind(cmd.orelse))
        return cmd

    proc.body = blind(proc.body)
    broken = behavior_instance.BehaviorInstance(proc, env)
    assert any("no definition reaching it" in r for r in broken.well_formed())
