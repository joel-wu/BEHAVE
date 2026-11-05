def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DataSystemsDistanceInnerProductInt8D64:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_vec_a, in_vec_b):
        inner_product = 0
        for i in range(64):
            a_i = to_signed((in_vec_a >> (8 * i)) & 0xFF, 8)
            b_i = to_signed((in_vec_b >> (8 * i)) & 0xFF, 8)
            inner_product += a_i * b_i
        distance = 1 - inner_product
        return {"out_distance": to_unsigned(distance, 21)}
