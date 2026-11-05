"""One immutable primitive registry is the authority for every Behavior stage."""

import ast
import itertools
import operator
from dataclasses import FrozenInstanceError, replace

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import lowering
from behave.sim.symbolic import encoder, interp, ir, primitives, ranges
from behave.sim.symbolic import typecheck as value_types

ARRAY_LIKE = {"arrayfill", "arraylit", "store", "ite"}
SCALAR = sorted(primitives.primitive_names() - ARRAY_LIKE)
WIDTH = 5
VALUES = [False, True, -8, -3, -1, 0, 1, 2, 3, 7]


def _registry_with(name, **changes):
    specs = [replace(spec, **changes) if spec.name == name else spec
             for spec in primitives.REGISTRY.specs]
    return primitives.PrimitiveRegistry(primitives.REGISTRY.version, specs)


def _registry_without(name):
    return primitives.PrimitiveRegistry(
        primitives.REGISTRY.version,
        [spec for spec in primitives.REGISTRY.specs if spec.name != name])


def _symbolic(op, values):
    expression = ir.Op(op, *(ir.Lit(value) for value in values))
    term = encoder._Encoder(None, {}, tag="registry").expr(
        expression, {})
    term = z3.simplify(term)
    if z3.is_true(term):
        return True
    if z3.is_false(term):
        return False
    return term.as_signed_long()


def _fits(value):
    return -(1 << (WIDTH - 1)) <= value < (1 << (WIDTH - 1))


def _admitted_literal_arguments(spec, values):
    terms = spec.signature.terms_for(len(values))
    kinds = []
    for term, value in zip(terms, values):
        if term is primitives.BOOLEAN:
            admitted = type(value) is bool
        elif term in {
                primitives.INTEGER, primitives.VALUE, primitives.WIDTH,
                primitives.SHIFT, primitives.ARRAY_LENGTH}:
            admitted = type(value) is int
        else:
            admitted = type(value) in (bool, int)
        if not admitted or primitives.argument_domain_error(
                term, literal=True, value=value) is not None:
            return False
        if term is primitives.SAME_SCALAR:
            kinds.append(type(value))
    return len(set(kinds)) <= 1


def test_the_registry_and_every_primitive_spec_are_immutable_and_versioned():
    assert primitives.REGISTRY.version == primitives.REGISTRY_VERSION
    assert len({spec.identity for spec in primitives.REGISTRY.specs}) == len(
        primitives.REGISTRY.specs)
    with pytest.raises(FrozenInstanceError):
        primitives.primitive("add").name = "sub"
    with pytest.raises(TypeError):
        primitives.REGISTRY._specs["add"] = primitives.primitive("sub")

    arguments = [primitives.VALUE]
    sources = [primitives.SourceBinding(
        primitives.SourceFamily.UNARY, ast.USub)]
    signature = primitives.TypeSignature(arguments, primitives.VALUE)
    spec = replace(
        primitives.primitive("neg"), signature=signature, sources=sources)
    arguments.clear()
    sources.clear()
    assert spec.signature.arguments == (primitives.VALUE,)
    assert len(spec.sources) == 1


@pytest.mark.parametrize("field", [
    "signature", "denotation", "range_transform", "symbolic",
])
def test_deleting_any_required_component_refuses_the_registry(field):
    with pytest.raises(primitives.PrimitiveRegistryError, match="primitive add"):
        replace(primitives.primitive("add"), **{field: None})


def test_a_source_operation_is_not_a_primitive():
    assert not (primitives.primitive_names() & primitives.SOURCE_OPERATIONS)
    assert "len" in primitives.SOURCE_OPERATIONS


def test_a_partial_source_binding_declares_domain_and_error_together():
    partial = primitives.primitive("fdiv")
    with pytest.raises(primitives.PrimitiveRegistryError, match="together"):
        replace(partial, source_domain=None)
    with pytest.raises(primitives.PrimitiveRegistryError, match="together"):
        replace(primitives.primitive("add"), source_error="Fault")


def test_the_ir_boundary_reads_the_registry_not_a_parallel_name_set(monkeypatch):
    body = ir.Assign("y", ir.Op("add", ir.Var("a"), ir.Lit(1)))
    assert ir.unlowered(body) == []
    monkeypatch.setattr(primitives, "REGISTRY", _registry_without("add"))
    assert [name for name, _expr in ir.unlowered(body)] == ["add"]


def test_the_frontend_reads_the_source_binding_from_the_registry(monkeypatch):
    expression, pending = lowering.lower_expression("a + b")
    assert expression.op == "add" and pending == []
    monkeypatch.setattr(
        primitives, "REGISTRY", _registry_with("add", sources=()))
    with pytest.raises(lowering.LoweringError) as caught:
        lowering.lower_expression("a + b")
    assert caught.value.code == "operator-unsupported"


