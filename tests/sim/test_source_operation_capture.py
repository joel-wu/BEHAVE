"""Independent native-operation oracles; these are not search qualification."""

import ast
import operator
from dataclasses import replace
from itertools import product

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.source_operations import instrument_operations
from behave.sim.behavior.source_plan import plan_source


def _load(source):
    plan = plan_source(source_model(source), source)
    events = []
    observed, _, hits = instrumented.load_source(
        source, goal_plan=plan,
        operation_observer=lambda site, operands, result: events.append((site, operands, result)))
    original, _, original_hits = instrumented.load_source(source, goal_plan=plan)
    return plan, observed["M"], original["M"], events, hits, original_hits


@pytest.mark.parametrize("spelling,expected", [
    ("a + b", operator.add), ("a - b", operator.sub),
    ("a * b", operator.mul), ("a // b", operator.floordiv),
    ("a % b", operator.mod), ("a & b", operator.and_),
    ("a | b", operator.or_), ("a ^ b", operator.xor),
    ("a << b", operator.lshift), ("a >> b", operator.rshift),
])
def test_native_binary_values_equal_the_independent_operator(spelling, expected):
    source = f"class M:\n def process(self, a, b): return {{'y': {spelling}}}\n"
    plan, observed, original, events, hits, original_hits = _load(source)
    assert len(plan.operations) == 1
    for a, b in product((-257, -1, 0, 1, 257), (1, 2, 7)):
        events.clear()
        assert observed().process(a, b) == original().process(a, b) == {"y": expected(a, b)}
        assert events == [(plan.operations[0], (a, b), expected(a, b))]
        assert hits == original_hits


@pytest.mark.parametrize("spelling,expected", [
    ("+a", operator.pos), ("-a", operator.neg), ("~a", operator.invert),
    ("not a", operator.not_),
])
def test_native_unary_preserves_value_and_type(spelling, expected):
    source = f"class M:\n def process(self, a): return {{'y': {spelling}}}\n"
    plan, observed, _, events, _, _ = _load(source)
    for value in (-257, 0, 257):
        events.clear()
        result = observed().process(value)["y"]
        assert type(result) is type(expected(value))
        assert events == [(plan.operations[0], (value,), expected(value))]


def test_same_line_nested_operators_execute_in_dependency_order_and_only_once():
    source = """class M:
 def process(self, a, b): return {'y': (a + b) * (a - b)}
"""
    plan, observed, _, events, _, _ = _load(source)
    assert len(plan.operations) == 3
    assert observed().process(9, 4) == {"y": 65}
    assert [(site.operator, operands, result) for site, operands, result in events] == [
        ("Add", (9, 4), 13), ("Sub", (9, 4), 5), ("Mult", (13, 5), 65)]


