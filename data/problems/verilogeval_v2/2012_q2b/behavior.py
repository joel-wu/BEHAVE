class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, y, w):
        return {"Y1": (y & 1) & w, "Y3": int((y & 54) != 0) & (w ^ 1)}
