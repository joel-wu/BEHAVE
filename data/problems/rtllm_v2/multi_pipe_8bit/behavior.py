"""Each enabled input has one product; clock latency belongs to the RTL."""
class Pipelined8BitMultiplier:
    def __init__(self):
        self.reset()

    def reset(self):
        pass

    def process(self, mul_a, mul_b):
        return {"mul_out": (mul_a & 255) * (mul_b & 255)}