def test_skipped_expression_emits_no_operation_and_keeps_structural_hits():
    source = """class M:
 def process(self, a, b):
  return {'y': (a and (b // a)) if b else (a + 7)}
"""
    _, observed, original, events, hits, original_hits = _load(source)
    for a, b in product((0, 1, 2), repeat=2):
        events.clear()
        assert observed().process(a, b) == original().process(a, b)
        assert hits == original_hits
        assert [(site.operator, operands, result) for site, operands, result in events] == (
            [("Add", (a, 7), a + 7)] if not b else
            [("FloorDiv", (b, a), b // a)] if a else [])


def test_only_reachable_runtime_closure_not_construction_or_annotations():
    source = """class M:
 def __init__(self): self.x = 1 + 2
 def reset(self): self.x = 3 + 4
 def unused(self, a): return a + 50
 def helper(self, a): return a * 2
 def process(self, a):
  x: (a + 99) = self.helper(a)
  return {'y': x + 7}
"""
    plan, observed, _, events, _, _ = _load(source)
    assert [(site.line, site.operator) for site in plan.operations] == [(5, "Mult"), (8, "Add")]
    model = observed()
    model.reset()
    assert not events
    assert model.process(3) == {"y": 13}
    assert [(site.line, operands, result) for site, operands, result in events] == [
        (5, (3, 2), 6), (8, (6, 7), 13)]


def test_comprehension_iterable_and_callback_keep_original_scope():
    source = """class M:
 def process(self, a):
  values = [a + k for k in range(a + 1)]
  return {'y': sorted(values, key=lambda value: -value)[0]}
"""
    plan, observed, original, events, hits, original_hits = _load(source)
    assert len(plan.operations) == 3
    assert observed().process(2) == original().process(2) == {"y": 4}
    assert [(site.operator, operands, result) for site, operands, result in events] == [
        ("Add", (2, 1), 3), ("Add", (2, 0), 2), ("Add", (2, 1), 3),
        ("Add", (2, 2), 4), ("USub", (2,), -2), ("USub", (3,), -3),
        ("USub", (4,), -4)]
    assert hits == original_hits


@pytest.mark.parametrize("expression,args,error", [
    ("(a + 7) // b", (1, 0), ZeroDivisionError),
    ("(a + 7) << b", (1, -1), ValueError),
    ("a // (b << a)", (-1, 0), ValueError),
])
def test_partial_failure_keeps_original_source_site_and_no_false_completion(expression, args, error):
    source = f"class M:\n def process(self, a, b): return {{'y': {expression}}}\n"
    plan, observed, original, events, hits, original_hits = _load(source)
    attributed = []
    for model in (original, observed):
        with pytest.raises(error) as caught:
            model().process(*args)
        attributed.append(instrumented.attribute_safety_failure(caught.value, plan.safety_sites))
    assert attributed[0] is not None and attributed[0] == attributed[1]
    assert hits == original_hits
    assert [(site.operator, operands, result) for site, operands, result in events] == (
        [("Add", (1, 7), 8)] if expression.startswith("(a +") else [])


def test_operands_execute_once_before_operator_and_result_identity_is_unchanged():
    source = """class M:
 def process(self, a): return {'y': a.left() + a.right()}
"""
    # The capture primitive also preserves object operators. This does NOT
    # admit opaque object calls to symbolic analysis or declare integer bins.
    _, observed, _, events, _, _ = _load(source)
    calls, result = [], object()

    class Left:
        def __add__(self, other):
            calls.append(("add", other))
            return result

    left, right = Left(), object()

    class Inputs:
        def left(self):
            calls.append("left")
            return left

        def right(self):
            calls.append("right")
            return right

    assert observed().process(Inputs())["y"] is result
    assert calls == ["left", "right", ("add", right)]
    assert len(events) == 1
    assert events[0][1] == (left, right) and events[0][2] is result


def test_hygienic_names_do_not_shadow_user_parameters_or_walrus_targets():
    source = """class M:
 def process(self, _behave_operation, _behave_left, _behave_right):
  value = (x := _behave_operation) + _behave_left
  return {'y': value + x + _behave_right}
"""
    _, observed, original, events, hits, original_hits = _load(source)
    assert observed().process(2, 3, 7) == original().process(2, 3, 7) == {"y": 14}
    assert len(events) == 3 and hits == original_hits


def test_augmented_assignment_is_not_rewritten_to_binary_assignment():
    source = """class M:
 def process(self, a, b):
  original = a
  a += [b]
  return {'y': original}
"""
    plan, observed, _, events, _, _ = _load(source)
    values = [1]
    assert not plan.operations
    assert observed().process(values, 2)["y"] is values
    assert values == [1, 2] and not events


def test_declared_operation_missing_from_source_is_an_instrumentation_error():
    source = "class M:\n def process(self, a): return {'y': a + 1}\n"
    plan = plan_source(source_model(source), source)
    bad = replace(plan, operations=(replace(plan.operations[0], line=200),))
    with pytest.raises(ValueError, match="omitted a declared source occurrence"):
        instrumented.load_source(source, goal_plan=bad, operation_observer=lambda *_args: None)


def test_source_ast_is_not_modified_when_inventorying_operations():
    source = "class M:\n def process(self, a): return {'y': -(a + 1)}\n"
    model = source_model(source)
    before = ast.dump(model.tree, include_attributes=True)
    plan = plan_source(model, source)
    assert len(plan.operations) == 2
    assert ast.dump(model.tree, include_attributes=True) == before


def test_generated_helpers_are_installed_once_and_leave_future_semantics_alone():
    source = '''"""original module documentation"""
from __future__ import annotations
class M:
 def process(self, a: unresolved_type): return {'y': -(a + 1)}
'''
    plan = plan_source(source_model(source), source)
    tree, hook = instrument_operations(ast.parse(source), plan.operations)
    assert not any(isinstance(node, ast.Lambda) for node in ast.walk(tree))
    assert len([node for node in tree.body if isinstance(node, ast.FunctionDef)]) == 2
    # Compile this fixed fixture directly: admission of a __future__ import is
    # a separate source-loader rule, not part of the capture transform's scope.
    namespace = {hook: lambda _site, _operands, result: result}
    exec(compile(tree, "behavior.py", "exec", dont_inherit=True), namespace)  # noqa: S102 - fixed test fixture
    assert namespace['__doc__'] == "original module documentation"
    assert namespace["M"].process.__annotations__ == {"a": "unresolved_type"}
    functions = {name: value for name, value in namespace.items()
                 if name.startswith("_behave_operation_")}
    for a in range(100):
        assert namespace["M"]().process(a) == {"y": -(a + 1)}
    assert all(namespace[name] is function for name, function in functions.items())
