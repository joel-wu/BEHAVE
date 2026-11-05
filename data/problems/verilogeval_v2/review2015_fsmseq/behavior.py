class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, data):
        state = self.state
        start_shifting = int(state == 4)
        if state == 0:
            self.state = 1 if data else 0
        elif state == 1:
            self.state = 2 if data else 0
        elif state == 2:
            self.state = 2 if data else 3
        elif state == 3:
            self.state = 4 if data else 0
        elif state == 4:
            self.state = 4 if data else 4
        return {"start_shifting": start_shifting}
