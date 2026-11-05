# SPDX-License-Identifier: Apache-2.0
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def even_ratio(n,d):
    a=-n if n<0 else n
    q=a//d
    r=a-q*d
    if 2*r>d or (2*r==d and q%2!=0):
        q+=1
    return -q if n<0 else q

def clip16(x):
    return max(-32768,min(32767,x))

class RepetitionPenalty16:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_tokens, in_length, in_scores):
        seen=0
        for j in range(8):
            if j<min(8,in_length):
                seen |= 1 << ((in_tokens >> (4*j))&15)
        result=seen << 256
        for v in range(16):
            value=s16(in_scores >> (16*v))
            if (seen >> v)&1:
                value=even_ratio(3*value,2) if value<0 else even_ratio(2*value,3)
                value=clip16(value)
            result |= (value&65535) << (16*v)
        return {"out_values": result}
