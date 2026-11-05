"""Source sequence semantics at the Python-to-SolverIR boundary.

These are semantic comparisons, not source-shape pins.  Each accepted model is
run natively and through the IR interpreter, including empty paths and batches
whose length is decided at run time.  A representation that reserves four cells
but reports four transactions on an execution that appended two fails here.
"""

from __future__ import annotations

import ast
import copy
import pickle
import sys
import textwrap

import pytest

from behave.sim.behavior import audit, budgets, step_alias
from behave.sim.symbolic import interp, ranges
from behave.sim.symbolic import typecheck as value_types
from behave.sim.behavior.extract import (
    ScopeResolver,
    _may_break,
    _may_continue,
    source_model,
)
from behave.sim.behavior.lowering import (
    LoweringError,
    _expression_reads_binding,
    _Lowerer,
    lower_with_state_ranges,
)
from behave.sim.behavior.source_batches import BatchContract, BatchForm
from behave.sim.behavior.source_sequences import (
    SequencePlan,
    SequencePlanError,
    _BatchFact,
    _bound_of,
    _ContextKey,
    _ContractFlow,
    _FunctionContracts,
    _RecordFact,
    _ScalarBindings,
    _scan_scope,
    _SourceQueryCache,
    plan_sequences,
    static_iterable,
    static_value,
)
from behave.sim.behavior.source_values import (
    NONE,
    SCALAR,
    UNKNOWN,
    SourceValueBudget,
    ValueContract,
    contract_of_value,
)
from behave.sim.symbolic.state_projection import (
    DynamicSequenceBinding,
    StateProjectionError,
)


def test_activation_context_identity_is_structural_and_pickle_stable():
    first = _ContextKey((7, (SCALAR,), ("nested", (SCALAR,))))
    same = _ContextKey((7, (SCALAR,), ("nested", (SCALAR,))))
    restored = pickle.loads(pickle.dumps(first))

    assert first == same == restored
    assert hash(first) == hash(same) == hash(restored)
    assert {first: "context"}[same] == "context"
    assert first[1:] == ((SCALAR,), ("nested", (SCALAR,)))


def _source(body, helpers=""):
    body = textwrap.indent(textwrap.dedent(body).strip(), " " * 8)
    helpers = textwrap.indent(textwrap.dedent(helpers).strip(), " " * 4)
    middle = f"{helpers}\n" if helpers.strip() else ""
    return ("class M:\n"
            "    def reset(self):\n"
            "        pass\n"
            f"{middle}"
            "    def process(self, a):\n"
            f"{body}\n")


def _helper_chain(count, *, array_state=False):
    """One public step followed by exactly ``count`` helper activations."""
    if type(count) is not int or count < 1:
        raise ValueError("a helper chain must be nonempty")
    methods = []
    for index in range(count):
        if index + 1 < count:
            body = f"return self.h{index + 1}(a)"
        elif array_state:
            body = "self.buf[0] = a\n        return {'y': self.buf[0]}"
        else:
            body = "return {'y': a}"
        methods.append(
            f"    def h{index}(self, a):\n        {body}\n")
    initial = ("    def __init__(self):\n        self.buf = [0]\n"
               if array_state else "")
    reset = ("    def reset(self):\n        self.buf = [0]\n"
             if array_state else "    def reset(self):\n        pass\n")
    return ("class M:\n" + initial + reset + "".join(methods)
            + "    def process(self, a):\n        return self.h0(a)\n")


def _native(source, a):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    try:
        value = model.process(a)
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__
    if value is None:
        return [], ""
    if isinstance(value, dict):
        return [dict(value)], ""
    return [dict(row) for row in value], ""


def _lowered(source, a, outputs=("y",)):
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=list(outputs))
    _state, out, _hits, error = interp.run_step(proc, {"a": a})
    return (None if error else [dict(row) for row in out.transactions],
            error)


def _assert_agrees(source, values, outputs=("y",)):
    for value in values:
        assert _lowered(source, value, outputs) == _native(source, value)


@pytest.mark.parametrize("kind", ["for", "while"])
@pytest.mark.parametrize("active_context", [False, True])
def test_loop_dispatch_records_context_before_transferring_control(
        monkeypatch, kind, active_context):
    source = _source("for i in range(a):\n    pass" if kind == "for"
                     else "while a:\n    break")
    model = source_model(source)
    planner = _FunctionContracts(model, ScopeResolver(model))
    context = object()
    if active_context:
        planner._context.append(context)
    statement = model.step.body[0]
    env, bindings = {"a": SCALAR}, _ScalarBindings()
    result = object()
    calls = []

    def transferred(fn, stmt, actual_env, actual_bindings):
        assert fn is model.step and stmt is statement
        assert actual_env is env and actual_bindings is bindings
        assert planner._statement_contexts == (
            {(context, id(statement))} if active_context else set())
        calls.append(stmt)
        return result

    monkeypatch.setattr(planner, f"_{kind}_statement", transferred)
    assert planner._stmt(model.step, statement, env, bindings) is result
    assert calls == [statement]


@pytest.mark.parametrize("kind", ["for", "while"])
@pytest.mark.parametrize("graph", [False, True], ids=["term", "graph"])
def test_loop_planning_keeps_exhaustion_break_continue_and_return(kind, graph):
    from behave.sim.behavior.lower_graph import GraphLowerer

    loop = ("for i in range(a & 3):\n    value = i + 1\n" if kind == "for" else
            "i = 0\nwhile i < (a & 3):\n    value = i + 1\n    i += 1\n")
    source = _source("values = []\n" + loop + '''\
    if value == 2 and (a & 4):
        break
    if value == 1 and (a & 8):
        continue
    values.append(value)
    if value == 2 and (a & 12) == 0:
        return {"y": 99}
else:
    values.append(7)
return {"y": sum(values)}
''')
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"],
        lowerer=GraphLowerer if graph else _Lowerer)
    for value in range(16):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        observed = (None if error else [dict(row) for row in output.transactions], error)
        assert observed == _native(source, value)


@pytest.mark.parametrize("kind", ["for", "while"])
@pytest.mark.parametrize("escape", ["break", "continue"])
@pytest.mark.parametrize("body,own_else,expected", [
    ("if a:\n    {escape}", "pass", True),
    ("for i in range(2):\n    {escape}", "pass", False),
    ("for i in range(2):\n    pass\nelse:\n    {escape}", "pass", True),
    ("pass", "{escape}", False),
    ("for i in range(2):\n    for j in range(2):\n        pass\n    else:\n        {escape}",
     "pass", False),
    ("for i in range(2):\n    pass\nelse:\n    for j in range(2):\n        pass\n    else:\n        {escape}",
     "pass", True),
    ("def helper():\n    for i in range(2):\n        {escape}", "pass", False),
])
def test_loop_exit_scan_follows_lexical_ownership(kind, escape, body, own_else, expected):
    header = "for outer in range(3):\n" if kind == "for" else "while a:\n"
    source = (header + textwrap.indent(body.format(escape=escape), "    ")
              + "\nelse:\n" + textwrap.indent(own_else.format(escape=escape), "    "))
    node = ast.parse(source).body[0]
    assert _may_break(node) == (expected and escape == "break")
    assert _may_continue(node) == (expected and escape == "continue")


@pytest.mark.parametrize("has_else", [False, True])
@pytest.mark.parametrize("has_normal", [False, True])
@pytest.mark.parametrize("has_break", [False, True])
def test_loop_exit_keeps_state_ownership_and_escaped_control(
        monkeypatch, has_else, has_normal, has_break):
    model = source_model(_source("for i in range(a):\n    pass\nelse:\n    pass"))
    planner = _FunctionContracts(model, ScopeResolver(model))
    stmt = model.step.body[0]
    if not has_else:
        stmt.orelse = []
    states = [({"path": name}, _ScalarBindings())
              for name in ("exhausted", "broken", "else", "break", "continue")]
    normal = [states[0]] if has_normal else []
    broken = [states[1]] if has_break else []
    first_return, else_return = object(), object()
    returned = [first_return]
    otherwise = _ContractFlow([states[2]], [else_return], [states[3]], [states[4]])
    called = []

    def run_else(fn, statements, incoming):
        assert fn is model.step and statements is stmt.orelse
        assert incoming is normal
        called.append(incoming)
        return otherwise

    monkeypatch.setattr(planner, "_block", run_else)
    flow = planner._loop_exit(model.step, stmt, normal, returned, broken)
    ran_else = has_else and has_normal
    expected_normal = broken + (otherwise.normal if ran_else else normal)
    assert len(called) == int(ran_else)
    assert len(flow.normal) == len(expected_normal)
    assert all(actual is expected for actual, expected in zip(
        flow.normal, expected_normal, strict=True))
    assert flow.returned == [first_return] + ([else_return] if ran_else else [])
    assert flow.returned == returned
    assert flow.breaks == ([states[3]] if ran_else else [])
    assert flow.continues == ([states[4]] if ran_else else [])

    # Flow containers are independent, but their state payloads are not copied.
    for source in (normal, broken, returned, otherwise.normal, otherwise.returned,
                   otherwise.breaks, otherwise.continues):
        source.clear()
    assert len(flow.normal) == len(expected_normal)
    assert all(actual is expected for actual, expected in zip(
        flow.normal, expected_normal, strict=True))
    assert flow.returned == [first_return] + ([else_return] if ran_else else [])
    assert flow.breaks == ([states[3]] if ran_else else [])
    assert flow.continues == ([states[4]] if ran_else else [])


def test_loop_exit_propagates_else_refusal_without_publishing_partial_flow(monkeypatch):
    model = source_model(_source("for i in range(a):\n    pass\nelse:\n    pass"))
    planner = _FunctionContracts(model, ScopeResolver(model))
    stmt = model.step.body[0]
    state = ({"a": SCALAR}, _ScalarBindings())
    returned = [SCALAR]
    failure = SequencePlanError(stmt.orelse[0], "else refused", "source-plan-inconsistent")

    def refuse(fn, statements, incoming):
        assert fn is model.step and statements is stmt.orelse
        assert incoming == [state]
        raise failure

    monkeypatch.setattr(planner, "_block", refuse)
    with pytest.raises(SequencePlanError) as captured:
        planner._loop_exit(model.step, stmt, [state], returned)
    assert captured.value is failure
    assert returned == [SCALAR]


@pytest.mark.parametrize("kind", ["for", "while"])
@pytest.mark.parametrize("outer_kind", ["for", "while"])
@pytest.mark.parametrize("escape", ["break", "continue"])
@pytest.mark.parametrize("graph", [False, True], ids=["term", "graph"])
@pytest.mark.solver
def test_nested_loop_else_control_targets_the_enclosing_loop(
        kind, outer_kind, escape, graph):
    from behave.sim.symbolic import encoder, plan
    from behave.sim.behavior.lower_graph import GraphLowerer

    z3 = pytest.importorskip("z3")
    loop = ("for i in range(a & 3):\n    value = i + 1\n" if kind == "for" else
            "i = 0\nwhile i < (a & 3):\n    value = i + 1\n    i += 1\n")
    inner = loop + f'''\
    if value == 2 and (a & 4):
        break
    values.append(value)
else:
    values.append(7)
    if a & 8:
        {escape}
values.append(5)
'''
    outer = ("for outer in range(3):\n" if outer_kind == "for" else
             "outer = 0\nwhile outer < 3:\n    outer += 1\n")
    source = _source("values = []\n" + outer
                     + textwrap.indent(inner, "    ")
                     + 'else:\n    values.append(9)\nreturn {"y": sum(values)}')
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"],
        lowerer=GraphLowerer if graph else _Lowerer)
    env = {**proc.state_ranges, "a": ranges.Range(0, 15)}
    planned = plan.plan_encoding(proc, env)
    assert planned.complete, planned.unresolved
    encoded = encoder.encode_step(proc, env=env, plan=planned)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for value in range(16):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        observed = (None if error else [dict(row) for row in output.transactions], error)
        expected = _native(source, value)
        assert observed == expected
        solver.push()
        solver.add(encoded.pre["a"] == value)
        assert solver.check() == z3.sat
        solver.add(z3.Or(z3.Not(encoded.ok), encoded.post["y"][0] != expected[0][0]["y"]))
        assert solver.check() == z3.unsat
        solver.pop()


