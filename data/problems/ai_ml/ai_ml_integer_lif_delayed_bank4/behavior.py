# SPDX-License-Identifier: MIT
# Integer/fixed-shape adaptation; see source.json for provenance and modifications.
def s16(x):
    x = x & 65535
    return x - 65536 if x >= 32768 else x

def away(n, d):
    return -((-n+d//2)//d) if n < 0 else (n+d//2)//d

class LIFDelayedBank4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.membrane=0

    def process(self, in_current, in_beta, in_threshold, in_clear):
        result=0
        next_membrane=0
        for i in range(4):
            old=0 if in_clear else s16(self.membrane >> (16*i))
            threshold=max(1,(in_threshold >> (16*i)) & 65535)
            value=away(((in_beta >> (8*i)) & 255)*old,256)+s16(in_current >> (16*i))
            if old > threshold:
                value -= threshold
            value=max(-32768,min(32767,value))
            next_membrane |= (value & 65535) << (16*i)
            if value > threshold:
                result |= 1 << (64+i)
        self.membrane=next_membrane
        result |= next_membrane
        return {"out_values": result}
