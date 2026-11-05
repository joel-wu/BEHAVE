def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


ZETAS = [
    -1044, -758, -359, -1517, 1493, 1422, 287, 202,
    -171, 622, 1577, 182, 962, -1202, -1474, 1468,
    573, -1325, 264, 383, -829, 1458, -1602, -130,
    -681, 1017, 732, 608, -1542, 411, -205, -1571,
    1223, 652, -552, 1015, -1293, 1491, -282, -1544,
    516, -8, -320, -666, -1618, -1162, 126, 1469,
    -853, -90, -271, 830, 107, -1421, -247, -951,
    -398, 961, -1508, -725, 448, -1065, 677, -1275,
    -1103, 430, 555, 843, -1251, 871, 1550, 105,
    422, 587, 177, -235, -291, -460, 1574, 1653,
    -246, 778, 1159, -147, -777, 1483, -602, 1119,
    -1590, 644, -872, 349, 418, 329, -156, -75,
    817, 1097, 603, 610, 1322, -1285, -1465, 384,
    -1215, -136, 1218, -1335, -874, 220, -1187, -1659,
    -1185, -1530, -1278, 794, -1510, -854, -870, 478,
    -108, -308, 996, 991, 958, -1460, 1522, 1628,
]


class KyberInvnttButterfly:
    def __init__(self):
        self.reset()

    def reset(self):
        self.out_a = 0
        self.out_b = 0

    def montgomery_reduce(self, t):
        u = to_signed(t * 62209, 16)
        r = t - u * 3329
        return r >> 16

    def barrett_reduce(self, x):
        t = ((20159 * x) + (1 << 25)) >> 26
        return x - t * 3329

    def process(self, in_twiddle_index, in_a, in_b):
        a = to_signed(in_a, 16)
        b = to_signed(in_b, 16)
        zeta = ZETAS[in_twiddle_index & 0x7F]
        out_a_signed = self.barrett_reduce(to_signed(a + b, 16))
        out_b_signed = self.montgomery_reduce(zeta * to_signed(b - a, 16))
        self.out_a = to_unsigned(out_a_signed, 16)
        self.out_b = to_unsigned(out_b_signed, 16)
        return {"out_a": self.out_a, "out_b": self.out_b}
