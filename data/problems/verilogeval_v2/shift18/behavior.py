class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, load, ena, amount, data):
        out = {"q": self.q}
        if load:
            self.q = data
        elif ena:
            if amount == 0:
                self.q = (self.q << 1) & ((1 << 64) - 1)
            elif amount == 1:
                self.q = (self.q << 8) & ((1 << 64) - 1)
            elif amount == 2:
                self.q = (self.q >> 1) | (self.q & (1 << 63))
            else:
                self.q = (self.q >> 8) | ((255 << 56) if (self.q >> 63) else 0)
        return out
