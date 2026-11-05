from fp_lib import fp32_mul, fp32_is_nan, fp32_is_inf

FP32_32768 = 0x47000000


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class FloatToQ15:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_f32):
        if fp32_is_nan(in_f32):
            q15 = 32767
        elif fp32_is_inf(in_f32):
            q15 = 32767 if ((in_f32 >> 31) & 1) == 0 else -32768
        else:
            p = fp32_mul(in_f32, FP32_32768)
            sign = (p >> 31) & 1
            exp = (p >> 23) & 0xFF
            frac = p & 0x7FFFFF
            if exp == 0:
                mag = 0
            else:
                e = exp - 127
                mant = (1 << 23) | frac
                if e < 0:
                    mag = 0
                elif e >= 31:
                    q15 = 32767 if sign == 0 else -32768
                    return {"out_q15": to_unsigned(q15, 16)}
                elif e >= 23:
                    mag = mant << (e - 23)
                else:
                    mag = mant >> (23 - e)
            val = -mag if sign else mag
            if val > 32767:
                q15 = 32767
            elif val < -32768:
                q15 = -32768
            else:
                q15 = val
        return {"out_q15": to_unsigned(q15, 16)}