def test_bounded_state_contract_closes_a_rolling_sequence():
    source = """class M:
    def __init__(self):
        self.values = []

    def reset(self):
        self.values = []

    def process(self, a):
        old_values = self.values
        values = old_values + [a]
        if len(values) > 4:
            values = values[1:]
        self.values = values
        return {"y": len(values)}
"""
    model = source_model(source)
    plan = plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"values": contract_of_value([])},
        input_contracts={"a": SCALAR}, state_bound=8)

    values = plan.state_contracts["values"]
    assert (values.minimum, values.maximum) == (0, 4)
    assert values.element == SCALAR

    # At the first step ``old_values`` is logically empty, but it must retain
    # the bounded state's backing capacity so the append is representable.
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=1)
    _state, output, _hits, error = interp.run_step(proc, {"a": 3})
    assert not error
    assert output.transactions == [{"y": 1}]


def test_state_closure_reuses_only_identical_static_source_queries():
    source = _source("""
        return {"y": (a + 1) * 2}
    """)
    model = source_model(source)
    resolver = ScopeResolver(model)
    expression = model.step.body[0].value.values[0]
    budget = SourceValueBudget(32)
    cache = _SourceQueryCache()
    first = _FunctionContracts(
        model, resolver, budget=budget, query_cache=cache)
    second = _FunctionContracts(
        model, resolver, budget=budget, query_cache=cache)

    assert first._static_scalar(model.step, expression, {"a": 3}) == 8
    after_first = budget.left
    assert second._static_scalar(model.step, expression, {"a": 3}) == 8
    assert budget.left == after_first

    # A later local outside the expression cannot change this proof.
    assert second._static_scalar(
        model.step, expression, {"a": 3, "unrelated": 99}) == 8
    assert budget.left == after_first

    assert second._static_scalar(model.step, expression, {"a": 4}) == 10
    assert budget.left < after_first


def test_structural_query_projects_only_the_expression_dependencies():
    source = _source("""
        return {"y": (a + 1) * 2}
    """)
    model = source_model(source)
    contracts = _FunctionContracts(model, ScopeResolver(model))
    expression = model.step.body[0].value.values[0]
    bindings = _ScalarBindings(
        {"a": 3, "unrelated": 99},
        scalar_bounds={
            "a": ranges.Bounds(3, 3),
            "unrelated": ranges.Bounds(-100, 100),
        },
    )

    projected = contracts._structural_bindings(bindings, expression)

    assert dict(projected) == {"a": 3}
    assert projected.bounds == {"a": ranges.Bounds(3, 3)}


def test_contract_cache_ignores_unread_abstract_locals():
    source = _source("""
        return {"y": (a + 1) * 2}
    """)
    model = source_model(source)
    budget = SourceValueBudget(64)
    query_cache = _SourceQueryCache()
    contracts = _FunctionContracts(
        model, ScopeResolver(model), budget=budget, query_cache=query_cache)
    contracts._context.append(("test",))
    expression = model.step.body[0].value.values[0]

    first = contracts._expr(
        model.step, expression,
        {"a": SCALAR, "unrelated": NONE},
        _ScalarBindings({"a": 3, "unrelated": 1}))
    after_first = budget.left
    second = contracts._expr(
        model.step, expression,
        {"a": SCALAR, "unrelated": SCALAR},
        _ScalarBindings({"a": 3, "unrelated": 99}))

    assert first is SCALAR
    assert second is SCALAR
    assert budget.left == after_first

    next_layer = _FunctionContracts(
        model, contracts.resolver, budget=budget, query_cache=query_cache)
    next_context = ("another-state-layer",)
    next_layer._context.append(next_context)
    assert next_layer._expr(
        model.step, expression,
        {"a": SCALAR, "unrelated": NONE},
        _ScalarBindings({"a": 3, "unrelated": -1})) is SCALAR
    assert budget.left == after_first
    # Reusing the root result must also reproduce the occurrence facts that
    # executable lowering asks for below that root.  Otherwise a later state
    # layer sees the same expression contract but UNKNOWN for its operands.
    for child in ast.walk(expression):
        if isinstance(child, ast.expr):
            assert next_layer.expression_contract(child, next_context) is SCALAR
            assert "unrelated" not in next_layer.fixed_bindings(
                child, next_context)
            assert "unrelated" not in next_layer.numeric_bounds(
                child, next_context)

    contracts._expr(
        model.step, expression,
        {"a": SCALAR, "unrelated": SCALAR},
        _ScalarBindings({"a": 4, "unrelated": 99}))
    assert budget.left < after_first


def test_contract_cache_reuses_array_selection_but_not_record_reads():
    source = _source("""
        return {"y": a}
    """)
    model = source_model(source)
    budget = SourceValueBudget(64)
    cache = _SourceQueryCache()
    contracts = _FunctionContracts(
        model, ScopeResolver(model), budget=budget, query_cache=cache)
    contracts._context.append(("array-selection",))
    expression = ast.parse("values[index] + 1", mode="eval").body
    array_env = {
        "values": contract_of_value([1, 2]),
        "index": SCALAR,
        "unrelated": NONE,
    }

    assert contracts._expr(
        model.step, expression, array_env,
        _ScalarBindings({"index": 0, "unrelated": 0})) is SCALAR
    after_first = budget.left
    assert contracts._expr(
        model.step, expression,
        {**array_env, "unrelated": SCALAR},
        _ScalarBindings({"index": 0, "unrelated": 99})) is SCALAR
    assert budget.left == after_first

    # Reading a fixed field is the point at which a live record builder's
    # schema is frozen.  Such a query remains path-sensitive and is never
    # hidden behind the pure-expression cache.
    record_read = ast.parse("record['y']", mode="eval").body
    record = _RecordFact((("y", SCALAR),), frozenset({"y"}))
    assert contracts._contract_query_key(
        model.step, record_read, {"record": record},
        _ScalarBindings(), None) is None


def test_pure_arguments_reuse_contracts_inside_uncached_calls(monkeypatch):
    model = source_model(_source('return {"y": int(not (-10 <= a <= 10))}'))
    expression = model.step.body[0].value.values[0]
    predicate = expression.args[0]
    budget = SourceValueBudget(200)
    cache = _SourceQueryCache()
    visits = []
    original = SourceValueBudget.spend

    def spend(self, node, amount=1):
        if sys._getframe(1).f_code.co_name == "contract_of_expression":
            visits.append(node)
        return original(self, node, amount)

    monkeypatch.setattr(SourceValueBudget, "spend", spend)
    for context in (("first",), ("next-state-layer",)):
        contracts = _FunctionContracts(model, ScopeResolver(model),
                                       budget=budget, query_cache=cache)
        contracts._context.append(context)
        assert contracts._expr(model.step, expression, {"a": SCALAR},
                               _ScalarBindings()) is SCALAR
        for node in ast.walk(predicate):
            if isinstance(node, ast.expr):
                assert contracts.expression_contract(node, context) is SCALAR
    # The call remains uncached; its pure argument is evaluated only once.
    assert visits.count(expression) == 2
    assert visits.count(predicate) == 1
    contracts._expr(model.step, expression, {"a": SCALAR},
                    _ScalarBindings({"a": 5}))
    assert visits.count(predicate) == 2  # A changed source binding is another query.


def test_cached_pure_arguments_do_not_suppress_repeated_helper_effects():
    source = '''class M:
    def reset(self):
        self.n = 0
    def take(self, x):
        self.n += 1
        return x + self.n
    def process(self, a):
        total = 0
        for i in range(3):
            total += self.take(int(not (2 <= a <= 5)))
        return {"y": total}
'''
    _assert_agrees(source, (0, 3, 7))


def test_scalar_refinement_cache_preserves_unrelated_path_ranges():
    source = _source("""
        return {"y": a}
    """)
    model = source_model(source)
    budget = SourceValueBudget(64)
    contracts = _FunctionContracts(
        model, ScopeResolver(model), budget=budget)
    predicate = ast.parse("bit == 1", mode="eval").body

    first = contracts._refine_scalar_condition(
        model.step, predicate,
        _ScalarBindings(scalar_bounds={
            "bit": ranges.Bounds(0, 1), "q": ranges.Bounds(0, 7)}),
        True)
    after_first = budget.left
    second = contracts._refine_scalar_condition(
        model.step, predicate,
        _ScalarBindings(
            scalar_bounds={
                "bit": ranges.Bounds(0, 1),
                "q": ranges.Bounds(100, 200)}),
        True)

    assert first.bounds["bit"] == ranges.Bounds(1, 1)
    assert first.bounds["q"] == ranges.Bounds(0, 7)
    assert second.bounds["bit"] == ranges.Bounds(1, 1)
    assert second.bounds["q"] == ranges.Bounds(100, 200)
    assert budget.left == after_first


def test_augmented_assignment_has_one_planning_occurrence_across_layers():
    source = _source("""
        q = a
        q += 1
        return {"y": q}
    """)
    model = source_model(source)
    statement = model.step.body[1]
    cache = _SourceQueryCache()
    first = _FunctionContracts(
        model, ScopeResolver(model), query_cache=cache)
    second = _FunctionContracts(
        model, first.resolver, query_cache=cache)

    expression = first._augmented_expression(statement)
    assert expression is second._augmented_expression(statement)
    assert isinstance(expression.left, ast.Name)
    assert isinstance(expression.left.ctx, ast.Load)


def test_numeric_query_cache_ignores_unread_ranges_across_layers():
    source = _source("""
        q = a
        q += 1
        return {"y": q}
    """)
    model = source_model(source)
    statement = model.step.body[1]
    budget = SourceValueBudget(64)
    cache = _SourceQueryCache()
    first = _FunctionContracts(
        model, ScopeResolver(model), budget=budget, query_cache=cache)
    second = _FunctionContracts(
        model, first.resolver, budget=budget, query_cache=cache)
    expression = first._augmented_expression(statement)

    first_bounds = first._bounds_of(
        model.step, expression,
        _ScalarBindings(scalar_bounds={
            "q": ranges.Bounds(0, 7),
            "unrelated": ranges.Bounds(10, 20),
        }))
    after_first = budget.left
    second_bounds = second._bounds_of(
        model.step, expression,
        _ScalarBindings(scalar_bounds={
            "q": ranges.Bounds(0, 7),
            "unrelated": ranges.Bounds(-200, 500),
        }))

    assert first_bounds == ranges.Bounds(1, 8)
    assert second_bounds == first_bounds
    assert budget.left == after_first

    assert second._bounds_of(
        model.step, expression,
        _ScalarBindings(scalar_bounds={
            "q": ranges.Bounds(2, 9),
            "unrelated": ranges.Bounds(-200, 500),
        })) == ranges.Bounds(3, 10)
    # Numeric bounds change, but all static subexpressions still have the
    # same answers; the shared static evaluator need not revisit them.
    assert budget.left == after_first


def test_bounded_state_contract_closes_nested_indexed_updates():
    source = """class M:
    def __init__(self):
        self.rows = [[], []]

    def reset(self):
        self.rows = [[], []]

    def process(self, a):
        i = a & 1
        self.rows[i] = self.rows[i] + [a]
        return {"y": len(self.rows[i])}
"""
    model = source_model(source)
    plan = plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"rows": contract_of_value([[], []])},
        input_contracts={"a": SCALAR}, state_bound=3)

    rows = plan.state_contracts["rows"]
    assert (rows.minimum, rows.maximum) == (2, 2)
    assert rows.element is not None
    assert (rows.element.minimum, rows.element.maximum) == (0, 3)
    assert rows.element.element == SCALAR


def test_selected_dynamic_row_keeps_its_backing_store_when_bound_locally():
    source = """class M:
    def reset(self):
        self.rows = [[], []]

    def process(self, a):
        index = a & 1
        old = self.rows[index]
        self.rows[index] = old + [a]
        return {"y": len(self.rows[index])}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=3)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (0, 1, 2):
        state, output, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert output.transactions == [native.process(value)]


def test_an_empty_comprehension_contributes_no_phantom_element_contract():
    source = """class M:
    def reset(self):
        self.data = [0, 0, 0, 0]
        self.count = 0

    def process(self, a):
        values = [self.data[i] for i in range(self.count)]
        values.append(a)
        self.count = len(values)
        self.data[0] = values[0]
        return {"y": len(values) * 16 + values[0]}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=1)
    state, output, _hits, error = interp.run_step(proc, {"a": 7})

    assert not error
    assert output.transactions == [{"y": 23}]
    assert state["count"] == 1


def test_nested_state_leaf_ranges_bound_later_indexed_loops():
    source = """class M:
    def reset(self):
        self.widths = [[0, 0], [0, 0]]
        self.position = 0

    def process(self, a):
        row = (a >> 1) & 1
        column = a & 1
        self.widths[row][column] = self.position
        self.position = (self.position + 1) & 7
        total = 0
        for i in range(self.widths[row][column]):
            total += i
        return {"y": total}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=8)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (0, 1, 2, 3, 0, 1, 2, 3):
        state, output, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert output.transactions == [native.process(value)]


def test_bounded_state_contract_carries_helper_state_effects():
    source = """class M:
    def __init__(self):
        self.values = []

    def reset(self):
        self.values = []

    def push(self, a):
        self.values = self.values + [a]

    def process(self, a):
        self.push(a)
        return {"y": len(self.values)}
