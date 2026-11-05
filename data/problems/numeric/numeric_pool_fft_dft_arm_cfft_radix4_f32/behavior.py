from fp_lib import fp32_add, fp32_sub, fp32_mul

ONE_BY_FOUR = 0x3E800000


class Radix4FftButterflyF32:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_inverse,
        in_xa_re,
        in_xa_im,
        in_xb_re,
        in_xb_im,
        in_xc_re,
        in_xc_im,
        in_xd_re,
        in_xd_im,
        in_w1_re,
        in_w1_im,
        in_w2_re,
        in_w2_im,
        in_w3_re,
        in_w3_im,
    ):
        Xaplusc = fp32_add(in_xa_re, in_xc_re)
        Xaminusc = fp32_sub(in_xa_re, in_xc_re)
        Yaplusc = fp32_add(in_xa_im, in_xc_im)
        Yaminusc = fp32_sub(in_xa_im, in_xc_im)
        Xbplusd = fp32_add(in_xb_re, in_xd_re)
        Xbminusd = fp32_sub(in_xb_re, in_xd_re)
        Ybplusd = fp32_add(in_xb_im, in_xd_im)
        Ybminusd = fp32_sub(in_xb_im, in_xd_im)

        y0_re_pre = fp32_add(Xaplusc, Xbplusd)
        y0_im_pre = fp32_add(Yaplusc, Ybplusd)

        if in_inverse:
            b1_re_pre = fp32_sub(Xaminusc, Ybminusd)
            b1_im_pre = fp32_add(Yaminusc, Xbminusd)
            b2_re_pre = fp32_sub(Xaplusc, Xbplusd)
            b2_im_pre = fp32_sub(Yaplusc, Ybplusd)
            b3_re_pre = fp32_add(Xaminusc, Ybminusd)
            b3_im_pre = fp32_sub(Yaminusc, Xbminusd)

            t20 = fp32_mul(b2_re_pre, in_w2_re)
            t21 = fp32_mul(b2_im_pre, in_w2_re)
            t22 = fp32_mul(b2_im_pre, in_w2_im)
            t23 = fp32_mul(b2_re_pre, in_w2_im)
            y1_re_pre = fp32_sub(t20, t22)
            y1_im_pre = fp32_add(t21, t23)

            t10 = fp32_mul(b1_re_pre, in_w1_re)
            t11 = fp32_mul(b1_im_pre, in_w1_re)
            t12 = fp32_mul(b1_im_pre, in_w1_im)
            t13 = fp32_mul(b1_re_pre, in_w1_im)
            y2_re_pre = fp32_sub(t10, t12)
            y2_im_pre = fp32_add(t11, t13)

            t30 = fp32_mul(b3_re_pre, in_w3_re)
            t31 = fp32_mul(b3_im_pre, in_w3_re)
            t32 = fp32_mul(b3_im_pre, in_w3_im)
            t33 = fp32_mul(b3_re_pre, in_w3_im)
            y3_re_pre = fp32_sub(t30, t32)
            y3_im_pre = fp32_add(t31, t33)

            y0_re = fp32_mul(y0_re_pre, ONE_BY_FOUR)
            y0_im = fp32_mul(y0_im_pre, ONE_BY_FOUR)
            y1_re = fp32_mul(y1_re_pre, ONE_BY_FOUR)
            y1_im = fp32_mul(y1_im_pre, ONE_BY_FOUR)
            y2_re = fp32_mul(y2_re_pre, ONE_BY_FOUR)
            y2_im = fp32_mul(y2_im_pre, ONE_BY_FOUR)
            y3_re = fp32_mul(y3_re_pre, ONE_BY_FOUR)
            y3_im = fp32_mul(y3_im_pre, ONE_BY_FOUR)
        else:
            b1_re_pre = fp32_add(Xaminusc, Ybminusd)
            b1_im_pre = fp32_sub(Yaminusc, Xbminusd)
            b2_re_pre = fp32_sub(Xaplusc, Xbplusd)
            b2_im_pre = fp32_sub(Yaplusc, Ybplusd)
            b3_re_pre = fp32_sub(Xaminusc, Ybminusd)
            b3_im_pre = fp32_add(Xbminusd, Yaminusc)

            t20 = fp32_mul(b2_re_pre, in_w2_re)
            t21 = fp32_mul(b2_im_pre, in_w2_re)
            t22 = fp32_mul(b2_im_pre, in_w2_im)
            t23 = fp32_mul(b2_re_pre, in_w2_im)
            y1_re = fp32_add(t20, t22)
            y1_im = fp32_sub(t21, t23)

            t10 = fp32_mul(b1_re_pre, in_w1_re)
            t11 = fp32_mul(b1_im_pre, in_w1_re)
            t12 = fp32_mul(b1_im_pre, in_w1_im)
            t13 = fp32_mul(b1_re_pre, in_w1_im)
            y2_re = fp32_add(t10, t12)
            y2_im = fp32_sub(t11, t13)

            t30 = fp32_mul(b3_re_pre, in_w3_re)
            t31 = fp32_mul(b3_im_pre, in_w3_re)
            t32 = fp32_mul(b3_im_pre, in_w3_im)
            t33 = fp32_mul(b3_re_pre, in_w3_im)
            y3_re = fp32_add(t30, t32)
            y3_im = fp32_sub(t31, t33)

            y0_re = y0_re_pre
            y0_im = y0_im_pre

        return {
            "out_y0_re": y0_re,
            "out_y0_im": y0_im,
            "out_y1_re": y1_re,
            "out_y1_im": y1_im,
            "out_y2_re": y2_re,
            "out_y2_im": y2_im,
            "out_y3_re": y3_re,
            "out_y3_im": y3_im,
        }