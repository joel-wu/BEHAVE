class EightLaneSum:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, in_values):
        total = 0
        for lane in range(8):
            value = (in_values >> (16 * lane)) & 65535
            if value >= 32768:
                value -= 65536
            total += value
        return {"out_sum": total & 524287}