"""
    model = source_model(source)
    plan = plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"values": contract_of_value([])},
        input_contracts={"a": SCALAR}, state_bound=3)

    values = plan.state_contracts["values"]
    assert (values.minimum, values.maximum) == (0, 3)
    assert values.element == SCALAR


def test_bounded_state_contract_carries_effects_from_return_expression():
    source = """class M:
    def __init__(self):
        self.values = []

    def reset(self):
        self.values = []

    def push(self, a):
        self.values = self.values + [a]
        return {"y": len(self.values)}

    def process(self, a):
        return self.push(a)
"""
    model = source_model(source)
    plan = plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"values": contract_of_value([])},
        input_contracts={"a": SCALAR}, state_bound=3)

    values = plan.state_contracts["values"]
    assert (values.minimum, values.maximum) == (0, 3)
    assert values.element == SCALAR


def test_state_closure_keeps_loop_local_used_by_a_later_state_write():
    source = """class M:
    def reset(self):
        self.count = 0

    def process(self, a):
        count = 0
        for i in range((a & 31) + 1):
            if i == 5:
                break
            count += 1
        self.count = count
        return {"y": self.count}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[5], outputs=["y"], bound=4)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()

    state = None
    for value in (0, 1, 4, 5, 7, 31):
        state, output, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert output.transactions == [native.process(value)]


def test_bounded_state_append_and_pop_close_a_pipeline_queue():
    source = """class M:
    def __init__(self):
        self.q = []

    def reset(self):
        self.q = []

    def process(self, a):
        result = None
        if len(self.q) >= 2:
            x, y = self.q.pop(0)
            result = {"y": x * y}
        self.q.append((a, a + 1))
        return result
"""
    model = source_model(source)
    plan = plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"q": contract_of_value([])},
        input_contracts={"a": SCALAR}, state_bound=6)

    queue = plan.state_contracts["q"]
    assert (queue.minimum, queue.maximum) == (0, 2)
    assert queue.element is not None
    assert queue.element.fixed_shape == (2,)

    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=6)
    _state, outputs, _hits, error = interp.run_trace(
        proc, [{"a": value} for value in (1, 2, 3, 4)])
    assert not error
    assert [out.transactions for out in outputs] == [
        [], [], [{"y": 2}], [{"y": 6}],
    ]

    # At depth one the read occurrence is provably empty, but its state
    # encoding must still use the tuple schema established by the append.
    shallow = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=1)
    _state, output, _hits, error = interp.run_step(shallow, {"a": 3})
    assert not error
    assert output.transactions == []


def _transient_frame_source(limit, helper):
    append = "self.put(a)" if helper else "self.q.append((a, a + 1))"
    return f'''class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.q = []
    def put(self, a):
        self.q.append((a, a + 1))
    def process(self, a):
        {append}
        if len(self.q) < {limit}:
            return None
        total = 0
        for x, y in self.q:
            total += x + y
        self.q = []
        return {{'y': total}}
'''


@pytest.mark.parametrize("limit", (1, 3, 5))
@pytest.mark.parametrize("helper", (False, True))
def test_transient_state_capacity_is_not_a_boundary_state(limit, helper):
    source = _transient_frame_source(limit, helper)
    model = source_model(source)
    planned = plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"q": contract_of_value([])},
        input_contracts={"a": SCALAR}, state_bound=limit * 2)
    assert planned.state_contracts["q"].maximum == limit - 1
    assert planned.state_storage_contracts["q"].maximum == limit
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=limit * 2)
    namespace = {}
    exec(source, namespace)
    native = namespace["M"]()
    values = tuple(range(limit * 2))
    state, outputs, _hits, error = interp.run_trace(
        proc, [{"a": value} for value in values])
    expected = []
    for value in values:
        result = native.process(value)
        expected.append([] if result is None else [result])
    assert not error
    assert [out.transactions for out in outputs] == expected
    assert proc.contract.projection("q").reconstruct(state) == native.q
    assert audit.model_audit(proc, namespace["M"], [
        [{"a": value} for value in values]]) == []


@pytest.mark.solver
def test_transient_state_goals_have_exact_queries_and_native_witnesses():
    pytest.importorskip("z3")
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.instance import lower
    from behave.sim.behavior.source_plan import plan_source
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
    from behave.sim.engines.local_ir import local_hit_any
    from behave.sim.engines.probing import Outcome

    source = _transient_frame_source(3, True)
    schema = TransactionSchema(
        [Endpoint("a", "input", 4), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=6)
    planned = plan_source(source_model(source), source)
    assert exact.frontend_catalogue_manifest() == planned.manifest
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=planned)
    model = namespace["M"]()
    state = dict(exact.proc.initial)
    trace = [{"a": a} for a in range(6)]
    for inputs in trace:
        for goal in planned.manifest:
            answer = local_hit_any(exact.proc, exact.env, state, (goal,),
                timeout_ms=60_000)
            assert answer.outcome in (Outcome.SAT, Outcome.LOCAL_UNSAT), (goal, answer.detail)
            if answer.outcome is Outcome.SAT:
                witness = copy.deepcopy(model)
                native_hits.clear()
                witness.process(**answer.candidate)
                assert goal in native_hits
        native_hits.clear()
        expected = model.process(**inputs)
        state, output, hits, error = interp.run_step(exact.proc, inputs, state)
        assert not error
        assert output.transactions == ([] if expected is None else [expected])
        assert hits == set(native_hits)
        assert exact.proc.contract.projection("q").reconstruct(state) == model.q
    assert audit.step_audit(exact.proc, exact.env, [trace], workers=1) == []


def test_bounded_lowering_omits_an_output_arm_before_it_is_reachable():
    source = """class M:
    def reset(self):
        self.phase = 0

    def process(self, a):
        result = None
        if self.phase == 1:
            result = {"y": a}
        self.phase = 1
        return result
"""
    shallow = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=1)
    state, output, _hits, error = interp.run_step(shallow, {"a": 9})

    assert not error
    assert state["phase"] == 1
    assert output.transactions == []


def test_graph_lowering_uses_correlated_source_reachability():
    source = """class M:
    def reset(self):
        self.active = 0
        self.index = 0
        self.hidden = 0

    def process(self, start, a):
        old_active = self.active
        if start:
            present = 1
            current = 0
        elif old_active:
            present = 1
            current = self.index
        else:
            present = 0
            current = 0
        result = None
        if present and current == 1:
            result = {"y": a}
            self.hidden = a
        self.active = present
        self.index = current + 1
        return result
"""
    shallow = lower_with_state_ranges(
        source, input_widths=[1, 4], outputs=["y"], bound=1)
    state, output, _hits, error = interp.run_step(
        shallow, {"start": 1, "a": 9})

    assert not error
    assert state["active"] == 1
    assert state["index"] == 1
    assert state["hidden"] == 0
    assert output.transactions == []


def test_local_pop_uses_logical_length_and_returns_the_removed_value():
    source = _source("""
        values = [10, 20, 30]
        selected = values.pop(a & 1)
        return {"y": selected + len(values)}
    """)
    _assert_agrees(source, range(4))


def test_pop_out_of_range_is_an_explicit_source_failure():
    source = _source("""
        values = [7]
        return {"y": values.pop(a)}
    """)
    _assert_agrees(source, range(4))


def test_one_path_join_preserves_independent_batch_evidence():
    row = ValueContract.record({"y": UNKNOWN})
    mapping = BatchContract.one(row)
    fact = _BatchFact(UNKNOWN, mapping)

    preserved = _FunctionContracts._merge_envs({"result": fact})["result"]
    assert preserved == fact

    joined = _FunctionContracts._merge_envs(
        {"result": NONE}, {"result": preserved})["result"]
    assert isinstance(joined, _BatchFact)
    assert joined.batch.forms == frozenset({
        BatchForm.NONE, BatchForm.MAPPING})


def test_while_built_batch_capacity_comes_from_the_task_input_domain():
    source = _source("""
        rows = []
        i = 0
        while i < a:
            rows.append({"y": i})
            i += 1
        return rows
    """)
    wide = lower_with_state_ranges(
        source, input_widths=[8], outputs=["y"])
    assert wide.output_capacity == 255
    _state, output, _hits, error = interp.run_step(wide, {"a": 255})
    assert not error
    assert len(output.transactions) == 255
    assert output.transactions[-1] == {"y": 254}

    narrow = lower_with_state_ranges(
        source, input_widths=[8], input_bounds={"a": (0, 3)},
        outputs=["y"])
    assert narrow.output_capacity == 3
    _state, output, _hits, error = interp.run_step(narrow, {"a": 3})
    assert not error
    assert output.transactions == [{"y": 0}, {"y": 1}, {"y": 2}]


