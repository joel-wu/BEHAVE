# SPDX-License-Identifier: BSD-3-Clause
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

class HuberLossGrad8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_prediction, in_target, in_delta_minus1):
        result=0
        total=0
        d=in_delta_minus1+1
        for i in range(8):
            e=s16(in_prediction >> (16*i))-s16(in_target >> (16*i))
            a=-e if e<0 else e
            total+=e*e if a<=d else 2*d*a-d*d
            gradient=max(-d,min(d,e))
            result |= (gradient&65535) << (32+16*i)
        result |= total
        return {"out_values": result}
