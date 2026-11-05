def decode_scaled(code):
    exponent = (code >> 3) & 15
    fraction = code & 7
    magnitude = fraction if exponent == 0 else (8 + fraction) << (exponent - 1)
    return -magnitude if code & 128 else magnitude


def encode_product(numerator):
    # numerator represents a real value times 2^18. Codebook units are 2^-9.
    magnitude = abs(numerator)
    best = 0
    distance = magnitude
    for code in range(1, 127):
        difference = abs(magnitude - decode_scaled(code) * 512)
        if difference < distance or (difference == distance and (code & 1) == 0):
            distance = difference
            best = code
    return best | (128 if numerator < 0 else 0)


class AiMlQuantFp8Fp8E4m3Gemm:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_layout, in_alpha, in_beta, in_a0, in_b0, in_c0):
        a = [(in_a0 >> (8*i)) & 255 for i in range(4)]
        b = [(in_b0 >> (8*i)) & 255 for i in range(4)]
        c = [(in_c0 >> (8*i)) & 255 for i in range(4)]
        layout = in_layout & 3
        alpha = in_alpha & 0xFFFFFFFF
        beta = in_beta & 0xFFFFFFFF
        result = 0
        for row in range(2):
            for col in range(2):
                cc = c[2*row+col]
                bad = (cc & 127) == 127
                value = beta * decode_scaled(cc) * 512
                for k in range(2):
                    aa = a[2*k+row] if layout == 0 or layout == 3 else a[2*row+k]
                    bb = b[2*col+k] if layout == 2 else b[2*k+col]
                    if (aa & 127) == 127 or (bb & 127) == 127:
                        bad = True
                    value += alpha * decode_scaled(aa) * decode_scaled(bb)
                code = 127 if bad else encode_product(value)
                result |= code << (8*(2*row+col))
        return {"out_d0": result}
