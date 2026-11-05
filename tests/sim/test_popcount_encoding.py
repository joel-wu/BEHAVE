"""Population-count circuits preserve signed magnitude at every source width."""

import random

import pytest
import z3

from behave.sim.symbolic.encoder import _Encoder


def _old_count(a):
    wide, magnitude = _Encoder._magnitude(a)
    out = z3.BitVecVal(0, a.size())
    for index in range(wide.size()):
        out += z3.If(z3.Extract(index, index, magnitude) == 1,
                     z3.BitVecVal(1, a.size()), z3.BitVecVal(0, a.size()))
    return out


@pytest.mark.parametrize("width", range(1, 10))
def test_popcount_exhaustive_signed_carrier_agrees_with_python_and_old_circuit(width):
    engine = _Encoder(None, {})
    source = z3.BitVec("a", width)
    actual = engine.popcount(source, width)
    old = _old_count(source)
    assert actual.size() == old.size() == width
    for bits in range(1 << width):
        signed = bits if bits < 1 << (width - 1) else bits - (1 << width)
        expected = signed.bit_count()
        value = z3.BitVecVal(bits, width)
        assert z3.simplify(z3.substitute(actual, (source, value))).as_long() == expected
        assert z3.simplify(z3.substitute(old, (source, value))).as_long() == expected


@pytest.mark.parametrize("width", [15, 16, 17, 31, 32, 63, 64, 127, 128, 255, 256, 257, 1024])
def test_wide_popcount_extremes_and_irregular_tree_sizes(width):
    engine = _Encoder(None, {})
    source = z3.BitVec("a", width)
    actual = engine.popcount(source, width)
    rng = random.Random(width)
    samples = {0, 1, (1 << width) - 1, 1 << (width - 1), (1 << (width - 1)) - 1}
    samples.update(rng.getrandbits(width) for _ in range(12))
    for bits in samples:
        signed = bits if bits < 1 << (width - 1) else bits - (1 << width)
        got = z3.simplify(z3.substitute(actual, (source, z3.BitVecVal(bits, width))))
        assert got.as_long() == signed.bit_count()


def test_count_tree_addition_width_is_linear_not_quadratic():
    width = 256
    expr = _Encoder(None, {}).popcount(z3.BitVec("a", width), width)
    seen, stack, widths = set(), [expr], []
    while stack:
        node = stack.pop()
        if node.get_id() in seen:
            continue
        seen.add(node.get_id())
        if node.decl().kind() == z3.Z3_OP_BADD:
            widths.append(node.size())
        stack.extend(node.children())
    assert len(widths) == width
    assert max(widths) == (width + 1).bit_length()
    assert sum(widths) < 4 * (width + 1)


@pytest.mark.solver
@pytest.mark.parametrize("negative", [False, True])
def test_255_ones_has_exact_signed_magnitude_witness(negative):
    source = z3.BitVec("a", 256)
    value = -source if negative else source
    count = _Encoder(None, {}).popcount(value, 9)
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)  # Explicit diagnostic budget, not a runtime policy.
    solver.add(source >= 0, count == 255)
    assert solver.check() == z3.sat
    witness = solver.model().eval(source).as_long()
    assert witness == (1 << 255) - 1
    assert (-witness if negative else witness).bit_count() == 255
