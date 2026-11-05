def to_unsigned(v, b):
    return v & ((1 << b) - 1)


TABLE = {
    1: {
        "out_p0_re": 0xBF99BE61, "out_p0_im": 0x00000000,
        "out_p1_re": 0x00000000, "out_p1_im": 0x00000000,
        "out_p2_re": 0x00000000, "out_p2_im": 0x00000000,
        "out_p3_re": 0x00000000, "out_p3_im": 0x00000000,
        "out_p4_re": 0x00000000, "out_p4_im": 0x00000000,
        "out_p5_re": 0x00000000, "out_p5_im": 0x00000000,
        "out_p6_re": 0x00000000, "out_p6_im": 0x00000000,
        "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
        "out_k_re": 0xBF99BE61, "out_k_im": 0x00000000,
    },
    2: {
        "out_p0_re": 0xBF85255B, "out_p0_im": 0x3F19BE61,
        "out_p1_re": 0xBF85255B, "out_p1_im": 0xBF19BE61,
        "out_p2_re": 0x00000000, "out_p2_im": 0x00000000,
        "out_p3_re": 0x00000000, "out_p3_im": 0x00000000,
        "out_p4_re": 0x00000000, "out_p4_im": 0x00000000,
        "out_p5_re": 0x00000000, "out_p5_im": 0x00000000,
        "out_p6_re": 0x00000000, "out_p6_im": 0x00000000,
        "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
        "out_k_re": 0x3FB8AA3B, "out_k_im": 0x00000000,
    },
    3: {
        "out_p0_re": 0xBF7CDF6F, "out_p0_im": 0x3F713FD5,
        "out_p1_re": 0xBF7CDF6F, "out_p1_im": 0xBF713FD5,
        "out_p2_re": 0xBF9FAA2F, "out_p2_im": 0x00000000,
        "out_p3_re": 0x00000000, "out_p3_im": 0x00000000,
        "out_p4_re": 0x00000000, "out_p4_im": 0x00000000,
        "out_p5_re": 0x00000000, "out_p5_im": 0x00000000,
        "out_p6_re": 0x00000000, "out_p6_im": 0x00000000,
        "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
        "out_k_re": 0xC014CA9E, "out_k_im": 0x00000000,
    },
    4: {
        "out_p0_re": 0xBF74803A, "out_p0_im": 0x3F9A6BEF,
        "out_p1_re": 0xBF74803A, "out_p1_im": 0xBF9A6BEF,
        "out_p2_re": 0xBFA84C3A, "out_p2_im": 0x3EC99423,
        "out_p3_re": 0xBFA84C3A, "out_p3_im": 0xBEC99423,
        "out_p4_re": 0x00000000, "out_p4_im": 0x00000000,
        "out_p5_re": 0x00000000, "out_p5_im": 0x00000000,
        "out_p6_re": 0x00000000, "out_p6_im": 0x00000000,
        "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
        "out_k_re": 0x408EB8F1, "out_k_im": 0x00000000,
    },
    5: {
        "out_p0_re": 0xBF6E44F2, "out_p0_im": 0x3FB701E5,
        "out_p1_re": 0xBF6E44F2, "out_p1_im": 0xBFB701E5,
        "out_p2_re": 0xBFABC7DC, "out_p2_im": 0x3F329D8D,
        "out_p3_re": 0xBFABC7DC, "out_p3_im": 0xBF329D8D,
        "out_p4_re": 0xBFBAE33E, "out_p4_im": 0x00000000,
        "out_p5_re": 0x00000000, "out_p5_im": 0x00000000,
        "out_p6_re": 0x00000000, "out_p6_im": 0x00000000,
        "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
        "out_k_re": 0xC11B8DE5, "out_k_im": 0xB4BAE33E,
    },
    6: {
        "out_p0_re": 0xBF694121, "out_p0_im": 0x3FD0428A,
        "out_p1_re": 0xBF694121, "out_p1_im": 0xBFD0428A,
        "out_p2_re": 0xBFAD2BA5, "out_p2_im": 0x3F737BEF,
        "out_p3_re": 0xBFAD2BA5, "out_p3_im": 0xBF737BEF,
        "out_p4_re": 0xBFC4EF45, "out_p4_im": 0x3EA0DAF1,
        "out_p5_re": 0xBFC4EF45, "out_p5_im": 0xBEA0DAF1,
        "out_p6_re": 0x00000000, "out_p6_im": 0x00000000,
        "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
        "out_k_re": 0x41BB9C9C, "out_k_im": 0x00000000,
    },
    7: {
        "out_p0_re": 0xBF6509F7, "out_p0_im": 0x3FE72492,
        "out_p1_re": 0xBF6509F7, "out_p1_im": 0xBFE72492,
        "out_p2_re": 0xBFAD8DD7, "out_p2_im": 0x3F95F9A6,
        "out_p3_re": 0xBFAD8DD7, "out_p3_im": 0xBF95F9A6,
        "out_p4_re": 0xBFCAE5BC, "out_p4_im": 0x3F145444,
        "out_p5_re": 0xBFCAE5BC, "out_p5_im": 0xBF145444,
        "out_p6_re": 0xBFD40046, "out_p6_im": 0x00000000,
        "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
        "out_k_re": 0xC2761D44, "out_k_im": 0x00000000,
    },
    8: {
        "out_p0_re": 0xBF616528, "out_p0_im": 0x3FFC3A4A,
        "out_p1_re": 0xBF616528, "out_p1_im": 0xBFFC3A4A,
        "out_p2_re": 0xBFAD67D2, "out_p2_im": 0x3FAF3CD8,
        "out_p3_re": 0xBFAD67D2, "out_p3_im": 0xBFAF3CD8,
        "out_p4_re": 0xBFCE9D19, "out_p4_im": 0x3F4FB4A9,
        "out_p5_re": 0xBFCE9D19, "out_p5_im": 0xBF4FB4A9,
        "out_p6_re": 0xBFDDD1B7, "out_p6_im": 0x3E89C3CC,
        "out_p7_re": 0xBFDDD1B7, "out_p7_im": 0xBE89C3CC,
        "out_k_re": 0x432D7506, "out_k_im": 0x36C00000,
    },
}

ZERO = {
    "out_p0_re": 0x00000000, "out_p0_im": 0x00000000,
    "out_p1_re": 0x00000000, "out_p1_im": 0x00000000,
    "out_p2_re": 0x00000000, "out_p2_im": 0x00000000,
    "out_p3_re": 0x00000000, "out_p3_im": 0x00000000,
    "out_p4_re": 0x00000000, "out_p4_im": 0x00000000,
    "out_p5_re": 0x00000000, "out_p5_im": 0x00000000,
    "out_p6_re": 0x00000000, "out_p6_im": 0x00000000,
    "out_p7_re": 0x00000000, "out_p7_im": 0x00000000,
    "out_k_re": 0x00000000, "out_k_im": 0x00000000,
}


class SignalProcessingCommFilterBessel:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_n):
        n = in_n & 0xFF
        result = TABLE.get(n, ZERO)
        return {k: to_unsigned(v, 32) for k, v in result.items()}
