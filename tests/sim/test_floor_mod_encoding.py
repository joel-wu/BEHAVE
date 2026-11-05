"""Floor modulo is a native signed-modulo circuit, not quotient times divisor."""
import random

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import closure_proof, encoder, ranges


@pytest.mark.parametrize("width", range(1, 9))
def test_native_modulo_equals_algebraic_encoding_for_all_bit_patterns(width):
    a, b = z3.BitVecs(f"mod_a_{width} mod_b_{width}", width)
    old = a - b * encoder._floor_div(a, b)
    new = encoder._floor_mod(a, b)
    assert new.decl().kind() == z3.Z3_OP_BSMOD
    solver = z3.Solver()
    solver.set(timeout=20_000)
    solver.add(old != new)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("width", (2, 8, 16, 32, 64, 128))
def test_modulo_totalization_and_signed_corners_match_python(width):
    edge = 1 << (width - 1)
    values = {-edge, -edge + 1, -1, 0, 1, edge - 1}
    rng = random.Random(width)
    values.update(rng.randrange(-edge, edge) for _ in range(10))
    for a in values:
        for b in values:
            term = encoder._Encoder.floor_mod(z3.BitVecVal(a, width), z3.BitVecVal(b, width))
            expected = a % b if b else 0
            assert z3.simplify(term).as_signed_long() == expected


@pytest.mark.parametrize("divisor", (-128, -9, -1, 1, 9, 127))
@pytest.mark.parametrize("private", (False, True))
def test_native_modulo_hull_is_proved_without_numerator_assumptions(divisor, private):
    context = z3.Context() if private else z3.main_ctx()
    a = z3.BitVec("mod_hull_a", 8, ctx=context)
    b = z3.BitVecVal(divisor, 8, ctx=context)
    proof = closure_proof._SyntacticClosureProof({})
    expected = ranges.Range(0, divisor - 1) if divisor > 0 else ranges.Range(divisor + 1, 0)
    assert proof.signed_hull(encoder._floor_mod(a, b)) == expected
    assert proof.signed_hull(a - b * encoder._floor_div(a, b)) == expected
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(a % b < expected.lo, a % b > expected.hi))
    assert solver.check() == z3.unsat


def test_zero_or_variable_divisor_has_no_unjustified_modulo_bound():
    a, b = z3.BitVecs("mod_unknown_a mod_unknown_b", 8)
    proof = closure_proof._SyntacticClosureProof({})
    assert proof._floor_mod_hull(a % b) is None
    assert proof._floor_mod_hull(a % z3.BitVecVal(0, 8)) is None
