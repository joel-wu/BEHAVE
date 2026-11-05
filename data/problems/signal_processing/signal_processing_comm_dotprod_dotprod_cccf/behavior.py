from fp_lib import fp32_add, fp32_mul, fp32_sub


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class signal_processing_comm_dotprod_dotprod_cccf:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_x0_re,
        in_x0_im,
        in_x1_re,
        in_x1_im,
        in_x2_re,
        in_x2_im,
        in_x3_re,
        in_x3_im,
        in_h0_re,
        in_h0_im,
        in_h1_re,
        in_h1_im,
        in_h2_re,
        in_h2_im,
        in_h3_re,
        in_h3_im,
    ):
        p0_rr = fp32_mul(in_x0_re, in_h0_re)
        p0_ii = fp32_mul(in_x0_im, in_h0_im)
        p0_ri = fp32_mul(in_x0_re, in_h0_im)
        p0_ir = fp32_mul(in_x0_im, in_h0_re)
        p0_re = fp32_sub(p0_rr, p0_ii)
        p0_im = fp32_add(p0_ri, p0_ir)

        p1_rr = fp32_mul(in_x1_re, in_h1_re)
        p1_ii = fp32_mul(in_x1_im, in_h1_im)
        p1_ri = fp32_mul(in_x1_re, in_h1_im)
        p1_ir = fp32_mul(in_x1_im, in_h1_re)
        p1_re = fp32_sub(p1_rr, p1_ii)
        p1_im = fp32_add(p1_ri, p1_ir)

        p2_rr = fp32_mul(in_x2_re, in_h2_re)
        p2_ii = fp32_mul(in_x2_im, in_h2_im)
        p2_ri = fp32_mul(in_x2_re, in_h2_im)
        p2_ir = fp32_mul(in_x2_im, in_h2_re)
        p2_re = fp32_sub(p2_rr, p2_ii)
        p2_im = fp32_add(p2_ri, p2_ir)

        p3_rr = fp32_mul(in_x3_re, in_h3_re)
        p3_ii = fp32_mul(in_x3_im, in_h3_im)
        p3_ri = fp32_mul(in_x3_re, in_h3_im)
        p3_ir = fp32_mul(in_x3_im, in_h3_re)
        p3_re = fp32_sub(p3_rr, p3_ii)
        p3_im = fp32_add(p3_ri, p3_ir)

        sum1_re = fp32_add(p0_re, p1_re)
        sum2_re = fp32_add(sum1_re, p2_re)
        sum3_re = fp32_add(sum2_re, p3_re)

        sum1_im = fp32_add(p0_im, p1_im)
        sum2_im = fp32_add(sum1_im, p2_im)
        sum3_im = fp32_add(sum2_im, p3_im)

        return {
            "out_y_re": to_unsigned(sum3_re, 32),
            "out_y_im": to_unsigned(sum3_im, 32),
        }