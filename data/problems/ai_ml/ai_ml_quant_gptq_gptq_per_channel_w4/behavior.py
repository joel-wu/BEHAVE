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

class GPTQPerChannelW4:
    def __init__(self, N=128):
        assert 1 <= N <= 128
        self.N = N
        self.reset()

    def reset(self):
        self.weights = [0]*128
        self.first = [0]*128
        self.second = [0]*128
        self.count = 0

    def process(self, in_weight, in_first, in_second, in_last):
        self.weights[self.count] = signed(in_weight,16)
        self.first[self.count] = signed(in_first,8)
        self.second[self.count] = signed(in_second,8)
        self.count += 1
        if in_last == 0 and self.count < self.N:
            return None
        value = update_group(self.weights,self.first,self.second,self.count)
        outputs = []
        for i in range(128):
            if i < self.count:
                outputs.append({"out_qweight": (value[2] >> (4*i)) & 15,
                                "out_scale": value[0], "out_zero": value[1],
                                "out_index": i, "out_last": int(i == self.count-1)})
        self.count = 0
        return outputs