def test_helper_while_capacity_tracks_derived_argument_ranges():
    source = _source("""
        limit = (a & 7) + 1
        values = self.rows(limit)
        return {"y": len(values)}
    """, helpers="""
        def rows(self, limit):
            result = []
            i = 0
            while i < limit:
                result.append(i)
                i += 1
            return result
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[8], outputs=["y"])
    _state, output, _hits, error = interp.run_step(proc, {"a": 255})
    assert not error
    assert output.transactions == [{"y": 8}]


def test_an_unranged_helper_return_invalidates_the_call_range():
    source = _source("""
        limit = self.maybe(a)
        i = 0
        while i < limit:
            i += 1
        return {"y": i}
    """, helpers="""
        def maybe(self, value):
            if value:
                return value & 7
    """)
    model = source_model(source)
    with pytest.raises(SequencePlanError) as caught:
        plan_sequences(
            model, ScopeResolver(model), input_ranges={
                "a": ranges.Range(0, 15),
            })
    assert caught.value.code == "while-trip-range-unproved"


def test_scalar_while_planning_is_structural_not_proportional_to_its_span():
    source = _source("""
        i = 0
        while i < a:
            i += 1
        return {"y": i}
    """)
    model = source_model(source)
    sequence_plan = plan_sequences(
        model, ScopeResolver(model), input_ranges={
            "a": ranges.Range(0, 65_535),
        })
    loop = next(node for node in model.step.body
                if isinstance(node, ast.While))

    assert sequence_plan.loop_span(
        loop, model.step, sequence_plan.root_context) == \
        ranges.Range(0, 65_535)


def test_a_clamp_proves_a_loop_ceiling_without_an_input_width():
    source = _source("""
        limit = a if a < 12 else 12
        i = 0
        while i < limit:
            i += 1
        return {"y": i}
    """)
    model = source_model(source)
    sequence_plan = plan_sequences(model, ScopeResolver(model))
    loop = next(node for node in model.step.body
                if isinstance(node, ast.While))

    assert sequence_plan.loop_span(
        loop, model.step, sequence_plan.root_context) == ranges.Range(0, 12)


def test_scalar_while_summary_does_not_underapproximate_an_accumulator():
    source = _source("""
        i = 0
        total = 0
        while i < 64:
            total += i
            i += 1
        rows = []
        j = 0
        while j < total:
            rows.append({"y": j})
            j += 1
        return rows
    """)

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1], outputs=["y"])

    assert caught.value.code == "while-trip-range-unproved"


def test_fixed_tuple_unpacking_reaches_structural_loop_bounds():
    source = """class M:
    def __init__(self):
        self.N = 2
        self.M = 3

    def reset(self):
        pass

    def process(self, a):
        n, m = self.N, self.M
        extent = n * m if m > 0 else 1
        values = [0] * extent
        for i in range(n):
            for j in range(m):
                values[i * m + j] = a + i + j
        return {"y": values[5]}
"""
    model = source_model(source)
    plan_sequences(
        model,
        ScopeResolver(model),
        budget=SourceValueBudget(limit=2_000),
    )
    proc = lower_with_state_ranges(
        source, input_bounds={"a": (0, 7)}, outputs=["y"])
    for value in (0, 3, 7):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": value + 3}]


def test_static_range_is_bounded_before_materialisation():
    node = ast.parse("range(1000000000)", mode="eval").body
    assert static_iterable(
        node, None, is_builtin=lambda name: name == "range") is None


@pytest.mark.parametrize(("expression", "expected"), [
    ("range(len(rows))", 8),
    ("range(1, len(rows))", 7),
    ("range(len(rows) - 1, -1, -1)", 8),
    ("reversed(range(len(rows)))", 8),
])
def test_iteration_bounds_reuse_proved_sequence_lengths(
        expression, expected):
    node = ast.parse(expression, mode="eval").body

    assert _bound_of(
        node, {"rows": 8}, None,
        lambda name: name in {"len", "range", "reversed"}) == expected


def test_static_format_width_is_bounded_before_formatting():
    node = ast.parse("f'{1:5000000}'", mode="eval").body
    assert static_value(node, None) is None


@pytest.mark.parametrize(("expression", "expected"), [
    ("4 + (4 & 1)", 4),
    ("5 | 2", 7),
    ("5 ^ 3", 6),
    ("3 << 4", 48),
    ("1 << 1000000", None),
    ("-9 >> 2", -3),
    ("1 << -1", None),
])
def test_static_integer_value_covers_lowered_bitwise_operators(
        expression, expected):
    node = ast.parse(expression, mode="eval").body
    assert static_value(node, None) == expected


def test_bitwise_derived_loop_bounds_do_not_expand_to_the_capability_limit():
    source = _source("""
        n = 4 + (4 & 1)
        outputs = []
        for i in range(n // 2):
            for j in range(n // 2):
                outputs.append({"y": a + i + j})
        return outputs
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"])

    assert proc.output_capacity == 4
    _state, output, _hits, error = interp.run_step(proc, {"a": 3})
    assert not error
    assert output.transactions == [
        {"y": 3}, {"y": 4}, {"y": 4}, {"y": 5},
    ]


def test_nested_comprehension_has_one_aggregate_source_plan_budget():
    source = _source("""
        return [{"y": i + j + k}
                for i in range(100)
                for j in range(100)
                for k in range(11)]
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "source-plan-budget-exceeded"


def test_helper_activation_limit_is_exact_in_production_lowering():
    exact = _helper_chain(budgets.HELPER_ACTIVATION_LIMIT)
    _assert_agrees(exact, [0, 7])

    outside = _helper_chain(budgets.HELPER_ACTIVATION_LIMIT + 1)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            outside, input_widths=[4], outputs=["y"])
    assert caught.value.code == "source-summary-depth-exceeded"


def test_alias_authority_uses_the_same_helper_activation_boundary():
    exact = source_model(_helper_chain(
        budgets.HELPER_ACTIVATION_LIMIT, array_state=True))
    assert step_alias.step_alias_hazard(
        exact, {"buf"}, ScopeResolver(exact),
        array_types={"buf": value_types.Array(1)},
        source_value_budget=SourceValueBudget()) is None

    outside = source_model(_helper_chain(
        budgets.HELPER_ACTIVATION_LIMIT + 1, array_state=True))
    with pytest.raises(LoweringError) as caught:
        step_alias.step_alias_hazard(
            outside, {"buf"}, ScopeResolver(outside),
            array_types={"buf": value_types.Array(1)},
            source_value_budget=SourceValueBudget())
    assert caught.value.code == "call-inlining-too-deep"


def test_alias_authority_charges_static_comparisons_once_per_occurrence():
    source = """class M:
    def __init__(self):
        self.buf = [0]

    def reset(self):
        self.buf = [0]

    def process(self, a):
        out = []
        for i in range(64):
            if 0 < 1 < 2:
                out.append([i])
        self.buf[0] = a
        return {"y": len(out)}
"""
    model = source_model(source)
    loop = next(node for node in ast.walk(model.step)
                if isinstance(node, ast.For))
    budget = SourceValueBudget(limit=2)

    assert step_alias.step_alias_hazard(
        model,
        {"buf"},
        ScopeResolver(model),
        loop_spans={id(loop): (64, 64)},
        array_types={"buf": value_types.Array(1)},
        source_value_budget=budget,
    ) is None
    assert budget.left == 0


@pytest.mark.parametrize("body", [
    """
        out = []
        for i in range(5):
            for j in range(5):
                for k in range(5):
                    out.append(i + j + k)
        return {"y": len(out)}
    """,
    """
        out = []
        for i in range(5):
            out += [j for j in range(5) for k in range(5)]
        return {"y": len(out)}
    """,
])
def test_nested_sequence_capacity_is_not_charged_as_analysis_work(
        monkeypatch, body):
    # The comprehension form genuinely visits more source facts than the
    # nested statements.  Keep enough work budget for either syntax while
    # remaining below the logical cardinality being proved.
    monkeypatch.setattr(budgets, "SOURCE_PLAN_NODES", 124)
    model = source_model(_source(body))

    specs, _initial = _scan_scope(model.step, ScopeResolver(model))
    [out] = [spec for (_owner, name), spec in specs.items()
             if name == "out"]
    assert out.capacity == 125


def test_syntax_only_capacity_walk_does_not_invent_a_while_span():
    source = _source("""
        out = []
        i = 0
        while i < a:
            out.append(i)
            i += 1
        return {"y": len(out)}
    """)
    model = source_model(source)

    with pytest.raises(SequencePlanError) as caught:
        _scan_scope(model.step, ScopeResolver(model))
    assert caught.value.code == "while-trip-plan-unavailable"


def test_capacity_reuses_fixed_facts_from_the_source_interpreter(monkeypatch):
    """A capacity projection must not replace already proved loop bounds by
    three independent copies of the generic unrolling ceiling.

    The constructor default and the derived local are interpreted by the
    source-contract pass before capacity is projected.  Their exact values
    make this an eight-row batch; 256 cubed would exceed the deliberately
    smaller aggregate budget below and refuse the same finite program.
    """
    monkeypatch.setattr(budgets, "SOURCE_PLAN_NODES", 50_000)
    source = textwrap.dedent("""
        class M:
            def __init__(self, n=2):
                self.n = n
                self.reset()

            def reset(self):
                pass

            def process(self, a):
                width = self.n
                out = []
                for i in range(self.n):
                    for j in range(width):
                        for k in range(self.n):
                            out.append({"y": a + i + j + k})
                return out
    """)
    _assert_agrees(source, [0, 1])


def test_unused_loop_target_stops_source_planning_at_a_fixed_point():
    source = _source("""
        value = a
        for _ in range(64):
            value = a + 1
            if a == 0:
                break
        return {"y": value}
    """)
    model = source_model(source)
    budget = SourceValueBudget(500)

    plan_sequences(
        model, ScopeResolver(model),
        input_ranges={"a": ranges.Range(0, 15)}, budget=budget)

    assert budget.left > 0
    _assert_agrees(source, [0, 1, 15])


def test_record_append_loop_uses_one_stable_schema_transfer():
    source = _source("""
        rows = []
        for i in range(a):
            for j in range(8):
                rows.append({"y": i + j})
        return rows
    """)
    model = source_model(source)
    budget = SourceValueBudget(1_000)

    plan = plan_sequences(
        model, ScopeResolver(model),
        input_ranges={"a": ranges.Range(0, 8)}, budget=budget)

    assert plan.output_capacity == 64
    assert budget.left > 0
    _assert_agrees(source, [0, 1, 4, 8])


def test_iteration_local_sequence_can_be_nested_in_a_stable_record():
    source = _source("""
        hypotheses = []
        for h in range(64):
            tokens = []
            for j in range(32):
                if j <= h:
                    tokens.append(j)
            hypotheses.append({"tokens": tokens, "score": h})
        return {"y": len(hypotheses[63]["tokens"])}
    """)
    model = source_model(source)
    budget = SourceValueBudget(2_000)

    plan_sequences(model, ScopeResolver(model), budget=budget)

    assert budget.left > 0


def test_iteration_local_fixed_array_uses_element_store_transfer():
    source = _source("""
        total = 0
        for h in range(8):
            scores = [0 for _ in range(8)]
            for t in range(8):
                scores[t] = a + h + t
            for t in range(8):
                total += scores[t]
        return {"y": total}
    """)
    model = source_model(source)
    budget = SourceValueBudget(1_000)

    plan_sequences(
        model, ScopeResolver(model),
        input_ranges={"a": ranges.Range(0, 15)}, budget=budget)

    assert budget.left > 0
    _assert_agrees(source, [0, 1, 15])


def test_iteration_local_repeated_array_uses_element_store_transfer():
    source = _source("""
        total = 0
        for h in range(8):
            scores = [0] * 8
            for t in range(8):
                scores[t] = a + h + t
            for t in range(8):
                total += scores[t]
        return {"y": total}
    """)
    model = source_model(source)
    budget = SourceValueBudget(1_000)

    plan_sequences(
        model, ScopeResolver(model),
        input_ranges={"a": ranges.Range(0, 15)}, budget=budget)

    assert budget.left > 0
    _assert_agrees(source, [0, 1, 15])


def test_element_swap_loop_preserves_one_record_array_contract():
    source = _source("""
        rows = [{"y": i} for i in range(32)]
        for i in range(32):
            best = i
            for j in range(i + 1, 32):
                if rows[j]["y"] < rows[best]["y"]:
                    best = j
            if best != i:
                rows[i], rows[best] = rows[best], rows[i]
        return {"y": rows[0]["y"]}
    """)
    model = source_model(source)
    budget = SourceValueBudget(2_000)

    plan_sequences(model, ScopeResolver(model), budget=budget)

    assert budget.left > 0
    _assert_agrees(source, [0])


def test_dynamic_length_range_reuses_the_sequence_contract_budget():
    source = _source("""
        rows = []
        for i in range(8):
            if i < a:
                rows.append(i + 1)
        total = 0
        for i in range(len(rows)):
            for j in range(32):
                total += rows[i] + j
        return {"y": total}
    """)
    model = source_model(source)
    budget = SourceValueBudget(5_000)

    plan_sequences(
        model, ScopeResolver(model), input_ranges={
            "a": ranges.Range(0, 15),
        }, budget=budget)

    assert budget.left > 0
    _assert_agrees(source, [0, 1, 4, 8, 15])


def test_reserved_capacity_is_not_the_run_time_length():
    source = _source("""
        xs = []
        if a:
            xs.append(7)
        return {"y": xs[0]}
    """)
    assert _native(source, 0) == (None, "IndexError")
    _assert_agrees(source, [0, 1])


def test_sequence_length_refines_against_a_bounded_scalar():
    source = _source("""
        limit = 2 if a > 2 else a
        rows = []
        if limit > 0:
            for i in range(2):
                if len(rows) < limit:
                    rows.append({"value": i + 1})
                else:
                    if rows[0]["value"] < 0:
                        return {"y": 9}
        return {"y": len(rows)}
    """)
    _assert_agrees(source, [0, 1, 2, 7])


def test_nonempty_sequence_can_grow_without_losing_its_prefix():
    source = _source("""
        xs = [3]
        if a:
            xs.append(7)
        return {"y": xs[1] if a else xs[0]}
    """)
    _assert_agrees(source, [0, 1])


def test_branch_assignments_share_one_run_time_length_variable():
    source = _source("""
        if a:
            xs = []
        else:
            xs = [4]
        xs.append(7)
        return {"y": len(xs) * 10 + xs[0]}
    """)
    _assert_agrees(source, [0, 1])


def test_exact_comprehension_length_does_not_escape_its_builder_branch():
    source = _source("""
        base = [a + i for i in range(4)]
        if a:
            xs = [base[i] + 1 for i in range(4)]
        else:
            xs = list(base)
        total = 0
        for i in range(4):
            total += xs[i]
        return {"y": total}
    """)
    _assert_agrees(source, [0, 1, 3, 7])


def test_exact_comprehension_keeps_length_when_later_mutated():
    source = _source("""
        xs = [a + i for i in range(2)]
        xs.append(a + 7)
        return {"y": len(xs) * 100 + xs[2]}
    """)
    _assert_agrees(source, [0, 1, 3, 7])


def test_iteration_uses_the_length_this_run_built():
    source = _source("""
        xs = []
        for i in range(4):
            if i < a:
                xs.append(i + 1)
        y = 0
        for value in xs:
            y += value
        return {"y": y}
    """)
    _assert_agrees(source, [0, 1, 2, 4])


def test_local_transaction_builder_preserves_count_order_and_fields():
    source = _source("""
        out = []
        for i in range(4):
            if i < a:
                out.append({"y": i, "z": a + i})
        return out
    """)
    _assert_agrees(source, [0, 1, 2, 4], outputs=("y", "z"))


def test_return_arms_with_different_bounds_join_at_one_output_type():
    source = _source("""
        if a:
            one = []
            one.append({"y": 7})
            return one
        two = []
        for i in range(2):
            two.append({"y": i})
        return two
    """)
    _assert_agrees(source, [0, 1])


@pytest.mark.parametrize("body", [
    'return [{"y": i} for i in range(4) if i < a]',
    ('return [{"y": i * 10 + j} for i in range(2) '
     'for j in range(3) if i + j < a]'),
])
def test_output_comprehensions_are_ordered_transaction_batches(body):
    source = _source(body)
    _assert_agrees(source, [0, 1, 2, 4])


def test_output_comprehension_uses_a_ranged_builtin_range():
    source = _source("""
        count = a & 3
        if count == 0:
            return None
        return [{"y": i} for i in range(count)]
    """)
    _assert_agrees(source, [0, 1, 2, 3, 7])


@pytest.mark.parametrize("returned", [
    '[{"y": value} for value in xs]',
    '[{"y": value} for value in reversed(xs)]',
])
def test_direct_output_comprehension_uses_the_grown_source_bound(returned):
    source = _source(f"""
        xs = []
        xs.append(1)
        xs.append(2)
        return {returned}
    """)
    _assert_agrees(source, [0])


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
@pytest.mark.parametrize("order", ["ROWS[n]", "reversed(ROWS[n])"])
def test_selected_static_iterables_may_have_different_lengths(lowerer, order):
    source = ("ROWS = {0: [], 1: [10], 2: [10, 20]}\n"
              "class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a):\n"
              "        n = a & 3\n"
              "        n = n if n <= 2 else 2\n"
              f"        return [{{'y': value}} for value in {order}] + "
              "[{'y': 99}]\n")
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in (0, 1, 2, 3, 7):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert [dict(row) for row in output.transactions] == \
            _native(source, value)[0]


def test_filtered_growth_still_reserves_every_reachable_output():
    source = _source("""
        xs = []
        for i in range(3):
            if i < a:
                xs.append(i + 1)
        return [{"y": value} for value in xs]
    """)
    _assert_agrees(source, [0, 1, 2, 3])


def test_module_constants_have_the_same_lexical_owner_in_the_value_plan():
    source = """
EXP_MASK = 0xff
MANT_MASK = 0x7fffff

def fields(u):
    sign = (u >> 31) & 1
    exp = (u >> 23) & EXP_MASK
    mant = u & MANT_MASK
    return sign, exp, mant

class M:
    def reset(self):
        pass

    def process(self, a):
        sign, exp, mant = fields(a)
        return {"y": (sign << 8) | exp | (mant & 1)}
"""
    _assert_agrees(source, [0, 1, 0x3F800000, 0xFFFFFFFF])


def test_helper_return_contract_is_keyed_by_its_activation_context():
    source = _source("""
        scalar = self.wrap(a)
        sequence = self.wrap([a, a + 1])
        return {"y": scalar + len(sequence)}
    """, helpers="""
        def ident(self, value):
            return value

        def wrap(self, value):
            return self.ident(value)
    """)
    _assert_agrees(source, [0, 1, 7])

    model = source_model(source)
    resolver = ScopeResolver(model)
    plan = plan_sequences(model, resolver, ("y",))
    process_calls = [
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wrap"
    ]
    scalar_call, sequence_call = sorted(process_calls, key=lambda node: node.lineno)
    sequence = ValueContract.sequence("list", 2, 2, SCALAR)
    scalar_context = plan.callee_context(
        scalar_call, (SCALAR,), (None,), plan.root_context)
    sequence_context = plan.callee_context(
        sequence_call, (sequence,), (None,), plan.root_context)
    ident_call = next(
        node for node in ast.walk(model.helpers["wrap"])
        if isinstance(node, ast.Call))
    assert plan.call_contract(
        ident_call, (SCALAR,), (None,), scalar_context) == SCALAR
    assert plan.call_contract(
        ident_call, (sequence,), (None,), sequence_context) == sequence


def test_helper_local_sequence_plan_is_keyed_by_activation_context():
    source = _source("""
        one = self.helper(1)
        two = self.helper([2, 3])
        return {"y": one[0] + two[0][1] + (a & 0)}
    """, helpers="""
        def helper(self, value):
            rows = []
            rows.append(value)
            return rows
    """)
    _assert_agrees(source, [0, 1, 7])

    model = source_model(source)
    resolver = ScopeResolver(model)
    plan = plan_sequences(model, resolver, ("y",))
    calls = sorted([
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "helper"
    ], key=lambda node: node.lineno)
    nested = ValueContract.sequence("list", 2, 2, SCALAR)
    contexts = [
        plan.callee_context(calls[0], (SCALAR,), (1,), plan.root_context),
        plan.callee_context(calls[1], (nested,), (None,), plan.root_context),
    ]
    expected = [
        ValueContract.sequence("list", 0, 1, SCALAR),
        ValueContract.sequence("list", 0, 1, nested),
    ]
    helper = model.helpers["helper"]
    assert [plan.binding_contract(helper, "rows", context)
            for context in contexts] == expected
    assert [plan.spec(helper, "rows", context).element
            for context in contexts] == [SCALAR, nested]


def test_nested_helper_summary_is_keyed_by_its_lexical_capture():
    source = _source("""
        scalar = self.outer(a)
        sequence = self.outer([a, a + 1])
        return {"y": scalar + len(sequence)}
    """, helpers="""
        def outer(self, value):
            def inner():
                return value
            return inner()
    """)
    _assert_agrees(source, [0, 1, 7])

    model = source_model(source)
    resolver = ScopeResolver(model)
    plan = plan_sequences(model, resolver, ("y",))
    outer = model.helpers["outer"]
    outer_calls = sorted([
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "outer"
    ], key=lambda node: node.lineno)
    inner_call = next(
        node for node in ast.walk(outer)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "inner")
    sequence = ValueContract.sequence("list", 2, 2, SCALAR)
    outer_contexts = [
        plan.callee_context(
            outer_calls[0], (SCALAR,), (None,), plan.root_context),
        plan.callee_context(
            outer_calls[1], (sequence,), (None,), plan.root_context),
    ]
    assert {
        plan.call_contract(inner_call, (), (), caller_context=context)
        for context in outer_contexts
    } == {SCALAR, sequence}


def test_transitive_sibling_closure_keeps_its_parent_activation():
    source = _source("""
        scalar = self.outer(a)
        sequence = self.outer([a, a + 1])
        return {"y": scalar + len(sequence)}
    """, helpers="""
        def outer(self, value):
            def left():
                return value
            def right():
                return left()
            return right()
    """)
    _assert_agrees(source, [0, 1, 7])


def test_one_call_occurrence_selects_each_capture_value_in_turn():
    source = _source("""
        n = 0
        def grab():
            return [j for j in range(n)]
        y = 0
        for n in [1, 2]:
            y += len(grab())
        return {"y": y + (a & 0)}
    """)
    _assert_agrees(source, [0, 1, 7])


def _call_selection_plan(returns=None, tags=(("int", 1), ("int", 2))):
    scope = ast.parse("def helper(): pass").body[0]
    node = ast.parse("helper()", mode="eval").body
    returns = returns or tuple(
        ValueContract.sequence("list", n, n, SCALAR) for n in (1, 2))
    keys = tuple(("caller", id(node), (), (), ((17, "n", SCALAR, tag),))
                 for tag in tags)
    plan = SequencePlan(
        call_contexts=zip(keys, returns),
        call_targets=zip(keys, ("first", "second")),
        context_functions=(("first", scope), ("second", scope)),
    )
    return plan, node, scope


@pytest.mark.parametrize("low,high,expected", [
    (1, 1, {"first"}), (2, 2, {"second"}), (1, 2, {"first", "second"}),
])
def test_plan_owns_capture_selection_without_an_emitter(low, high, expected):
    plan, node, scope = _call_selection_plan()
    reads = []

    def fact(owner, name):
        reads.append((owner, name))
        return SCALAR, ranges.Range(low, high)

    contract, context = plan.select_call(
        node, (), (), "caller", capture_fact=fact)
    assert set(plan._contexts_for(scope, context)) == expected
    assert (contract.minimum, contract.maximum) == (low, high)
    assert reads == [(17, "n")]


def test_plan_does_not_cache_capture_facts_across_rebindings():
    plan, node, _scope = _call_selection_plan()
    for n, expected in ((1, "first"), (2, "second"), (1, "first")):
        _contract, context = plan.select_call(
            node, (), (), "caller",
            capture_fact=lambda _owner, _name: (SCALAR, ranges.Range(n, n)))
        assert context == expected


def test_plan_reports_absent_lexical_activation():
    plan, node, _scope = _call_selection_plan()
    with pytest.raises(LoweringError) as caught:
        plan.select_call(node, (), (), "caller",
                         capture_fact=lambda *_: (None, None))
    assert caught.value.code == "closure-activation-unresolved"


def test_plan_does_not_invent_an_activation_outside_the_source_plan():
    plan, node, _scope = _call_selection_plan()
    assert plan.select_call(
        node, (), (), "caller",
        capture_fact=lambda *_: (SCALAR, ranges.Range(3, 3))) == (UNKNOWN, None)


@pytest.mark.parametrize("value,expected", [(0, "first"), (1, "second")])
def test_plan_matches_boolean_capture_facts(value, expected):
    plan, node, _scope = _call_selection_plan(tags=(("bool", False), ("bool", True)))
    _contract, context = plan.select_call(
        node, (), (), "caller",
        capture_fact=lambda *_: (SCALAR, ranges.Range(value, value)))
    assert context == expected


def test_plan_retains_incompatible_return_diagnostic():
    plan, node, _scope = _call_selection_plan(returns=(
        SCALAR, ValueContract.sequence("list", 1, 1, SCALAR)))
    with pytest.raises(LoweringError) as caught:
        plan.select_call(node, (), (), "caller",
                         capture_fact=lambda *_: (SCALAR, None))
    assert caught.value.code == "helper-return-shape-disagrees"
    assert caught.value.node is node


def test_plan_does_not_query_captures_of_an_unmatched_caller():
    plan, node, _scope = _call_selection_plan()

    def unexpected(*_):
        pytest.fail("a different caller's capture was read")

    assert plan.select_call(node, (), (), "elsewhere", capture_fact=unexpected) == (
        UNKNOWN, None)


@pytest.mark.parametrize("tags", [
    (("str", "left"), ("str", "right")),
    (("unproved",), ("unproved",)),
])
def test_plan_does_not_exclude_unrepresented_capture_values(tags):
    plan, node, scope = _call_selection_plan(tags=tags)
    _contract, context = plan.select_call(
        node, (), (), "caller", capture_fact=lambda *_: (SCALAR, None))
    expected = {"second"} if tags[0] == tags[1] else {"first", "second"}
    assert set(plan._contexts_for(scope, context)) == expected


def test_transitive_closure_observes_rebinding_within_one_activation():
    source = _source("""
        k = 1
        def get():
            out = []
            for i in range(k):
                out.append(i + 1)
            return out
        def invoke():
            return get()
        one = invoke()
        k = 2
        two = invoke()
        return {"y": one[0] * 10 + two[1] + (a & 0)}
    """)
    _assert_agrees(source, [0, 1, 7])


def test_comprehension_target_does_not_replace_an_outer_closure_cell():
    source = _source("""
        values = self.outer([a, a + 1])
        return {"y": len(values)}
    """, helpers="""
        def outer(self, value):
            def grab():
                return value
            return [grab() for value in [0]][0]
    """)
    _assert_agrees(source, [0, 1, 7])


def test_comprehension_contract_keeps_its_iteration_bindings_at_a_call():
    source = _source("""
        values = self.outer([a, a + 1])
        return {"y": len(values)}
    """, helpers="""
        def outer(self, values):
            def get(index):
                return values[index]
            return [get(index) for index in range(2)]
    """)
    _assert_agrees(source, [0, 1, 7])


def test_dynamic_comprehension_target_contract_reaches_a_helper_summary():
    source = _source("""
        values = self.outer([[1, 2], [3, 4]])
        return {"y": values[1][0] + (a & 0)}
    """, helpers="""
        def ident(self, value):
            return value

        def outer(self, rows):
            return [self.ident(row) for row in rows]
    """)
    _assert_agrees(source, [0, 1, 7])

    model = source_model(source)
    plan = plan_sequences(model, ScopeResolver(model), ("y",))
    row = ValueContract.sequence("list", 2, 2, SCALAR)
    rows = ValueContract.sequence("list", 2, 2, row)
    outer_call = next(
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "outer")
    outer_context = plan.callee_context(
        outer_call, (rows,), (None,), plan.root_context)
    ident_call = next(
        node for node in ast.walk(model.helpers["outer"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ident")
    assert plan.call_contract(
        ident_call, (row,), (None,), outer_context) == row


def test_static_ifexp_does_not_require_an_unreachable_helper_activation():
    source = _source("""
        return {"y": self.hidden() if False else 1}
    """, helpers="""
        def hidden(self):
            return [7]
    """)

    _assert_agrees(source, [0, 1, 7])

    model = source_model(source)
    plan = plan_sequences(model, ScopeResolver(model), ("y",))
    hidden_call = next(
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "hidden")
    assert plan.call_contract(hidden_call) is UNKNOWN


def test_bounded_ifexp_does_not_require_an_unreachable_helper_activation():
    source = """class M:
    def reset(self):
        self.state = 0

    def hidden(self, a):
        return a + 1

    def process(self, a):
        y = self.hidden(a) if self.state == 8 else 1
        self.state = 1
        return {"y": y}
"""
    model = source_model(source)
    plan = plan_sequences(
        model, ScopeResolver(model), ("y",),
        declaration_contracts={"state": SCALAR},
        input_contracts={"a": SCALAR},
        input_ranges={"state": ranges.Range(0, 0)}, state_bound=1)
    conditional = next(
        node for node in ast.walk(model.step)
        if isinstance(node, ast.IfExp))

    assert plan.branch_feasible(
        model.step, conditional.test, True, plan.root_context) is False
    assert plan.branch_feasible(
        model.step, conditional.test, False, plan.root_context) is True

    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=1)
    _state, output, _hits, error = interp.run_step(proc, {"a": 3})
    assert not error
    assert output.transactions == [{"y": 1}]


def test_sequence_reachability_cannot_erase_a_dynamic_control_arm(monkeypatch):
    """The contract planner is not a second authority over step control."""

    source = _source("""
        if a:
            y = 1
        else:
            y = 2
        return {"y": y}
    """)
    monkeypatch.setattr(
        SequencePlan, "statement_reached", lambda *_args, **_kwargs: False)

    proc = lower_with_state_ranges(source, input_widths=[1], outputs=["y"])
    for value, expected in ((0, 2), (1, 1)):
        _state, out, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert out.transactions == [{"y": expected}]


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
@pytest.mark.parametrize("expression", [
    "[self.hidden() for _ in [0] if False]",
    "[self.hidden() for _ in [0] if False if self.predicate()]",
    "[self.hidden() for _ in [] if self.predicate()]",
    "[self.hidden() for _ in [0] for j in [] if self.predicate()]",
])
def test_static_false_comprehension_filter_does_not_reach_its_element(
        lowerer, expression):
    from behave.sim.behavior import instrumented
    from behave.sim.symbolic import ir
    from behave.sim.behavior.source_plan import plan_source

    source = _source(f"return {expression}", helpers="""
        def hidden(self):
            return 7
        def predicate(self):
            return True
    """)

    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    prepared = plan_source(source_model(source), source)
    namespace, _sites, hits = instrumented.load_source(source, goal_plan=prepared)
    native = namespace["M"]()
    assert set().union(*(site.goals for site in prepared.coverage_sites)) <= ir.goals_of(proc.body)
    for value in (0, 1, 7):
        hits.clear()
        assert native.process(value) == []
        _state, out, actual_hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert out.transactions == []
        assert actual_hits == set(hits)

    model = source_model(source)
    plan = plan_sequences(model, ScopeResolver(model), ("y",))
    hidden_call = next(
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "hidden")
    assert plan.call_contract(hidden_call) is UNKNOWN


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_comprehension_pruned_goal_does_not_hide_a_live_helper_call(lowerer):
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.source_plan import plan_source

    source = _source("""
        empty = [self.value(a) for _ in [0] if False]
        return [{'y': self.value(a)} for i in [0, 1] if i]
    """, helpers="""
        def value(self, a):
            return a + 1 if a else 0
    """)
    prepared = plan_source(source_model(source), source)
    namespace, _sites, hits = instrumented.load_source(source, goal_plan=prepared)
    native = namespace["M"]()
    proc = lower_with_state_ranges(
        source, input_widths=[3], outputs=["y"], lowerer=lowerer)
    for value in (0, 1, 7):
        hits.clear()
        expected = native.process(value)
        assert expected == [{'y': value + 1 if value else 0}]
        _state, output, actual_hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert output.transactions == expected
        assert actual_hits == set(hits)


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_static_true_comprehension_filter_still_checks_its_element(lowerer):
    source = _source("""
        return [self.hidden() for _ in [0] if True]
    """, helpers="""
        def hidden(self):
            return 7
    """)

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    assert caught.value.code == "output-batch-element-unsupported"


def test_static_false_chained_comparison_stops_before_later_helper():
    source = _source("""
        return {"y": int(0 > 1 < self.hidden())}
    """, helpers="""
        def hidden(self):
            return 7
    """)

    _assert_agrees(source, [0, 1, 7])

    model = source_model(source)
    plan = plan_sequences(model, ScopeResolver(model), ("y",))
    hidden_call = next(
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "hidden")
    assert plan.call_contract(hidden_call) is UNKNOWN


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_unreachable_append_forms_do_not_constrain_the_source_subset(lowerer):
    source = _source("""
        values = []
        if False:
            values.append(1, 2)
            unused = values.append(3)
        return {"y": a}
    """)

    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in (0, 1, 7):
        _state, out, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert out.transactions == [{"y": value}]


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
@pytest.mark.parametrize(("statement", "code"), [
    ("values.append(1, 2)", "call-argument-form"),
    ("unused = values.append(3)", "append-result-used"),
])
def test_reached_append_forms_are_still_refused_precisely(
        lowerer, statement, code):
    source = _source(f"""
        values = []
        {statement}
        return {{"y": a}}
    """)

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    assert caught.value.code == code


@pytest.mark.parametrize("trips", [2, 12, 31])
@pytest.mark.parametrize("conditional", [False, True])
def test_accelerated_append_occurrence_encloses_all_reached_lengths(trips, conditional):
    append = "values.append(a + i)"
    if conditional:
        append = "if a & 1:\n                " + append
    source = _source(f"""
        values = []
        for i in range({trips}):
            {append}
        return {{"y": len(values)}}
    """)
    model = source_model(source)
    prepared = plan_sequences(model, ScopeResolver(model), ("y",))
    occurrence = next(node for node in ast.walk(model.step)
                      if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "append")
    contract = prepared.binding_contract(model.step, "values", occurrence=occurrence)
    assert contract.minimum == 1
    assert contract.maximum == trips


def test_append_capacity_is_local_to_the_reached_helper_activation():
    source = _source("""
        empty = self.helper(0)
        one = self.helper(1)
        return {"y": len(empty) + len(one) + (a & 0)}
    """, helpers="""
        def helper(self, count):
            values = []
            for _ in range(count):
                values.append(7)
            return values
    """)

    _assert_agrees(source, [0, 1, 7])

    model = source_model(source)
    plan = plan_sequences(model, ScopeResolver(model), ("y",))
    calls = sorted([
        node for node in ast.walk(model.step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "helper"
    ], key=lambda node: node.lineno)
    contexts = [
        plan.callee_context(
            call, (SCALAR,), (count,), plan.root_context)
        for call, count in zip(calls, (0, 1))
    ]
    helper = model.helpers["helper"]
    assert plan.spec(helper, "values", contexts[0]) is None
    assert plan.spec(helper, "values", contexts[1]).capacity == 1


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_a_grown_list_capture_uses_its_logical_length_at_the_call(lowerer):
    source = _source("""
        rows = []
        rows.append(a)
        def read():
            return rows
        return {"y": len(read())}
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in (0, 1, 7):
        _state, out, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert out.transactions == [{"y": 1}]


def test_compact_lowering_joins_only_the_reached_capture_activations():
    source = _source("""
        count = 0
        def grab():
            values = []
            for value in range(count):
                values.append(value)
            return values
        y = 0
        for count in [1, 2]:
            y += len(grab())
        return {"y": y + (a & 0)}
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=_Lowerer)
    assert proc.graph is None
    for value in (0, 1, 7):
        _state, out, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert out.transactions == [{"y": 3}]


def test_compact_context_uses_the_run_time_length_for_indexing():
    source = _source("""
        count = 0
        def grab():
            values = []
            for value in range(count):
                values.append(value + 10)
            return values
        y = 0
        for count in [1, 2]:
            values = grab()
            y += values[count - 1]
        return {"y": y + (a & 0)}
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=_Lowerer)
    assert proc.graph is None
    for value in (0, 1, 7):
        _state, out, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert out.transactions == [{"y": 21}]


def test_compact_context_preserves_transitive_correlated_captures():
    source = _source("""
        count = 0
        base = 0
        def leaf():
            values = []
            for offset in range(count):
                values.append(base + offset)
            return values
        def grab():
            return leaf()
        y = 0
        for count in [1, 2]:
            base = 3 - count
            values = grab()
            y += values[count - 1]
        return {"y": y + (a & 0)}
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=_Lowerer)
    assert proc.graph is None
    for value in (0, 1, 7):
        _state, out, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert out.transactions == [{"y": 4}]


def test_aggregate_context_refuses_incompatible_binding_contracts():
    scope = ast.parse("def helper(): pass").body[0]
    scalar_context = ("scalar",)
    sequence_context = ("sequence",)
    plan = SequencePlan(
        binding_contracts=(
            ((scalar_context, "value"), SCALAR),
            ((sequence_context, "value"),
             ValueContract.sequence("list", 2, 2, SCALAR)),
        ),
        context_functions=(
            (scalar_context, scope),
            (sequence_context, scope),
        ),
        root_context=scalar_context,
    )
    aggregate = plan.combine_contexts((scalar_context, sequence_context))

    with pytest.raises(LoweringError) as caught:
        plan.binding_contract(scope, "value", aggregate)
    assert caught.value.code == "source-value-contract-disagrees"


@pytest.mark.parametrize("body", [
    """
        def recurse():
            return recurse()
        return {"y": len(recurse()) + (a & 0)}
    """,
    """
        def recurse():
            return other()
        def other():
            return recurse()
        return {"y": len(recurse()) + (a & 0)}
    """,
])
def test_recursive_nested_summaries_are_a_typed_boundary(body):
    source = _source(body)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "recursive-helper-unsupported"


def test_filtered_comprehension_is_an_ordinary_dynamic_sequence_too():
    source = _source("""
        xs = [i * 2 for i in range(4) if i < a]
        y = 0
        for value in xs:
            y += value
        return {"y": y}
    """)
    _assert_agrees(source, [0, 1, 2, 4])


def test_comprehension_rebinding_reads_the_complete_previous_value():
    source = _source("""
        xs = [i * 2 for i in range(4) if i < a]
        xs = [value + 1 for value in xs]
        y = len(xs) * 100
        for value in xs:
            y += value
        return {"y": y}
    """)
    _assert_agrees(source, [0, 1, 2, 4])


def test_comprehension_target_shadowing_is_not_an_outer_read():
    source = _source("""
        xs = [xs for xs in range(2) if xs < a]
        xs.append(9)
        return {"y": len(xs) * 100 + xs[-1]}
    """)
    _assert_agrees(source, [0, 1, 2])


def test_comprehension_outer_read_check_obeys_lambda_scope():
    reads_outer = ast.parse(
        "[(lambda: xs[0])() for i in range(2)]", mode="eval").body
    shadows_outer = ast.parse(
        "[(lambda xs: xs[0])([i]) for i in range(2)]", mode="eval").body

    assert _expression_reads_binding(reads_outer, "xs")
    assert not _expression_reads_binding(shadows_outer, "xs")


def test_indexed_augmented_assignment_updates_the_element_range():
    source = _source("""
        xs = [0, 0]
        xs[0] += a
        y = 1 if xs[0] > 0 else 0
        return {"y": y}
    """)
    _assert_agrees(source, [0, 1, 3])


@pytest.mark.parametrize("write", ("values[0] = a", "values[:] = [a]"))
def test_array_assignment_updates_flow_facts_before_helper_inlining(write):
    source = f"""def signed4(value):
    value &= 15
    if value & 8:
        value -= 16
    return value


class M:
    def reset(self):
        pass

    def process(self, a):
        values = [0]
        {write}
        return {{"y": signed4(values[0]) + 16}}
"""
    _assert_agrees(source, [0, 1, 7, 8, 15])


def test_bounded_state_plan_keeps_a_later_output_arm():
    source = """class M:
    def reset(self):
        self.pending = 0

    def process(self, a):
        old_pending = self.pending
        self.pending = a & 1
        if old_pending:
            return {"y": 7}
        return None
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=3)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (1, 0, 0):
        state, output, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        expected = native.process(value)
        assert output.transactions == ([] if expected is None else [expected])


def test_a_selected_cell_does_not_refine_every_other_cell():
    source = _source("""
        xs = [0, a]
        if xs[0] > 0:
            y = 0
        elif xs[1] > 0:
            y = 1
        else:
            y = 2
        return {"y": y}
    """)
    _assert_agrees(source, [0, 1, 3])


def test_transaction_builder_can_start_with_a_comprehension_then_append():
    source = _source("""
        out = [{"y": i} for i in range(2) if i < a]
        out.append({"y": 9})
        return out
    """)
    _assert_agrees(source, [0, 1, 2])


def test_a_full_dynamic_sequence_slice_copies_its_logical_length():
    source = _source("""
        xs = [i for i in range(4) if i < a]
        ys = xs[:]
        return {"y": len(ys)}
    """)
    _assert_agrees(source, range(6))


def test_fixed_computed_output_mapping_uses_declared_port_names():
    source = _source(
        'return {f"out_{i}": a + i for i in range(3)}')
    outputs = ("out_0", "out_1", "out_2")
    _assert_agrees(source, [0, 3, 7], outputs=outputs)


@pytest.mark.parametrize("key", ['"o" + str(i)', 'f"o{i}"'])
@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_builtin_string_conversion_in_finite_output_keys(key, lowerer):
    source = _source(f'''
        out = {{"o0": 0, "o1": 0, "o2": 0, "o3": 0}}
        for i in range(4):
            out[{key}] = a + i
        return out
    ''')
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["o0", "o1", "o2", "o3"], lowerer=lowerer)
    for value in (0, 3, 15):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert [dict(row) for row in output.transactions] == _native(source, value)[0]


def test_builtin_string_conversion_in_bounded_output_key_selector():
    source = _source('''
        out = {"o0": 0, "o1": 0, "o2": 0, "o3": 0}
        i = a & 3
        out["o" + str(i)] = 99
        return out
    ''')
    _assert_agrees(source, [0, 1, 3, 7], outputs=("o0", "o1", "o2", "o3"))


def test_static_string_conversion_requires_builtin_authority():
    node = ast.parse('"o" + str(i)', mode="eval").body
    assert static_value(node, None, {"i": 2}) is None
    assert static_value(node, None, {"i": 2}, is_builtin=lambda _: False) is None
    assert static_value(node, None, {"i": 2}, is_builtin=lambda _: True) == "o2"
    assert static_value(node, None, {"i": 2, "str": 7}, is_builtin=lambda _: True) is None


def test_builtin_string_conversion_in_output_comprehension():
    _assert_agrees(_source('return {"o" + str(i): a + i for i in range(3)}'),
                   [0, 3, 7], outputs=("o0", "o1", "o2"))


def test_shadowed_string_conversion_is_not_folded_as_builtin():
    source = 'def str(i):\n    return "1"\n\n' + _source('''
        out = {"o0": 0, "o1": 0}
        for i in range(2):
            out["o" + str(i)] = a + i
        return out
    ''')
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[4], outputs=["o0", "o1"])


def test_computed_output_names_can_be_inferred_without_a_schema():
    source = _source(
        'return {f"out_{i:02d}": a + i for i in range(3)}')
    proc = lower_with_state_ranges(source, input_widths=[4])
    _state, out, _hits, error = interp.run_step(proc, {"a": 5})
    assert not error
    assert out.transactions == [{"out_00": 5, "out_01": 6, "out_02": 7}]


def test_nested_computed_output_mapping_preserves_generator_order():
    source = _source(
        'return {f"o_{i}_{j}": a + 10 * i + j '
        'for i in range(2) for j in range(2)}')
    outputs = ("o_0_0", "o_0_1", "o_1_0", "o_1_1")
    _assert_agrees(source, [0, 4], outputs=outputs)


def test_computed_output_mapping_uses_static_filters_and_last_key_write():
    source = _source(
        'return {"out": a + i for i in range(4) if i != 1}')
    _assert_agrees(source, [0, 4], outputs=("out",))


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_fixed_comprehension_record_can_be_updated_fieldwise(lowerer):
    source = _source("""
        out = {f"o{i}": a + i for i in range(3)}
        out["n"] = 3
        for i in range(3):
            out[f"o{i}"] = a + i + 10
        return out
    """)
    outputs = ("o0", "o1", "o2", "n")
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=list(outputs), lowerer=lowerer)
    for value in (0, 3, 7):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert [dict(row) for row in output.transactions] == \
            _native(source, value)[0]


def test_bounded_integer_selects_a_computed_record_field():
    source = _source("""
        out = {f"o{i}": a + i for i in range(4)}
        index = a & 3
        out[f"o{index}"] = 99
        return out
    """)
    _assert_agrees(
        source, [0, 1, 3, 7], outputs=("o0", "o1", "o2", "o3"))


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_local_read_only_lookup_table_flows_through_a_helper(lowerer):
    source = _source("""
        roles = self.roles(a & 7, (a >> 3) & 1)
        if roles is None:
            return {"y": 0}
        return {"y": (100 * self.code(roles[0])
                      + 10 * self.code(roles[1])
                      + self.code(roles[2]))}
    """, helpers="""
        def roles(self, index, reverse):
            if reverse:
                table = {
                    1: ("Z", "P", "N"),
                    2: ("P", "Z", "N"),
                }
            else:
                table = {
                    1: ("Z", "N", "P"),
                    2: ("P", "N", "Z"),
                }
            return table.get(index, None)

        def code(self, role):
            if role == "P":
                return 1
            if role == "N":
                return 2
            return 3
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in range(16):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert [dict(row) for row in output.transactions] == \
            _native(source, value)[0]


def test_local_lookup_table_mutation_is_not_treated_as_a_static_read():
    source = _source("""
        table = {0: (1, 2), 1: (3, 4)}
        table[0] = (5, 6)
        return {"y": table[a & 1][0]}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "static-collection-mutated"


def test_empty_mapping_remains_a_record_builder_until_its_writes_define_it():
    source = _source("""
        out = {}
        out["y"] = a + 1
        return out
    """)
    _assert_agrees(source, [0, 3, 7])


def test_none_tests_narrow_nested_static_mapping_lookups():
    source = """\
TABLE = {
    0: {0: (1, 2), 1: (3,)},
    1: {0: (4,), 2: (5, 6)},
}
class M:
    def reset(self):
        pass
    def process(self, a):
        family = TABLE.get(a & 3, None)
        if family is None:
            return {"found": 0, "bit": 0}
        row = None if family is None else family.get((a >> 2) & 3, None)
        if row is None:
            return {"found": 0, "bit": 0}
        return {"found": 1, "bit": row[0]}
"""
    _assert_agrees(source, range(16), outputs=("found", "bit"))


def test_numeric_rom_selection_uses_ordinary_typed_equality():
    source = """\
ROM = (3, 5, 7, 9)
class M:
    def reset(self):
        pass
    def process(self, a):
        if ROM[a & 3] == ((a >> 2) * 2 + 3):
            y = 1
        else:
            y = 0
        return {"y": y}
"""
    _assert_agrees(source, range(16))


def test_fixed_record_items_are_eliminated_into_output_fields():
    source = ("TABLE = {\n"
              "    0: {'right': 2, 'left': 1},\n"
              "    1: {'right': 4, 'left': 3},\n"
              "}\n"
              "ZERO = {'right': 0, 'left': 0}\n"
              "class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a):\n"
              "        row = TABLE.get(a & 3, ZERO)\n"
              "        return {k: v + a for k, v in row.items()}\n")
    _assert_agrees(source, [0, 1, 2, 3], outputs=("left", "right"))


def test_record_items_follow_the_source_insertion_order():
    source = ("TABLE = {0: {'right': 2, 'left': 1}}\n"
              "ZERO = {'right': 0, 'left': 0}\n"
              "class M:\n"
              "    def reset(self):\n"
              "        self.n = 0\n"
              "    def tag(self, value):\n"
              "        self.n += 1\n"
              "        return 100 * self.n + value\n"
              "    def process(self, a):\n"
              "        row = TABLE.get(a, ZERO)\n"
              "        return {k: self.tag(v) for k, v in row.items()}\n")
    _assert_agrees(source, [0, 1], outputs=("left", "right"))


def test_record_items_refuse_a_path_dependent_insertion_order():
    source = ("TABLE = {\n"
              "    0: {'right': 2, 'left': 1},\n"
              "    1: {'left': 3, 'right': 4},\n"
              "}\n"
              "ZERO = {'right': 0, 'left': 0}\n"
              "class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a):\n"
              "        row = TABLE.get(a & 1, ZERO)\n"
              "        return {k: v for k, v in row.items()}\n")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["left", "right"])
    assert caught.value.code == "record-iteration-order-unproved"


def test_data_dependent_output_key_set_is_refused():
    source = _source(
        'return {f"out_{i}": i for i in range(3) if i < a}')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4], outputs=["out_0", "out_1", "out_2"])
    assert caught.value.code == "output-fields-data-dependent"


