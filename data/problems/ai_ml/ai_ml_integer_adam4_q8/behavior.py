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

class Adam4Q8:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_params, in_gradient, in_mean, in_square, in_step_minus1):
        result=0
        a=1 << (in_step_minus1+1)
        b=a-1
        for i in range(4):
            p=s16(in_params >> (16*i))
            g=s16(in_gradient >> (16*i))
            m=s16(in_mean >> (16*i))
            v=(in_square >> (32*i)) & 4294967295
            mnew=even_ratio(m+g,2)
            vnew=(v+g*g)//2
            vhat=vnew*a//b
            d=root34(vhat)+1
            delta=even_ratio(16*mnew*a,b*d)
            result |= (clip16(p-delta)&65535) << (16*i)
            result |= (mnew&65535) << (64+16*i)
            result |= vnew << (128+32*i)
        return {"out_values": result}
