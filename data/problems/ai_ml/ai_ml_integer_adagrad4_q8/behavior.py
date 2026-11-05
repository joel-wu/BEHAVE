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

def root34(x):
    r=0
    for k in range(16,-1,-1):
        candidate=r+(1<<k)
        if candidate*candidate<=x:
            r=candidate
    return r

class Adagrad4Q8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_params, in_gradient, in_accumulator):
        result=0
        for i in range(4):
            p=s16(in_params >> (16*i))
            g=s16(in_gradient >> (16*i))
            v=(in_accumulator >> (32*i)) & 4294967295
            vnew=min(4294967295,v+g*g)
            d=root34(vnew)+1
            pnew=clip16(p-even_ratio(16*g,d))
            result |= (pnew&65535) << (16*i)
            result |= vnew << (64+32*i)
        return {"out_values": result}
