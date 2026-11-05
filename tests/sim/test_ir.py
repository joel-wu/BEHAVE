"""Unit tests for the Behavior intermediate representation.

The checks establish the typed, total command boundary consumed by the
interpreter and encoder: failures carry safety goals, goal classes are
unambiguous, loops are finite, and every output is assigned.
"""
import pytest

from behave.sim.symbolic.ir import (
    Assign,
    Fail,
    For,
    If,
    Lit,
    Mark,
    Op,
    Procedure,
    Seq,
    Skip,
    Store,
    Var,
    normalized,
    walk,
)


def _proc(body, outputs=("y",), state=(), inputs=("a",), locals=()):
    return Procedure(state, inputs, locals, outputs, body)


def test_a_plain_procedure_is_normalized():
    body = Seq(Mark("branch:m.py:1:0:0:T"), Assign("y", Var("a")))
    assert normalized(_proc(body)) == []


def test_walk_visits_every_command_in_preorder():
    body = Seq(Skip(), If(Var("a"), Assign("y", Lit(1)), Assign("y", Lit(2))))
    kinds = [type(c).__name__ for c in walk(body)]
    assert kinds == ["Seq", "Skip", "If", "Assign", "Assign"]


@pytest.mark.parametrize('consumer', ['ir', 'ssa', 'slicing', 'purity', 'nodes'])
def test_expression_consumers_share_linear_dag_traversal(consumer):
    from behave.sim.symbolic import ir, slicing, ssa

    visits = {}

    class CountedOp(Op):
        def __getattribute__(self, name):
            if name == 'args':
                key = id(self)
                visits[key] = visits.get(key, 0) + 1
                assert visits[key] == 1, 'a shared operand was expanded again'
            return super().__getattribute__(name)

    expr = ir.Sel(Var('memory'), Var('index'))
    for _ in range(40):
        expr = CountedOp('add', expr, expr)
    if consumer == 'nodes':
        # Validation also shares the walk across distinct command roots.
        nodes = tuple(ir.expression_nodes(Seq(Assign('x', expr), Assign('y', expr))))
        assert len(nodes) == 43
    elif consumer == 'purity':
        assert list(ir._pure(Assign('y', expr))) == []
    else:
        if consumer == 'slicing':
            names = {'previous'}
            slicing.reads(expr, names)
            assert names == {'previous', 'memory', 'index'}
        else:
            reader = ir._reads if consumer == 'ir' else ssa._reads
            assert list(reader(expr)) == ['memory', 'index']
    assert len(visits) == 40


def test_expression_reads_are_iterative_and_do_not_cache_mutable_ir():
    from behave.sim.symbolic import ir, slicing, ssa

    leaf = Var('first')
    expr = leaf
    for _ in range(2500):
        expr = Op('add', expr, Lit(1))
    assert list(ir._reads(expr)) == ['first']
    assert list(ssa._reads(expr)) == ['first']
    leaf.name = 'second'
    names = set()
    slicing.reads(expr, names)
    assert names == {'second'}
    assert list(ir._pure(Assign('y', expr))) == []


def test_shared_expression_reads_keep_each_definition_context():
    from behave.sim.symbolic import cfg, ir, ssa

    expr = Op('add', Var('x'), Lit(1))
    graph = cfg.CFG({'entry': cfg.Block('entry', [
        Assign('before', expr), Assign('x', Lit(0)), Assign('after', expr)
    ], cfg.Return())}, 'entry')
    reasons = ssa.undefined_uses(graph, set())
    assert len(reasons) == 1 and 'x is read' in reasons[0]
    body = ir._sequence(graph.blocks['entry'].instrs)
    reasons = []
    ir._reads_before_assignment(body, set(), (), reasons)
    assert reasons == ['x is read before it is assigned']


def test_a_deep_sequence_fingerprint_is_independent_of_association():
    """Association is construction history, and hashing it must stay linear."""
    from behave.sim.symbolic.ir import command_fingerprint

    parts = [Assign(f"v{index}", Lit(index)) for index in range(1500)]
    left = parts[0]
    for part in parts[1:]:
        left = Seq(left, part)
    right = parts[-1]
    for part in reversed(parts[:-1]):
        right = Seq(part, right)
    assert command_fingerprint(left) == command_fingerprint(right)


