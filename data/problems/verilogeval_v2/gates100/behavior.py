class Behavior:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, value):
        count = 0
        for i in range(100):
            count += (value >> i) & 1
        return {"out_and": 1 if count == 100 else 0, "out_or": 1 if count else 0, "out_xor": count & 1}
