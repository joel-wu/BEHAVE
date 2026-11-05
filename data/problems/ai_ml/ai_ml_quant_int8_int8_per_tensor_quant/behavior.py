class Int8PerTensorQuant:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_values, in_scale, in_zero_point):
        scale = in_scale & 0xFFFFFFFF
        zero = in_zero_point & 255
        if zero >= 128:
            zero -= 256
        result = 0
        if scale == 0:
            return {"out_quantized": 0, "out_error": 1}
        for lane in range(8):
            value = (in_values >> (16 * lane)) & 65535
            if value >= 32768:
                value -= 65536
            magnitude = -value if value < 0 else value
            numerator = magnitude * 256
            q = numerator // scale
            remainder = numerator % scale
            if remainder * 2 > scale or (remainder * 2 == scale and (q & 1) != 0):
                q += 1
            if value < 0:
                q = -q
            q += zero
            if q > 127:
                q = 127
            elif q < -128:
                q = -128
            result |= (q & 255) << (8 * lane)
        return {"out_quantized": result, "out_error": 0}
