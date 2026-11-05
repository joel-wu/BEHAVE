def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def mul_q16(a, b):
    return to_signed((a * b) >> 16, 32)


def div_q16(a, b):
    if b == 0:
        return 0
    return to_signed((a << 16) // b, 32)


def sqrt_q16(x):
    if x <= 0:
        return 0
    target = x << 16
    lo = 0
    hi = 1 << 24
    for _ in range(25):
        mid = (lo + hi + 1) >> 1
        if mid * mid <= target:
            lo = mid
        else:
            hi = mid - 1
    return to_signed(lo, 32)


class SymmetricTridiagonalStep4x4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(
        self,
        in_a00,
        in_a10,
        in_a11,
        in_a20,
        in_a21,
        in_a22,
        in_a30,
        in_a31,
        in_a32,
        in_a33,
    ):
        one_q16 = 0x00010000
        neg_half_q16 = to_signed(0xFFFF8000, 32)

        A00 = to_signed(in_a00, 32)
        A10 = to_signed(in_a10, 32)
        A11 = to_signed(in_a11, 32)
        A20 = to_signed(in_a20, 32)
        A21 = to_signed(in_a21, 32)
        A22 = to_signed(in_a22, 32)
        A30 = to_signed(in_a30, 32)
        A31 = to_signed(in_a31, 32)
        A32 = to_signed(in_a32, 32)
        A33 = to_signed(in_a33, 32)

        alpha0_in = A10
        x0 = A20
        x1 = A30
        sigma0 = to_signed(mul_q16(x0, x0) + mul_q16(x1, x1), 32)
        if sigma0 == 0:
            tau0 = 0
            beta0 = alpha0_in
            v0_tail0 = x0
            v0_tail1 = x1
        else:
            norm0 = sqrt_q16(to_signed(mul_q16(alpha0_in, alpha0_in) + sigma0, 32))
            beta0 = to_signed(-norm0, 32) if alpha0_in >= 0 else norm0
            tau0 = div_q16(to_signed(beta0 - alpha0_in, 32), beta0)
            scale0 = div_q16(one_q16, to_signed(alpha0_in - beta0, 32))
            v0_tail0 = mul_q16(x0, scale0)
            v0_tail1 = mul_q16(x1, scale0)

        E0 = beta0
        V10 = E0
        V20 = v0_tail0
        V30 = v0_tail1

        if tau0 != 0:
            hv0 = one_q16
            hv1 = v0_tail0
            hv2 = v0_tail1

            S00 = A11
            S01 = A21
            S02 = A31
            S10 = A21
            S11 = A22
            S12 = A32
            S20 = A31
            S21 = A32
            S22 = A33

            t0 = to_signed(mul_q16(S00, hv0) + mul_q16(S01, hv1) + mul_q16(S02, hv2), 32)
            t1 = to_signed(mul_q16(S10, hv0) + mul_q16(S11, hv1) + mul_q16(S12, hv2), 32)
            t2 = to_signed(mul_q16(S20, hv0) + mul_q16(S21, hv1) + mul_q16(S22, hv2), 32)

            xv0 = mul_q16(tau0, t0)
            xv1 = mul_q16(tau0, t1)
            xv2 = mul_q16(tau0, t2)

            dot0 = to_signed(mul_q16(xv0, hv0) + mul_q16(xv1, hv1) + mul_q16(xv2, hv2), 32)
            alpha_corr0 = mul_q16(mul_q16(neg_half_q16, tau0), dot0)

            w0 = to_signed(xv0 + mul_q16(alpha_corr0, hv0), 32)
            w1 = to_signed(xv1 + mul_q16(alpha_corr0, hv1), 32)
            w2 = to_signed(xv2 + mul_q16(alpha_corr0, hv2), 32)

            S00 = to_signed(S00 - mul_q16(hv0, w0) - mul_q16(w0, hv0), 32)
            S01 = to_signed(S01 - mul_q16(hv0, w1) - mul_q16(w0, hv1), 32)
            S02 = to_signed(S02 - mul_q16(hv0, w2) - mul_q16(w0, hv2), 32)
            S11 = to_signed(S11 - mul_q16(hv1, w1) - mul_q16(w1, hv1), 32)
            S12 = to_signed(S12 - mul_q16(hv1, w2) - mul_q16(w1, hv2), 32)
            S22 = to_signed(S22 - mul_q16(hv2, w2) - mul_q16(w2, hv2), 32)

            A11 = S00
            A21 = S01
            A31 = S02
            A22 = S11
            A32 = S12
            A33 = S22

        D0 = A00

        alpha1_in = A21
        y0 = A31
        sigma1 = mul_q16(y0, y0)
        if sigma1 == 0:
            tau1 = 0
            beta1 = alpha1_in
            v1_tail0 = y0
        else:
            norm1 = sqrt_q16(to_signed(mul_q16(alpha1_in, alpha1_in) + sigma1, 32))
            beta1 = to_signed(-norm1, 32) if alpha1_in >= 0 else norm1
            tau1 = div_q16(to_signed(beta1 - alpha1_in, 32), beta1)
            scale1 = div_q16(one_q16, to_signed(alpha1_in - beta1, 32))
            v1_tail0 = mul_q16(y0, scale1)

        E1 = beta1
        V21 = E1
        V31 = v1_tail0

        if tau1 != 0:
            gv0 = one_q16
            gv1 = v1_tail0

            T00 = A22
            T01 = A32
            T10 = A32
            T11 = A33

            u0 = to_signed(mul_q16(T00, gv0) + mul_q16(T01, gv1), 32)
            u1 = to_signed(mul_q16(T10, gv0) + mul_q16(T11, gv1), 32)

            yv0 = mul_q16(tau1, u0)
            yv1 = mul_q16(tau1, u1)

            dot1 = to_signed(mul_q16(yv0, gv0) + mul_q16(yv1, gv1), 32)
            alpha_corr1 = mul_q16(mul_q16(neg_half_q16, tau1), dot1)

            z0 = to_signed(yv0 + mul_q16(alpha_corr1, gv0), 32)
            z1 = to_signed(yv1 + mul_q16(alpha_corr1, gv1), 32)

            T00 = to_signed(T00 - mul_q16(gv0, z0) - mul_q16(z0, gv0), 32)
            T01 = to_signed(T01 - mul_q16(gv0, z1) - mul_q16(z0, gv1), 32)
            T11 = to_signed(T11 - mul_q16(gv1, z1) - mul_q16(z1, gv1), 32)

            A22 = T00
            A32 = T01
            A33 = T11

        D1 = A11
        tau2 = 0
        E2 = A32
        D2 = A22
        D3 = A33
        V32 = E2

        return {
            "out_d0": to_unsigned(D0, 32),
            "out_d1": to_unsigned(D1, 32),
            "out_d2": to_unsigned(D2, 32),
            "out_d3": to_unsigned(D3, 32),
            "out_e0": to_unsigned(E0, 32),
            "out_e1": to_unsigned(E1, 32),
            "out_e2": to_unsigned(E2, 32),
            "out_tau0": to_unsigned(tau0, 32),
            "out_tau1": to_unsigned(tau1, 32),
            "out_tau2": to_unsigned(tau2, 32),
            "out_v10": to_unsigned(V10, 32),
            "out_v20": to_unsigned(V20, 32),
            "out_v30": to_unsigned(V30, 32),
            "out_v21": to_unsigned(V21, 32),
            "out_v31": to_unsigned(V31, 32),
            "out_v32": to_unsigned(V32, 32),
        }