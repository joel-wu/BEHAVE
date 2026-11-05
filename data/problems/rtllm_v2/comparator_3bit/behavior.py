class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, A, B):
        return {"A_greater": int(A > B), "A_equal": int(A == B), "A_less": int(A < B)}
