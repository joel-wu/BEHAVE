"""Complete finite state transport cannot invent, omit or coerce components."""

import pytest
import z3

from behave.sim.rtl.symbolic_state import (
    equal_component,
    image_constraints,
    image_terms,
    read_component,
    state_mapping,
)


@pytest.mark.parametrize('private', [False, True])
def test_batched_fixed_terms_match_individual_simultaneous_substitution(private, monkeypatch):
    from behave.sim.rtl.symbolic_state import fold_fixed_terms

    context = z3.Context() if private else None
    x, y = z3.BitVecs('fixed_x fixed_y', 9, ctx=context)
    b = z3.Bool('fixed_b', ctx=context)
    # x -> y is simultaneous, not transitively rewritten using y -> 3.
    pairs = ((x, y), (y, z3.BitVecVal(3, 9, ctx=context)),
             (b, z3.BoolVal(False, ctx=context)))
    roots = (x, x + y, b, z3.If(b, x, y), z3.SignExt(7, x), x, b)
    expected = tuple(z3.simplify(z3.substitute(term, *pairs)) for term in roots)
    original, calls = z3.substitute, []

    def substitute(term, *bindings):
        calls.append(len(bindings))
        return original(term, *bindings)

    monkeypatch.setattr(z3, 'substitute', substitute)
    actual = fold_fixed_terms(roots, pairs)
    assert calls == [len(pairs)]
    assert all(left.eq(right) for left, right in zip(actual, expected, strict=True))
    assert actual[0].eq(y) and z3.is_false(actual[2])
    assert fold_fixed_terms((), pairs) == ()
    assert calls == [len(pairs)]


def test_batched_fixed_terms_honors_cancellation_without_publishing():
    from behave.sim.rtl.symbolic_state import fold_fixed_terms

    calls = []

    def check():
        calls.append(None)
        if len(calls) == 2:
            raise RuntimeError('cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        fold_fixed_terms((z3.Int('fixed_cancel'),), (), check=check)


def test_nested_record_pins_and_enumerates_the_same_complete_state():
    b, i, v = z3.Bool("b"), z3.Int("i"), z3.BitVec("v", 8)
    image = {"flags": (b,), "nested": {"counter": i, "word": v}, "empty": ()}
    value = [("flags", [True]), ("nested", [("counter", 7), ("word", 255)]), ("empty", [])]
    solver = z3.Solver()
    solver.add(*image_constraints(image, value, "state"))
    assert solver.check() == z3.sat
    model = solver.model()
    assert read_component(model, b) is True
    assert read_component(model, i) == 7
    assert read_component(model, v) == 255
    assert {term.get_id() for term in image_terms(image)} == {b.get_id(), i.get_id(), v.get_id()}
    for term in image_terms(image):
        solver.push()
        solver.add(term != model.eval(term))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize("sort,value", [
    (z3.BoolSort(), 2), (z3.BoolSort(), 1.0), (z3.BoolSort(), "true"),
    (z3.IntSort(), True), (z3.IntSort(), 1.0), (z3.BitVecSort(8), True),
    (z3.BitVecSort(8), -1), (z3.BitVecSort(8), 256),
])
def test_scalar_constraints_reject_coercion_and_modular_truncation(sort, value):
    with pytest.raises((TypeError, ValueError)):
        equal_component(z3.Const("value", sort), value, "state.value")


@pytest.mark.parametrize("value", [{"x": 1}, {"b": 1, "x": 2}, {}, [("b", 1), ("b", 0)]])
def test_image_rejects_missing_extra_and_duplicate_names(value):
    with pytest.raises((TypeError, ValueError)):
        image_constraints({"b": z3.Bool("b")}, value, "state")


def test_transport_rejects_non_string_record_keys():
    with pytest.raises(TypeError):
        state_mapping({1: True}, "state")


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [0, 1, 1000])
def test_boolean_junctions_preserve_exact_ast_order_and_duplicates(private, count):
    from behave.sim.rtl.symbolic_state import assertion_conjunction, assertion_disjunction

    context = z3.Context() if private else None
    x, y = z3.Bools('x y', ctx=context)
    terms = ([x, y, x, z3.BoolVal(False, ctx=context)] * count)
    for build, ordinary in ((assertion_conjunction, z3.And), (assertion_disjunction, z3.Or)):
        actual = build(iter(terms))
        assert actual.eq(ordinary(*terms))


@pytest.mark.parametrize('build', ['assertion_conjunction', 'assertion_disjunction'])
def test_boolean_junctions_reject_mixed_context_and_honor_cancellation(build):
    from behave.sim.rtl import symbolic_state

    operation = getattr(symbolic_state, build)
    with pytest.raises(ValueError):
        operation([z3.Bool('x'), z3.Bool('y', ctx=z3.Context())])
    with pytest.raises(ValueError):
        operation([z3.Int('x')])
    def cancel():
        raise InterruptedError('cancelled junction')
    with pytest.raises(InterruptedError):
        operation([z3.Bool('x')], check=cancel)


@pytest.mark.parametrize('private', [False, True])
def test_scalar_difference_matches_z3_and_shares_only_typed_literals(private):
    from behave.sim.rtl.symbolic_state import scalar_difference

    context = z3.Context() if private else None
    literals = {}
    terms = (z3.Bool('b', ctx=context), z3.Int('i', ctx=context),
             z3.BitVec('v', 8, ctx=context), z3.BitVec('w', 16, ctx=context))
    for term in terms:
        for value in ((False, True) if z3.is_bool(term) else (-1, 0, 1, 256)):
            expected = term != value
            assert scalar_difference(term, value, literals).eq(expected)
            count = len(literals)
            assert scalar_difference(term, value, literals).eq(expected)
            assert len(literals) == count
    numeral = z3.IntVal(7, ctx=context)
    assert scalar_difference(terms[1], numeral, literals).eq(terms[1] != numeral)
    other = z3.Context()
    assert scalar_difference(z3.Int('i', ctx=other), 1, literals).ctx == other
