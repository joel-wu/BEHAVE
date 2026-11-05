class GPTNeoXMLPGELU:
    def __init__(self, hidden_size=4, intermediate_size=4, data_w=16, frac_w=12):
        self.hidden_size = hidden_size
        self.intermediate_size = intermediate_size
        self.data_w = data_w
        self.frac_w = frac_w
        self.reset()

    def reset(self):
        return None

    def _to_unsigned(self, v, b):
        return v & ((1 << b) - 1)

    def _to_signed(self, v, b):
        v &= (1 << b) - 1
        if v & (1 << (b - 1)):
            v -= 1 << b
        return v

    def _sat_data(self, v):
        lo = -(1 << (self.data_w - 1))
        hi = (1 << (self.data_w - 1)) - 1
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v

    def _qmul(self, a, b):
        return (a * b) >> self.frac_w

    def _div_trunc_zero(self, n, d):
        if d == 0:
            return 0
        if n >= 0:
            return n // d
        return -(((-n) // d))

    def _unpack_vec(self, packed, n):
        mask = (1 << self.data_w) - 1
        return [self._to_signed((packed >> (i * self.data_w)) & mask, self.data_w) for i in range(n)]

    def _unpack_mat(self, packed, rows, cols):
        mask = (1 << self.data_w) - 1
        out = []
        for r in range(rows):
            row = []
            for c in range(cols):
                idx = r * cols + c
                row.append(self._to_signed((packed >> (idx * self.data_w)) & mask, self.data_w))
            out.append(row)
        return out

    def _pack_vec(self, vec):
        out = 0
        for i, v in enumerate(vec):
            out |= self._to_unsigned(v, self.data_w) << (i * self.data_w)
        return out

    def process(self, in_hidden_states, in_w1, in_b1, in_w2, in_b2):
        q = self.frac_w
        one = 1 << q
        c = ((187548 << q) + (1 << 21)) >> 22
        s = ((3346570 << q) + (1 << 21)) >> 22

        x = self._unpack_vec(in_hidden_states, self.hidden_size)
        b1 = self._unpack_vec(in_b1, self.intermediate_size)
        b2 = self._unpack_vec(in_b2, self.hidden_size)
        w1 = self._unpack_mat(in_w1, self.intermediate_size, self.hidden_size)
        w2 = self._unpack_mat(in_w2, self.hidden_size, self.intermediate_size)

        pre_gelu = []
        for i in range(self.intermediate_size):
            acc1 = b1[i]
            for j in range(self.hidden_size):
                acc1 = acc1 + self._qmul(w1[i][j], x[j])
            pre_gelu.append(self._sat_data(acc1))

        act = []
        for i in range(self.intermediate_size):
            xg = pre_gelu[i]
            x2 = self._qmul(xg, xg)
            x3 = self._qmul(x2, xg)
            u = self._sat_data(xg + self._qmul(c, x3))
            v = self._sat_data(self._qmul(s, u))
            v2 = self._qmul(v, v)
            num = v * (27 * one + v2)
            den = 27 * one + 9 * v2
            t = self._div_trunc_zero(num, den)
            if t < -one:
                t = -one
            if t > one:
                t = one
            g = one + t
            h = self._qmul(xg, g)
            act.append(self._sat_data(h >> 1))

        y = []
        for k in range(self.hidden_size):
            acc2 = b2[k]
            for i in range(self.intermediate_size):
                acc2 = acc2 + self._qmul(w2[k][i], act[i])
            y.append(self._sat_data(acc2))

        return {"out_hidden_states": self._pack_vec(y)}
