class RingCounter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 1

    def process(self):
        state = self.state
        self.state = ((state << 1) | (state >> 7)) & 255
        return {"out": state}
