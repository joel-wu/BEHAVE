class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, data, done_counting, ack):
        state = self.state
        shift_ena = int(state >= 4 and state < 8)
        counting = int(state == 8)
        done = int(state == 9)
        if state == 0:
            self.state = 1 if data else 0
        elif state == 1:
            self.state = 2 if data else 0
        elif state == 2:
            self.state = 2 if data else 3
        elif state == 3:
            self.state = 4 if data else 0
        elif state < 8:
            self.state = state + 1
        elif state == 8:
            self.state = 9 if done_counting else 8
        else:
            self.state = 0 if ack else 9
        return {"shift_ena": shift_ena, "counting": counting, "done": done}
