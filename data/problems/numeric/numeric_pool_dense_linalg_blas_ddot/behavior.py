def to_unsigned(v, b):
    return v & ((1 << b) - 1)


def to_signed(v, b):
    v &= (1 << b) - 1
    if v & (1 << (b - 1)):
        v -= 1 << b
    return v


class DotProduct5:
    def __init__(self):
        self.reset()

    def reset(self):
        self.element_count = 0
        self.running_sum = 0

    def process(self, in_dx, in_dy):
        element_count = self.element_count
        running_sum = self.running_sum
        dx = to_signed(in_dx, 16)
        dy = to_signed(in_dy, 16)
        product = dx * dy
        new_running_sum = to_signed(product, 35) if element_count == 0 else to_signed(running_sum + product, 35)
        if element_count == 4:
            self.element_count = 0
            self.running_sum = 0
            return {"out_ddot": to_unsigned(new_running_sum, 35)}
        self.element_count = element_count + 1
        self.running_sum = new_running_sum
        return None