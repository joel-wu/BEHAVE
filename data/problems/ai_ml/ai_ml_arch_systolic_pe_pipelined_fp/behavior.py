from fp_lib import fp32_add, fp32_mul


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlArchSystolicPePipelinedFp:
    def __init__(self):
        self.reset()

    def reset(self):
        self.c1 = 0
        self.c2 = 0
        self.last_s = 0

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
        c1 = self.c1
        c2 = self.c2
        last_s = self.last_s

        in_a = to_unsigned(in_a, 32)
        in_b = to_unsigned(in_b, 32)
        in_d = to_unsigned(in_d, 32)
        dataflow = to_unsigned(in_control_dataflow, 1)
        propagate = to_unsigned(in_control_propagate, 1)
        shift = to_unsigned(in_control_shift, 5)
        in_id = to_unsigned(in_id, 8)
        in_last = to_unsigned(in_last, 1)

        flip = 1 if last_s != propagate else 0
        shift_offset = shift if flip else 0
        shifted_c1 = to_unsigned(c1, 32) >> shift_offset
        shifted_c2 = to_unsigned(c2, 32) >> shift_offset

        out_a = in_a
        out_control_dataflow = dataflow
        out_control_propagate = propagate
        out_control_shift = shift
        out_id = in_id
        out_last = in_last
        bad_dataflow = 0

        if dataflow == 0:
            mul_ab = fp32_mul(in_a, in_b)
            out_b = in_b
            if propagate == 1:
                out_c = shifted_c1
                new_c1 = in_d
                new_c2 = fp32_add(c2, mul_ab)
            else:
                out_c = shifted_c2
                new_c1 = fp32_add(c1, mul_ab)
                new_c2 = in_d
        else:
            if propagate == 1:
                out_c = c1
                mul_ab = fp32_mul(in_a, c2)
                out_b = fp32_add(in_b, mul_ab)
                new_c1 = in_d
                new_c2 = c2
            else:
                out_c = c2
                mul_ab = fp32_mul(in_a, c1)
                out_b = fp32_add(in_b, mul_ab)
                new_c1 = c1
                new_c2 = in_d

        self.c1 = to_unsigned(new_c1, 32)
        self.c2 = to_unsigned(new_c2, 32)
        self.last_s = propagate

        return {
            "out_a": to_unsigned(out_a, 32),
            "out_b": to_unsigned(out_b, 32),
            "out_c": to_unsigned(out_c, 32),
            "out_control_dataflow": to_unsigned(out_control_dataflow, 1),
            "out_control_propagate": to_unsigned(out_control_propagate, 1),
            "out_control_shift": to_unsigned(out_control_shift, 5),
            "out_id": to_unsigned(out_id, 8),
            "out_last": to_unsigned(out_last, 1),
            "bad_dataflow": to_unsigned(bad_dataflow, 1),
        }