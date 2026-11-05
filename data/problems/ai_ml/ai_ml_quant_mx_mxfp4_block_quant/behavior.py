# Midpoints multiplied by four, between consecutive finite E2M1 codes.
MIDPOINTS = (1, 3, 5, 7, 10, 14, 20)


def quantize_one(value, scale):
    if scale == 255:
        return 0
    sign = (value >> 12) & 8
    exponent = (value >> 7) & 255
    fraction = value & 127
    significand = fraction if exponent == 0 else 128 + fraction
    shift = -scale - 4 if exponent == 0 else exponent - scale - 5
    payload = 0
    # Compare 4*abs(x/scale) to integer midpoints. Values below 1/4
    # are exactly zero after rounding; this also bounds every shift.
    if shift >= -8:
        if shift >= 0:
            magnitude = significand << shift
            for k in range(7):
                boundary = MIDPOINTS[k]
                if magnitude > boundary or (magnitude == boundary and (k & 1)):
                    payload = k + 1
        else:
            for k in range(7):
                boundary = MIDPOINTS[k] << (-shift)
                if significand > boundary or (significand == boundary and (k & 1)):
                    payload = k + 1
    return sign | payload


class ai_ml_quant_mx_mxfp4_block_quant:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15, in_x16, in_x17, in_x18, in_x19, in_x20, in_x21, in_x22, in_x23, in_x24, in_x25, in_x26, in_x27, in_x28, in_x29, in_x30, in_x31):
        xs = [in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15, in_x16, in_x17, in_x18, in_x19, in_x20, in_x21, in_x22, in_x23, in_x24, in_x25, in_x26, in_x27, in_x28, in_x29, in_x30, in_x31]
        maximum = 0
        for i in range(32):
            magnitude = xs[i] & 32767
            if magnitude > maximum:
                maximum = magnitude
        scale = (maximum >> 7) - 2
        if scale < 0:
            scale = 0
        if maximum >= 32640:
            scale = 255
        q = [0] * 32
        for i in range(32):
            q[i] = quantize_one(xs[i], scale)
        return {
            "out_scale": scale,
            "out_q0": q[0] | (q[1] << 4),
            "out_q1": q[2] | (q[3] << 4),
            "out_q2": q[4] | (q[5] << 4),
            "out_q3": q[6] | (q[7] << 4),
            "out_q4": q[8] | (q[9] << 4),
            "out_q5": q[10] | (q[11] << 4),
            "out_q6": q[12] | (q[13] << 4),
            "out_q7": q[14] | (q[15] << 4),
            "out_q8": q[16] | (q[17] << 4),
            "out_q9": q[18] | (q[19] << 4),
            "out_q10": q[20] | (q[21] << 4),
            "out_q11": q[22] | (q[23] << 4),
            "out_q12": q[24] | (q[25] << 4),
            "out_q13": q[26] | (q[27] << 4),
            "out_q14": q[28] | (q[29] << 4),
            "out_q15": q[30] | (q[31] << 4)
        }
