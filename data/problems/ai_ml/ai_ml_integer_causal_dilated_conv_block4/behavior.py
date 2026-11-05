# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

class CausalDilatedConvBlock4:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_history, in_x, in_weights):
        sequence=in_history | (in_x << 64)
        result=in_x << 256
        for t in range(4):
            for o in range(2):
                value=0
                for c in range(2):
                    for k in range(3):
                        value+=s8(in_weights >> (8*(6*o+3*c+k)))*s8(sequence >> (8*(2*(t+2*k)+c)))
                result |= (value&4294967295) << (32*(2*t+o))
        return {"out_values": result}
