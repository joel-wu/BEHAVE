class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value, state):
        a = state & 1
        b = (state >> 1) & 1
        c = (state >> 2) & 1
        d = (state >> 3) & 1
        next_state = ((a | c) & (value ^ 1)) | (((a | b | d) & value) << 1) | (((b | d) & (value ^ 1)) << 2) | ((c & value) << 3)
        return {"next_state": next_state, "out": d}