def test_the_type_checker_reads_the_registered_signature(monkeypatch):
    expression = ir.Op("add", ir.Var("a"), ir.Var("b"))
    held = {"a": value_types.VALUE, "b": value_types.VALUE}
    faults = []
    assert value_types._of_expression(
        expression, held, faults, "test") == value_types.VALUE
    assert faults == []

    unary = primitives.TypeSignature((primitives.VALUE,), primitives.VALUE)
    monkeypatch.setattr(
        primitives, "REGISTRY", _registry_with("add", signature=unary))
    faults = []
    assert value_types._of_expression(
        expression, held, faults, "test") is None
    assert faults and "has 2 operands" in faults[0]


def test_the_interpreter_reads_the_registered_denotation(monkeypatch):
    expression = ir.Op("add", ir.Lit(7), ir.Lit(2))
    assert interp._Interp({}).expr(expression) == 9
    monkeypatch.setattr(
        primitives, "REGISTRY",
        _registry_with("add", denotation=operator.sub))
    assert interp._Interp({}).expr(expression) == 5


def test_the_range_planner_reads_the_registered_transformer(monkeypatch):
    expression = ir.Op("add", ir.Lit(2), ir.Lit(3))
    assert ranges.of_expression(expression, {}) == ranges.Range(5, 5)
    assert ranges.bounds_of_expression(expression, {}) == ranges.Bounds(5, 5)
    monkeypatch.setattr(
        primitives, "REGISTRY",
        _registry_with(
            "add", range_transform=lambda _a, _b:
            primitives.PrimitiveInterval(11, 11)))
    assert ranges.of_expression(expression, {}) == ranges.Range(11, 11)
    assert ranges.bounds_of_expression(expression, {}) == ranges.Bounds(11, 11)


def test_the_encoder_reads_the_registered_symbolic_operation(monkeypatch):
    assert _symbolic("add", (7, 2)) == 9
    monkeypatch.setattr(
        primitives, "REGISTRY",
        _registry_with(
            "add", symbolic=lambda _context, args, _width:
            args[0] - args[1]))
    assert _symbolic("add", (7, 2)) == 5


def test_partial_source_operators_bind_to_total_guarded_primitives():
    cases = (
        (ast.FloorDiv, "fdiv", "DivZero",
         primitives.SourceDomain.RIGHT_NONZERO, (7, 0)),
        (ast.Mod, "fmod", "DivZero",
         primitives.SourceDomain.RIGHT_NONZERO, (7, 0)),
        (ast.LShift, "shl", "NegativeShift",
         primitives.SourceDomain.RIGHT_NONNEGATIVE, (7, -1)),
        (ast.RShift, "shr", "NegativeShift",
         primitives.SourceDomain.RIGHT_NONNEGATIVE, (7, -1)),
    )
    for operator_type, name, error, domain, invalid in cases:
        spec = primitives.source_primitive(
            primitives.SourceFamily.BINARY, operator_type)
        assert spec.name == name
        assert spec.source_error == error
        assert spec.source_domain is domain
        assert spec.denotation(*invalid) == 0


@pytest.mark.parametrize("spec", primitives.REGISTRY.specs,
                         ids=lambda spec: spec.name)
def test_every_registered_denotation_has_its_declared_arity(spec):
    arity = 3 if spec.signature.variadic else len(spec.signature.arguments)
    assert spec.signature.terms_for(arity) is not None


@pytest.mark.parametrize("op", SCALAR)
def test_each_scalar_primitive_agrees_with_its_exact_encoding(op):
    spec = primitives.primitive(op)
    arity = len(spec.signature.arguments)
    checked = 0
    for values in itertools.product(VALUES, repeat=arity):
        if not _admitted_literal_arguments(spec, values):
            continue
        expected = spec.denotation(*values)
        if not _fits(expected):
            continue
        assert _symbolic(op, values) == expected, (op, values)
        checked += 1
    minimum = ((1 << arity) - 1
               if primitives.BOOLEAN in spec.signature.arguments
               else (4 if arity == 1 else 8))
    assert checked > minimum, (
        f"{op} was barely exercised: {checked} cases")


def test_array_primitives_share_concrete_type_range_and_symbolic_specs():
    literal = primitives.primitive("arraylit")
    store = primitives.primitive("store")
    conditional = primitives.primitive("ite")
    assert literal.denotation(1, 2) == [1, 2]
    assert store.denotation([1, 2], 0, 7) == [7, 2]
    assert conditional.denotation(0, [1], [2]) == [2]
    assert literal.signature.result is primitives.ARRAY
    assert store.signature.result is primitives.ARRAY
    assert conditional.signature.result is primitives.SAME
