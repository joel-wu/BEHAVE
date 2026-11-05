class AiMlArchSystolicPeBasicMac:
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
        def to_unsigned(v, b):
            return v & ((1 << b) - 1)

        a = to_unsigned(in_a, 8)
        b = to_unsigned(in_b, 16)
        d = to_unsigned(in_d, 16)
        dataflow = to_unsigned(in_control_dataflow, 1)
        propagate = to_unsigned(in_control_propagate, 1)
        shift = to_unsigned(in_control_shift, 5)
        ident = to_unsigned(in_id, 4)
        last = to_unsigned(in_last, 1)

        c1 = self.c1
        c2 = self.c2
        last_propagate = self.last_propagate

        flip = 1 if last_propagate != propagate else 0
        shift_offset = shift if flip else 0

        if dataflow == 0:
            if propagate == 1:
                out_c = 0 if shift_offset >= 16 else (c1 >> shift_offset)
                out_b = b
                new_c1 = d
                new_c2 = to_unsigned(c2 + a * b, 16)
            else:
                out_c = 0 if shift_offset >= 16 else (c2 >> shift_offset)
                out_b = b
                new_c1 = to_unsigned(c1 + a * b, 16)
                new_c2 = d
        else:
            if propagate == 1:
                out_c = c1
                out_b = to_unsigned(b + a * c2, 16)
                new_c1 = d
                new_c2 = c2
            else:
                out_c = c2
                out_b = to_unsigned(b + a * c1, 16)
                new_c1 = c1
                new_c2 = d

        self.c1 = to_unsigned(new_c1, 16)
        self.c2 = to_unsigned(new_c2, 16)
        self.last_propagate = propagate

        return {
            "out_a": a,
            "out_b": to_unsigned(out_b, 16),
            "out_c": to_unsigned(out_c, 16),
            "out_control_dataflow": dataflow,
            "out_control_propagate": propagate,
            "out_control_shift": shift,
            "out_id": ident,
            "out_last": last,
            "bad_dataflow": 0,
        }