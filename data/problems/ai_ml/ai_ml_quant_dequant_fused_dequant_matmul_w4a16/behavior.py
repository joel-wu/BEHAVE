from fp_lib import bf16_to_fp32, fp32_add, fp32_mul, fp32_to_bf16


INT4_TO_FP32 = {
    0x0: 0x00000000,
    0x1: 0x3F800000,
    0x2: 0x40000000,
    0x3: 0x40400000,
    0x4: 0x40800000,
    0x5: 0x40A00000,
    0x6: 0x40C00000,
    0x7: 0x40E00000,
    0x8: 0xC1000000,
    0x9: 0xC0E00000,
    0xA: 0xC0C00000,
    0xB: 0xC0A00000,
    0xC: 0xC0800000,
    0xD: 0xC0400000,
    0xE: 0xC0000000,
    0xF: 0xBF800000,
}


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class AiMlQuantDequantFusedDequantMatmulW4A16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_act, in_w_packed, in_scale):
        acts = [bf16_to_fp32((in_act >> (16 * i)) & 0xFFFF) for i in range(16)]
        scales = [bf16_to_fp32((in_scale >> (16 * i)) & 0xFFFF) for i in range(4)]

        products = []
        for i in range(16):
            q_i_fp32 = INT4_TO_FP32[(in_w_packed >> (4 * i)) & 0xF]
            w_i_fp32 = fp32_mul(q_i_fp32, scales[i >> 2])
            products.append(fp32_mul(acts[i], w_i_fp32))

        acc = products[0]
        for i in range(1, 16):
            acc = fp32_add(acc, products[i])

        return {"out_bf16": to_unsigned(fp32_to_bf16(acc), 16)}