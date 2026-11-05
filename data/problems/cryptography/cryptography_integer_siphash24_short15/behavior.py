# SPDX-License-Identifier: CC0-1.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def rol64(x, n):
    return ((x << n) | (x >> (64-n))) & 18446744073709551615

class SipHash24Short15:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_key, in_message, in_length):
        k0 = in_key & 18446744073709551615
        k1 = in_key >> 64
        v0 = 0x736f6d6570736575 ^ k0
        v1 = 0x646f72616e646f6d ^ k1
        v2 = 0x6c7967656e657261 ^ k0
        v3 = 0x7465646279746573 ^ k1
        for phase in range(3):
            if phase != 0 or in_length >= 8:
                word = 0
                if phase == 0:
                    word = in_message & 18446744073709551615
                elif phase == 1:
                    word = in_length << 56
                    for j in range(7):
                        if j < (in_length & 7):
                            word |= ((in_message >> (8*((in_length & 8)+j))) & 255) << (8*j)
                if phase < 2:
                    v3 ^= word
                else:
                    v2 ^= 255
                for round_index in range(4):
                    if phase == 2 or round_index < 2:
                        v0 = (v0 + v1) & 18446744073709551615
                        v1 = rol64(v1, 13) ^ v0
                        v0 = rol64(v0, 32)
                        v2 = (v2 + v3) & 18446744073709551615
                        v3 = rol64(v3, 16) ^ v2
                        v0 = (v0 + v3) & 18446744073709551615
                        v3 = rol64(v3, 21) ^ v0
                        v2 = (v2 + v1) & 18446744073709551615
                        v1 = rol64(v1, 17) ^ v2
                        v2 = rol64(v2, 32)
                if phase < 2:
                    v0 ^= word
        result = v0 ^ v1 ^ v2 ^ v3
        return {"out_values": result}
