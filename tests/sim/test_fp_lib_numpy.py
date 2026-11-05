"""Compare integer FP helpers with NumPy binary32 arithmetic.

The oracle uses RNE and gradual underflow, with the API's canonical arithmetic
NaN. It does not flush either operands or results. Directed cases cover signed
zero, subnormals, the minimum normal, infinity, and NaNs.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
REPO_ROOT = PKG_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from behave.sim.behavior.fp_lib import (
    FP32_INF_NEG,
    FP32_INF_POS,
    FP32_NAN,
    FP32_ONE,
    bf16_to_fp32,
    fp32_abs,
    fp32_add,
    fp32_cmp,
    fp32_div,
    fp32_is_inf,
    fp32_is_nan,
    fp32_is_zero,
    fp32_mul,
    fp32_neg,
    fp32_rsqrt,
    fp32_sqrt,
    fp32_sub,
    fp32_to_bf16,
    fp32_to_int32_sat,
    int32_to_fp32,
)

MASK32 = 0xFFFFFFFF
SIGN32 = 0x80000000
EXP32_MASK = 0x7F800000
MANT32_MASK = 0x007FFFFF
INT32_MAX = 0x7FFFFFFF
INT32_MIN = -0x80000000
N_NORMAL = 50
NORMAL_SEEDS = {
    "fp32_add": 0xADDA0001,
    "fp32_sub": 0x5AB50001,
    "fp32_mul": 0xC0DE0001,
    "fp32_div": 0xD1A10001,
}


EDGE_BITS = [
    0x00000000,  # +0
    0x80000000,  # -0
    0x00000001,  # +min subnormal
    0x80000001,  # -min subnormal
    0x007FFFFF,  # +max subnormal
    0x807FFFFF,  # -max subnormal
    0x00800000,  # +min normal
    0x80800000,  # -min normal
    0x3F800000,  # +1.0
    0xBF800000,  # -1.0
    FP32_INF_POS,
    FP32_INF_NEG,
    FP32_NAN,
    0xFFC00000,
    0x7F800001,
]


def _u32(u: int) -> int:
    return int(u) & MASK32


def _bits_to_f32(u: int):
    return np.array([_u32(u)], dtype=np.uint32).view(np.float32)[0]


def _f32_to_bits(x) -> int:
    return int(np.array([x], dtype=np.float32).view(np.uint32)[0])


def _is_nan_bits(u: int) -> bool:
    u = _u32(u)
    return (u & EXP32_MASK) == EXP32_MASK and (u & MANT32_MASK) != 0


def _is_inf_bits(u: int) -> bool:
    u = _u32(u)
    return (u & EXP32_MASK) == EXP32_MASK and (u & MANT32_MASK) == 0


def _is_zero_bits(u: int) -> bool:
    return (_u32(u) & ~SIGN32) == 0


def _fp_result(u: int) -> int:
    u = _u32(u)
    if _is_nan_bits(u):
        return FP32_NAN
    return u


def _normal_bits(seed: int, n: int = N_NORMAL, *, positive: bool | None = None) -> list[int]:
    """Generate normal finite FP32 encodings, with moderate exponents."""
    rng = random.Random(seed)
    out: list[int] = []
    while len(out) < n:
        sign = rng.randrange(2) if positive is None else int(not positive)
        exp = rng.randrange(96, 160)
        mant = rng.getrandbits(23)
        out.append((sign << 31) | (exp << 23) | mant)
    return out


def _normal_pairs(seed: int, n: int = N_NORMAL) -> list[tuple[int, int]]:
    a_vals = _normal_bits(seed, n)
    b_vals = _normal_bits(seed ^ 0x51515151, n)
    return list(zip(a_vals, b_vals))


def _normal_ints(seed: int, n: int = N_NORMAL) -> list[int]:
    rng = random.Random(seed)
    return [rng.randint(INT32_MIN, INT32_MAX) for _ in range(n)]


def _fmt_unary(a: int, expected: int, got: int) -> str:
    return f"a={a:#010x}, expected={expected:#010x}, got={got:#010x}"


def _fmt_binary(a: int, b: int, expected: int, got: int) -> str:
    return f"a={a:#010x}, b={b:#010x}, expected={expected:#010x}, got={got:#010x}"


def _assert_no_failures(name: str, failures: list[str]) -> None:
    assert not failures, f"{name} mismatches:\n" + "\n".join(failures)


def _oracle_binary(op: str, a: int, b: int) -> int:
    af = _bits_to_f32(a)
    bf = _bits_to_f32(b)
    with np.errstate(all="ignore"):
        if op == "add":
            out = np.float32(af + bf)
        elif op == "sub":
            out = np.float32(af - bf)
        elif op == "mul":
            out = np.float32(af * bf)
        elif op == "div":
            out = np.float32(af / bf)
        else:
            raise ValueError(op)
    return _fp_result(_f32_to_bits(out))


def _oracle_sqrt(a: int) -> int:
    af = _bits_to_f32(a)
    with np.errstate(all="ignore"):
        out = np.sqrt(af, dtype=np.float32)
    return _fp_result(_f32_to_bits(out))


def _oracle_rsqrt(a: int) -> int:
    return _oracle_binary("div", FP32_ONE, _oracle_sqrt(a))


def _oracle_cmp(a: int, b: int) -> int:
    aa = a
    bb = b
    if _is_nan_bits(aa) or _is_nan_bits(bb):
        return -2
    if _is_zero_bits(aa) and _is_zero_bits(bb):
        return 0
    af = _bits_to_f32(aa)
    bf = _bits_to_f32(bb)
    if af < bf:
        return -1
    if af > bf:
        return 1
    return 0


def _oracle_fp32_to_int32_sat(u: int) -> int:
    if _is_nan_bits(u):
        return 0
    if _is_inf_bits(u):
        return INT32_MIN if (u & SIGN32) else INT32_MAX
    x = float(_bits_to_f32(u))
    if math.isnan(x):
        return 0
    if x >= INT32_MAX:
        return INT32_MAX
    if x <= INT32_MIN:
        return INT32_MIN
    return int(x)


def _oracle_int32_to_fp32(i: int) -> int:
    if i > INT32_MAX:
        i = INT32_MAX
    elif i < INT32_MIN:
        i = INT32_MIN
    return _fp_result(_f32_to_bits(np.float32(i)))


def _oracle_fp32_to_bf16(u: int) -> int:
    u = _u32(u)
    sign = (u >> 31) & 1
    exp = (u >> 23) & 0xFF
    mant = u & MANT32_MASK
    if exp == 0xFF:
        if mant != 0:
            return (sign << 15) | 0x7FC0
        return (sign << 15) | 0x7F80
    top = u >> 16
    discarded = u & 0xFFFF
    halfway = 0x8000
    if discarded > halfway or (discarded == halfway and (top & 1)):
        top += 1
    return top & 0xFFFF


@pytest.mark.parametrize(
    ("name", "impl"),
    [
        ("fp32_add", fp32_add),
        ("fp32_sub", fp32_sub),
        ("fp32_mul", fp32_mul),
        ("fp32_div", fp32_div),
    ],
)
def test_binary_fp_ops_normal_50(name, impl):
    op = name.removeprefix("fp32_")
    failures = []
    for a, b in _normal_pairs(NORMAL_SEEDS[name]):
        expected = _oracle_binary(op, a, b)
        got = _u32(impl(a, b))
        if got != expected:
            failures.append(_fmt_binary(a, b, expected, got))
    _assert_no_failures(f"{name} normal_50", failures)


@pytest.mark.parametrize(
    ("name", "impl"),
    [
        ("fp32_add", fp32_add),
        ("fp32_sub", fp32_sub),
        ("fp32_mul", fp32_mul),
        ("fp32_div", fp32_div),
    ],
)
def test_binary_fp_ops_edges(name, impl):
    op = name.removeprefix("fp32_")
    pairs = [(a, b) for a in EDGE_BITS for b in EDGE_BITS]
    failures = []
    for a, b in pairs:
        expected = _oracle_binary(op, a, b)
        got = _u32(impl(a, b))
        if got != expected:
            failures.append(_fmt_binary(a, b, expected, got))
            if len(failures) >= 30:
                break
    _assert_no_failures(f"{name} edges", failures)


@pytest.mark.parametrize("op", ["add", "sub", "mul", "div"])
def test_binary_fp_ops_raw_and_subnormal_inputs(op):
    from behave.sim.behavior import fp_lib

    rng = random.Random(7542019)
    impl = getattr(fp_lib, f"fp32_{op}")
    for index in range(4096):
        a, b = rng.getrandbits(32), rng.getrandbits(32)
        # Uniform bit sampling rarely combines two subnormals. Exercise that
        # case explicitly, with both signs and mixed normal/subnormal pairs.
        if index % 2 == 0:
            a &= SIGN32 | MANT32_MASK
        if index % 3 == 0:
            b &= SIGN32 | MANT32_MASK
        expected = _oracle_binary(op, a, b)
        actual = impl(a, b)
        assert actual == expected, _fmt_binary(a, b, expected, actual)


def test_fp32_sqrt_normal_50():
    failures = []
    for a in _normal_bits(0x519732A1, positive=True):
        expected = _oracle_sqrt(a)
        got = _u32(fp32_sqrt(a))
        if got != expected:
            failures.append(_fmt_unary(a, expected, got))
    _assert_no_failures("fp32_sqrt normal_50", failures)


def test_fp32_sqrt_edges():
    failures = []
    for a in EDGE_BITS:
        expected = _oracle_sqrt(a)
        got = _u32(fp32_sqrt(a))
        if got != expected:
            failures.append(_fmt_unary(a, expected, got))
    _assert_no_failures("fp32_sqrt edges", failures)


def test_fp32_rsqrt_normal_50():
    failures = []
    for a in _normal_bits(0x46A94721, positive=True):
        expected = _oracle_rsqrt(a)
        got = _u32(fp32_rsqrt(a))
        if got != expected:
            failures.append(_fmt_unary(a, expected, got))
    _assert_no_failures("fp32_rsqrt normal_50", failures)


def test_fp32_rsqrt_edges():
    failures = []
    for a in EDGE_BITS:
        expected = _oracle_rsqrt(a)
        got = _u32(fp32_rsqrt(a))
        if got != expected:
            failures.append(_fmt_unary(a, expected, got))
    _assert_no_failures("fp32_rsqrt edges", failures)


def test_fp32_cmp_normal_50():
    failures = []
    for a, b in _normal_pairs(0xC0FFEE):
        expected = _oracle_cmp(a, b)
        got = fp32_cmp(a, b)
        if got != expected:
            failures.append(f"a={a:#010x}, b={b:#010x}, expected={expected}, got={got}")
    _assert_no_failures("fp32_cmp normal_50", failures)


def test_fp32_cmp_edges():
    failures = []
    for a in EDGE_BITS:
        for b in EDGE_BITS:
            expected = _oracle_cmp(a, b)
            got = fp32_cmp(a, b)
            if got != expected:
                failures.append(f"a={a:#010x}, b={b:#010x}, expected={expected}, got={got}")
                if len(failures) >= 30:
                    break
        if len(failures) >= 30:
            break
    _assert_no_failures("fp32_cmp edges", failures)


def test_fp32_abs_normal_50():
    for a in _normal_bits(0xAB50):
        assert fp32_abs(a) == (a & 0x7FFFFFFF)


def test_fp32_abs_edges():
    for a in EDGE_BITS:
        assert fp32_abs(a) == (a & 0x7FFFFFFF)


def test_fp32_neg_normal_50():
    for a in _normal_bits(0x0E94):
        assert fp32_neg(a) == (a ^ SIGN32)


def test_fp32_neg_edges():
    for a in EDGE_BITS:
        assert fp32_neg(a) == (a ^ SIGN32)


def test_fp32_classifiers_normal_50():
    for a in _normal_bits(0xC1A55):
        assert not fp32_is_zero(a)
        assert not fp32_is_nan(a)
        assert not fp32_is_inf(a)


def test_fp32_classifiers_edges():
    for a in EDGE_BITS:
        assert fp32_is_zero(a) == _is_zero_bits(a)
        assert fp32_is_nan(a) == _is_nan_bits(a)
        assert fp32_is_inf(a) == _is_inf_bits(a)


def test_fp32_to_int32_sat_normal_50():
    failures = []
    for a in _normal_bits(0x1A732):
        expected = _oracle_fp32_to_int32_sat(a)
        got = fp32_to_int32_sat(a)
        if got != expected:
            failures.append(f"a={a:#010x}, expected={expected}, got={got}")
    _assert_no_failures("fp32_to_int32_sat normal_50", failures)


def test_fp32_to_int32_sat_edges():
    edge_cases = EDGE_BITS + [0x4EFFFFFF, 0x4F000000, 0xCF000000, 0xCF000001]
    failures = []
    for a in edge_cases:
        expected = _oracle_fp32_to_int32_sat(a)
        got = fp32_to_int32_sat(a)
        if got != expected:
            failures.append(f"a={a:#010x}, expected={expected}, got={got}")
    _assert_no_failures("fp32_to_int32_sat edges", failures)


def test_int32_to_fp32_normal_50():
    failures = []
    for i in _normal_ints(0x1234):
        expected = _oracle_int32_to_fp32(i)
        got = _u32(int32_to_fp32(i))
        if got != expected:
            failures.append(f"i={i}, expected={expected:#010x}, got={got:#010x}")
    _assert_no_failures("int32_to_fp32 normal_50", failures)


def test_int32_to_fp32_edges():
    edge_cases = [
        INT32_MIN,
        INT32_MIN + 1,
        -16_777_217,
        -16_777_216,
        -1,
        0,
        1,
        16_777_216,
        16_777_217,
        INT32_MAX - 1,
        INT32_MAX,
    ]
    failures = []
    for i in edge_cases:
        expected = _oracle_int32_to_fp32(i)
        got = _u32(int32_to_fp32(i))
        if got != expected:
            failures.append(f"i={i}, expected={expected:#010x}, got={got:#010x}")
    _assert_no_failures("int32_to_fp32 edges", failures)


def test_bf16_to_fp32_normal_50():
    for u in [(x >> 16) for x in _normal_bits(0xBF16)]:
        assert bf16_to_fp32(u) == ((u & 0xFFFF) << 16)


def test_bf16_to_fp32_edges():
    for u in [(x >> 16) for x in EDGE_BITS]:
        assert bf16_to_fp32(u) == ((u & 0xFFFF) << 16)


def test_fp32_to_bf16_normal_50():
    failures = []
    for a in _normal_bits(0x3216):
        expected = _oracle_fp32_to_bf16(a)
        got = fp32_to_bf16(a)
        if got != expected:
            failures.append(f"a={a:#010x}, expected={expected:#06x}, got={got:#06x}")
    _assert_no_failures("fp32_to_bf16 normal_50", failures)


def test_fp32_to_bf16_edges():
    failures = []
    for a in EDGE_BITS:
        expected = _oracle_fp32_to_bf16(a)
        got = fp32_to_bf16(a)
        if got != expected:
            failures.append(f"a={a:#010x}, expected={expected:#06x}, got={got:#06x}")
    _assert_no_failures("fp32_to_bf16 edges", failures)
