def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CompressionFlacPredictFlacLpcPredictorOrderLe16:
    def __init__(self):
        self.reset()

    def reset(self):
        self._dummy = 0

    def process(
        self,
        in_order,
        in_lp_quantization,
        in_sample,
        in_warmup0,
        in_warmup1,
        in_warmup2,
        in_warmup3,
        in_warmup4,
        in_warmup5,
        in_warmup6,
        in_warmup7,
        in_warmup8,
        in_warmup9,
        in_warmup10,
        in_warmup11,
        in_warmup12,
        in_warmup13,
        in_warmup14,
        in_warmup15,
        in_qlp_coeff0,
        in_qlp_coeff1,
        in_qlp_coeff2,
        in_qlp_coeff3,
        in_qlp_coeff4,
        in_qlp_coeff5,
        in_qlp_coeff6,
        in_qlp_coeff7,
        in_qlp_coeff8,
        in_qlp_coeff9,
        in_qlp_coeff10,
        in_qlp_coeff11,
        in_qlp_coeff12,
        in_qlp_coeff13,
        in_qlp_coeff14,
        in_qlp_coeff15,
    ):
        order = in_order
        lp_quantization = in_lp_quantization & 0x1F
        sample = to_signed(in_sample, 32)
        history = [
            to_signed(in_warmup0, 32),
            to_signed(in_warmup1, 32),
            to_signed(in_warmup2, 32),
            to_signed(in_warmup3, 32),
            to_signed(in_warmup4, 32),
            to_signed(in_warmup5, 32),
            to_signed(in_warmup6, 32),
            to_signed(in_warmup7, 32),
            to_signed(in_warmup8, 32),
            to_signed(in_warmup9, 32),
            to_signed(in_warmup10, 32),
            to_signed(in_warmup11, 32),
            to_signed(in_warmup12, 32),
            to_signed(in_warmup13, 32),
            to_signed(in_warmup14, 32),
            to_signed(in_warmup15, 32),
        ]
        coeff = [
            to_signed(in_qlp_coeff0, 32),
            to_signed(in_qlp_coeff1, 32),
            to_signed(in_qlp_coeff2, 32),
            to_signed(in_qlp_coeff3, 32),
            to_signed(in_qlp_coeff4, 32),
            to_signed(in_qlp_coeff5, 32),
            to_signed(in_qlp_coeff6, 32),
            to_signed(in_qlp_coeff7, 32),
            to_signed(in_qlp_coeff8, 32),
            to_signed(in_qlp_coeff9, 32),
            to_signed(in_qlp_coeff10, 32),
            to_signed(in_qlp_coeff11, 32),
            to_signed(in_qlp_coeff12, 32),
            to_signed(in_qlp_coeff13, 32),
            to_signed(in_qlp_coeff14, 32),
            to_signed(in_qlp_coeff15, 32),
        ]
        total = 0
        for j in range(16):
            if j < order:
                total += coeff[j] * history[j]
        prediction = total >> lp_quantization
        residual = sample - prediction
        return {"out_residual": to_unsigned(residual, 32)}