class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = 0

    def process(self, shift_ena, count_ena, data):
        out = {"q": self.q}
        if shift_ena:
            self.q = ((self.q << 1) | data) & 15
        elif count_ena:
            self.q = (self.q - 1) & 15
        return out
