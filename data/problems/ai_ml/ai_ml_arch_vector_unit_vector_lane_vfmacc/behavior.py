def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def ashr32(v, s):
    v = to_signed(v, 32)
    s &= 0x1F
    return to_signed(v >> s, 32)


class VectorLaneVFMACC:
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
        last_propagate = self.last_propagate

        a = to_signed(in_a, 16)
        b = to_signed(in_b, 32)
        d = to_signed(in_d, 32)
        dataflow = to_unsigned(in_control_dataflow, 1)
        propagate = to_unsigned(in_control_propagate, 1)
        shift = to_unsigned(in_control_shift, 5)

        flip = 1 if last_propagate != propagate else 0
        shift_offset = shift if flip else 0
        bad_dataflow = 0

        if dataflow == 0:
            if propagate == 1:
                out_c = ashr32(old_c1, shift_offset)
                out_b = b
                mac = old_c2 + a * b
                self.c2 = to_signed(mac, 32)
                self.c1 = d
            else:
                out_c = ashr32(old_c2, shift_offset)
                out_b = b
                mac = old_c1 + a * b
                self.c1 = to_signed(mac, 32)
                self.c2 = d
        else:
            if propagate == 1:
                out_c = old_c1
                out_b = to_signed(b + a * old_c2, 32)
                self.c1 = d
                self.c2 = old_c2
            else:
                out_c = old_c2
                out_b = to_signed(b + a * old_c1, 32)
                self.c2 = d
                self.c1 = old_c1

        self.last_propagate = propagate

        return {
            "out_a": to_unsigned(a, 16),
            "out_b": to_unsigned(out_b, 32),
            "out_c": to_unsigned(out_c, 32),
            "out_control_dataflow": dataflow,
            "out_control_propagate": propagate,
            "out_control_shift": shift,
            "out_id": to_unsigned(in_id, 8),
            "out_last": to_unsigned(in_last, 1),
            "bad_dataflow": bad_dataflow,
        }