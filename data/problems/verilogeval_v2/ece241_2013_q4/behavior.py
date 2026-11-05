class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, s):
        state = self.state
        fr2 = int(state == 0)
        fr1 = int(state <= 2)
        fr0 = int(state < 5)
        dfr = int(state == 0 or state == 2 or state == 4)
        if state == 0:
            self.state = 1 if s & 1 else 0
        elif state <= 2:
            self.state = 3 if s & 2 else state if s & 1 else 0
        elif state <= 4:
            self.state = 5 if s & 4 else state if s & 2 else 2
        else:
            self.state = 5 if s & 4 else 4
        return {"fr2": fr2, "fr1": fr1, "fr0": fr0, "dfr": dfr}
