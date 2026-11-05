"""Pre-edge observation of two independently selected instruction registers."""
class InstructionRegister:
    def __init__(self):
        self.reset()

    def reset(self):
        self.p1 = 0
        self.p2 = 0

    def process(self, fetch, data):
        ins = self.p1 >> 5
        ad1 = self.p1 & 31
        ad2 = self.p2
        if (fetch & 3) == 1:
            self.p1 = data & 255
        elif (fetch & 3) == 2:
            self.p2 = data & 255
        return {"ins": ins, "ad1": ad1, "ad2": ad2}
