"""Small exhaustive checks for the algebraic source-index separation proof."""

from itertools import combinations, product

import pytest

from behave.sim.rtl.source_index import affine_index


def scalar(width, signed=False, four_state=True):
    return {"kind": "PredefinedIntegerType", "bitWidth": width,
            "isSigned": signed, "isFourState": four_state}


def literal(value, type_):
    return {"kind": "IntegerLiteral", "value": str(value), "type": type_}


def variable(name, type_):
    return {"kind": "NamedValue", "symbol": name + " source_name", "type": type_}


def binary(op, left, right):
    return {"kind": "BinaryOp", "op": op, "left": left, "right": right, "type": left["type"]}


def _bits(node, valuation):
    kind = node["kind"]
    if kind == "IntegerLiteral":
        answer = int(node["value"])
    elif kind == "NamedValue":
        answer = valuation[node["symbol"].split()[0]]
    elif kind == "UnaryOp":
        answer = -_bits(node["operand"], valuation)
    else:
        left, right = _bits(node["left"], valuation), _bits(node["right"], valuation)
        answer = {"Add": lambda: left + right, "Subtract": lambda: left - right,
                  "Multiply": lambda: left * right}[node["op"]]()
    return answer & ((1 << node["type"]["bitWidth"]) - 1)


def _index(node, valuation):
    value, type_ = _bits(node, valuation), node["type"]
    return value - (1 << type_["bitWidth"]) if type_["isSigned"] and value >> (type_["bitWidth"] - 1) else value


@pytest.mark.parametrize("width,signed", tuple(product((2, 3, 4), (False, True))))
def test_separation_matches_every_small_bit_vector_valuation(width, signed):
    type_ = scalar(width, signed)
    i, j = variable("i", type_), variable("j", type_)
    one, two, three = (literal(value, type_) for value in (1, 2, 3))
    negative = {"kind": "UnaryOp", "op": "Minus", "operand": i, "type": type_}
    mixed = binary("Add", binary("Multiply", i, three), binary("Multiply", j, two))
    forms = [i, j, one, three, negative, mixed,
             binary("Add", i, one), binary("Add", one, i),
             binary("Add", binary("Subtract", i, one), two),
             binary("Add", negative, one), binary("Add", mixed, one),
             binary("Add", i, literal((1 << width) + 1, type_)),
             binary("Subtract", i, i)]
    proofs = [affine_index(node, {}, stable=lambda _: True) for node in forms]
    assert all(proof is not None for proof in proofs)
    assert proofs[6] == proofs[7] == proofs[8] == proofs[11]
    assert proofs[0].disjoint(proofs[6])
    assert not proofs[0].disjoint(proofs[1])
    valuations = [dict(zip(("i", "j"), values)) for values in product(range(1 << width), repeat=2)]
    for (left, pleft), (right, pright) in combinations(zip(forms, proofs), 2):
        if pleft.disjoint(pright):
            assert all(_index(left, values) != _index(right, values) for values in valuations)


def test_selectors_need_unchanged_compiler_symbols_not_matching_source_names():
    type_ = scalar(8)
    i, j = variable("scope1", type_), variable("scope2", type_)
    first = affine_index(i, {}, stable=lambda _: True)
    second = affine_index(binary("Add", j, literal(1, type_)), {}, stable=lambda _: True)
    assert not first.disjoint(second)
    assert affine_index(i, {}, stable=lambda _: False) is None


@pytest.mark.parametrize("source,destination", [
    (scalar(4), scalar(3)), (scalar(4), scalar(5)),
    (scalar(4, four_state=True), scalar(4, four_state=False)),
])
def test_width_changes_and_unknown_to_concrete_conversions_are_not_erased(source, destination):
    cast = {"kind": "Conversion", "operand": variable("i", source), "type": destination}
    assert affine_index(cast, {}, stable=lambda _: True) is None


def test_unknown_literals_and_nonlinear_indices_have_no_affine_certificate():
    type_ = scalar(4)
    unknown = {"kind": "IntegerLiteral", "value": "4'bx", "type": type_}
    assert affine_index(unknown, {}, stable=lambda _: True) is None
    assert affine_index(binary("Multiply", variable("i", type_), variable("j", type_)),
                        {}, stable=lambda _: True) is None


def test_literal_indices_keep_signed_integer_meaning():
    signed = affine_index(literal(-1, scalar(4, True)), {}, stable=lambda _: True)
    unsigned = affine_index(literal(15, scalar(4)), {}, stable=lambda _: True)
    wider = affine_index(literal(-1, scalar(8, True)), {}, stable=lambda _: True)
    assert signed.disjoint(unsigned)
    assert not signed.disjoint(wider)
