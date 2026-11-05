# Q1.31 cosine coefficients, including exact +/-1 in a 33-bit carrier.
COS_Q31 = (2147483648,1984016189,1518500250,821806413,0,-821806413,
           -1518500250,-1984016189,-2147483648,-1984016189,-1518500250,
           -821806413,0,821806413,1518500250,1984016189)


def round_signed(n, d):
    magnitude = -n if n < 0 else n
    q = (magnitude + d // 2) // d
    return -q if n < 0 else q


class FSKModulator:
    def __init__(self, M_BITS=1, K=2, PHASE_W=32, OUT_W=16):
        assert (1 <= M_BITS <= 4 and (1 << M_BITS) <= K <= 64
                and 8 <= PHASE_W <= 32 and 8 <= OUT_W <= 16), "unsupported FSK configuration"
        self.m_bits = M_BITS
        self.k = K
        self.phase_w = PHASE_W
        self.out_w = OUT_W
        self.reset()

    def reset(self):
        self.phase_acc = 0

    def _phase_inc(self, s):
        m = 1 << self.m_bits
        numerator = (2 * s - (m - 1)) * (1 << (self.phase_w - 2))
        return round_signed(numerator, m - 1) & ((1 << self.phase_w) - 1)

    def _coefficient(self, index):
        value = round_signed(COS_Q31[index], 1 << (32 - self.out_w))
        limit = 1 << (self.out_w - 1)
        return max(-limit, min(limit - 1, value)) & ((1 << self.out_w) - 1)

    def process(self, in_symbol):
        symbol = in_symbol & ((1 << self.m_bits) - 1)
        increment = self._phase_inc(symbol)
        out = []
        for _ in range(self.k):
            index = self.phase_acc >> (self.phase_w - 4)
            out.append({"out_i": self._coefficient(index),
                        "out_q": self._coefficient((index - 4) & 15)})
            self.phase_acc = (self.phase_acc + increment) & ((1 << self.phase_w) - 1)
        return out
