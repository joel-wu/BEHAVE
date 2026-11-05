# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def rol64(x, n):
    return ((x << n) | (x >> (64-n))) & 18446744073709551615

class SHA3_256Short31:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_message, in_length):
        rotations=[0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39, 41, 45, 15, 21, 8, 18, 2, 61, 56, 14]
        constants=[1, 32898, 9223372036854808714, 9223372039002292224, 32907, 2147483649, 9223372039002292353, 9223372036854808585, 138, 136, 2147516425, 2147483658, 2147516555, 9223372036854775947, 9223372036854808713, 9223372036854808579, 9223372036854808578, 9223372036854775936, 32778, 9223372039002259466, 9223372039002292353, 9223372036854808704, 2147483649, 9223372039002292232]
        state=[0]*25
        columns=[0]*5
        mixed=[0]*25
        for i in range(31):
            if i<in_length:
                state[i//8] |= ((in_message>>(8*i))&255) << (8*(i%8))
        state[in_length//8] ^= 6 << (8*(in_length%8))
        state[16] ^= 128 << 56
        for r in range(24):
            for x in range(5):
                columns[x]=state[x]^state[x+5]^state[x+10]^state[x+15]^state[x+20]
            for x in range(5):
                d=columns[(x+4)%5]^rol64(columns[(x+1)%5],1)
                for y in range(5):
                    state[x+5*y] ^= d
            for x in range(5):
                for y in range(5):
                    mixed[y+5*((2*x+3*y)%5)]=rol64(state[x+5*y],rotations[x+5*y])
            for x in range(5):
                for y in range(5):
                    state[x+5*y]=(mixed[x+5*y]^((~mixed[(x+1)%5+5*y])&mixed[(x+2)%5+5*y]))&18446744073709551615
            state[0] ^= constants[r]
        result=0
        for i in range(4):
            result |= state[i] << (64*i)
        return {"out_values": result}
