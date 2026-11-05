def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class CompressionJpegQuantJpegQuantizeBlockInt16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def flss(self, val):
        val &= 0xFFFF
        if val == 0:
            return 0
        bit = 16
        if (val & 0xFF00) == 0:
            bit -= 8
            val = (val << 8) & 0xFFFF
        if (val & 0xF000) == 0:
            bit -= 4
            val = (val << 4) & 0xFFFF
        if (val & 0xC000) == 0:
            bit -= 2
            val = (val << 2) & 0xFFFF
        if (val & 0x8000) == 0:
            bit -= 1
        return bit

    def compute_reciprocal(self, wi):
        wi &= 0xFFFF
        if wi <= 1:
            return 1, 0, -16
        b = self.flss(wi) - 1
        r = 16 + b
        pow2r = 1 << r
        fq = pow2r // wi
        fr = pow2r - fq * wi
        corr = wi >> 1
        if fr == 0:
            fq >>= 1
            r -= 1
        elif fr <= (wi >> 1):
            corr += 1
        else:
            fq += 1
        return fq, corr, r - 16

    def quantize_one(self, qi, wi):
        qi = to_signed(qi, 16)
        recip, corr, shift = self.compute_reciprocal(wi)
        mag = -qi if qi < 0 else qi
        product = (mag + corr) * recip
        quot = product >> (shift + 16)
        result = -quot if qi < 0 else quot
        return to_unsigned(result, 16)

    def process(
        self,
        in_q00, in_q01, in_q02, in_q03, in_q04, in_q05, in_q06, in_q07,
        in_q08, in_q09, in_q10, in_q11, in_q12, in_q13, in_q14, in_q15,
        in_q16, in_q17, in_q18, in_q19, in_q20, in_q21, in_q22, in_q23,
        in_q24, in_q25, in_q26, in_q27, in_q28, in_q29, in_q30, in_q31,
        in_q32, in_q33, in_q34, in_q35, in_q36, in_q37, in_q38, in_q39,
        in_q40, in_q41, in_q42, in_q43, in_q44, in_q45, in_q46, in_q47,
        in_q48, in_q49, in_q50, in_q51, in_q52, in_q53, in_q54, in_q55,
        in_q56, in_q57, in_q58, in_q59, in_q60, in_q61, in_q62, in_q63,
        in_w00, in_w01, in_w02, in_w03, in_w04, in_w05, in_w06, in_w07,
        in_w08, in_w09, in_w10, in_w11, in_w12, in_w13, in_w14, in_w15,
        in_w16, in_w17, in_w18, in_w19, in_w20, in_w21, in_w22, in_w23,
        in_w24, in_w25, in_w26, in_w27, in_w28, in_w29, in_w30, in_w31,
        in_w32, in_w33, in_w34, in_w35, in_w36, in_w37, in_w38, in_w39,
        in_w40, in_w41, in_w42, in_w43, in_w44, in_w45, in_w46, in_w47,
        in_w48, in_w49, in_w50, in_w51, in_w52, in_w53, in_w54, in_w55,
        in_w56, in_w57, in_w58, in_w59, in_w60, in_w61, in_w62, in_w63
    ):
        q = [
            in_q00, in_q01, in_q02, in_q03, in_q04, in_q05, in_q06, in_q07,
            in_q08, in_q09, in_q10, in_q11, in_q12, in_q13, in_q14, in_q15,
            in_q16, in_q17, in_q18, in_q19, in_q20, in_q21, in_q22, in_q23,
            in_q24, in_q25, in_q26, in_q27, in_q28, in_q29, in_q30, in_q31,
            in_q32, in_q33, in_q34, in_q35, in_q36, in_q37, in_q38, in_q39,
            in_q40, in_q41, in_q42, in_q43, in_q44, in_q45, in_q46, in_q47,
            in_q48, in_q49, in_q50, in_q51, in_q52, in_q53, in_q54, in_q55,
            in_q56, in_q57, in_q58, in_q59, in_q60, in_q61, in_q62, in_q63,
        ]
        w = [
            in_w00, in_w01, in_w02, in_w03, in_w04, in_w05, in_w06, in_w07,
            in_w08, in_w09, in_w10, in_w11, in_w12, in_w13, in_w14, in_w15,
            in_w16, in_w17, in_w18, in_w19, in_w20, in_w21, in_w22, in_w23,
            in_w24, in_w25, in_w26, in_w27, in_w28, in_w29, in_w30, in_w31,
            in_w32, in_w33, in_w34, in_w35, in_w36, in_w37, in_w38, in_w39,
            in_w40, in_w41, in_w42, in_w43, in_w44, in_w45, in_w46, in_w47,
            in_w48, in_w49, in_w50, in_w51, in_w52, in_w53, in_w54, in_w55,
            in_w56, in_w57, in_w58, in_w59, in_w60, in_w61, in_w62, in_w63,
        ]
        c = [self.quantize_one(q[i], w[i]) for i in range(64)]
        return {
            "out_c00": c[0],   "out_c01": c[1],   "out_c02": c[2],   "out_c03": c[3],
            "out_c04": c[4],   "out_c05": c[5],   "out_c06": c[6],   "out_c07": c[7],
            "out_c08": c[8],   "out_c09": c[9],   "out_c10": c[10],  "out_c11": c[11],
            "out_c12": c[12],  "out_c13": c[13],  "out_c14": c[14],  "out_c15": c[15],
            "out_c16": c[16],  "out_c17": c[17],  "out_c18": c[18],  "out_c19": c[19],
            "out_c20": c[20],  "out_c21": c[21],  "out_c22": c[22],  "out_c23": c[23],
            "out_c24": c[24],  "out_c25": c[25],  "out_c26": c[26],  "out_c27": c[27],
            "out_c28": c[28],  "out_c29": c[29],  "out_c30": c[30],  "out_c31": c[31],
            "out_c32": c[32],  "out_c33": c[33],  "out_c34": c[34],  "out_c35": c[35],
            "out_c36": c[36],  "out_c37": c[37],  "out_c38": c[38],  "out_c39": c[39],
            "out_c40": c[40],  "out_c41": c[41],  "out_c42": c[42],  "out_c43": c[43],
            "out_c44": c[44],  "out_c45": c[45],  "out_c46": c[46],  "out_c47": c[47],
            "out_c48": c[48],  "out_c49": c[49],  "out_c50": c[50],  "out_c51": c[51],
            "out_c52": c[52],  "out_c53": c[53],  "out_c54": c[54],  "out_c55": c[55],
            "out_c56": c[56],  "out_c57": c[57],  "out_c58": c[58],  "out_c59": c[59],
            "out_c60": c[60],  "out_c61": c[61],  "out_c62": c[62],  "out_c63": c[63],
        }