def test_a_failure_that_reaches_no_goal_is_rejected():
    body = Seq(Assign("y", Lit(0)), Fail("DivZero"))
    assert any("reaches no goal" in r for r in normalized(_proc(body)))


def test_a_failure_carrying_its_goal_is_accepted():
    body = Seq(Assign("y", Lit(0)),
               If(Op("truth", Var("a")),
                  Fail("DivZero", "safety:div"), Skip()))
    assert normalized(_proc(body)) == []


@pytest.mark.parametrize("goal", [None, "", 7])
def test_a_mark_requires_one_public_goal_identifier(goal):
    reasons = normalized(_proc(Seq(Mark(goal), Assign("y", Lit(0)))))
    assert "mark names no nonempty string goal" in reasons


def test_one_goal_cannot_be_both_coverage_and_safety():
    body = If(
        Op("truth", Var("a")),
        Seq(Mark("g"), Assign("y", Lit(0))),
        Fail("Stopped", "g"),
    )
    reasons = normalized(_proc(body))
    assert any("both mark and fail" in reason for reason in reasons)


def test_a_mark_in_the_other_branch_no_longer_has_to_be_reasoned_about():
    """These three tests used to be about an analysis that asked whether some
    mark appears before a failure, where the obligation is that every path
    reaching it passes one. A mark in the other branch satisfied it and should
    not have. The failure carries its goal now, so the question is gone rather
    than answered: there is no program in which the failure is reached and the
    goal is not."""
    body = If(Op("truth", Var("a")),
              Mark("safety:div"), Fail("DivZero"))
    assert any("reaches no goal" in r for r in normalized(_proc(body)))


def test_both_commands_that_report_a_goal_are_collected():
    from behave.sim.symbolic.ir import goals_of

    body = Seq(Mark("branch:m.py:1:0:0:T"),
               If(Op("truth", Var("a")),
                  Fail("DivZero", "safety:div"), Skip()))
    assert goals_of(body) == {"branch:m.py:1:0:0:T", "safety:div"}


def test_an_unassigned_output_is_rejected():
    reasons = normalized(_proc(Skip(), outputs=("y",)))
    assert any("output y is not assigned" in r for r in reasons)


def test_an_output_assigned_on_only_one_path_is_rejected():
    """What "well-formed procedures denote functions" needs. An output left
    unassigned on some path reads whatever was there, so the same inputs do not
    give the same outputs, and the two readings are each free to fill the hole
    their own way while agreeing with each other."""
    one_arm = If(Op("truth", Var("a")), Assign("y", Lit(1)), Skip())
    assert any("not assigned on every path" in r
               for r in normalized(_proc(one_arm, outputs=("y",))))

    both_arms = If(
        Op("truth", Var("a")), Assign("y", Lit(1)), Assign("y", Lit(2)))
    assert normalized(_proc(both_arms, outputs=("y",))) == []


def test_a_loop_assigns_what_its_bound_says_it_does():
    """Exact, not conservative. A bound is a literal here, since that is what
    makes the step a finite term, so how many times the body runs is known: a
    positive bound runs it and a bound of zero does not.

    Saying less than that would report the analysis rather than the artifact,
    since a loop with a literal bound followed by a read of what it assigned is
    the ordinary way to write a reduction.
    """
    runs = For("i", 4, Assign("y", Lit(1)))
    assert normalized(_proc(runs, outputs=("y",))) == []

    never = For("i", 0, Assign("y", Lit(1)))
    assert any("not assigned on every path" in r
               for r in normalized(_proc(never, outputs=("y",))))


def test_a_loop_has_one_structural_body_independent_of_its_trip_count():
    """Semantic IR size is not the later guarded-encoding size."""
    from behave.sim.symbolic.ir import cost

    body = Seq(Assign("z", Var("z")), Assign("y", Var("z")))
    assert cost(body) == 3                      # the sequence and its two parts
    assert cost(For("i", 0, body)) == 4
    assert cost(For("i", 100, body)) == 4
    assert cost(For("i", Var("n"), body)) == 4
    assert cost(For("i", 2, For("j", 3, body))) == 5
    # A loop that does not run still holds a body the readers carry.
    assert cost(For("i", 0, body)) == 1 + 3


