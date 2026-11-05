def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class AiMlArchBufferBankPingPongDoubleBuffer:
    def __init__(self, N=16, ELEM_W=32, ROWS=1, COLS=1, SCALE_W=32, ACT_W=3):
        self.N = 16
        self.ELEM_W = 32
        self.ROWS = 1
        self.COLS = 1
        self.SCALE_W = SCALE_W
        self.ACT_W = ACT_W
        self.ADDR_W = 4
        self.DATA_W = 32
        self.MASK_W = 4
        self.reset()

    def reset(self):
        self.mem = [0] * self.N

    def process(
        self,
        in_is_read,
        in_addr,
        in_data,
        in_acc,
        in_mask,
        in_scale,
        in_igelu_qb,
        in_igelu_qc,
        in_iexp_qln2,
        in_iexp_qln2_inv,
        in_act,
        in_full,
        in_fromDMA,
    ):
        addr = in_addr & ((1 << self.ADDR_W) - 1)
        old_word = self.mem[addr]

        if in_is_read:
            return {
                "out_data": to_unsigned(old_word, self.DATA_W),
                "out_scale": to_unsigned(in_scale, self.SCALE_W),
                "out_igelu_qb": to_unsigned(in_igelu_qb, self.ELEM_W),
                "out_igelu_qc": to_unsigned(in_igelu_qc, self.ELEM_W),
                "out_iexp_qln2": to_unsigned(in_iexp_qln2, self.ELEM_W),
                "out_iexp_qln2_inv": to_unsigned(in_iexp_qln2_inv, self.ELEM_W),
                "out_act": to_unsigned(in_act, self.ACT_W),
                "out_fromDMA": to_unsigned(in_fromDMA, 1),
                "out_acc_bank_id": 0,
            }

        if in_acc:
            replacement = to_unsigned(
                to_signed(old_word, self.ELEM_W) + to_signed(in_data, self.ELEM_W),
                self.DATA_W,
            )
        else:
            replacement = to_unsigned(in_data, self.DATA_W)

        new_word = old_word
        for j in range(self.MASK_W):
            if (in_mask >> j) & 1:
                byte = (replacement >> (8 * j)) & 0xFF
                new_word = (new_word & ~(0xFF << (8 * j))) | (byte << (8 * j))

        self.mem[addr] = to_unsigned(new_word, self.DATA_W)
        return None