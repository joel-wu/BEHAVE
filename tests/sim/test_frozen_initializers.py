"""Finite initialization uses lexical certificates, not host candidate code."""

import ast

import pytest

from behave.sim.behavior.source_rom import evaluate_frozen, freeze_value, update_frozen_table
from behave.sim.behavior.source_values import SourceValueBudget, SourceValueBudgetExceeded


def frozen(expression, values=None, *, builtins=('list', 'tuple', 'range'), **kwargs):
    values = values or {}
    return evaluate_frozen(
        ast.parse(expression, mode='eval').body,
        lambda node: values.get(node.id) if isinstance(node, ast.Name) else None,
        builtin_of=lambda call: call.func.id if isinstance(call.func, ast.Name)
        and call.func.id in builtins else None, **kwargs)


@pytest.mark.parametrize('expression,expected', [
    ('list((1,3,5))', [1, 3, 5]),
    ('tuple([1,3,5])', (1, 3, 5)),
    ('list()', []),
    ('tuple()', ()),
    ('list(range(5,0,-2))', [5, 3, 1]),
    ('[i*2+1 for i in range(3)]', [1, 3, 5]),
    ('[x+y for (x,y) in ((1,2),(3,4))]', [3, 7]),
    ('[x+y for x in range(3) for y in range(x) if y == 0]', [1, 2]),
    ('[x for x in (False,1,2) if x]', [1, 2]),
    ('[i for i in range(i)]', [0, 1, 2]),
    ('[[i for i in range(i)] for i in range(3)]', [[], [0], [0, 1]]),
    ('[x for x in range(2) for range in (3,)]', [0, 1]),
])
def test_closed_constructors_and_comprehensions(expression, expected):
    result = frozen(expression, {'i': 3})
    assert result is not None
    assert result.to_source_value() == expected
    assert type(result.to_source_value()) is type(expected)


@pytest.mark.parametrize('expression', [
    'list(range(3))', '[i for i in range(3)]', 'tuple((1,2))',
])
def test_constructor_requires_builtin_certificate(expression):
    assert frozen(expression, builtins=()) is None


@pytest.mark.parametrize('expression', [
    '[x for x in (0,1) for y in range(2) for range in (3,)]',
    '[list((1,2)) for list in (3,)]',
    '[i for i in range(3) if missing]',
    'list(range(3,5,0))', 'list(1)', 'list(x=(1,2))',
    '[x for (x,y) in ((1,),)]', 'list(range(10**50))',
])
def test_unknown_invalid_and_shadowed_initializers_stay_unknown(expression):
    assert frozen(expression) is None


def test_local_name_cannot_resolve_as_module_helper():
    fn = ast.parse('def f(x): return x + 1').body[0]
    assert frozen('[f(0) for f in (1,2)]', function_of=lambda call: fn) is None


def test_helper_reads_module_globals_not_comprehension_locals():
    fn = ast.parse('def f(x): return i + x').body[0]
    result = frozen('[f(i) for i in range(3)]', {'i': 20}, function_of=lambda call: fn)
    assert result.to_source_value() == [20, 21, 22]


def test_outer_compound_fact_cannot_override_a_local_binding():
    node = ast.parse('[i+1 for i in range(3)]', mode='eval').body
    result = evaluate_frozen(node, lambda n: 100 if isinstance(n, ast.BinOp) else None,
                             builtin_of=lambda call: call.func.id)
    assert result.to_source_value() == [1, 2, 3]


def test_materialization_and_nested_iterations_share_work_budget():
    assert frozen('list(range(1000))', max_nodes=30) is None
    assert frozen('[x+y for x in range(100) for y in range(100)]', max_nodes=1000) is None
    with pytest.raises(SourceValueBudgetExceeded):
        frozen('list(range(100))', budget=SourceValueBudget(20))


def test_sequence_copy_retains_nested_source_carriers():
    result = frozen('list(T)', {'T': freeze_value(([1,2], (3,4)))})
    assert result.to_source_value() == [[1, 2], (3, 4)]


@pytest.mark.parametrize('loop', [
    'for i in range(3):\n TABLE[i] = i + 1',
    'for i in range(2,-1,-1):\n TABLE[i-3] = i + 1',
    'for i in range(3):\n for j in range(2):\n  TABLE[i] = i + 1',
    'for i in range(3):\n if i:\n  TABLE[i] = TABLE[i-1] + 1\n else:\n  TABLE[i] = 1',
])
def test_finite_initialization_writes_in_source_order(loop):
    result = update_frozen_table(ast.parse(loop).body[0], 'TABLE', freeze_value([0]*3),
                                 lambda node: None, builtin_of=lambda call: call.func.id)
    assert result.to_source_value() == [1, 2, 3]


@pytest.mark.parametrize('body', [
    'ALIAS = TABLE\n ALIAS[i] = 7',
    'TABLE.append(i)', 'TABLE[i+2] = i', 'TABLE[i] = [i]',
    'TABLE[i] += 1', 'break', 'del TABLE[i]',
])
def test_unrepresented_loop_mutation_does_not_make_a_table_fact(body):
    loop = ast.parse('for i in range(3):\n ' + body).body[0]
    assert update_frozen_table(loop, 'TABLE', freeze_value([0]*3), lambda node: None,
                               builtin_of=lambda call: call.func.id) is None


def test_initializer_helper_cannot_read_a_stale_loop_global():
    fn = ast.parse('def adjust(x): return i + x').body[0]
    loop = ast.parse('for i in range(3):\n TABLE[i] = adjust(0)').body[0]
    assert update_frozen_table(loop, 'TABLE', freeze_value([0]*3),
                               lambda node: 100 if isinstance(node, ast.Name) and node.id == 'i' else None,
                               function_of=lambda call: fn, builtin_of=lambda call: call.func.id) is None


@pytest.mark.parametrize('value,expected', [(0, 0), (7, 3), (-8, 4), (True, 1)])
def test_bit_length_requires_an_exact_primitive_receiver(value, expected):
    assert frozen('x.bit_length()', {'x': value}).payload == expected
    assert frozen('x.bit_length(1)', {'x': value}) is None
    assert frozen('x.bit_length()', {'x': freeze_value([value])}) is None


@pytest.mark.parametrize('statement', [
    'assert x == 0', 'global OTHER\n OTHER = x', 'x[0] = 3',
    'OTHER.append(x)', 'raise ValueError()',
])
def test_helper_path_must_complete_without_unrepresented_effects(statement):
    fn = ast.parse('def f(x):\n ' + statement + '\n return 7').body[0]
    assert frozen('f(1)', function_of=lambda call: fn) is None


def test_closed_integer_helper_checks_assertions_and_primitive_method():
    fn = ast.parse('def f(x):\n assert x > 0\n return x.bit_length()').body[0]
    result = frozen('[f(x) for x in (1,3,8)]', function_of=lambda call: fn)
    assert result.to_source_value() == [1, 2, 4]