def test_chained_comparison_evaluates_middle_once_and_short_circuits():
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.calls = 0\n"
              "    def take(self, value):\n"
              "        self.calls += 1\n"
              "        return value\n"
              "    def process(self, a):\n"
              "        yes = 0 < self.take(a) < self.take(7)\n"
              "        return {\"y\": (10 if yes else 0) + self.calls}\n")
    _assert_agrees(source, [0, 1, 7])


def test_chained_comparison_captures_scalar_before_later_state_write():
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.value = 0\n"
              "        self.calls = 0\n"
              "    def replace(self):\n"
              "        self.value = 7\n"
              "        self.calls += 1\n"
              "        return 2\n"
              "    def process(self, a):\n"
              "        self.value = a\n"
              "        yes = 0 < self.value < self.replace()\n"
              "        return {'y': (10 if yes else 0) + self.calls}\n")
    _assert_agrees(source, [0, 1, 2, 7])


def test_same_local_spelling_has_a_plan_per_lexical_scope():
    source = _source("""
        xs = []
        xs.append(a)
        return {"y": self.bump(a) + xs[0]}
    """, helpers="""
        def bump(self, value):
            xs = [1]
            xs.append(value + 1)
            return xs[0] + xs[1]
    """)
    _assert_agrees(source, [0, 3, 7])


