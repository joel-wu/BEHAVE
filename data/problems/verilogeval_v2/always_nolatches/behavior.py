class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, scancode):
        return {"left": 1 if scancode == 57451 else 0, "down": 1 if scancode == 57458 else 0, "right": 1 if scancode == 57460 else 0, "up": 1 if scancode == 57461 else 0}
