# SPDX-License-Identifier: Unlicense
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def xtime(x):
    return ((x << 1) ^ (27 if x & 128 else 0)) & 255

class AES128Encrypt:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_key, in_plaintext):
        box = [99, 124, 119, 123, 242, 107, 111, 197, 48, 1, 103, 43, 254, 215, 171, 118, 202, 130, 201, 125, 250, 89, 71, 240, 173, 212, 162, 175, 156, 164, 114, 192, 183, 253, 147, 38, 54, 63, 247, 204, 52, 165, 229, 241, 113, 216, 49, 21, 4, 199, 35, 195, 24, 150, 5, 154, 7, 18, 128, 226, 235, 39, 178, 117, 9, 131, 44, 26, 27, 110, 90, 160, 82, 59, 214, 179, 41, 227, 47, 132, 83, 209, 0, 237, 32, 252, 177, 91, 106, 203, 190, 57, 74, 76, 88, 207, 208, 239, 170, 251, 67, 77, 51, 133, 69, 249, 2, 127, 80, 60, 159, 168, 81, 163, 64, 143, 146, 157, 56, 245, 188, 182, 218, 33, 16, 255, 243, 210, 205, 12, 19, 236, 95, 151, 68, 23, 196, 167, 126, 61, 100, 93, 25, 115, 96, 129, 79, 220, 34, 42, 144, 136, 70, 238, 184, 20, 222, 94, 11, 219, 224, 50, 58, 10, 73, 6, 36, 92, 194, 211, 172, 98, 145, 149, 228, 121, 231, 200, 55, 109, 141, 213, 78, 169, 108, 86, 244, 234, 101, 122, 174, 8, 186, 120, 37, 46, 28, 166, 180, 198, 232, 221, 116, 31, 75, 189, 139, 138, 112, 62, 181, 102, 72, 3, 246, 14, 97, 53, 87, 185, 134, 193, 29, 158, 225, 248, 152, 17, 105, 217, 142, 148, 155, 30, 135, 233, 206, 85, 40, 223, 140, 161, 137, 13, 191, 230, 66, 104, 65, 153, 45, 15, 176, 84, 187, 22]
        state = [0] * 16
        key = [0] * 16
        shifted = [0] * 16
        for i in range(16):
            key[i] = (in_key >> (8*i)) & 255
            state[i] = ((in_plaintext >> (8*i)) & 255) ^ key[i]
        rcon = 1
        for round_index in range(1, 11):
            t0 = box[key[13]] ^ rcon
            t1 = box[key[14]]
            t2 = box[key[15]]
            t3 = box[key[12]]
            key[0] ^= t0
            key[1] ^= t1
            key[2] ^= t2
            key[3] ^= t3
            for i in range(4, 16):
                key[i] ^= key[i-4]
            rcon = xtime(rcon)
            for col in range(4):
                for row in range(4):
                    shifted[4*col+row] = box[state[4*((col+row)%4)+row]]
            if round_index < 10:
                for col in range(4):
                    a = shifted[4*col]
                    b = shifted[4*col+1]
                    c = shifted[4*col+2]
                    d = shifted[4*col+3]
                    total = a ^ b ^ c ^ d
                    shifted[4*col] = a ^ total ^ xtime(a ^ b)
                    shifted[4*col+1] = b ^ total ^ xtime(b ^ c)
                    shifted[4*col+2] = c ^ total ^ xtime(c ^ d)
                    shifted[4*col+3] = d ^ total ^ xtime(d ^ a)
            for i in range(16):
                state[i] = shifted[i] ^ key[i]
        result = 0
        for i in range(16):
            result |= state[i] << (8*i)
        return {"out_values": result}
