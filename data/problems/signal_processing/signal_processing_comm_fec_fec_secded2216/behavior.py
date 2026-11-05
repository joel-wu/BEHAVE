P_ROWS = [0x993C, 0x3E8A, 0xEE60, 0xE1D1, 0x13C7, 0x443F]

SYNDROME_W1 = {
    0x07: 0,
    0x13: 1,
    0x23: 2,
    0x31: 3,
    0x25: 4,
    0x29: 5,
    0x0E: 6,
    0x16: 7,
    0x26: 8,
    0x1A: 9,
    0x19: 10,
    0x38: 11,
    0x32: 12,
    0x1C: 13,
    0x0D: 14,
    0x2C: 15,
    0x01: 16,
    0x02: 17,
    0x04: 18,
    0x08: 19,
    0x10: 20,
    0x20: 21,
}


def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def popcount16(x):
    c = 0
    for i in range(16):
        c += (x >> i) & 1
    return c


class SignalProcessingCommFecFecSecded2216:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_encode, in_parity, in_data0, in_data1):
        in_encode = to_unsigned(in_encode, 1)
        in_parity = to_unsigned(in_parity, 6)
        in_data0 = to_unsigned(in_data0, 8)
        in_data1 = to_unsigned(in_data1, 8)

        message = (in_data0 << 8) | in_data1

        parity = 0
        for i in range(6):
            parity = ((parity << 1) | (popcount16(P_ROWS[i] & message) & 1))
        parity = to_unsigned(parity, 6)

        if in_encode:
            return {
                "out_parity": parity,
                "out_data0": in_data0,
                "out_data1": in_data1,
                "out_status": 0,
            }

        syndrome = 0
        for i in range(6):
            pbit = (in_parity >> (5 - i)) & 1
            mbit = popcount16(P_ROWS[i] & message) & 1
            syndrome = (syndrome << 1) | (pbit ^ mbit)
        syndrome = to_unsigned(syndrome, 6)

        if syndrome == 0:
            status = 0
            e0 = 0
            e1 = 0
            e2 = 0
        elif syndrome in SYNDROME_W1:
            status = 1
            n = SYNDROME_W1[syndrome]
            quot = 0
            rem = n
            if n >= 16:
                quot = 2
                rem = n - 16
            elif n >= 8:
                quot = 1
                rem = n - 8
            e0 = 0
            e1 = 0
            e2 = 0
            bit = 1 << rem
            if quot == 0:
                e2 = bit
            elif quot == 1:
                e1 = bit
            else:
                e0 = bit
        else:
            status = 2
            e0 = 0
            e1 = 0
            e2 = 0

        return {
            "out_parity": to_unsigned(in_parity ^ e0, 6),
            "out_data0": to_unsigned(in_data0 ^ e1, 8),
            "out_data1": to_unsigned(in_data1 ^ e2, 8),
            "out_status": to_unsigned(status, 2),
        }