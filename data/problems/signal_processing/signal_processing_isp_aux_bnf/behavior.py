def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class SignalProcessingIspAuxBnf:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(
        self,
        in_p00, in_p01, in_p02, in_p03, in_p04,
        in_p10, in_p11, in_p12, in_p13, in_p14,
        in_p20, in_p21, in_p22, in_p23, in_p24,
        in_p30, in_p31, in_p32, in_p33, in_p34,
        in_p40, in_p41, in_p42, in_p43, in_p44,
        in_dw00, in_dw01, in_dw02, in_dw03, in_dw04,
        in_dw10, in_dw11, in_dw12, in_dw13, in_dw14,
        in_dw20, in_dw21, in_dw22, in_dw23, in_dw24,
        in_dw30, in_dw31, in_dw32, in_dw33, in_dw34,
        in_dw40, in_dw41, in_dw42, in_dw43, in_dw44,
        in_rw0, in_rw1, in_rw2, in_rw3,
        in_rthres0, in_rthres1, in_rthres2,
        in_clip
    ):
        p = [
            [in_p00, in_p01, in_p02, in_p03, in_p04],
            [in_p10, in_p11, in_p12, in_p13, in_p14],
            [in_p20, in_p21, in_p22, in_p23, in_p24],
            [in_p30, in_p31, in_p32, in_p33, in_p34],
            [in_p40, in_p41, in_p42, in_p43, in_p44],
        ]
        dw = [
            [in_dw00, in_dw01, in_dw02, in_dw03, in_dw04],
            [in_dw10, in_dw11, in_dw12, in_dw13, in_dw14],
            [in_dw20, in_dw21, in_dw22, in_dw23, in_dw24],
            [in_dw30, in_dw31, in_dw32, in_dw33, in_dw34],
            [in_dw40, in_dw41, in_dw42, in_dw43, in_dw44],
        ]
        c = in_p22
        num = 0
        den = 0
        for i in range(5):
            for j in range(5):
                d = p[i][j] - c if p[i][j] >= c else c - p[i][j]
                if d >= in_rthres0:
                    rwsel = in_rw0
                elif d < in_rthres0 and d >= in_rthres1:
                    rwsel = in_rw1
                elif d < in_rthres1 and d >= in_rthres2:
                    rwsel = in_rw2
                else:
                    rwsel = in_rw3
                w = rwsel * dw[i][j]
                num += p[i][j] * w
                den += w
        f = 0 if den == 0 else num // den
        out_pixel = in_clip if f > in_clip else f
        return {"out_pixel": to_unsigned(out_pixel, 16)}