def to_unsigned(v, b):
    return v & ((1 << b) - 1)


class Hamming3126Codec:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_encode, in_symbol):
        in_encode = to_unsigned(in_encode, 1)
        in_symbol = to_unsigned(in_symbol, 31)

        def parity32(x):
            x = to_unsigned(x, 32)
            x ^= x >> 16
            x ^= x >> 8
            x ^= x >> 4
            x ^= x >> 2
            x ^= x >> 1
            return x & 1

        if in_encode:
            sym_dec = in_symbol & 0x03FFFFFF
            p1 = parity32(sym_dec & 0x036AD555)
            p2 = parity32(sym_dec & 0x02D9B333)
            p4 = parity32(sym_dec & 0x01C78F0F)
            p8 = parity32(sym_dec & 0x003F80FF)
            p16 = parity32(sym_dec & 0x00007FFF)
            encoded_word = (
                ((sym_dec & 0x00007FFF) << 0)
                | ((sym_dec & 0x003F8000) << 1)
                | ((sym_dec & 0x01C00000) << 2)
                | ((sym_dec & 0x02000000) << 3)
                | (p1 << 30)
                | (p2 << 29)
                | (p4 << 27)
                | (p8 << 23)
                | (p16 << 15)
            )
            return {"out_symbol": to_unsigned(encoded_word, 31)}

        sym_enc = in_symbol
        s1 = parity32(sym_enc & 0x55555555)
        s2 = parity32(sym_enc & 0x33333333)
        s4 = parity32(sym_enc & 0x0F0F0F0F)
        s8 = parity32(sym_enc & 0x00FF00FF)
        s16 = parity32(sym_enc & 0x0000FFFF)
        z = (s16 << 4) | (s8 << 3) | (s4 << 2) | (s2 << 1) | s1
        if z != 0:
            sym_enc ^= 1 << (31 - z)
        sym_dec = (
            ((sym_enc & 0x00007FFF) >> 0)
            | ((sym_enc & 0x007F0000) >> 1)
            | ((sym_enc & 0x07000000) >> 2)
            | ((sym_enc & 0x10000000) >> 3)
        )
        return {"out_symbol": to_unsigned(sym_dec, 26)}
