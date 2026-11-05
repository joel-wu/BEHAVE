def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SignalProcessingCommSequenceBSequence:
    def __init__(self, num_bits=32):
        assert num_bits >= 1
        self.num_bits = num_bits
        self.count_bits = num_bits.bit_length()
        self.reset()

    def reset(self):
        self.sequence = 0

    def process(self, in_op, in_bit, in_index, in_sequence):
        s_old = self.sequence
        mask = (1 << self.num_bits) - 1
        x = in_sequence & mask
        out_bit = 0
        out_corr = 0

        if in_op == 0:
            s_new = 0
            out_sequence = 0
            out_count = 0
        elif in_op == 1:
            s_new = x
            out_sequence = x
            out_count = x.bit_count()
            out_corr = self.num_bits
        elif in_op == 2:
            out_bit = in_bit & 1
            s_new = ((s_old << 1) | out_bit) & mask
            out_sequence = s_new
            out_count = s_new.bit_count()
        elif in_op == 3:
            out_bit = (s_old >> (self.num_bits - 1)) & 1
            s_new = ((s_old << 1) | out_bit) & mask
            out_sequence = s_new
            out_count = s_new.bit_count()
        else:
            s_new = s_old
            if in_op == 4:
                out_sequence = s_old ^ x
                out_count = out_sequence.bit_count()
            elif in_op == 5:
                out_sequence = s_old & x
                out_count = out_sequence.bit_count()
            else:
                out_sequence = s_old
                out_count = s_old.bit_count()
                if in_op == 7:
                    if in_index < self.num_bits:
                        out_bit = (s_old >> in_index) & 1
                elif in_op == 8:
                    out_corr = self.num_bits - (s_old ^ x).bit_count()

        self.sequence = s_new
        return {
            "out_sequence": to_unsigned(out_sequence, self.num_bits),
            "out_bit": to_unsigned(out_bit, 1),
            "out_count": to_unsigned(out_count, self.count_bits),
            "out_corr": to_unsigned(out_corr, self.count_bits),
        }
