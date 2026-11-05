class BitRegister:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q_state = 0

    def process(self, in_d):
        self.q_state = in_d & 1
        return {"out_q": self.q_state}