def test_each_helper_invocation_gets_its_own_sequence_state():
    source = _source("""
        return {"y": self.bump(a) + self.bump(a + 1)}
    """, helpers="""
        def bump(self, value):
            xs = []
            xs.append(value)
            return xs[0]
    """)
    _assert_agrees(source, [0, 3, 7])


def test_dynamic_outer_sequence_carries_fixed_recursive_elements():
    source = _source("""
        rows = []
        if a:
            rows.append([a, a + 1])
        return {"y": rows[0][1] if a else 0}
    """)
    _assert_agrees(source, [0, 1, 3, 7])


def test_nested_sequence_padding_has_the_planned_recursive_shape():
    source = _source("""
        rows = [[1, 2]]
        if a:
            rows.append([a, a + 1])
        return {"y": len(rows) * 100 + rows[-1][0] * 10 + rows[-1][1]}
    """)
    _assert_agrees(source, [0, 1, 3, 7])


def test_filtered_comprehension_can_build_fixed_rows_dynamically():
    source = _source("""
        rows = [[i, i + 1] for i in range(4) if i < a]
        y = 0
        for row in rows:
            y += 10 * row[0] + row[1]
        return {"y": y}
    """)
    _assert_agrees(source, [0, 1, 2, 4])


def test_dynamic_sequence_elements_must_have_one_recursive_shape():
    source = _source("""
        rows = []
        if a:
            rows.append([a, a + 1])
        else:
            rows.append([a])
        return {"y": rows[0][0]}
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "sequence-element-shape-disagrees"


def test_nested_dynamic_sequence_carries_per_cell_length_metadata():
    source = _source("""
        row = []
        if a:
            row.append(a)
        rows = []
        rows.append(row)
        return {"y": len(rows[0])}
    """)
    _assert_agrees(source, [0, 1, 3, 7])


@pytest.mark.parametrize("body", [
    """
        row = [1, 2]
        rows = []
        rows.append(row)
        row[0] = 9
        return {"y": rows[0][0]}
    """,
    """
        row = [1, 2]
        rows = []
        rows.append(row)
        rows.append(row)
        rows[0][0] = 9
        return {"y": rows[1][0]}
    """,
    """
        row = [1, 2]
        rows = [row for _ in range(2)]
        rows[0][0] = 9
        return {"y": rows[1][0]}
    """,
])
def test_dynamic_sequences_refuse_observable_element_aliases(body):
    """The fixed-array representation copies elements; Python lists alias."""
    source = _source(body)
    assert _native(source, 0) == ([{"y": 9}], "")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_a_fresh_row_per_iteration_is_not_an_alias():
    source = _source("""
        rows = []
        for i in range(2):
            row = [i, i + 1]
            rows.append(row)
        rows[0][0] = 9
        return {"y": rows[1][0]}
    """)
    _assert_agrees(source, [0, 1, 7])


def test_parallel_row_permutation_preserves_alias_freedom():
    source = _source("""
        rows = [[1, 2], [3, 4]]
        rows[0], rows[1] = rows[1], rows[0]
        rows[0][0] = a
        return {"y": rows[1][0]}
    """)
    _assert_agrees(source, [0, 1, 7])


@pytest.mark.parametrize("body", [
    """
        rows = [[1], [2]]
        held = rows[0]
        rows[0], rows[1] = rows[1], rows[0]
        rows[1][0] = a
        return {"y": held[0]}
    """,
    """
        rows = [[1], [2]]
        rows[0], rows[1] = rows[1], rows[1]
        rows[0][0] = a
        return {"y": rows[1][0]}
    """,
])
def test_parallel_assignment_does_not_hide_observable_aliases(body):
    source = _source(body)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_fresh_nested_rows_do_not_alias_an_existing_state_array():
    """A prior abstract step must not leave its locals in the next one."""
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.rows = [[0, 0], [0, 0]]\n"
              "    def process(self, a):\n"
              "        rows = []\n"
              "        for i in range(2):\n"
              "            row = []\n"
              "            for j in range(2):\n"
              "                row.append(a + i + j)\n"
              "            rows.append(row)\n"
              "        self.rows = [[rows[i][j] for j in range(2)]\n"
              "                     for i in range(2)]\n"
              "        return {'y': self.rows[1][1]}\n")
    _assert_agrees(source, [0, 1, 7])


def test_a_fixed_loop_stores_distinct_fresh_rows_in_state():
    """Each concrete loop iteration executes the allocation site anew."""
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.rows = [[0, 0], [0, 0], [0, 0]]\n"
              "    def process(self, a):\n"
              "        for i in range(0, 3):\n"
              "            self.rows[i] = [a + i, a - i]\n"
              "        self.rows[0][0] = 9\n"
              "        return {'y': self.rows[2][0]}\n")
    _assert_agrees(source, [0, 1, 7])


@pytest.mark.parametrize("body, readonly_fields", [
    ("""
        row = [a]
        self.left = row
        self.right = row
        return {"y": self.left[0]}
    """, True),
    ("""
        row = [a]
        self.rows = [row, row]
        return {"y": self.rows[0][0]}
    """, False),
])
def test_retained_sharing_respects_mutation_and_native_field_boundaries(body, readonly_fields):
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.left = [0]\n"
              "        self.right = [0]\n"
              "        self.rows = [[0], [0]]\n"
              "    def process(self, a):\n"
              + textwrap.indent(textwrap.dedent(body).strip(), " " * 8)
              + "\n")
    if readonly_fields:
        _assert_agrees(source, [0, 1, 7])
        return
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "array-assigned-by-reference"


def test_a_fresh_row_per_comprehension_iteration_is_not_an_alias():
    source = _source("""
        rows = [[i, i + 1] for i in range(2)]
        rows[0][0] = 9
        return {"y": rows[1][0]}
    """)
    _assert_agrees(source, [0, 1, 7])


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_fixed_tuple_comprehension_target_is_destructured_by_value(lowerer):
    source = _source("""
        values = [x * 10 + y
                  for x, y in ((a, a + 1), (a + 2, a + 3))]
        return {"y": values[0] + values[1]}
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in (0, 3, 7):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert [dict(row) for row in output.transactions] == _native(
            source, value)[0]


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_fixed_starred_comprehension_target_gets_one_value_list(lowerer):
    source = _source("""
        values = [x * 100 + middle[0] * 10 + z
                  for x, *middle, z in ((a, a + 1, a + 2, a + 3),)]
        return {"y": values[0]}
    """)
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in (0, 3, 7):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error
        assert [dict(row) for row in output.transactions] == _native(
            source, value)[0]


def test_read_only_sequence_aliases_need_no_refusal():
    source = _source("""
        row = [a, a + 1]
        rows = []
        rows.append(row)
        rows.append(row)
        return {"y": rows[0][0] + rows[1][1]}
    """)
    _assert_agrees(source, [0, 3, 7])


def test_helper_return_contract_sizes_recursive_sequence_elements():
    source = _source("""
        rows = []
        rows.append(self.pair(a))
        return {"y": rows[0][0] * 10 + rows[0][1]}
    """, helpers="""
        def pair(self, value):
            return [value, value + 1]
    """)
    _assert_agrees(source, [0, 3, 7])


def test_scalar_helper_inside_a_row_does_not_erase_its_shape():
    source = _source("""
        rows = []
        rows.append([self.bump(a), a])
        return {"y": rows[0][0] * 10 + rows[0][1]}
    """, helpers="""
        def bump(self, value):
            return value + 1
    """)
    _assert_agrees(source, [0, 3, 7])


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_fixed_helper_tuple_expands_once_into_positional_arguments(lowerer):
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.calls = 0\n"
              "    def pair(self, value):\n"
              "        self.calls += 1\n"
              "        return value, value + 1\n"
              "    def combine(self, x, y, seen):\n"
              "        return x * 100 + y * 10 + seen\n"
              "    def process(self, a):\n"
              "        return {'y': self.combine(*self.pair(a), "
              "self.calls)}\n")
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in (0, 3, 7):
        _state, output, _hits, error = interp.run_step(
            proc, {"a": value})
        assert not error
        assert [dict(row) for row in output.transactions] == _native(
            source, value)[0]


