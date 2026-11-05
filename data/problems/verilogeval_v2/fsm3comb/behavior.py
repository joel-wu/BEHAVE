class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value, state):
        if value:
            next_state = 3 if state == 2 else 1
        else:
            next_state = 2 if state & 1 else 0
        return {"next_state": next_state, "out": int(state == 3)}
