def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class ComplexConjugateDotProduct:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x_mem = [0] * 16
        self.y_mem = [0] * 16

    def process(self, in_n, in_incx, in_incy, in_x_base, in_y_base):
        n = min(in_n & 0x7, 4)
        incx = to_signed(in_incx, 4)
        incy = to_signed(in_incy, 4)
        x_base = in_x_base & 0xF
        y_base = in_y_base & 0xF

        if n == 0:
            return {"out_real": 0, "out_imag": 0}

        x_start = x_base if incx >= 0 else x_base + (1 - n) * incx
        y_start = y_base if incy >= 0 else y_base + (1 - n) * incy

        sum_real = 0
        sum_imag = 0

        for k in range(4):
            if k < n:
                x_addr = (x_start + k * incx) & 0xF
                y_addr = (y_start + k * incy) & 0xF

                x = self.x_mem[x_addr]
                y = self.y_mem[y_addr]
                xr = x & ((1 << 64) - 1)
                xi = x >> 64
                yr = y & ((1 << 64) - 1)
                yi = y >> 64

                xr = to_signed(xr, 64)
                xi = to_signed(xi, 64)
                yr = to_signed(yr, 64)
                yi = to_signed(yi, 64)

                sum_real += xr * yr + xi * yi
                sum_imag += xr * yi - xi * yr

        return {
            "out_real": to_unsigned(sum_real, 128),
            "out_imag": to_unsigned(sum_imag, 128),
        }
