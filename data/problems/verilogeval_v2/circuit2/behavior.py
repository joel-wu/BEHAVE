class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d):
        return {"q": a ^ b ^ c ^ d ^ 1}
