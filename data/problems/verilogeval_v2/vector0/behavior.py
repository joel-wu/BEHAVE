class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, vec):
        return {"outv": vec, "o2": (vec >> 2) & 1, "o1": (vec >> 1) & 1, "o0": vec & 1}