def test_representation_cost_counts_recursive_value_leaves():
    from behave.sim.symbolic import typecheck as value_types
    from behave.sim.symbolic.ir import (
        procedure_representation_cost,
        representation_cost,
    )

    scalar = Assign("x", Lit(1))
    matrix_type = value_types.Array(2, value_types.Array(3))
    matrix = Op("arraylit",
                Op("arraylit", Lit(1), Lit(2), Lit(3)),
                Op("arraylit", Lit(4), Lit(5), Lit(6)))
    assign = Assign("m", matrix)
    store = Store("m", Lit(1), Op("arraylit", Lit(7), Lit(8), Lit(9)))

    assert representation_cost(assign, {"m": matrix_type}) \
        > representation_cost(scalar, {"x": value_types.VALUE})
    assert representation_cost(store, {"m": matrix_type}) >= 6
    assert representation_cost(For("i", 4, assign), {"m": matrix_type}) \
        == representation_cost(For("i", 100, assign), {"m": matrix_type})

    proc = Procedure(("m",), ("a",), (), ("y",), scalar,
                     types={"m": matrix_type, "a": value_types.VALUE,
                            "x": value_types.VALUE})
    # The boundary carries the two array wrappers and six leaves of ``m``,
    # plus the scalar input ``a``.
    assert procedure_representation_cost(proc) \
        == representation_cost(scalar, proc.types) + 10


def test_representation_cost_is_iterative_and_saturates_before_products():
    from behave.sim.symbolic import typecheck as value_types
    from behave.sim.symbolic.ir import (
        cost,
        procedure_representation_cost,
        representation_cost,
    )

    # Deeper than Python's recursion limit in both independent dimensions: a
    # recursive value representation and the expression carrying it.  Measuring
    # either must not turn the census into a recursion-limit test.
    kind = value_types.VALUE
    for _ in range(2_000):
        kind = value_types.Array(1, kind)
    expr = Var("a")
    for _ in range(2_000):
        expr = Op("add", expr, Lit(1))
    body = Assign("y", expr)
    proc = Procedure(("s",), ("a",), (), ("y",), body,
                     types={"s": kind, "a": value_types.VALUE,
                            "y": value_types.VALUE})
    assert procedure_representation_cost(proc, limit=50) == 51

    shared = Var("a")
    dag = Assign("y", Op("add", shared, shared))
    assert representation_cost(dag, {"a": value_types.VALUE,
                                     "y": value_types.VALUE}) == 5

    expression_cycle = Op("add")
    expression_cycle.args = (expression_cycle, Lit(1))
    with pytest.raises(ValueError, match="expression contains itself"):
        representation_cost(Assign("y", expression_cycle), {
            "y": value_types.VALUE})

    command_cycle = Seq(Skip(), Skip())
    command_cycle.first = command_cycle
    with pytest.raises(ValueError, match="command contains itself"):
        representation_cost(command_cycle, {})
    with pytest.raises(ValueError, match="command contains itself"):
        cost(command_cycle)

    type_cycle = value_types.Array(1)
    object.__setattr__(type_cycle, "elem", type_cycle)
    with pytest.raises(ValueError, match="type contains itself"):
        representation_cost(Assign("a", Var("a")), {"a": type_cycle})

    # Saturation is decided by division before multiplication.  The result is
    # the single over-limit sentinel, not an enormous intermediate integer.
    huge = value_types.Array(10 ** 100_000, value_types.Array(10 ** 100_000))
    assert representation_cost(Assign("a", Var("a")), {"a": huge},
                               limit=10) == 11

    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError, match="nonnegative integer"):
            procedure_representation_cost(proc, limit=bad)


def test_representation_structure_has_exact_depth_and_preserves_dags():
    from behave.sim.symbolic import typecheck as value_types
    from behave.sim.symbolic.ir import (
        RepresentationStructureError,
        validate_procedure_representation,
    )

    def nested(depth):
        kind = value_types.VALUE
        for _ in range(depth):
            kind = value_types.Array(1, kind)
        return kind

    at_limit = Procedure(
        ("s",), (), (), (), Skip(), types={"s": nested(4)})
    over_limit = Procedure(
        ("s",), (), (), (), Skip(), types={"s": nested(5)})
    assert validate_procedure_representation(
        at_limit, limit=100, max_depth=5) > 0
    with pytest.raises(RepresentationStructureError) as caught:
        validate_procedure_representation(
            over_limit, limit=100, max_depth=5)
    assert caught.value.code == "representation-depth-exceeded"

    # Sharing is not a back-edge.  The value of the shared expression is read
    # twice and the work metric charges both occurrences, but the structural
    # validator must not mistake that DAG for a cycle.
    shared = Op("add", Lit(1), Lit(2))
    dag = Procedure((), (), (), ("y",),
                    Assign("y", Op("add", shared, shared)),
                    types={"y": value_types.VALUE})
    assert validate_procedure_representation(
        dag, limit=100, max_depth=10) > 0


