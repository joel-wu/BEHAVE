class JohnsonCounter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q_state = 0

    def process(self):
        q = self.q_state
        self.q_state = (q >> 1) | ((1 - (q & 1)) << 63)
        return {"Q": q}
