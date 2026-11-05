def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class ClassicRsaMontMulLimb:
    def __init__(self, NW=4):
        self.NW = NW
        self.reset()

    def reset(self):
        self._dummy = 0

    def process(self, in_a, in_b, in_n, in_m0):
        nw = self.NW
        B = 1 << 64
        mask64 = B - 1

        a = [(in_a >> (64 * i)) & mask64 for i in range(nw)]
        b = [(in_b >> (64 * i)) & mask64 for i in range(nw)]
        n = [(in_n >> (64 * i)) & mask64 for i in range(nw)]

        p = [0] * (2 * nw)
        for i in range(nw):
            carry = 0
            for j in range(nw):
                s = p[i + j] + a[i] * b[j] + carry
                p[i + j] = s & mask64
                carry = s >> 64
            p[i + nw] = carry

        t = [0] * (2 * nw + 1)
        for i in range(2 * nw):
            t[i] = p[i]

        for i in range(nw):
            k = (t[i] * in_m0) & mask64
            carry = 0
            for j in range(nw):
                s = t[i + j] + k * n[j] + carry
                t[i + j] = s & mask64
                carry = s >> 64
            idx = i + nw
            # ``t`` has one guard limb, so at most ``nw + 1`` positions remain.
            for _ in range(nw + 1):
                if carry != 0:
                    s = t[idx] + carry
                    t[idx] = s & mask64
                    carry = s >> 64
                    idx += 1

        u = [t[nw + j] for j in range(nw)]

        u_ge_n = 1
        for j in range(nw - 1, -1, -1):
            if u[j] > n[j]:
                u_ge_n = 1
                break
            if u[j] < n[j]:
                u_ge_n = 0
                break

        if (t[2 * nw] != 0) or (u_ge_n == 1):
            result = [0] * nw
            borrow = 0
            for j in range(nw):
                diff = u[j] - n[j] - borrow
                if diff < 0:
                    result[j] = diff + B
                    borrow = 1
                else:
                    result[j] = diff
                    borrow = 0
        else:
            result = u

        out_result = 0
        for i in range(nw):
            out_result |= (result[i] & mask64) << (64 * i)

        return {"out_result": to_unsigned(out_result, 64 * nw)}
