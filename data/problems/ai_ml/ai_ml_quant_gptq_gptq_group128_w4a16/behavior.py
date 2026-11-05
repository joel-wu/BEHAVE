# GPTQ column update, fixed integer realization. See SOURCE_LICENSE.
def signed(x, bits):
    return ((x+(1 << (bits-1))) & ((1 << bits)-1))-(1 << (bits-1))

def nearest_even(x, divisor):
    magnitude = abs(x)
    q = magnitude//divisor
    r = magnitude-q*divisor
    if 2*r > divisor or (2*r == divisor and (q & 1) != 0):
        q += 1
    if x < 0:
        q = -q
    return q

def update_group(weights, first, second, count):
    low = 0
    high = 0
    for i in range(128):
        if i < count:
            low = min(low, weights[i])
            high = max(high, weights[i])
    scale = max(1, (high-low+14)//15)
    zero = low+8*scale
    work = [0]*128
    codes = [0]*128
    for i in range(128):
        if i < count:
            work[i] = weights[i]*65536
    for i in range(128):
        if i < count:
            code = min(15,max(0,nearest_even(work[i]-low*65536,scale*65536)))
            codes[i] = (code-8) & 15
            error = work[i]-(low+code*scale)*65536
            if i+1 < count:
                work[i+1] -= nearest_even(error*first[i],256)
            if i+2 < count:
                work[i+2] -= nearest_even(error*second[i],256)
    packed = 0
    for i in range(128):
        packed |= codes[i] << (4*i)
    return [scale, zero & 65535, packed]

class GPTQGroup128:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_weights, in_first, in_second):
        weights = [0]*128
        first = [0]*128
        second = [0]*128
        for i in range(128):
            weights[i] = signed(in_weights >> (16*i),16)
            first[i] = signed(in_first >> (8*i),8)
            second[i] = signed(in_second >> (8*i),8)
        result = update_group(weights,first,second,128)
        return {"out_scale": result[0], "out_zero": result[1], "out_codes": result[2]}
