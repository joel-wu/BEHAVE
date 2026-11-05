class Lfsr4:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self):
        state = self.state
        feedback = 1 ^ ((state >> 3) & 1) ^ ((state >> 2) & 1)
        self.state = ((state << 1) | feedback) & 15
        return {"out": state}
