# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.


class MatMulU8ZeroPoints:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_a, in_b, in_a_zero, in_b_zero):
        result = 0
        for i in range(2):
            for j in range(3):
                value = 0
                for k in range(8):
                    a = ((in_a >> (8*(8*i+k))) & 255) - in_a_zero
                    b = ((in_b >> (8*(3*k+j))) & 255) - in_b_zero
                    value += a*b
                result |= (value & 4294967295) << (32*(3*i+j))
        return {"out_values": result}
