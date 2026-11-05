"""Square-root regressions using platform sqrt and exact binary32 narrowing.

These random checks supplement the integer SoftFloat audit and directed
bit-pattern examples. rsqrt is tested as the specified two-operation composition.
"""
from __future__ import annotations

import math
import random
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
if str(PKG_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT.parent))

from behave.sim.behavior.fp_lib import (
    FP32_INF_POS,
    FP32_NAN,
    FP32_ONE,
    fp32_div,
    fp32_rsqrt,
    fp32_sqrt,
)


def fp32_to_pyfloat(u: int) -> float:
    return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]


def pyfloat_to_fp32_rne(x: float) -> int:
    """Narrow a finite square-root result to binary32 using RNE."""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def oracle_sqrt(u: int) -> int:
    """Compute the bit-exact RNE FP32 sqrt expected of fp_lib."""
    sign = (u >> 31) & 1
    exp = (u >> 23) & 0xFF
    mant = u & 0x7FFFFF
    # Mirror fp_lib's edge-case handling exactly:
    if exp == 0xFF and mant != 0:
        return FP32_NAN
    if exp == 0 and mant == 0:
        return u
    if sign == 1:
        return FP32_NAN
    if exp == 0xFF:
        return FP32_INF_POS
    # Positive finite, including subnormal: defer to platform sqrt.
    return pyfloat_to_fp32_rne(math.sqrt(fp32_to_pyfloat(u)))


# ── named known values ────────────────────────────────────────────────

def test_named_values():
    cases = {
        0x3F800000: 0x3F800000,  # sqrt(1.0) = 1.0
        0x40000000: 0x3FB504F3,  # sqrt(2.0)
        0x3F000000: 0x3F3504F3,  # sqrt(0.5)
        0x40800000: 0x40000000,  # sqrt(4.0) = 2.0
        0x3E800000: 0x3F000000,  # sqrt(0.25) = 0.5
        0x41800000: 0x40800000,  # sqrt(16.0) = 4.0
    }
    cases[0x42C80000] = pyfloat_to_fp32_rne(math.sqrt(100.0))  # sqrt(100) = 10
    cases[0x44800000] = pyfloat_to_fp32_rne(math.sqrt(1024.0))  # sqrt(1024) = 32

    for inp, expected in cases.items():
        got = fp32_sqrt(inp)
        assert got == expected, (
            f"sqrt({inp:#010x} = {fp32_to_pyfloat(inp)}): expected {expected:#010x} "
            f"({fp32_to_pyfloat(expected)}), got {got:#010x} ({fp32_to_pyfloat(got)})"
        )
    print(f"named values: {len(cases)} pass")


# ── edge cases ────────────────────────────────────────────────────────

def test_edges():
    # +0
    assert fp32_sqrt(0x00000000) == 0x00000000
    # -0 (sign preserved)
    assert fp32_sqrt(0x80000000) == 0x80000000
    # +Inf
    assert fp32_sqrt(FP32_INF_POS) == FP32_INF_POS
    # -Inf → NaN
    assert fp32_sqrt(0xFF800000) == FP32_NAN
    # NaN → NaN
    assert fp32_sqrt(0x7FC00001) == FP32_NAN
    assert fp32_sqrt(0xFFC00001) == FP32_NAN
    # Negative finite → NaN
    assert fp32_sqrt(0xBF800000) == FP32_NAN  # -1.0
    assert fp32_sqrt(0xC0490FDB) == FP32_NAN  # -π
    # Positive subnormal roots are normal. Negative nonzero roots are NaN.
    assert fp32_sqrt(0x00000001) == 0x1A3504F3
    assert fp32_sqrt(0x00000002) == 0x1A800000
    assert fp32_sqrt(0x007FFFFF) == 0x1FFFFFFF
    assert fp32_sqrt(0x80000001) == FP32_NAN
    assert fp32_sqrt(0x807FFFFF) == FP32_NAN
    print("edges: all pass")


# ── rsqrt edges ────────────────────────────────────────────────────────

def test_rsqrt_edges():
    # rsqrt(+1) = 1
    assert fp32_rsqrt(FP32_ONE) == FP32_ONE
    # rsqrt(+0) = +Inf
    assert fp32_rsqrt(0x00000000) == FP32_INF_POS
    # rsqrt(-0) = -Inf  (sqrt preserves -0; 1/-0 = -Inf)
    assert fp32_rsqrt(0x80000000) == 0xFF800000
    # rsqrt(+Inf) = +0
    assert fp32_rsqrt(FP32_INF_POS) == 0x00000000
    # rsqrt(NaN) = NaN
    assert fp32_rsqrt(0x7FC00001) == FP32_NAN
    # rsqrt(negative finite) = NaN
    assert fp32_rsqrt(0xBF800000) == FP32_NAN
    # sqrt(2**-148) = 2**-74, then its reciprocal is 2**74.
    assert fp32_rsqrt(0x00000002) == 0x64800000
    assert fp32_rsqrt(0x80000001) == FP32_NAN
    print("rsqrt edges: all pass")


# ── randomized ────────────────────────────────────────────────────────

def test_random(n: int = 50_000) -> None:
    rng = random.Random(0)
    fails: list[tuple[int, int, int]] = []
    for _ in range(n):
        bits = rng.getrandbits(32)
        expected = oracle_sqrt(bits)
        got = fp32_sqrt(bits)
        if got != expected:
            fails.append((bits, expected, got))
            if len(fails) < 5:
                print(f"  FAIL: input={bits:#010x} ({fp32_to_pyfloat(bits)}), "
                      f"expected={expected:#010x}, got={got:#010x}")
    assert not fails, f"{len(fails)} failures out of {n}"
    print(f"random sqrt: {n} cases pass")


def test_random_rsqrt(n: int = 20_000) -> None:
    """rsqrt is defined as fp32_div(1, fp32_sqrt(u)); verify the contract holds
    by recomputing against the same composition (no oracle vs platform — the
    platform's 1/sqrt is computed in FP64, two roundings against FP32 may
    diverge by a single ULP from our deliberate composition)."""
    rng = random.Random(1)
    for _ in range(n):
        bits = rng.getrandbits(32)
        expected = fp32_div(FP32_ONE, fp32_sqrt(bits))
        got = fp32_rsqrt(bits)
        assert got == expected, (
            f"rsqrt mismatch: input={bits:#010x}, "
            f"expected={expected:#010x}, got={got:#010x}"
        )
    print(f"random rsqrt: {n} cases pass")


if __name__ == "__main__":
    test_edges()
    test_named_values()
    test_rsqrt_edges()
    test_random()
    test_random_rsqrt()
    print("ALL PASS")
