from fp_lib import bf16_to_fp32, fp32_to_bf16, fp32_add, fp32_mul, fp32_div, fp32_sqrt, fp32_is_zero


class ai_ml_tx_norm_rms_llama_rmsnorm:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_w0, in_w1, in_w2, in_w3):
        x0 = bf16_to_fp32(in_x0)
        x1 = bf16_to_fp32(in_x1)
        x2 = bf16_to_fp32(in_x2)
        x3 = bf16_to_fp32(in_x3)
        w0 = bf16_to_fp32(in_w0)
        w1 = bf16_to_fp32(in_w1)
        w2 = bf16_to_fp32(in_w2)
        w3 = bf16_to_fp32(in_w3)

        sq0 = fp32_mul(x0, x0)
        sq1 = fp32_mul(x1, x1)
        sq2 = fp32_mul(x2, x2)
        sq3 = fp32_mul(x3, x3)
        sum01 = fp32_add(sq0, sq1)
        sum23 = fp32_add(sq2, sq3)
        sum_all = fp32_add(sum01, sum23)
        mean = fp32_mul(sum_all, 0x3E800000)
        mean_eps = fp32_add(mean, 0x358637BD)

        sqrt_mean = fp32_sqrt(mean_eps)
        if fp32_is_zero(sqrt_mean):
            scale = 0x7F800000
        else:
            scale = fp32_div(0x3F800000, sqrt_mean)

        # LlamaRMSNorm casts the normalized activations to the input dtype
        # before multiplying by its learned weight.
        norm0 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x0, scale)))
        norm1 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x1, scale)))
        norm2 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x2, scale)))
        norm3 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x3, scale)))
        prod0 = fp32_mul(w0, norm0)
        prod1 = fp32_mul(w1, norm1)
        prod2 = fp32_mul(w2, norm2)
        prod3 = fp32_mul(w3, norm3)

        return {
            "out_y0": fp32_to_bf16(prod0) & 0xFFFF,
            "out_y1": fp32_to_bf16(prod1) & 0xFFFF,
            "out_y2": fp32_to_bf16(prod2) & 0xFFFF,
            "out_y3": fp32_to_bf16(prod3) & 0xFFFF,
        }
