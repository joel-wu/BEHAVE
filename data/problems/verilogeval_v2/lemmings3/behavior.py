class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, bump_left, bump_right, ground, dig):
        state = self.state
        walk_left = int(state == 0)
        walk_right = int(state == 1)
        aaah = int(state == 2 or state == 3)
        digging = int(state == 4 or state == 5)
        if state < 2:
            if not ground:
                self.state = state + 2
            elif dig:
                self.state = state + 4
            elif (state == 0 and bump_left) or (state == 1 and bump_right):
                self.state = 1 - state
        elif state == 2 or state == 3:
            if ground:
                self.state = state - 2
        elif state == 4 or state == 5:
            if not ground:
                self.state = state - 2
        return {"walk_left": walk_left, "walk_right": walk_right, "aaah": aaah, "digging": digging}
