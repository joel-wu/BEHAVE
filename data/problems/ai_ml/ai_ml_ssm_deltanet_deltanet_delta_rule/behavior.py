def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DeltaNetDeltaRule:
    def __init__(self, K=4, V=4, W=32):
        self.K = K
        self.V = V
        self.W = W
        self.reset()

    def reset(self):
        self.H_state = [[0 for _ in range(self.V)] for _ in range(self.K)]

    def process(
        self,
        in_init,
        in_clear_state,
        in_scale,
        in_beta_scalar,
        in_beta_headwise,
        in_q,
        in_k,
        in_v,
        in_beta_vector,
        in_initial_state,
    ):
        K = self.K
        V = self.V
        W = self.W
        mask = (1 << W) - 1

        q = [to_signed((in_q >> (i * W)) & mask, W) for i in range(K)]
        k = [to_signed((in_k >> (i * W)) & mask, W) for i in range(K)]
        v = [to_signed((in_v >> (j * W)) & mask, W) for j in range(V)]
        beta_vector = [to_signed((in_beta_vector >> (j * W)) & mask, W) for j in range(V)]
        scale = to_signed(in_scale, W)
        beta_scalar = to_signed(in_beta_scalar, W)

        initial_state = [
            [
                to_signed((in_initial_state >> (((i * V) + j) * W)) & mask, W)
                for j in range(V)
            ]
            for i in range(K)
        ]

        if in_clear_state:
            old_state = [[0 for _ in range(V)] for _ in range(K)]
        elif in_init:
            old_state = [[initial_state[i][j] for j in range(V)] for i in range(K)]
        else:
            old_state = [[self.H_state[i][j] for j in range(V)] for i in range(K)]

        beta = [beta_vector[j] if in_beta_headwise else beta_scalar for j in range(V)]

        projection = []
        for j in range(V):
            s = 0
            for i in range(K):
                s += to_signed(old_state[i][j] * k[i], W)
            projection.append(to_signed(s, W))

        u = [to_signed(v[j] - projection[j], W) for j in range(V)]
        delta_v = [to_signed(u[j] * beta[j], W) for j in range(V)]

        new_state = []
        for i in range(K):
            row = []
            for j in range(V):
                update_term = to_signed(k[i] * delta_v[j], W)
                row.append(to_signed(old_state[i][j] + update_term, W))
            new_state.append(row)

        scaled_q = [to_signed(q[i] * scale, W) for i in range(K)]

        o = []
        for j in range(V):
            s = 0
            for i in range(K):
                s += to_signed(new_state[i][j] * scaled_q[i], W)
            o.append(to_signed(s, W))

        self.H_state = [[new_state[i][j] for j in range(V)] for i in range(K)]

        out_u = 0
        out_o = 0
        out_final_state = 0

        for j in range(V):
            out_u |= to_unsigned(u[j], W) << (j * W)
            out_o |= to_unsigned(o[j], W) << (j * W)

        for i in range(K):
            for j in range(V):
                out_final_state |= to_unsigned(new_state[i][j], W) << (((i * V) + j) * W)

        return {
            "out_o": out_o,
            "out_u": out_u,
            "out_final_state": out_final_state,
        }