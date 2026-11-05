def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


def asr32(v, s):
    v = to_signed(v, 32)
    if s >= 32:
        return 0 if v >= 0 else -1
    return v >> s


class CompressionFlacPredictFlacResidualCompute:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = [0] * 32
        self.valid_history = 0

    def process(
        self,
        in_sample,
        in_order,
        in_lp_quantization,
        in_qlp_coeff_0,
        in_qlp_coeff_1,
        in_qlp_coeff_2,
        in_qlp_coeff_3,
        in_qlp_coeff_4,
        in_qlp_coeff_5,
        in_qlp_coeff_6,
        in_qlp_coeff_7,
        in_qlp_coeff_8,
        in_qlp_coeff_9,
        in_qlp_coeff_10,
        in_qlp_coeff_11,
        in_qlp_coeff_12,
        in_qlp_coeff_13,
        in_qlp_coeff_14,
        in_qlp_coeff_15,
        in_qlp_coeff_16,
        in_qlp_coeff_17,
        in_qlp_coeff_18,
        in_qlp_coeff_19,
        in_qlp_coeff_20,
        in_qlp_coeff_21,
        in_qlp_coeff_22,
        in_qlp_coeff_23,
        in_qlp_coeff_24,
        in_qlp_coeff_25,
        in_qlp_coeff_26,
        in_qlp_coeff_27,
        in_qlp_coeff_28,
        in_qlp_coeff_29,
        in_qlp_coeff_30,
        in_qlp_coeff_31,
    ):
        sample_n = to_signed(in_sample, 32)
        order = in_order & 0x3F
        lp_quantization = in_lp_quantization & 0x3F
        if order < 1 or order > 32:
            return {"out_residual": 0, "out_error": 1}
        coeff = [
            to_signed(in_qlp_coeff_0, 32),
            to_signed(in_qlp_coeff_1, 32),
            to_signed(in_qlp_coeff_2, 32),
            to_signed(in_qlp_coeff_3, 32),
            to_signed(in_qlp_coeff_4, 32),
            to_signed(in_qlp_coeff_5, 32),
            to_signed(in_qlp_coeff_6, 32),
            to_signed(in_qlp_coeff_7, 32),
            to_signed(in_qlp_coeff_8, 32),
            to_signed(in_qlp_coeff_9, 32),
            to_signed(in_qlp_coeff_10, 32),
            to_signed(in_qlp_coeff_11, 32),
            to_signed(in_qlp_coeff_12, 32),
            to_signed(in_qlp_coeff_13, 32),
            to_signed(in_qlp_coeff_14, 32),
            to_signed(in_qlp_coeff_15, 32),
            to_signed(in_qlp_coeff_16, 32),
            to_signed(in_qlp_coeff_17, 32),
            to_signed(in_qlp_coeff_18, 32),
            to_signed(in_qlp_coeff_19, 32),
            to_signed(in_qlp_coeff_20, 32),
            to_signed(in_qlp_coeff_21, 32),
            to_signed(in_qlp_coeff_22, 32),
            to_signed(in_qlp_coeff_23, 32),
            to_signed(in_qlp_coeff_24, 32),
            to_signed(in_qlp_coeff_25, 32),
            to_signed(in_qlp_coeff_26, 32),
            to_signed(in_qlp_coeff_27, 32),
            to_signed(in_qlp_coeff_28, 32),
            to_signed(in_qlp_coeff_29, 32),
            to_signed(in_qlp_coeff_30, 32),
            to_signed(in_qlp_coeff_31, 32),
        ]
        old_history = self.history
        old_valid_history = self.valid_history
        result = None
        if old_valid_history >= order:
            total = 0
            for j in range(order):
                total = to_signed(total + coeff[j] * old_history[j], 32)
            residual = to_signed(sample_n - asr32(total, lp_quantization), 32)
            result = {"out_residual": to_unsigned(residual, 32), "out_error": 0}
        self.history = [sample_n] + old_history[:31]
        self.valid_history = old_valid_history + 1 if old_valid_history < 32 else 32
        return result
