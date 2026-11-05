class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B):
        result = (A - B) & 18446744073709551615
        overflow = ((A ^ B) & (A ^ result)) >> 63
        return {"result": result, "overflow": overflow}
