from fp_lib import fp32_add, fp32_mul, fp32_div, fp32_cmp

QWORDS = (3235905536, 3233808384, 3231711232, 3229614080, 3225419776, 3221225472, 3212836864, 0, 1065353216, 1073741824, 1077936128, 1082130432, 1084227584, 1086324736, 1088421888)

def round_integer(word):
    exponent = (word >> 23) & 255
    if exponent < 126:
        return 0
    # Called only for finite values with magnitude less than 256.
    shift = 150 - exponent
    mantissa = (word & 0x7FFFFF) | 0x800000
    integer = mantissa >> shift
    remainder = mantissa & ((1 << shift) - 1)
    halfway = 1 << (shift - 1)
    if remainder > halfway or (remainder == halfway and integer & 1):
        integer = integer + 1
    return -integer if word >> 31 else integer


class AWQFusedDequantMatMul:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_w00, in_w01, in_w02, in_w03, in_w10, in_w11, in_w12, in_w13, in_w20, in_w21, in_w22, in_w23, in_w30, in_w31, in_w32, in_w33, in_s0, in_s1, in_s2, in_s3):
        x = [in_x0, in_x1, in_x2, in_x3]
        w = [in_w00, in_w01, in_w02, in_w03, in_w10, in_w11, in_w12, in_w13, in_w20, in_w21, in_w22, in_w23, in_w30, in_w31, in_w32, in_w33]
        s = [in_s0, in_s1, in_s2, in_s3]
        bad = False
        for value in x + w:
            if ((value >> 23) & 255) == 255:
                bad = True
        for value in s:
            if value >> 31 or not 0 < ((value >> 23) & 255) < 255:
                bad = True
        scaled_x = [fp32_div(x[j], s[j]) for j in range(4)]
        y = [0] * 4
        loss = 0
        packed_q = 0
        packed_scale = 0
        for i in range(4):
            scaled_w = [fp32_mul(w[4 * i + j], s[j]) for j in range(4)]
            peak = 0
            for value in scaled_w:
                if ((value >> 23) & 255) == 255:
                    bad = True
                if fp32_cmp(value & 0x7FFFFFFF, peak) > 0:
                    peak = value & 0x7FFFFFFF
            scale = fp32_div(peak, 0x40E00000) if peak != 0 else 0x3F800000
            if peak != 0 and ((scale >> 23) & 255) == 0:
                bad = True
            packed_scale = packed_scale | (scale << (32 * i))
            original = 0
            for j in range(4):
                ratio = fp32_div(scaled_w[j], scale)
                magnitude = ratio & 0x7FFFFFFF
                if magnitude >= 0x7F800000:
                    bad = True
                    quant = 0
                elif magnitude >= 0x40E00000:
                    quant = -7 if ratio >> 31 else 7
                else:
                    quant = round_integer(ratio)
                packed_q = packed_q | ((quant & 15) << (4 * (4 * i + j)))
                dequant = fp32_mul(QWORDS[quant + 7], scale)
                y[i] = fp32_add(y[i], fp32_mul(scaled_x[j], dequant))
                original = fp32_add(original, fp32_mul(x[j], w[4 * i + j]))
            error = fp32_add(original, y[i] ^ 0x80000000)
            loss = fp32_add(loss, fp32_mul(error, error))
        loss = fp32_mul(loss, 0x3E800000)
        for value in scaled_x + y + [loss]:
            if ((value >> 23) & 255) == 255:
                bad = True
        return {"out_y0": 0 if bad else y[0], "out_y1": 0 if bad else y[1],
                "out_y2": 0 if bad else y[2], "out_y3": 0 if bad else y[3],
                "out_loss": 0 if bad else loss, "out_qweight": 0 if bad else packed_q,
                "out_quant_scale": 0 if bad else packed_scale, "out_error": 1 if bad else 0}
