def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ChunkwiseDeltaNetDeltaRule:
    KEY_DIM = 1
    VALUE_DIM = 1
    ELEM_W = 16

    def __init__(self, key_dim=1, value_dim=1, elem_w=16):
        assert key_dim >= 1 and value_dim >= 1 and elem_w >= 2
        self.KEY_DIM = key_dim
        self.VALUE_DIM = value_dim
        self.ELEM_W = elem_w
        self.reset()

    def reset(self):
        pass

    def process(self, in_q, in_k, in_v, in_beta, in_state):
        q = [to_signed((in_q >> (self.ELEM_W * i)) & ((1 << self.ELEM_W) - 1), self.ELEM_W) for i in range(self.KEY_DIM)]
        k = [to_signed((in_k >> (self.ELEM_W * i)) & ((1 << self.ELEM_W) - 1), self.ELEM_W) for i in range(self.KEY_DIM)]
        v = [to_signed((in_v >> (self.ELEM_W * j)) & ((1 << self.ELEM_W) - 1), self.ELEM_W) for j in range(self.VALUE_DIM)]
        beta = to_signed(in_beta & ((1 << self.ELEM_W) - 1), self.ELEM_W)

        state = [[to_signed((in_state >> (self.ELEM_W * (i * self.VALUE_DIM + j))) & ((1 << self.ELEM_W) - 1), self.ELEM_W) for j in range(self.VALUE_DIM)] for i in range(self.KEY_DIM)]

        proj = [0 for _ in range(self.VALUE_DIM)]
        for j in range(self.VALUE_DIM):
            acc = 0
            for i in range(self.KEY_DIM):
                acc += k[i] * state[i][j]
            proj[j] = acc

        residual = [v[j] - proj[j] for j in range(self.VALUE_DIM)]

        next_state = [[0 for _ in range(self.VALUE_DIM)] for _ in range(self.KEY_DIM)]
        for i in range(self.KEY_DIM):
            for j in range(self.VALUE_DIM):
                next_state[i][j] = state[i][j] + beta * k[i] * residual[j]

        out_o = 0
        for j in range(self.VALUE_DIM):
            acc = 0
            for i in range(self.KEY_DIM):
                acc += q[i] * next_state[i][j]
            out_o |= to_unsigned(acc, self.ELEM_W) << (self.ELEM_W * j)

        out_state = 0
        for i in range(self.KEY_DIM):
            for j in range(self.VALUE_DIM):
                out_state |= to_unsigned(next_state[i][j], self.ELEM_W) << (self.ELEM_W * (i * self.VALUE_DIM + j))

        return {"out_o": out_o, "out_state": out_state}
