def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def ashr16(v, s):
    x = to_signed(v, 16)
    if s >= 16:
        return 0xFFFF if x < 0 else 0x0000
    return to_unsigned(x >> s, 16)


class SystolicSparseSkipPE:
    def __init__(self):
        self.reset()

    def reset(self):
        self.c1 = 0
        self.c2 = 0
        self.last_propagate = 0

    def process(self, in_a, in_b, in_d,
                in_control_dataflow, in_control_propagate, in_control_shift,
                in_id, in_last):
        old_c1 = self.c1
        old_c2 = self.c2
        old_last_propagate = self.last_propagate

        a_s = to_signed(in_a, 8)
        b_s = to_signed(in_b, 16)
        d_u = to_unsigned(in_d, 16)
        c1_s = to_signed(old_c1, 16)
        c2_s = to_signed(old_c2, 16)

        flip = 1 if old_last_propagate != (in_control_propagate & 1) else 0
        shift_offset = (in_control_shift & 0x1F) if flip else 0

        out_a = to_unsigned(in_a, 8)
        out_control_dataflow = to_unsigned(in_control_dataflow, 1)
        out_control_propagate = to_unsigned(in_control_propagate, 1)
        out_control_shift = to_unsigned(in_control_shift, 5)
        out_id = to_unsigned(in_id, 8)
        out_last = to_unsigned(in_last, 1)
        bad_dataflow = 0

        if (in_control_dataflow & 1) == 0:
            if (in_control_propagate & 1) == 1:
                out_c = ashr16(old_c1, shift_offset)
                out_b = to_unsigned(in_b, 16)
                mac = c2_s + a_s * b_s
                self.c2 = to_unsigned(mac, 16)
                self.c1 = d_u
            else:
                out_c = ashr16(old_c2, shift_offset)
                out_b = to_unsigned(in_b, 16)
                mac = c1_s + a_s * b_s
                self.c1 = to_unsigned(mac, 16)
                self.c2 = d_u
        else:
            if (in_control_propagate & 1) == 1:
                out_c = to_unsigned(old_c1, 16)
                mac = b_s + a_s * c2_s
                out_b = to_unsigned(mac, 16)
                self.c1 = d_u
                self.c2 = old_c2
            else:
                out_c = to_unsigned(old_c2, 16)
                mac = b_s + a_s * c1_s
                out_b = to_unsigned(mac, 16)
                self.c1 = old_c1
                self.c2 = d_u

        self.last_propagate = to_unsigned(in_control_propagate, 1)

        return {
            "out_a": out_a,
            "out_b": to_unsigned(out_b, 16),
            "out_c": to_unsigned(out_c, 16),
            "out_control_dataflow": out_control_dataflow,
            "out_control_propagate": out_control_propagate,
            "out_control_shift": out_control_shift,
            "out_id": out_id,
            "out_last": out_last,
            "bad_dataflow": 0,
        }