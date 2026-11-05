class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, value):
        state = self.state
        disc = int(state == 8)
        flag = int(state == 9)
        err = int(state == 7)
        if state == 8 or state == 9:
            self.state = value
        elif state == 7:
            self.state = 7 if value else 0
        elif state == 6:
            self.state = 7 if value else 9
        elif state == 5:
            self.state = 6 if value else 8
        else:
            self.state = state + 1 if value else 0
        return {"disc": disc, "flag": flag, "err": err}
