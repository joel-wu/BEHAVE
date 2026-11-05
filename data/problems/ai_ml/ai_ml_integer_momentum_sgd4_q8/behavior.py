# SPDX-License-Identifier: BSD-3-Clause
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

class MomentumSGD4Q8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_params, in_gradient, in_momentum, in_first, in_nesterov):
        result=0
        for i in range(4):
            p=s16(in_params >> (16*i))
            g=s16(in_gradient >> (16*i))
            m=s16(in_momentum >> (16*i))
            mnew=g if in_first else clip16(even_ratio(3*m+4*g,4))
            delta=even_ratio(4*g+3*mnew,64) if in_nesterov else even_ratio(mnew,16)
            result |= (clip16(p-delta)&65535) << (16*i)
            result |= (mnew&65535) << (64+16*i)
        return {"out_values": result}