def test_representation_validation_walks_large_initial_memories_linearly():
    """A concrete array is traversed in place, not recopied per cursor cell."""
    from behave.sim.symbolic import typecheck as value_types
    from behave.sim.symbolic.ir import validate_procedure_representation

    cells = 1 << 16
    proc = Procedure(
        ("mem",), (), (), (), Skip(),
        types={"mem": value_types.Array(cells)},
        initial={"mem": [0] * cells},
    )

    assert validate_procedure_representation(
        proc, limit=2 * cells + 2, max_depth=10) == cells + 2


def test_representation_structure_reports_expression_type_and_value_cycles():
    from behave.sim.symbolic import typecheck as value_types
    from behave.sim.symbolic.ir import (
        RepresentationStructureError,
        validate_procedure_representation,
    )

    expression = Op("add")
    expression.args = (expression, Lit(1))
    expression_proc = Procedure(
        (), (), (), ("y",), Assign("y", expression),
        types={"y": value_types.VALUE})

    type_cycle = value_types.Array(1)
    object.__setattr__(type_cycle, "elem", type_cycle)
    type_proc = Procedure(
        ("s",), (), (), (), Skip(), types={"s": type_cycle})

    value_cycle = []
    value_cycle.append(value_cycle)
    value_proc = Procedure(
        ("s",), (), (), (), Skip(), initial={"s": value_cycle})

    for proc in (expression_proc, type_proc, value_proc):
        with pytest.raises(RepresentationStructureError) as caught:
            validate_procedure_representation(
                proc, limit=100, max_depth=10)
        assert caught.value.code == "representation-cycle"


def test_production_representation_budget_admits_the_boundary_and_refuses_next(
        monkeypatch):
    """The recursive measure is a production gate, with one exact boundary.

    A command-only budget sees both procedures below as one ``skip``.  Their
    state boundary carries an array wrapper and three versus four scalar
    leaves, so the complete costs are exactly five and six.  Five is admitted;
    six is represented by
    the saturating over-limit sentinel and refused by name.
    """
    from behave.sim.behavior import budgets
    from behave.sim.symbolic import typecheck as value_types
    from behave.sim.symbolic.ir import (
        RepresentationStructureError,
        validate_procedure_representation,
    )

    from behave.sim.symbolic import capabilities

    monkeypatch.setattr(capabilities, "TERM_BUDGET", 5)
    at_limit = Procedure(
        ("s",), (), (), (), Skip(),
        types={"s": value_types.Array(3)})
    over_limit = Procedure(
        ("s",), (), (), (), Skip(),
        types={"s": value_types.Array(4)})

    assert validate_procedure_representation(at_limit) == 5
    with pytest.raises(RepresentationStructureError) as caught:
        validate_procedure_representation(over_limit)
    assert caught.value.code == "representation-budget-exceeded"

    assert budgets.as_dict()["TERM_BUDGET"]["measure"] \
        == "finite exact-encoding and recursive representation work"


def test_recursive_representation_budget_does_not_gate_semantic_lowering(
        monkeypatch):
    """A finite semantic step may exist when its exact representation is refused."""
    from behave.sim.behavior import lowering
    from behave.sim.symbolic import capabilities
    from behave.sim.symbolic.ir import (
        RepresentationStructureError,
        validate_procedure_representation,
    )

    source = """\
class M:
    def reset(self):
        self.a = [0] * 64
    def process(self, x):
        return {"y": self.a[x & 63]}
"""
    monkeypatch.setattr(capabilities, "TERM_BUDGET", 80)
    proc = lowering.lower_procedure(source, input_widths=[6])
    with pytest.raises(RepresentationStructureError) as caught:
        validate_procedure_representation(proc)
    assert caught.value.code == "representation-budget-exceeded"


