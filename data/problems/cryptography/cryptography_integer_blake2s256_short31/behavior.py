# SPDX-License-Identifier: CC0-1.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def ror32(x, n):
    return ((x >> n) | (x << (32-n))) & 4294967295

class Blake2s256Short31:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_message, in_length):
        iv = [1779033703, 3144134277, 1013904242, 2773480762, 1359893119, 2600822924, 528734635, 1541459225]
        sigma = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3, 11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4, 7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8, 9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13, 2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9, 12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11, 13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10, 6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5, 10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0]
        h = [0] * 8
        v = [0] * 16
        message = [0] * 16
        for i in range(31):
            if i < in_length:
                message[i//4] |= ((in_message >> (8*i)) & 255) << (8*(i%4))
        for i in range(8):
            h[i] = iv[i]
        h[0] ^= 0x01010020
        for i in range(8):
            v[i] = h[i]
            v[8+i] = iv[i]
        v[12] ^= in_length
        v[14] ^= 4294967295
        for round_index in range(10):
            for j in range(8):
                a = j % 4
                b = 4 + (a if j < 4 else (a+1)%4)
                c = 8 + (a if j < 4 else (a+2)%4)
                d = 12 + (a if j < 4 else (a+3)%4)
                x = message[sigma[16*round_index+2*j]]
                y = message[sigma[16*round_index+2*j+1]]
                v[a] = (v[a]+v[b]+x) & 4294967295
                v[d] = ror32(v[d]^v[a],16)
                v[c] = (v[c]+v[d]) & 4294967295
                v[b] = ror32(v[b]^v[c],12)
                v[a] = (v[a]+v[b]+y) & 4294967295
                v[d] = ror32(v[d]^v[a],8)
                v[c] = (v[c]+v[d]) & 4294967295
                v[b] = ror32(v[b]^v[c],7)
        result = 0
        for i in range(8):
            result |= (h[i]^v[i]^v[i+8]) << (32*i)
        return {"out_values": result}
