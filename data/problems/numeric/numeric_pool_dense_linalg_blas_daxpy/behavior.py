class DaxpyElementUpdate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.dx = [0] * 256
        self.dy = [0] * 256

    def process(self, in_n, in_k, in_da, in_incx, in_incy):
        n = in_n & 0xFF
        k = in_k & 0xFF

        da_bits = in_da & 0xFFFFFFFFFFFFFFFF
        da = da_bits if da_bits < (1 << 63) else da_bits - (1 << 64)

        incx_bits = in_incx & 0xFF
        incx = incx_bits if incx_bits < (1 << 7) else incx_bits - (1 << 8)

        incy_bits = in_incy & 0xFF
        incy = incy_bits if incy_bits < (1 << 7) else incy_bits - (1 << 8)

        active = (n != 0) and (k < n) and (da != 0)
        if active:
            ix0 = 0 if incx >= 0 else (1 - n) * incx
            iy0 = 0 if incy >= 0 else (1 - n) * incy

            dx_req_addr = (ix0 + k * incx) & 0xFF
            dy_req_addr = (iy0 + k * incy) & 0xFF

            x_bits = self.dx[dx_req_addr] & 0xFFFFFFFFFFFFFFFF
            y_bits = self.dy[dy_req_addr] & 0xFFFFFFFFFFFFFFFF

            x = x_bits if x_bits < (1 << 63) else x_bits - (1 << 64)
            y = y_bits if y_bits < (1 << 63) else y_bits - (1 << 64)

            product_bits = (da * x) & 0xFFFFFFFFFFFFFFFF
            p = product_bits if product_bits < (1 << 63) else product_bits - (1 << 64)
            sum_bits = (y + p) & 0xFFFFFFFFFFFFFFFF

            self.dy[dy_req_addr] = sum_bits

        return {"out_done": 1}