"""Consecutive groups of four valid unsigned samples, independent of clock gaps."""
class Accu:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.partial_sum = 0

    def process(self, data_in):
        total = self.partial_sum + (data_in & 255)
        if self.count == 3:
            self.count = 0
            self.partial_sum = 0
            return {"data_out": total}
        self.count += 1
        self.partial_sum = total
        return None
