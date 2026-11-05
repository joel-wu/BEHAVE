class CountMinSketchUpdateD4W256:
    def __init__(self):
        self.reset()

    def reset(self):
        self.counter = [0] * 1024

    def process(self, in_h1, in_h2, in_delta):
        h1 = in_h1 & 255
        h2 = in_h2 & 255
        delta = in_delta & 65535
        counts = 0
        estimate = 65535
        for row in range(4):
            column = (h1 + row * h2) & 255
            address = row * 256 + column
            value = self.counter[address] + delta
            if value > 65535:
                value = 65535
            self.counter[address] = value
            counts = counts | (value << (16 * row))
            if value < estimate:
                estimate = value
        return {"out_counts": counts, "out_estimate": estimate}
