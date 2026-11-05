"""Mealy 10011 detector, observed before each non-reset clock edge."""
class Fsm:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = 0

    def process(self, IN):
        bit = IN & 1
        match = 1 if self.history == 9 and bit == 1 else 0
        self.history = ((self.history << 1) | bit) & 15
        return {"MATCH": match}
