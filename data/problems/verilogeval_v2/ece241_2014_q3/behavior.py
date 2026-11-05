class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, c, d):
        return {"mux_in": (c | d) | ((c | (d ^ 1)) << 2) | ((c & d) << 3)}