def test_a_step_over_the_budget_is_refused_by_both_lowerings(monkeypatch):
    """Both paths enforce the same structural IR ceiling."""
    from behave.sim.behavior import lowering
    from behave.sim.behavior.lowering import LoweringError, lower_procedure

    monkeypatch.setattr(lowering, "SEMANTIC_IR_NODES", 20)
    updates = "\n".join("        z = z + a" for _ in range(24))
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a):\n'
           '        z = 0\n'
           f'{updates}\n'
           '        return {"y": z}\n')
    from behave.sim.behavior.lower_graph import GraphLowerer
    from behave.sim.behavior.lowering import _Lowerer

    # The term path, which measures the finished term.
    with pytest.raises(LoweringError) as caught:
        lower_procedure(src, lowerer=_Lowerer)
    assert caught.value.code == "semantic-ir-budget-exceeded"

    # The graph path is the default and retains the same structured loops.
    with pytest.raises(LoweringError) as caught:
        lower_procedure(src, lowerer=GraphLowerer)
    assert caught.value.code == "semantic-ir-budget-exceeded"


def test_a_variable_read_before_it_is_assigned_is_reported_separately():
    """Reported by `may_read_unassigned` rather than by `normalized`, because
    the analysis is path-insensitive: it takes a loop to assign nothing and a
    conditional to assign what both arms do, so a read under a guard that is
    always true when the write ran is reported anyway. On the models that
    lower it fires on about a quarter of them, and calling that a
    well-formedness failure would report the analysis's weakness as the
    artifact's."""
    from behave.sim.symbolic.ir import may_read_unassigned

    body = Seq(Assign("t", Var("u")), Assign("y", Var("t")))
    proc = _proc(body, outputs=("y",), locals=("t", "u"))
    assert normalized(proc) == []
    assert any("u is read before it is assigned"
               in r for r in may_read_unassigned(proc))


def test_the_state_and_the_inputs_are_assigned_at_the_start():
    """A run begins with them in hand, so reading one is not reading something
    unassigned."""
    body = Assign("y", Op("add", Var("a"), Var("s")))
    assert normalized(_proc(body, outputs=("y",), inputs=("a",),
                            state=("s",))) == []


def test_a_store_reads_the_array_before_it_replaces_one_cell():
    from behave.sim.symbolic.ir import may_read_unassigned

    body = Store("y", Lit(0), Lit(7))
    assert any("read by a store before it is assigned" in reason
               for reason in may_read_unassigned(_proc(body)))

    initialized = Seq(
        Assign("t", Op("arraylit", Lit(0), Lit(0))),
        Seq(Store("t", Lit(0), Lit(7)), Assign("y", Lit(0))))
    assert may_read_unassigned(_proc(initialized, locals=("t",))) == []


def test_recursive_array_types_are_immutable_and_pickle_by_value():
    import pickle

    from behave.sim.symbolic import typecheck as value_types

    kind = value_types.Array(2, value_types.Array(3))
    with pytest.raises(AttributeError):
        kind.cells = 9
    assert pickle.loads(pickle.dumps(kind)) == kind


def test_a_negative_loop_trip_count_executes_no_iterations():
    from behave.sim.symbolic.interp import run_step

    body = Seq(Assign("y", Lit(1)), For("j", -1, Assign("y", Lit(2))))
    proc = _proc(body)
    assert normalized(proc) == []
    _state, output, _hits, error = run_step(proc, {"a": 0})
    assert not error
    assert output.transactions == [{"y": 1}]


def test_assignment_to_an_undeclared_variable_is_rejected():
    body = Assign("z", Lit(1))
    reasons = normalized(_proc(body))
    assert any("undeclared z" in r for r in reasons)


@pytest.mark.parametrize(
    "write, message",
    [
        (Assign("a", Lit(1)), "input a is assigned"),
        (Store("a", Lit(0), Lit(1)), "input a is stored into"),
    ],
)
def test_ir_inputs_are_read_only(write, message):
    body = Seq(write, Assign("y", Lit(0)))
    assert message in normalized(_proc(body))


def test_a_loop_body_is_traversed():
    body = Seq(Assign("y", Lit(0)), For("j", 4, Mark("g")))
    assert any(isinstance(c, Mark) for c in walk(body))
    assert normalized(_proc(body)) == []


def test_expressions_carry_operations_and_arrays():
    e = Op("add", Var("a"), Lit(1))
    assert repr(e) == "add(a, 1)"
