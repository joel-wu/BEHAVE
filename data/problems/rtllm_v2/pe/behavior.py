"""Every-clock unsigned multiply-accumulate modulo 2**32."""
class MultiplyAccumulate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.acc = 0

    def process(self, a, b):
        value = self.acc
        self.acc = (self.acc + (a & 4294967295) * (b & 4294967295)) & 4294967295
        return {"c": value}
