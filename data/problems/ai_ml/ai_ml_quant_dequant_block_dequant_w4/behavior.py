from fp_lib import bf16_to_fp32, int32_to_fp32, fp32_mul, fp32_to_bf16


def to_signed(v, bits):
    v = v & ((1 << bits) - 1)
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


class AiMlQuantDequantBlockDequantW4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_q, in_scale, in_zero_point):
        q = to_signed(in_q, 4)
        zero_point = to_signed(in_zero_point, 8)
        d = int32_to_fp32(q - zero_point)
        scale = bf16_to_fp32(in_scale)
        return {"out_data": fp32_to_bf16(fp32_mul(scale, d))}