def test_runtime_length_sequence_cannot_supply_positional_arguments():
    source = _source("""
        values = []
        if a:
            values.append(a)
        return {"y": self.first(*values)}
    """, helpers="""
        def first(self, value):
            return value
    """)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "call-starred-argument-not-fixed"


def test_exact_comprehension_can_replace_fixed_sequence_state():
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.values = [0, 0]\n"
              "    def process(self, a):\n"
              "        updated = [a + i for i in range(2)]\n"
              "        self.values = updated\n"
              "        return {'y': self.values[0] + self.values[1]}\n")
    _assert_agrees(source, [0, 3, 7])


def test_fixed_append_loop_can_replace_fixed_sequence_state():
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.values = [0, 0]\n"
              "    def process(self, a):\n"
              "        updated = []\n"
              "        for i in range(2):\n"
              "            updated.append(a + i)\n"
              "        self.values = updated\n"
              "        return {'y': self.values[0] + self.values[1]}\n")
    _assert_agrees(source, [0, 3, 7])


def test_completed_fixed_inner_builder_can_be_appended_by_value():
    source = _source("""
        rows = []
        for i in range(2):
            row = []
            for j in range(2):
                row.append(a + i + j)
            rows.append(row)
        return {"y": rows[0][0] + rows[1][1]}
    """)
    _assert_agrees(source, [0, 3, 7])


