class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value, state):
        next_state = 0
        if value:
            next_state = (int((state & 769) != 0) << 1) | ((state & 62) << 1) | (int((state & 192) != 0) << 7)
        else:
            next_state = int((state & 927) != 0) | ((state & 96) << 3)
        return {"next_state": next_state, "out1": int((state & 768) != 0), "out2": int((state & 640) != 0)}
