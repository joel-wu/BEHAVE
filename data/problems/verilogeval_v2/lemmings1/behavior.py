class Behavior:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 0

    def process(self, bump_left, bump_right):
        state = self.state
        walk_left = int(state == 0)
        walk_right = int(state == 1)
        if state < 2:
            if (state == 0 and bump_left) or (state == 1 and bump_right):
                self.state = 1 - state
        return {"walk_left": walk_left, "walk_right": walk_right}
