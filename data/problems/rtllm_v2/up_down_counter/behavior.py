"""Every-clock modulo-65536 count, observed before the edge."""
class UpDownCounter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count_reg = 0

    def process(self, up_down):
        value = self.count_reg
        if up_down & 1:
            self.count_reg = (self.count_reg + 1) & 65535
        else:
            self.count_reg = (self.count_reg - 1) & 65535
        return {"count": value}
