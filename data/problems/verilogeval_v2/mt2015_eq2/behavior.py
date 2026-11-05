class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B):
        return {"z": int(A == B)}
