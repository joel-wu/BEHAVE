from fp_lib import fp32_add, fp32_sub, fp32_mul, fp32_div, fp32_cmp, fp32_to_int32_sat


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def fp32_from_uint8(k):
    sign = 0
    if k == 0:
        return 0
    if k < 2:
        exp = 127
        frac = 0
    elif k < 4:
        exp = 128
        frac = (k - 2) << 22
    elif k < 8:
        exp = 129
        frac = (k - 4) << 21
    elif k < 16:
        exp = 130
        frac = (k - 8) << 20
    elif k < 32:
        exp = 131
        frac = (k - 16) << 19
    elif k < 64:
        exp = 132
        frac = (k - 32) << 18
    elif k < 128:
        exp = 133
        frac = (k - 64) << 17
    else:
        exp = 134
        frac = (k - 128) << 16
    return (sign << 31) | (exp << 23) | frac


class LinearInterpF32:
    def __init__(self, mem_init=None):
        self.table = [0] * 256
        if mem_init is not None:
            image = mem_init.get("table", {})
            for address in range(256):
                self.table[address] = image.get(address, 0) & 0xFFFFFFFF
        self.reset()

    def reset(self):
        self.pending = 0

    def process(self, in_x):
        x1 = 0x00000000
        xSpacing = 0x3B800000
        nValues = 256

        t = fp32_div(fp32_sub(in_x, x1), xSpacing)
        i = fp32_to_int32_sat(t)

        if fp32_cmp(in_x, x1) < 0:
            y = self.table[0]
        else:
            i_u = to_unsigned(i, 32)
            if i_u >= nValues - 1:
                y = self.table[nValues - 1]
            else:
                x0 = fp32_add(x1, fp32_mul(fp32_from_uint8(i_u), xSpacing))
                x1_seg = fp32_add(x1, fp32_mul(fp32_from_uint8(i_u + 1), xSpacing))
                y0 = self.table[i_u]
                y1 = self.table[i_u + 1]
                num = fp32_sub(y1, y0)
                den = fp32_sub(x1_seg, x0)
                frac = fp32_div(num, den)
                dx = fp32_sub(in_x, x0)
                y = fp32_add(y0, fp32_mul(dx, frac))
        return {"out_y": to_unsigned(y, 32)}