def test_runtime_length_sequence_replaces_state_with_a_fixed_backing_store():
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.values = [0, 0]\n"
              "    def process(self, a):\n"
              "        updated = [0]\n"
              "        if a:\n"
              "            updated.append(a)\n"
              "        self.values = updated\n"
              "        return {'y': len(self.values)}\n")
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=4)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (0, 3, 0, 7):
        state, out, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


def test_nested_runtime_lengths_round_trip_through_state():
    source = """class M:
    def reset(self):
        self.rows = [[], []]

    def process(self, a):
        index = a & 1
        self.rows[index] = self.rows[index] + [a]
        return {"y": len(self.rows[index]) * 100 + self.rows[index][-1]}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=4)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    native = namespace["M"]()
    native.reset()
    state = None
    for value in (0, 1, 2, 3):
        state, out, _hits, error = interp.run_step(
            proc, {"a": value}, state)
        assert not error
        assert out.transactions == [native.process(value)]


def test_dynamic_state_audit_uses_logical_lengths_not_padding():
    source = """class M:
    def reset(self):
        self.rows = [[], []]

    def process(self, a):
        index = a & 1
        self.rows[index] = self.rows[index] + [a]
        return {"y": len(self.rows[index])}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=4)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]
    trace = [{"a": value} for value in (0, 1, 2)]

    assert audit.model_audit(proc, model, [trace]) == []

    native, state = model(), None
    native.reset()
    for inputs in trace:
        state, _out, _hits, error = interp.run_step(proc, inputs, state)
        assert not error
        native.process(**inputs)
    projection = proc.contract.projection("rows")
    assert projection.reconstruct(state) == native.rows
    with pytest.raises(StateProjectionError, match="cover every dynamic depth"):
        DynamicSequenceBinding(
            projection.name, projection.data_variable,
            projection.contract, ())
    assert proc.derived().contract.projection("rows") is projection
    restored = pickle.loads(pickle.dumps(proc))
    assert (restored.contract.projection("rows").identity
            == projection.identity)
    assert restored.contract.projection("rows").reconstruct(state) == native.rows

    padded = copy.deepcopy(state)
    padded[projection.data_variable][0][-1] = 99
    assert projection.reconstruct(padded) == native.rows

    changed = copy.deepcopy(state)
    changed[projection.data_variable][0][0] = 99
    assert projection.reconstruct(changed) != native.rows

    invalid = copy.deepcopy(state)
    _depth, length_variable = projection.length_variables[-1]
    invalid[length_variable][0] = proc.contract.projection(
        "rows").contract.element.maximum + 1
    with pytest.raises(StateProjectionError, match="outside its contract"):
        projection.reconstruct(invalid)


def test_fixed_length_helper_builder_can_be_embedded():
    source = _source("""
        rows = []
        rows.append(self.row(a))
        return {"y": rows[0][0] * 10 + rows[0][1]}
    """, helpers="""
        def row(self, value):
            built = []
            for i in range(2):
                built.append(value + i)
            return built
    """)
    _assert_agrees(source, [0, 3, 7])


@pytest.mark.parametrize("lowerer", [None, _Lowerer], ids=("graph", "term"))
def test_fixed_loop_postcondition_refines_a_helpers_backing_store(lowerer):
    source = _source("""
        values = self.row(a)
        return {"y": values[15]}
    """, helpers="""
        def row(self, value):
            built = []
            for i in range(16):
                built.append(value + i)
            return built
    """)

    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    for value in (0, 3, 7):
        _state, output, _hits, error = interp.run_step(
            proc, {"a": value})
        assert not error
        assert output.transactions == [{"y": value + 15}]


def test_helper_with_data_dependent_length_carries_per_cell_metadata():
    source = _source("""
        rows = []
        rows.append(self.row(a))
        return {"y": len(rows[0])}
    """, helpers="""
        def row(self, value):
            built = []
            if value:
                built.append(value)
            return built
    """)
    _assert_agrees(source, [0, 1, 3, 7])


def test_unused_ragged_helper_does_not_shrink_the_step_subset():
    source = _source("""
        return {"y": a}
    """, helpers="""
        def unused(self):
            rows = []
            rows.append([1])
            rows.append([1, 2])
            return rows
    """)
    _assert_agrees(source, [0, 3, 7])


def test_returned_path_does_not_plan_dead_sequence_mutations():
    source = _source("""
        values = []
        values.append(1)
        return {"y": values[0]}
        values.append([1])
    """)

    _assert_agrees(source, [0, 3, 7])


def test_lexically_shadowed_range_uses_the_helpers_length():
    source = ("def range(_count):\n"
              "    return [0, 1, 2]\n"
              "class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a):\n"
              "        values = []\n"
              "        for value in range(1):\n"
              "            values.append(value)\n"
              "        return {'y': len(values)}\n")
    _assert_agrees(source, [0, 3, 7])


def test_loop_target_keeps_the_nested_list_carrier():
    source = _source("""
        rows = [[1, 2]]
        y = 0
        for row in rows:
            joined = row + [3]
            y = joined[2]
        return {"y": y}
    """)
    _assert_agrees(source, [0, 3, 7])


def test_loop_target_does_not_turn_a_tuple_row_into_a_list():
    source = _source("""
        rows = [(1, 2)]
        for row in rows:
            joined = row + [3]
        return {"y": 0}
    """)
    assert _native(source, 0) == (None, "TypeError")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "sequence-concat-carrier-mismatch"


def test_break_preserves_a_helper_built_row_logical_length():
    source = _source("""
        rows = []
        rows.append(self.row(a))
        return {"y": len(rows[0])}
    """, helpers="""
        def row(self, value):
            built = []
            for i in range(2):
                if value and i:
                    break
                built.append(value + i)
            return built
    """)
    _assert_agrees(source, [0, 1, 3, 7])
