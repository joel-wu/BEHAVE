def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ai_ml_arch_systolic_systolic_int8_quant:
    def __init__(self):
        self.reset()

    def reset(self):
        self.c1 = 0
        self.c2 = 0
        self.last_propagate = 0

    def process(
        self,
        in_a,
        in_b,
        in_d,
        in_control_dataflow,
        in_control_propagate,
        in_control_shift,
        in_id,
        in_last,
    ):
        old_c1 = self.c1
        old_c2 = self.c2
        old_last_propagate = self.last_propagate

        a_s = to_signed(in_a, 8)
        b_s = to_signed(in_b, 32)
        d_s = to_signed(in_d, 32)
        dataflow = to_unsigned(in_control_dataflow, 1)
        propagate = to_unsigned(in_control_propagate, 1)
        shift = to_unsigned(in_control_shift, 5)

        flip = 1 if old_last_propagate != propagate else 0
        shift_offset = shift if flip else 0

        if dataflow == 0:
            b_low8_s = to_signed(in_b, 8)
            product32 = to_signed(a_s * b_low8_s, 16)
            product32 = to_signed(product32, 32)
            out_b_s = b_s
            if propagate == 1:
                out_c_s = to_signed(old_c1, 32) >> shift_offset
                new_c2 = to_signed(old_c2 + product32, 32)
                new_c1 = d_s
            else:
                out_c_s = to_signed(old_c2, 32) >> shift_offset
                new_c1 = to_signed(old_c1 + product32, 32)
                new_c2 = d_s
        else:
            if propagate == 1:
                out_c_s = to_signed(old_c1, 32)
                weight8 = to_signed(old_c2, 8)
                product32 = to_signed(a_s * weight8, 16)
                product32 = to_signed(product32, 32)
                out_b_s = to_signed(b_s + product32, 32)
                new_c1 = d_s
                new_c2 = old_c2
            else:
                out_c_s = to_signed(old_c2, 32)
                weight8 = to_signed(old_c1, 8)
                product32 = to_signed(a_s * weight8, 16)
                product32 = to_signed(product32, 32)
                out_b_s = to_signed(b_s + product32, 32)
                new_c2 = d_s
                new_c1 = old_c1

        self.c1 = to_signed(new_c1, 32)
        self.c2 = to_signed(new_c2, 32)
        self.last_propagate = propagate

        return {
            "out_a": to_unsigned(in_a, 8),
            "out_b": to_unsigned(out_b_s, 32),
            "out_c": to_unsigned(out_c_s, 32),
            "out_control_dataflow": dataflow,
            "out_control_propagate": propagate,
            "out_control_shift": shift,
            "out_id": to_unsigned(in_id, 1),
            "out_last": to_unsigned(in_last, 1),
            "bad_dataflow": 0,
        }