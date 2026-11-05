def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def is_nan(a):
    return ((a >> 23) & 0xFF) == 0xFF and (a & 0x7FFFFF) != 0


def is_zero(a):
    return (a & 0x7FFFFFFF) == 0


def fp32_lt(a, b):
    a_nan = is_nan(a)
    b_nan = is_nan(b)
    a_zero = is_zero(a)
    b_zero = is_zero(b)
    if a_nan or b_nan:
        return False
    if a_zero and b_zero:
        return False
    a_sign = (a >> 31) & 1
    b_sign = (b >> 31) & 1
    if a_sign != b_sign:
        return a_sign == 1
    a_mag = a & 0x7FFFFFFF
    b_mag = b & 0x7FFFFFFF
    if a_sign == 0:
        return a_mag < b_mag
    return a_mag > b_mag


class Fp32MaxNoIdx16:
    def __init__(self):
        self.reset()

    def reset(self):
        self.running_max = 0xFF7FFFFF
        self.element_count = 0

    def process(self, in_value):
        in_value = to_unsigned(in_value, 32)
        running_max = self.running_max
        element_count = self.element_count
        updated_running_max = in_value if fp32_lt(running_max, in_value) else running_max
        if element_count == 15:
            self.running_max = 0xFF7FFFFF
            self.element_count = 0
            return {"out_max": to_unsigned(updated_running_max, 32)}
        self.running_max = updated_running_max
        self.element_count = element_count + 1
        return None
