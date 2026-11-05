# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s8(x):
    x = x & 255
    return x - 256 if x >= 128 else x

def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def clip8(x):
    if x < -128:
        return -128
    if x > 127:
        return 127
    return x

def away(n, d):
    return -((-n+d//2)//d) if n < 0 else (n+d//2)//d

class HardGateGRU2:
    def __init__(self):
        self.reset()

    def reset(self):
        self.hidden=0

    def process(self, in_data, in_weights, in_recurrent, in_bias, in_clear):
        old=[0,0] if in_clear else [s8(self.hidden),s8(self.hidden >> 8)]
        gates=[0,0,0,0]
        for g in range(2):
            for i in range(2):
                value=128*s16(in_bias >> (16*(2*g+i)))
                for j in range(2):
                    value += s8(in_weights >> (8*(4*g+2*i+j)))*s8(in_data >> (8*j))
                    value += s8(in_recurrent >> (8*(4*g+2*i+j)))*old[j]
                gates[2*g+i]=max(0,min(128,away(value,512)+64))
        result=0
        for i in range(2):
            value=128*s16(in_bias >> (16*(4+i)))
            for j in range(2):
                value += s8(in_weights >> (8*(8+2*i+j)))*s8(in_data >> (8*j))
                value += s8(in_recurrent >> (8*(8+2*i+j)))*away(gates[2+j]*old[j],128)
            candidate=clip8(away(value,128))
            updated=clip8(away((128-gates[i])*candidate+gates[i]*old[i],128))
            result |= (updated & 255) << (8*i)
        self.hidden=result
        return {"out_values": result}
