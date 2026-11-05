class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, a, b, c, d):
        return {"out": ((c ^ 1) & (b ^ 1)) | ((d ^ 1) & (a ^ 1)) | (a & c & d) | (b & c & d)}
